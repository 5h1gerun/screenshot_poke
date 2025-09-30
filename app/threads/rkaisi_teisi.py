from __future__ import annotations

import os
import threading
import time
from typing import Optional, Tuple
import queue

import cv2

from app.obs_client import ObsClient
from app.utils.image import Rect, crop_image_by_rect, match_template
try:
    from app.utils.native_match import (
        match_template_region_native as _match_native_region,
        NATIVE_AVAILABLE as _NATIVE_MATCH,
    )
except Exception:
    def _match_native_region(*_args, **_kwargs):
        return False
    _NATIVE_MATCH = False
from app.utils.logging import UiLogger
from app.utils import pairs as pairs_utils


class RkaisiTeisiThread(threading.Thread):
    """Start/Stop OBS recording depending on template presence.

    Equivalent to the original behavior with safer, clearer structure.
    """

    MATCH_THRESHOLD = 0.4

    def __init__(self, obs: ObsClient, base_dir: str, logger: Optional[UiLogger] = None, source_name: str = "Capture1", result_queue: Optional["queue.Queue"] = None) -> None:
        super().__init__(daemon=True)
        self._obs = obs
        self._base = base_dir
        self._log = logger or UiLogger()
        self._stop = threading.Event()
        self._recording = False
        self._rec_start_ts: Optional[float] = None
        self._source = source_name
        # Optional: publish stop marker for default-win logic
        self._rq = result_queue

        # Paths
        self._scene_path = os.path.join(self._base, "scene2.png")
        self._masu1_tpl = os.path.join(self._base, "masu1.png")
        self._mark_tpl = os.path.join(self._base, "mark.png")
        self._masu1_crop = os.path.join(self._base, "masu1cropped.png")
        self._mark_crop = os.path.join(self._base, "markcropped.png")

        # Rects
        self._masu1_rect: Rect = ((1541, 229), (1651, 843))
        self._mark_rect: Rect = ((0, 0), (96, 72))

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self._log.log("[録開始/停止] スレッド開始")
        try:
            while not self._stop.is_set():
                self._loop()
        except Exception as e:
            self._log.log(f"[録開始/停止] エラー: {e}")
        finally:
            if self._recording:
                self._log.log("[録開始/停止] 終了時に録画を停止します")
                try:
                    self._obs.stop_recording()
                except Exception:
                    pass
                # Try to associate images with the recording window
                try:
                    if self._rec_start_ts is not None:
                        root_base = os.path.dirname(self._base)
                        pairs_utils.associate_recording_window(root_base, self._rec_start_ts, time.time())
                except Exception:
                    pass
            self._log.log("[録開始/停止] スレッド停止")

    # --- internals ---
    def _loop(self) -> None:
        # Screenshot and crop
        self._obs.take_screenshot(self._source, self._scene_path)
        img = cv2.imread(self._scene_path)
        if img is None:
            if self._stop.wait(0.5):
                return
            return

        masu1_crop_img = crop_image_by_rect(img, self._masu1_rect)
        mark_crop_img = crop_image_by_rect(img, self._mark_rect)
        cv2.imwrite(self._masu1_crop, masu1_crop_img)
        cv2.imwrite(self._mark_crop, mark_crop_img)

        masu_tpl = cv2.imread(self._masu1_tpl)
        mark_tpl = cv2.imread(self._mark_tpl)
        if masu_tpl is None or mark_tpl is None:
            self._log.log("[録開始/停止] テンプレートが見つからないため待機")
            if self._stop.wait(1):
                return
            return

        try:
            use_native = (os.getenv("USE_NATIVE_MATCH", "1") or "1").strip().lower() not in ("0", "false", "no")
        except Exception:
            use_native = True

        cond_start = False
        if use_native and _NATIVE_MATCH:
            cond_start = _match_native_region(self._scene_path, self._masu1_rect, self._masu1_tpl, float(self.MATCH_THRESHOLD))
        else:
            cond_start = match_template(masu1_crop_img, masu_tpl, self.MATCH_THRESHOLD, grayscale=False)

        if (not self._recording) and cond_start:
            self._log.log("[録開始/停止] 'masu1' 検出 → 録画開始")
            started = False
            try:
                self._obs.start_recording()
                # Verify it actually started (poll briefly)
                for _ in range(10):
                    st = self._obs.is_recording()
                    if st is True:
                        started = True
                        break
                    time.sleep(0.2)
                # One retry if not started
                if not started:
                    self._obs.start_recording()
                    for _ in range(10):
                        st = self._obs.is_recording()
                        if st is True:
                            started = True
                            break
                        time.sleep(0.2)
            except Exception as e:
                self._log.log(f"[録開始/停止] 録画開始に失敗: {e}")
                started = False

            if started:
                self._recording = True
                self._rec_start_ts = time.time()
                if self._stop.wait(140):
                    return
                return
            else:
                self._log.log("[録開始/停止] 録画が開始されませんでした")
                if self._stop.wait(1):
                    return
                return

        cond_stop = False
        if use_native and _NATIVE_MATCH:
            cond_stop = _match_native_region(self._scene_path, self._mark_rect, self._mark_tpl, float(self.MATCH_THRESHOLD))
        else:
            cond_stop = match_template(mark_crop_img, mark_tpl, self.MATCH_THRESHOLD, grayscale=False)
        if self._recording and cond_stop:
            self._log.log("[録開始/停止] 'mark' 検出 → 録画停止")
            # Emit a stop marker for association/default-win logic
            try:
                if self._rq is not None:
                    self._rq.put({"timestamp": time.time(), "type": "stop"}, timeout=0.05)
            except Exception:
                pass
            stopped = False
            try:
                self._obs.stop_recording()
                for _ in range(10):
                    st = self._obs.is_recording()
                    if st is False:
                        stopped = True
                        break
                    time.sleep(0.2)
                if not stopped:
                    self._obs.stop_recording()
                    for _ in range(10):
                        st = self._obs.is_recording()
                        if st is False:
                            stopped = True
                            break
                        time.sleep(0.2)
            except Exception as e:
                self._log.log(f"[録開始/停止] 録画停止に失敗: {e}")
                stopped = False
            finally:
                # Proceed with bookkeeping regardless; we'll best-effort associate
                try:
                    if self._rec_start_ts is not None:
                        root_base = os.path.dirname(self._base)
                        pairs_utils.associate_recording_window(root_base, self._rec_start_ts, time.time())
                except Exception:
                    pass
                self._rec_start_ts = None
                self._recording = False if stopped else self._recording
            if self._stop.wait(0.5):
                return


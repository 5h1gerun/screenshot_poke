from __future__ import annotations

import os
import threading
import time
from typing import Optional, Tuple
import queue

import cv2

from app.obs_client import ObsClient
from app.utils.image import Rect, crop_image_by_rect, match_template
from app.utils.logging import UiLogger
from app.utils import pairs as pairs_utils


try:
    from app.threads.native_wrappers import is_available as _native_ok, RkaisiTeisiNativeThread as _NativeRkaisi
except Exception:
    def _native_ok() -> bool:  # type: ignore
        return False
    _NativeRkaisi = None  # type: ignore


class PyRkaisiTeisiThread(threading.Thread):
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
        # Poll/guard durations (seconds), tunable via env. Defaults preserve prior behavior.
        try:
            import os as _os
            self._poll_sec = float((_os.getenv("RKAISI_START_POLL_SEC", "2") or 2))
        except Exception:
            self._poll_sec = 2.0
        try:
            import os as _os
            self._guard_sec = float((_os.getenv("RKAISI_GUARD_SEC", "140") or 140))
        except Exception:
            self._guard_sec = 140.0

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
                        rec_dir = os.getenv("RECORDINGS_DIR", "").strip()
                        if not rec_dir:
                            self._log.log("[組合せ] RECORDINGS_DIR が未設定のため録画ファイルを特定できません")
                        else:
                            start_ts = float(self._rec_start_ts)
                            end_ts = time.time()
                            video = pairs_utils.find_recording_file(rec_dir, start_ts, end_ts)
                            if not video:
                                self._log.log("[組合せ] 録画ファイルが見つかりませんでした（時間範囲/拡張子/マージンを確認）")
                            else:
                                mapping = pairs_utils.associate_recording_window(root_base, start_ts, end_ts)
                                base = os.path.basename(video)
                                if mapping is not None:
                                    self._log.log(f"[組合せ] 画像と録画を関連付けました -> {base}")
                                else:
                                    self._log.log(f"[組合せ] 関連付けに失敗しました -> {base}")
                except Exception as e:
                    self._log.log(f"[組合せ] エラー: {e}")
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

        if (not self._recording) and match_template(masu1_crop_img, masu_tpl, self.MATCH_THRESHOLD, grayscale=False):
            self._log.log("[録開始/停止] 'masu1' 検出 → 録画開始")
            started = False
            unknown_count = 0
            try:
                try:
                    method = self._obs.start_recording_diag()
                    self._log.log(f"[録開始/停止] 開始メソッド: {method}")
                except Exception:
                    # Fallback to legacy wrapper
                    self._obs.start_recording()
                    self._log.log("[録開始/停止] 開始メソッド: legacy")
                # Verify it actually started (poll briefly)
                iters = max(1, int(self._poll_sec / 0.2))
                for _ in range(iters):
                    st = self._obs.is_recording()
                    if st is True:
                        started = True
                        break
                    if st is None:
                        unknown_count += 1
                    time.sleep(0.2)
                # One retry if not started
                if not started:
                    try:
                        method2 = self._obs.start_recording_diag()
                        self._log.log(f"[録開始/停止] 再試行メソッド: {method2}")
                    except Exception:
                        self._obs.start_recording()
                        self._log.log("[録開始/停止] 再試行メソッド: legacy")
                    for _ in range(iters):
                        st = self._obs.is_recording()
                        if st is True:
                            started = True
                            break
                        if st is None:
                            unknown_count += 1
                        time.sleep(0.2)
            except Exception as e:
                self._log.log(f"[録開始/停止] 録画開始に失敗: {e}")
                started = False

            if (not started) and unknown_count >= 16:
                # 状態が取れない互換環境向け: 環境変数で強制開始扱い
                try:
                    import os as _os
                    assume = (_os.getenv("RKAISI_ASSUME_START", "false") or "false").strip().lower()
                except Exception:
                    assume = "false"
                if assume in ("1", "true", "yes", "on"):
                    self._log.log("[録開始/停止] 録画状態が不明のため開始扱いで継続 (RKAISI_ASSUME_START=true)")
                    started = True

            if started:
                self._recording = True
                self._rec_start_ts = time.time()
                if self._stop.wait(self._guard_sec):
                    return
                return
            else:
                self._log.log("[録開始/停止] 録画が開始されませんでした")
                if self._stop.wait(1):
                    return
                return

        if self._recording and match_template(mark_crop_img, mark_tpl, self.MATCH_THRESHOLD, grayscale=False):
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


class RkaisiTeisiThread(threading.Thread):
    """Dispatch to native C++ implementation if available; else Python fallback.

    Matches the previous RkaisiTeisiThread interface.
    """

    MATCH_THRESHOLD = 0.4

    def __init__(self, obs: ObsClient, base_dir: str, logger: Optional[UiLogger] = None, source_name: str = "Capture1", result_queue: Optional["queue.Queue"] = None) -> None:
        self._use_native = bool(_native_ok())
        self._th: Optional[threading.Thread]
        if self._use_native and _NativeRkaisi is not None:
            # Native expects handan dir (where scene2.png and templates live)
            self._th = _NativeRkaisi(obs, base_dir, logger, source_name=source_name, result_queue=result_queue, threshold=self.MATCH_THRESHOLD)
        else:
            self._th = PyRkaisiTeisiThread(obs, base_dir, logger, source_name=source_name, result_queue=result_queue)

    def start(self) -> None:  # type: ignore[override]
        if self._th:
            self._th.start()

    def is_alive(self) -> bool:  # type: ignore[override]
        try:
            return bool(self._th and self._th.is_alive())
        except Exception:
            return False

    def join(self, timeout: Optional[float] = None) -> None:  # type: ignore[override]
        try:
            if self._th:
                self._th.join(timeout=timeout)
        except Exception:
            pass

    def stop(self) -> None:
        try:
            if self._th and hasattr(self._th, "stop"):
                getattr(self._th, "stop")()  # type: ignore[misc]
        except Exception:
            pass

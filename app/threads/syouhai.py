from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

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


class SyouhaiThread(threading.Thread):
    """Detect win/lose/disconnect labels and update text source with counters."""

    def __init__(
        self,
        obs: ObsClient,
        base_dir: str,
        logger: Optional[UiLogger] = None,
        source_name: str = "Capture1",
        result_queue: Optional["queue.Queue"] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._obs = obs
        self._base = base_dir
        self._handan = os.path.join(base_dir, "handantmp")
        os.makedirs(self._handan, exist_ok=True)
        self._log = logger or UiLogger()
        self._stop = threading.Event()
        self._source = source_name
        # Optional: publish detected results to a shared queue for association
        self._result_queue = result_queue

        self._scene_path = os.path.join(self._handan, "scene1.png")
        # Rects
        self._rects: Dict[str, Rect] = {
            "win": ((450, 990), (696, 1020)),
            "lose": ((480, 960), (730, 1045)),
            "disconnect": ((372, 654), (1548, 774)),
        }
        # Templates
        self._tpl_paths = {
            "win": os.path.join(self._handan, "win.png"),
            "lose": os.path.join(self._handan, "lose.png"),
            "disconnect": os.path.join(self._handan, "disconnect.png"),
        }
        self._tpls = {k: cv2.imread(p, cv2.IMREAD_GRAYSCALE) for k, p in self._tpl_paths.items()}

        self._counts = {"win": 0, "lose": 0, "disconnect": 0}
        self._text_source = "sensekiText1"
        # Threshold is tunable via env var; lower is more sensitive
        try:
            import os as _os
            self._threshold = float((_os.getenv("SYOUHAI_THRESHOLD", "0.2") or 0.2))
        except Exception:
            self._threshold = 0.2
        # Edge-triggering to avoid double counting without sleeps
        self._prev_label: Optional[str] = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self._log.log("[勝敗検出] スレッド開始")
        try:
            while not self._stop.is_set():
                self._loop()
        except Exception as e:
            self._log.log(f"[勝敗検出] エラー: {e}")
        finally:
            self._log.log("[勝敗検出] スレッド停止")

    # --- internals ---
    def _loop(self) -> None:
        self._obs.take_screenshot(self._source, self._scene_path)
        scene = cv2.imread(self._scene_path)
        if scene is None:
            self._log.log("[勝敗検出] スクリーンショットの読み込みに失敗")
            # No rest mode with minimal backoff to avoid hammering OBS
            try:
                if self._stop.wait(0.01):
                    return
            except Exception:
                pass
            return

        h, w = scene.shape[:2]
        # Prepare cropped regions and evaluate templates
        crops: Dict[str, Optional[object]] = {}
        for name, rect in self._rects.items():
            (x1, y1), (x2, y2) = rect
            if not (0 <= x1 < w and 0 <= y1 < h and 0 <= x2 <= w and 0 <= y2 <= h):
                self._log.log(f"[勝敗検出] 領域が範囲外: {name}")
                crops[name] = None
                continue
            crops[name] = crop_image_by_rect(scene, rect)

        def _match(name: str) -> bool:
            tpl = self._tpls.get(name)
            img = crops.get(name)
            if tpl is None or img is None:
                return False
            try:
                use_native = (os.getenv("USE_NATIVE_MATCH", "1") or "1").strip().lower() not in ("0", "false", "no")
            except Exception:
                use_native = True
            if use_native and _NATIVE_MATCH:
                try:
                    rect = self._rects.get(name)
                    if rect is None:
                        return False
                    return _match_native_region(self._scene_path, rect, self._tpl_paths[name], float(self._threshold))
                except Exception:
                    return False
            try:
                return match_template(img, tpl, threshold=self._threshold, grayscale=True)
            except Exception:
                return False

        is_lose = _match("lose")
        is_dc = _match("disconnect")
        is_win = _match("win")

        # Only emit on explicit detection.
        # 'Win by fallback' is handled by ResultAssociationThread when images arrive
        # without a recent lose/disconnect detection.
        result: Optional[str] = None
        if is_lose:
            result = "lose"
        elif is_dc:
            result = "disconnect"
        elif is_win:
            result = "win"

        # Edge trigger: emit only when label changes from previous
        if result != self._prev_label:
            if result is not None:
                now = time.time()
                self._counts[result] += 1
                jp = {"win": "勝ち", "lose": "負け", "disconnect": "回線切断"}.get(result, result)
                self._log.log(f"[勝敗検出] {jp} を検出 → {self._counts[result]}")

                # Update OBS text source with current counters
                text = f"Win: {self._counts['win']} - Lose: {self._counts['lose']} - DC: {self._counts['disconnect']}"
                try:
                    self._obs.update_text_source(self._text_source, text)
                    self._log.log(f"[勝敗検出] テキストを更新: {text}")
                except Exception as e:
                    self._log.log(f"[勝敗検出] テキスト更新に失敗: {e}")

                # Publish to result queue for association with new images
                if self._result_queue is not None:
                    try:
                        self._result_queue.put({"timestamp": now, "result": result}, timeout=0.1)
                    except Exception:
                        pass
            # Update previous label (None when nothing detected)
            self._prev_label = result


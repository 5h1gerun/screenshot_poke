from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

import cv2

from app.obs_client import ObsClient
from app.utils.image import Rect, crop_image_by_rect, match_template
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
        self._threshold = 0.2
        # Simple cooldown to avoid double counting while overlays persist
        self._cooldown_sec = 10.0
        self._last_emit_ts = 0.0

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
            if self._stop.wait(0.5):
                return
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

        if result is not None:
            now = time.time()
            if now - self._last_emit_ts < self._cooldown_sec:
                # Cooldown to avoid double counting while overlay persists
                if self._stop.wait(0.5):
                    return
                return
            self._last_emit_ts = now

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

            if self._stop.wait(self._cooldown_sec):
                return


from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional, Tuple

import cv2

from app.obs_client import ObsClient
from app.utils.image import Rect, crop_image_by_rect, match_template
from app.utils.logging import UiLogger


class SyouhaiThread(threading.Thread):
    """Detect win/lose/disconnect labels and update text source with counters."""

    def __init__(self, obs: ObsClient, base_dir: str, logger: Optional[UiLogger] = None, source_name: str = "Capture1") -> None:
        super().__init__(daemon=True)
        self._obs = obs
        self._base = base_dir
        self._handan = os.path.join(base_dir, "handantmp")
        os.makedirs(self._handan, exist_ok=True)
        self._log = logger or UiLogger()
        self._stop = threading.Event()
        self._source = source_name

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
        self._threshold = 0.3

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

        detected_any = False
        h, w = scene.shape[:2]
        self._log.log(f"[勝敗検出] スクリーンショットサイズ: {w}x{h}")

        for name, rect in self._rects.items():
            if self._stop.is_set():
                return
            (x1, y1), (x2, y2) = rect
            if not (0 <= x1 < w and 0 <= y1 < h and 0 <= x2 <= w and 0 <= y2 <= h):
                self._log.log(f"[勝敗検出] 領域が範囲外: {name}")
                continue
            cropped = crop_image_by_rect(scene, rect)
            tpl = self._tpls.get(name)
            if tpl is None:
                self._log.log(f"[勝敗検出] テンプレートが存在しません: {name}")
                continue
            if match_template(cropped, tpl, threshold=self._threshold, grayscale=True):
                self._counts[name] += 1
                jp = {"win": "勝ち", "lose": "負け", "disconnect": "回線切断"}.get(name, name)
                self._log.log(f"[勝敗検出] {jp} を検出 → {self._counts[name]}")
                detected_any = True
                if self._stop.wait(10):
                    return

        if detected_any:
            text = f"Win: {self._counts['win']} - Lose: {self._counts['lose']} - DC: {self._counts['disconnect']}"
            try:
                self._obs.update_text_source(self._text_source, text)
                self._log.log(f"[勝敗検出] テキストを更新: {text}")
            except Exception as e:
                self._log.log(f"[勝敗検出] テキスト更新に失敗: {e}")
            if self._stop.wait(1.0):
                return


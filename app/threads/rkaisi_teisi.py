from __future__ import annotations

import os
import threading
import time
from typing import Optional, Tuple

import cv2

from app.obs_client import ObsClient
from app.utils.image import Rect, crop_image_by_rect, match_template
from app.utils.logging import UiLogger


class RkaisiTeisiThread(threading.Thread):
    """Start/Stop OBS recording depending on template presence.

    Equivalent to the original behavior with safer, clearer structure.
    """

    MATCH_THRESHOLD = 0.4

    def __init__(self, obs: ObsClient, base_dir: str, logger: Optional[UiLogger] = None) -> None:
        super().__init__(daemon=True)
        self._obs = obs
        self._base = base_dir
        self._log = logger or UiLogger()
        self._stop = threading.Event()
        self._recording = False

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
        self._log.log("[RkaisiTeisi] Thread started")
        try:
            while not self._stop.is_set():
                self._loop()
        except Exception as e:
            self._log.log(f"[RkaisiTeisi] Error: {e}")
        finally:
            if self._recording:
                self._log.log("[RkaisiTeisi] Stopping recording on exit")
                try:
                    self._obs.stop_recording()
                except Exception:
                    pass
            self._log.log("[RkaisiTeisi] Thread stopped")

    # --- internals ---
    def _loop(self) -> None:
        # Screenshot and crop
        self._obs.take_screenshot("Capture1", self._scene_path)
        img = cv2.imread(self._scene_path)
        if img is None:
            time.sleep(0.5)
            return

        masu1_crop_img = crop_image_by_rect(img, self._masu1_rect)
        mark_crop_img = crop_image_by_rect(img, self._mark_rect)
        cv2.imwrite(self._masu1_crop, masu1_crop_img)
        cv2.imwrite(self._mark_crop, mark_crop_img)

        masu_tpl = cv2.imread(self._masu1_tpl)
        mark_tpl = cv2.imread(self._mark_tpl)
        if masu_tpl is None or mark_tpl is None:
            self._log.log("[RkaisiTeisi] Templates not found; waiting")
            time.sleep(1)
            return

        if (not self._recording) and match_template(masu1_crop_img, masu_tpl, self.MATCH_THRESHOLD, grayscale=False):
            self._log.log("[RkaisiTeisi] Detected 'masu1' -> start recording")
            self._obs.start_recording()
            self._recording = True
            time.sleep(1)
            return

        if self._recording and match_template(mark_crop_img, mark_tpl, self.MATCH_THRESHOLD, grayscale=False):
            self._log.log("[RkaisiTeisi] Detected 'mark' -> stop recording")
            self._obs.stop_recording()
            self._recording = False
            time.sleep(0.5)


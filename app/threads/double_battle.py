from __future__ import annotations

import datetime
import os
import threading
import time
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

from app.obs_client import ObsClient
from app.utils.image import crop_by_coords_list, crop_image_by_rect, match_template
from app.utils.logging import UiLogger


class DoubleBattleThread(threading.Thread):
    """Detect a specific board state by template matching and prepare output images.

    Behavior mirrors the original DoubleBattleThread but uses ObsClient and helpers.
    """

    def __init__(
        self,
        obs: ObsClient,
        base_dir: str,
        logger: Optional[UiLogger] = None,
        source_name: str = "Capture1",
        capture_interval_sec: float = 2.0,
    ) -> None:
        super().__init__(daemon=True)
        self._obs = obs
        self._base_dir = base_dir
        self._log = logger or UiLogger()
        self._stop = threading.Event()
        self._source = source_name
        # Loop sleep between iterations. 0.0 for "no rest" continuous capture.
        try:
            self._interval = float(capture_interval_sec or 0)
        except Exception:
            self._interval = 0.0

        # Paths
        self._handan = os.path.join(base_dir, "handantmp")
        self._haisin = os.path.join(base_dir, "haisin")
        self._koutiku = os.path.join(base_dir, "koutiku")
        os.makedirs(self._handan, exist_ok=True)
        os.makedirs(self._haisin, exist_ok=True)
        os.makedirs(self._koutiku, exist_ok=True)

        self._scene_path = os.path.join(self._handan, "scene.png")
        self._masu_path = os.path.join(self._handan, "masu.png")
        self._haisinsensyutu_path = os.path.join(self._haisin, "haisinsensyutu.png")
        self._haisinyou_path = os.path.join(self._haisin, "haisinyou.png")

        self._ref_files = [f"banme{i}.jpg" for i in range(1, 5)]
        self._ref_paths = [os.path.join(self._handan, f) for f in self._ref_files]

        # Coords (x, y)
        self._masu_rect = ((1541, 229), (1651, 843))
        self._screenshot_rect = ((1221, 150), (1655, 850))

    # --- public ---
    def stop(self):
        self._stop.set()

    # --- threading.Thread ---
    def run(self) -> None:
        self._log.log("[ダブルバトル] スレッド開始")
        try:
            while not self._stop.is_set():
                self._iteration()
                # Sleep but remain responsive to stop (0 for no rest)
                if self._stop.wait(self._interval):
                    return
        except Exception as e:
            self._log.log(f"[ダブルバトル] エラー: {e}")
        finally:
            self._log.log("[ダブルバトル] スレッド停止")

    # --- internals ---
    def _iteration(self) -> None:
        # 1) Ensure we have a scene screenshot
        for _ in range(10):
            if self._stop.is_set():
                return
            try:
                self._obs.take_screenshot(self._source, self._scene_path)
            except Exception as e:
                self._log.log(f"[ダブルバトル] スクリーンショット取得に失敗: {e}")
            if os.path.exists(self._scene_path):
                break
            time.sleep(0.5)

        # 2) Crop the main region and write temp
        scene_img = cv2.imread(self._scene_path)
        if scene_img is None:
            return
        crop = crop_image_by_rect(scene_img, self._screenshot_rect)
        cropped_path = os.path.join(self._handan, "screenshot_cropped.png")
        cv2.imwrite(cropped_path, crop)
        self._log.log("[ダブルバトル] screenshot_cropped.png を出力")

        # 3) Detect presence of 'masu' template in its area
        masu_img = cv2.imread(self._masu_path)
        if masu_img is None:
            raise FileNotFoundError(f"masu.png not found: {self._masu_path}")
        masu_area = crop_image_by_rect(cv2.imread(self._scene_path), self._masu_rect)
        masu_area_path = os.path.join(self._handan, "masu_area.png")
        cv2.imwrite(masu_area_path, masu_area)

        if match_template(masu_area, masu_img, threshold=0.6, grayscale=False):
            self._log.log("[ダブルバトル] 'masu' テンプレートを検出")

            # Keep recent crop for broadcasting
            cv2.imwrite(self._haisinyou_path, crop)

            # Save timestamped copy
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = os.path.join(self._koutiku, f"{ts}.png")
            cv2.imwrite(dst, crop)
            self._log.log(f"[ダブルバトル] 保存しました: {dst}")

            # While masu continues to appear, attempt to match reference tiles
            while match_template(masu_area, masu_img, threshold=0.6, grayscale=False):
                if self._stop.is_set():
                    return
                self._obs.take_screenshot(self._source, self._scene_path)
                scene = cv2.imread(self._scene_path)
                masu_area = crop_image_by_rect(scene, self._masu_rect)
                cv2.imwrite(masu_area_path, masu_area)

                tag_images = [cv2.imread(p) for p in self._ref_paths]
                if any(t is None for t in tag_images):
                    self._log.log("[ダブルバトル] 参照画像が見つからないためスキップ")
                    time.sleep(1)
                    continue

                coords: Sequence[Tuple[int, int, int, int]] = (
                    (146, 138, 933, 255),
                    (146, 255, 933, 372),
                    (146, 372, 933, 489),
                    (146, 489, 933, 606),
                    (146, 606, 933, 723),
                    (146, 723, 933, 840),
                )
                cropped_new = crop_by_coords_list(scene, coords)

                matched_new: list[np.ndarray] = []
                all_ok = True
                for idx, tag in enumerate(tag_images):
                    found = False
                    for c in cropped_new:
                        if c.shape[0] >= tag.shape[0] and c.shape[1] >= tag.shape[1]:
                            res = cv2.matchTemplate(c, tag, cv2.TM_CCOEFF_NORMED)
                            if np.any(res >= 0.8):
                                matched_new.append(c)
                                found = True
                                break
                    if not found:
                        self._log.log(f"[ダブルバトル] タグ{idx + 1} が見つかりません")
                        all_ok = False
                        break

                if all_ok and len(matched_new) == 4:
                    combined = cv2.vconcat(matched_new)
                    cv2.imwrite(self._haisinsensyutu_path, combined)
                    self._log.log(f"[ダブルバトル] 抽出画像を書き出し: {self._haisinsensyutu_path}")
                # Stay responsive to stop while looping
                if self._stop.wait(1):
                    return


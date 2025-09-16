from __future__ import annotations

from typing import Iterable, Sequence, Tuple

import cv2
import numpy as np


Coord = Tuple[int, int]
Rect = Tuple[Coord, Coord]


def crop_image_by_rect(img, rect: Rect):
    (x1, y1), (x2, y2) = rect
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    return img[y1:y2, x1:x2]


def crop_by_coords_list(img, coords: Sequence[Tuple[int, int, int, int]]):
    out = []
    for (x1, y1, x2, y2) in coords:
        out.append(img[int(y1):int(y2), int(x1):int(x2)])
    return out


def match_template(image, template, threshold: float, grayscale: bool = True) -> bool:
    if grayscale:
        if len(image.shape) == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        if len(template.shape) == 3:
            template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)
    return max_val >= threshold


def find_any_match(candidates: Iterable, template, threshold: float) -> bool:
    for c in candidates:
        if match_template(c, template, threshold, grayscale=True):
            return True
    return False


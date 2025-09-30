import os
from pathlib import Path

import numpy as np
from PIL import Image

from app.utils.native_match import match_template_native, match_template_region_native, is_available


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    tmp = root / "native" / "output"
    tmp.mkdir(parents=True, exist_ok=True)
    img_path = tmp / "test_img.png"
    tpl_path = tmp / "test_tpl.png"

    # Build a simple synthetic image: white background with a black rectangle
    img = np.full((100, 160, 3), 255, dtype=np.uint8)
    # black box with white diagonal (to avoid zero-variance template)
    img[30:60, 60:90] = 0
    for d in range(0, 30, 3):
        img[30+d, 60+d] = 255
    Image.fromarray(img).save(img_path)

    tpl = np.zeros((30, 30, 3), dtype=np.uint8)
    for d in range(0, 30, 3):
        tpl[d, d] = 255
    Image.fromarray(tpl).save(tpl_path)

    print("native_available:", is_available())

    ok1 = match_template_native(str(img_path), str(tpl_path), 0.8)
    print("global_match:", ok1)

    # Wrong region (no overlap)
    ok2 = match_template_region_native(str(img_path), ((0, 0), (20, 20)), str(tpl_path), 0.8)
    print("roi_miss:", ok2)

    # Correct region
    ok3 = match_template_region_native(str(img_path), ((60, 30), (90, 60)), str(tpl_path), 0.8)
    print("roi_hit:", ok3)

    # Expect: available=True, global_match=True, roi_miss=False, roi_hit=True
    if not is_available() or not ok1 or ok2 or not ok3:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

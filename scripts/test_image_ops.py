import os
from pathlib import Path
import numpy as np
from PIL import Image

from app.utils.native_thumb import (
    generate_thumbnail_native,
    generate_thumbnails_batch_native,
    crop_resize_native,
    vconcat_native,
    is_available,
)


def make_image(path: Path, w: int, h: int, color: tuple[int, int, int]) -> None:
    img = np.full((h, w, 3), color, dtype=np.uint8)
    Image.fromarray(img).save(path)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out = root / 'native' / 'output'
    out.mkdir(parents=True, exist_ok=True)

    a = out / 'a.png'
    b = out / 'b.png'
    c = out / 'c.png'
    make_image(a, 160, 100, (255, 0, 0))
    make_image(b, 200, 80, (0, 255, 0))
    make_image(c, 120, 60, (0, 0, 255))

    # Single thumbnail
    t1 = out / 'a_t.jpg'
    print('single_thumb:', generate_thumbnail_native(str(a), str(t1), 64))

    # Batch thumbnails
    ins = [str(a), str(b), str(c)]
    outs = [str(out / 'a_b.jpg'), str(out / 'b_b.jpg'), str(out / 'c_b.jpg')]
    print('batch_count:', generate_thumbnails_batch_native(ins, outs, 64))

    # Crop+resize from b
    cr = out / 'b_crop.png'
    print('crop_resize:', crop_resize_native(str(b), str(cr), ((10, 10), (150, 60)), 80))

    # Vertical concat
    vc = out / 'vc.png'
    print('vconcat:', vconcat_native([str(a), str(b), str(c)], str(vc)))

    return 0


if __name__ == '__main__':
    raise SystemExit(main())


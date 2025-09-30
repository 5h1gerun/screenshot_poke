from __future__ import annotations

from pathlib import Path
from PIL import Image


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    src = root / 'icon.png'
    dst = root / 'packaging' / 'app.ico'
    if not src.exists():
        print('icon.png not found; skip')
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(src).convert('RGBA')
    sizes = [16, 24, 32, 48, 64, 128, 256]
    img.save(dst, format='ICO', sizes=[(s, s) for s in sizes])
    print('wrote', dst)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())


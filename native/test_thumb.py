import sys, os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '\\..')
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '\\..\\app')
sys.path.append(os.path.dirname(os.path.abspath(__file__)) + '\\..\\..')
from app.utils.native_thumb import generate_thumbnail_native, is_available
from pathlib import Path
print('native available:', is_available())
src = str(Path('haisin/haisinsensyutu.png').resolve())
outdir = Path('native'); outdir.mkdir(exist_ok=True)
out = str((outdir / 'test_thumb.jpg').resolve())
print('src exists:', os.path.exists(src))
print('out path:', out)
ok = generate_thumbnail_native(src, out, 320)
print('ok:', ok)
print('out exists:', os.path.exists(out))

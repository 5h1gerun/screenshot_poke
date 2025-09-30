from __future__ import annotations

import os
import sys
import ctypes
from pathlib import Path

_dll = None
_available = False

def _load_dll() -> None:
    global _dll, _available
    if _dll is not None:
        return
    # Search paths: PyInstaller onefile temp (sys._MEIPASS),
    # executable dir (frozen), repo root (dev), current dir (fallback)
    roots = []
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(Path(meipass))
    except Exception:
        pass
    try:
        if getattr(sys, "frozen", False):
            roots.append(Path(sys.executable).resolve().parent)
    except Exception:
        pass
    try:
        roots.append(Path(__file__).resolve().parents[2])
    except Exception:
        pass
    roots.append(Path.cwd())
    candidates = []
    for root in roots:
        candidates.append(root / "native" / "build" / "thumbnail_wic.dll")
        candidates.append(root / "native" / "thumbnail_wic.dll")
    for p in candidates:
        try:
            if p.exists():
                _dll = ctypes.WinDLL(str(p))
                # int gen_thumbnail_w(const wchar_t* in, const wchar_t* out, int max_w)
                _dll.gen_thumbnail_w.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
                _dll.gen_thumbnail_w.restype = ctypes.c_int
                # int gen_thumbnails_w(const wchar_t** in_paths, int count, const wchar_t** out_paths, int max_w)
                try:
                    _dll.gen_thumbnails_w.argtypes = [ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_int, ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_int]
                    _dll.gen_thumbnails_w.restype = ctypes.c_int
                except Exception:
                    pass
                # int crop_resize_w(const wchar_t* in, const wchar_t* out, int x, int y, int w, int h, int max_w)
                try:
                    _dll.crop_resize_w.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
                    _dll.crop_resize_w.restype = ctypes.c_int
                except Exception:
                    pass
                # int vconcat_w(const wchar_t** in_paths, int count, const wchar_t* out_path)
                try:
                    _dll.vconcat_w.argtypes = [ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_int, ctypes.c_wchar_p]
                    _dll.vconcat_w.restype = ctypes.c_int
                except Exception:
                    pass
                _available = True
                return
        except Exception:
            _dll = None
            _available = False
    _available = False


def is_available() -> bool:
    _load_dll()
    return bool(_available)


def generate_thumbnail_native(in_path: str, out_path: str, max_w: int) -> bool:
    """Generate a thumbnail using native DLL if available.

    Returns True on success, False if DLL not available or failed.
    """
    _load_dll()
    if not _available:
        return False
    try:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
    except Exception:
        pass
    try:
        rc = _dll.gen_thumbnail_w(ctypes.c_wchar_p(in_path), ctypes.c_wchar_p(out_path), int(max_w))
        return rc == 0
    except Exception:
        return False


def generate_thumbnails_batch_native(in_paths, out_paths, max_w: int) -> int:
    _load_dll()
    if not _available or not hasattr(_dll, 'gen_thumbnails_w'):
        return 0
    try:
        n = min(len(in_paths), len(out_paths))
        if n <= 0:
            return 0
        arr_in = (ctypes.c_wchar_p * n)(*map(str, in_paths[:n]))
        arr_out = (ctypes.c_wchar_p * n)(*map(str, out_paths[:n]))
        rc = _dll.gen_thumbnails_w(arr_in, ctypes.c_int(n), arr_out, ctypes.c_int(int(max_w)))
        return int(rc)
    except Exception:
        return 0


def crop_resize_native(in_path: str, out_path: str, rect, max_w: int) -> bool:
    _load_dll()
    if not _available or not hasattr(_dll, 'crop_resize_w'):
        return False
    try:
        (x1, y1), (x2, y2) = rect
        x, y = int(x1), int(y1)
        w, h = int(x2) - int(x1), int(y2) - int(y1)
        rc = _dll.crop_resize_w(ctypes.c_wchar_p(in_path), ctypes.c_wchar_p(out_path), ctypes.c_int(x), ctypes.c_int(y), ctypes.c_int(w), ctypes.c_int(h), ctypes.c_int(int(max_w)))
        return rc == 0
    except Exception:
        return False


def vconcat_native(in_paths, out_path: str) -> bool:
    _load_dll()
    if not _available or not hasattr(_dll, 'vconcat_w'):
        return False
    try:
        n = len(in_paths)
        if n <= 0:
            return False
        arr = (ctypes.c_wchar_p * n)(*map(str, in_paths))
        rc = _dll.vconcat_w(arr, ctypes.c_int(n), ctypes.c_wchar_p(out_path))
        return rc == 0
    except Exception:
        return False


# Friendly alias for import site
NATIVE_AVAILABLE = is_available()

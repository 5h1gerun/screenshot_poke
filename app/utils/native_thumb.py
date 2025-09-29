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


# Friendly alias for import site
NATIVE_AVAILABLE = is_available()

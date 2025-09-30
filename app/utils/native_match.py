from __future__ import annotations

import os
import sys
import ctypes
from pathlib import Path
from typing import Tuple

_dll = None
_available = False


def _load_dll() -> None:
    global _dll, _available
    if _dll is not None:
        return
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
                # int match_template_w(const wchar_t*, const wchar_t*, float, int* out_match)
                _dll.match_template_w.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_float, ctypes.POINTER(ctypes.c_int)]
                _dll.match_template_w.restype = ctypes.c_int
                # int match_template_region_w(const wchar_t*, int x, int y, int w, int h, const wchar_t*, float, int*)
                _dll.match_template_region_w.argtypes = [
                    ctypes.c_wchar_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_wchar_p, ctypes.c_float, ctypes.POINTER(ctypes.c_int)
                ]
                _dll.match_template_region_w.restype = ctypes.c_int
                _available = True
                return
        except Exception:
            _dll = None
            _available = False
    _available = False


def is_available() -> bool:
    _load_dll()
    return bool(_available)


def match_template_native(image_path: str, template_path: str, threshold: float) -> bool:
    _load_dll()
    if not _available:
        return False
    try:
        m = ctypes.c_int(0)
        rc = _dll.match_template_w(ctypes.c_wchar_p(image_path), ctypes.c_wchar_p(template_path), ctypes.c_float(threshold), ctypes.byref(m))
        if rc != 0:
            return False
        return bool(m.value)
    except Exception:
        return False


Rect = Tuple[Tuple[int, int], Tuple[int, int]]


def match_template_region_native(image_path: str, rect: Rect, template_path: str, threshold: float) -> bool:
    _load_dll()
    if not _available:
        return False
    try:
        (x1, y1), (x2, y2) = rect
        x, y = int(x1), int(y1)
        w, h = int(x2) - int(x1), int(y2) - int(y1)
        if w <= 0 or h <= 0:
            return False
        m = ctypes.c_int(0)
        rc = _dll.match_template_region_w(
            ctypes.c_wchar_p(image_path), ctypes.c_int(x), ctypes.c_int(y), ctypes.c_int(w), ctypes.c_int(h),
            ctypes.c_wchar_p(template_path), ctypes.c_float(threshold), ctypes.byref(m)
        )
        if rc != 0:
            return False
        return bool(m.value)
    except Exception:
        return False


NATIVE_AVAILABLE = is_available()


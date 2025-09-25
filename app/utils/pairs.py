from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Dict, List, Optional, Tuple

from app.utils import paths as paths_utils


# Support both legacy (YYYYMMDD_HHMMSS) and OBS-style
# - Preferred (current): YYYY-MM-DD_HH-MM-SS
# - Also accept:        YYYY-MM-DD HH-MM-SS (space)
_NAME_TS_RE_OBS = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}[ _]\d{2}-\d{2}-\d{2})")
_NAME_TS_RE_LEGACY = re.compile(r"^(?P<ts>\d{8}_\d{6})")


def _pairs_json_path(base_dir: str) -> str:
    return paths_utils.get_pairs_json_path(base_dir)


def load_pairs(base_dir: str) -> Dict[str, str]:
    path = _pairs_json_path(base_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                # only keep str->str mappings
                return {str(k): str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    except Exception:
        pass
    return {}


def save_pairs(base_dir: str, mapping: Dict[str, str]) -> None:
    path = _pairs_json_path(base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _parse_name_ts(name: str) -> Optional[dt.datetime]:
    # Try OBS-style first
    m = _NAME_TS_RE_OBS.match(name)
    if m:
        s = m.group("ts")
        try:
            # Try underscore first (preferred)
            try:
                return dt.datetime.strptime(s, "%Y-%m-%d_%H-%M-%S")
            except Exception:
                return dt.datetime.strptime(s, "%Y-%m-%d %H-%M-%S")
        except Exception:
            pass
    # Fallback to legacy format
    m2 = _NAME_TS_RE_LEGACY.match(name)
    if m2:
        s2 = m2.group("ts")
        try:
            return dt.datetime.strptime(s2, "%Y%m%d_%H%M%S")
        except Exception:
            pass
    return None


def list_images_in_range(base_dir: str, start: float, end: float) -> List[str]:
    """Return list of image file names in koutiku within [start, end] (epoch seconds).

    Uses the timestamp embedded in file names like YYYYMMDD_HHMMSS.* when available;
    falls back to file mtime if the name doesn't match.
    """
    koutiku = paths_utils.get_koutiku_dir(base_dir)
    out: List[str] = []
    try:
        entries = []
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        with os.scandir(koutiku) as it:
            for e in it:
                if not e.is_file():
                    continue
                if os.path.splitext(e.name)[1].lower() not in exts:
                    continue
                entries.append(e)
        for e in entries:
            t: Optional[float] = None
            dt_from_name = _parse_name_ts(e.name)
            if dt_from_name is not None:
                t = dt_from_name.timestamp()
            else:
                try:
                    t = e.stat().st_mtime
                except Exception:
                    t = None
            if t is None:
                continue
            if start <= t <= end:
                out.append(e.name)
    except Exception:
        pass
    return sorted(out)


def find_recording_file(
    recordings_dir: str,
    start: float,
    end: float,
    exts: Tuple[str, ...] = (".mkv", ".mp4", ".mov", ".flv"),
    margin_sec: float = 20.0,
) -> Optional[str]:
    """Find the recording file likely created for a session [start, end].

    Heuristic: pick the newest file whose mtime is between
    (start - margin) and (end + margin). If multiple, choose the one with mtime closest to end.
    """
    if not recordings_dir or not os.path.isdir(recordings_dir):
        return None
    # Allow override via env var RECORDINGS_MATCH_MARGIN_SEC (seconds)
    try:
        import os as _os
        _m = float((_os.getenv("RECORDINGS_MATCH_MARGIN_SEC", str(margin_sec)) or margin_sec))
        margin_sec = _m
    except Exception:
        pass
    candidates: List[Tuple[str, float]] = []  # (path, mtime)
    lo = start - max(0.0, margin_sec)
    hi = end + max(0.0, margin_sec)
    try:
        with os.scandir(recordings_dir) as it:
            for e in it:
                if not e.is_file():
                    continue
                if os.path.splitext(e.name)[1].lower() not in exts:
                    continue
                try:
                    mt = e.stat().st_mtime
                except Exception:
                    continue
                if lo <= mt <= hi:
                    candidates.append((e.path, mt))
    except Exception:
        return None
    if not candidates:
        return None
    # sort by closeness to end time, then by mtime desc
    candidates.sort(key=lambda x: (abs(x[1] - end), -x[1]))
    return candidates[0][0]


def associate_recording_window(base_dir: str, start: float, end: float) -> Optional[Dict[str, str]]:
    """Associate images captured within [start, end] with the detected recording file.

    Returns the updated mapping dict if a recording is found; otherwise None.
    """
    rec_dir = os.getenv("RECORDINGS_DIR", "").strip()
    # Optional override of extensions via env, e.g., ".mkv,.mp4"
    exts_env = os.getenv("RECORDINGS_EXTS", "").strip()
    exts: Tuple[str, ...] = (".mkv", ".mp4", ".mov", ".flv")
    if exts_env:
        try:
            items = [s.strip().lower() for s in exts_env.split(",") if s.strip()]
            if items:
                exts = tuple(items)
        except Exception:
            pass

    video = find_recording_file(rec_dir, start, end, exts=exts)
    if not video:
        return None

    names = list_images_in_range(base_dir, start, end)
    if not names:
        return None

    mapping = load_pairs(base_dir)
    for n in names:
        mapping[n] = video
    save_pairs(base_dir, mapping)
    return mapping


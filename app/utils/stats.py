from __future__ import annotations

import csv
import datetime as dt
import json
import os
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw


def _results_csv_path(base_dir: str) -> str:
    return os.path.join(base_dir, "koutiku", "_results.csv")


def append_result(base_dir: str, image_name: str, result: str, ts: Optional[float] = None) -> None:
    """Append a result row to CSV: timestamp,image,result

    - timestamp: ISO-8601 local time
    - image: file name only (not path)
    - result: one of win/lose/disconnect (free text tolerated)
    """
    path = _results_csv_path(base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    t = dt.datetime.fromtimestamp(ts or dt.datetime.now().timestamp())
    ts_str = t.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(["timestamp", "image", "result"])  # header
            w.writerow([ts_str, image_name, result])
    except Exception:
        # Best-effort; swallow to avoid crashing threads
        pass


def load_results(base_dir: str) -> List[Tuple[dt.datetime, str, str]]:
    out: List[Tuple[dt.datetime, str, str]] = []
    path = _results_csv_path(base_dir)
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                try:
                    t = dt.datetime.strptime(row.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
                    img = str(row.get("image", ""))
                    res = str(row.get("result", ""))
                    out.append((t, img, res))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def aggregate_by_day(
    rows: Iterable[Tuple[dt.datetime, str, str]],
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
) -> List[Tuple[dt.date, int, int, int]]:
    """Return list of (date, win, lose, disconnect) sorted by date.

    Filters to [start, end] if provided.
    """
    agg: Dict[dt.date, Dict[str, int]] = {}
    for t, _img, res in rows:
        d = t.date()
        if start and d < start:
            continue
        if end and d > end:
            continue
        if d not in agg:
            agg[d] = {"win": 0, "lose": 0, "disconnect": 0}
        key = "win" if res not in ("lose", "disconnect") else res
        agg[d][key] = agg[d].get(key, 0) + 1
    out: List[Tuple[dt.date, int, int, int]] = []
    for d in sorted(agg.keys()):
        c = agg[d]
        out.append((d, c.get("win", 0), c.get("lose", 0), c.get("disconnect", 0)))
    return out


def compute_totals(rows: Iterable[Tuple[dt.datetime, str, str]]) -> Tuple[int, int, int, float]:
    win = lose = dc = 0
    for _t, _img, res in rows:
        if res == "lose":
            lose += 1
        elif res == "disconnect":
            dc += 1
        else:
            win += 1
    total = win + lose + dc
    wr = (win / (win + lose)) * 100.0 if (win + lose) > 0 else 0.0
    return win, lose, dc, wr


def add_result_tag(base_dir: str, image_name: str, result: str) -> None:
    """Update koutiku/_tags.json to include the result as a tag for the image."""
    tags_path = os.path.join(base_dir, "koutiku", "_tags.json")
    os.makedirs(os.path.dirname(tags_path), exist_ok=True)
    data: Dict[str, List[str]] = {}
    try:
        with open(tags_path, "r", encoding="utf-8") as f:
            obj = json.load(f)
            if isinstance(obj, dict):
                # Coerce to list[str]
                for k, v in obj.items():
                    if isinstance(v, list):
                        data[k] = [str(x) for x in v if x]
    except Exception:
        data = {}
    data.setdefault(image_name, [])
    tag = str(result)
    if tag not in data[image_name]:
        data[image_name].append(tag)
        try:
            with open(tags_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def render_winrate_chart(
    per_day: List[Tuple[dt.date, int, int, int]],
    size: Tuple[int, int] = (900, 320),
) -> Image.Image:
    """Render a simple win-rate line chart using PIL.

    - X axis: days
    - Y axis (0-100%): win-rate computed as win/(win+lose)
    - Light grid and markers
    """
    w, h = size
    img = Image.new("RGB", size, (250, 250, 255))
    draw = ImageDraw.Draw(img)
    # Padding
    pad_l, pad_r, pad_t, pad_b = 48, 16, 16, 30
    inner_w = max(1, w - pad_l - pad_r)
    inner_h = max(1, h - pad_t - pad_b)
    # Axes
    x0, y0 = pad_l, h - pad_b
    x1, y1 = w - pad_r, pad_t
    draw.rectangle([x0, y1, x1, y0], outline=(80, 80, 80))
    # Horizontal grid (0,25,50,75,100)
    for i, pct in enumerate([0, 25, 50, 75, 100]):
        y = y0 - inner_h * (pct / 100.0)
        color = (220, 220, 230) if pct % 50 else (200, 200, 210)
        draw.line([(x0, y), (x1, y)], fill=color)
        draw.text((6, y - 6), f"{pct}%", fill=(60, 60, 60))
    # Data points
    n = len(per_day)
    if n == 0:
        draw.text((pad_l + 10, pad_t + 10), "No data", fill=(80, 80, 80))
        return img
    step = inner_w / max(1, (n - 1))
    pts = []
    for i, (_d, win, lose, _dc) in enumerate(per_day):
        denom = win + lose
        wr = (win / denom) * 100.0 if denom > 0 else 0.0
        x = x0 + step * i
        y = y0 - inner_h * (wr / 100.0)
        pts.append((x, y))
    # Line
    if len(pts) >= 2:
        draw.line(pts, fill=(40, 90, 200), width=2)
    # Markers
    for x, y in pts:
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(40, 90, 200))
    # X ticks (sparse)
    max_ticks = 8
    idxs = list(range(n))
    if n > max_ticks:
        # pick roughly evenly spaced indices
        step_idx = max(1, n // max_ticks)
        idxs = list(range(0, n, step_idx))
        if idxs[-1] != n - 1:
            idxs.append(n - 1)
    for i in idxs:
        d = per_day[i][0]
        x = x0 + step * i
        draw.line([(x, y0), (x, y0 + 4)], fill=(80, 80, 80))
        label = d.strftime("%m/%d")
        draw.text((x - 12, y0 + 6), label, fill=(60, 60, 60))
    return img


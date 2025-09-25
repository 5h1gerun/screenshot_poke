from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional, Tuple
import os

from app.utils.logging import UiLogger
from app.utils import paths as paths_utils
from app.obs_client import ObsClient
from app.utils import stats as stats_utils


class ResultAssociationThread(threading.Thread):
    """Associate new images in koutiku/ with detected results from SyouhaiThread.

    Logic:
    - Poll koutiku for new image files and enqueue them in order of discovery.
    - Consume results pushed by SyouhaiThread via a shared queue and pair them
      FIFO with pending images.
    - On pair, append to CSV and add a tag to _tags.json (e.g., win/lose/disconnect).
    - If results are delayed, keep images queued; if images are delayed, keep
      results queued. Optionally default to 'win' after a timeout to prevent
      starvation (configurable, off by default).
    """

    def __init__(
        self,
        base_dir: str,
        result_queue: "queue.Queue",
        logger: Optional[UiLogger] = None,
        default_win_timeout: float = 0.0,
        obs: Optional[ObsClient] = None,
        text_source: str = "sensekiText1",
        season: Optional[str] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._base = base_dir
        self._koutiku = paths_utils.get_koutiku_dir(base_dir)
        os.makedirs(self._koutiku, exist_ok=True)
        self._log = logger or UiLogger()
        self._stop = threading.Event()
        self._rq = result_queue
        self._seen: set[str] = set()
        # (path, timestamp)
        self._pending_images: Deque[Tuple[str, float]] = deque()
        # result dicts: {"timestamp": float, "result": str, ...}
        self._pending_results: Deque[Dict[str, object]] = deque()
        # stop markers (timestamps) from recording stop events
        self._pending_stops: Deque[float] = deque()
        self._default_win_timeout = float(default_win_timeout or 0)
        self._first_unpaired_ts: Optional[float] = None
        self._obs = obs
        self._text_source = text_source
        try:
            self._tol = float(os.getenv("ASSOC_TIME_TOLERANCE_SEC", "20") or 20)
        except Exception:
            self._tol = 20.0
        # Normalize season for tagging and CSV:
        # - If only digits like "13", convert to "S13"
        # - Else keep as-is
        sraw = (season or "").strip()
        if sraw.isdigit() and sraw:
            self._season = f"S{sraw}"
        else:
            self._season = sraw

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self._log.log("[結果連携] koutiku フォルダ監視を開始")
        # Initialize seen files to avoid bulk processing on startup
        try:
            for name in os.listdir(self._koutiku):
                p = os.path.join(self._koutiku, name)
                if os.path.isfile(p) and self._is_img(name):
                    self._seen.add(p)
        except Exception:
            pass

        while not self._stop.is_set():
            # 1) Scan for new images
            try:
                for name in sorted(os.listdir(self._koutiku)):
                    if self._stop.is_set():
                        return
                    p = os.path.join(self._koutiku, name)
                    if p in self._seen:
                        continue
                    if not os.path.isfile(p) or not self._is_img(name):
                        continue
                    # Ensure the file is stable (size doesn't change briefly)
                    try:
                        s1 = os.path.getsize(p)
                        time.sleep(0.05)
                        s2 = os.path.getsize(p)
                        if s1 != s2:
                            continue
                    except Exception:
                        continue
                    # Determine image timestamp (prefer mtime)
                    try:
                        ts = os.path.getmtime(p)
                    except Exception:
                        ts = time.time()
                    self._seen.add(p)
                    self._pending_images.append((p, ts))
                    if self._first_unpaired_ts is None:
                        self._first_unpaired_ts = time.time()
                    self._log.log(f"[結果連携] 新規画像: {os.path.basename(p)}")
            except Exception as e:
                self._log.log(f"[結果連携] スキャンエラー: {e}")

            # 2) Drain any available results quickly (non-blocking)
            try:
                while True:
                    item = self._rq.get_nowait()
                    if isinstance(item, dict):
                        ts = float(item.get("timestamp") or time.time())
                        if "result" in item:
                            self._pending_results.append(item)
                        elif str(item.get("type", "")).lower() == "stop":
                            self._pending_stops.append(ts)
                        else:
                            self._log.log("[結果連携] 未知の結果オブジェクトを受信")
                    else:
                        self._log.log("[結果連携] 未知の結果オブジェクトを受信")
            except Exception:
                pass

            # 3) Pair pending items FIFO
            self._pair_items()

            # 4) Optional default-to-win if results never arrive
            if (
                self._default_win_timeout > 0
                and self._first_unpaired_ts is not None
                and self._pending_images
                and not self._pending_results
            ):
                if time.time() - self._first_unpaired_ts >= self._default_win_timeout:
                    # synthesize a win result
                    self._pending_results.append({"timestamp": time.time(), "result": "win", "synthetic": True})
                    self._log.log("[結果連携] タイムアウトのため '勝ち' を割当")
                    self._pair_items()

            if self._stop.wait(0.2):
                return

        self._log.log("[結果連携] 監視を停止")

    # --- internals ---
    def _pair_items(self) -> None:
        def _pop_best_result_for(ts_img: float) -> Optional[Dict[str, object]]:
            if not self._pending_results:
                return None
            # Choose result with smallest |ts - ts_img| if within tolerance
            idx_best = -1
            best_delta = 1e9
            for i, r in enumerate(self._pending_results):
                try:
                    tr = float(r.get("timestamp") or 0)
                except Exception:
                    continue
                d = abs(tr - ts_img)
                if d < best_delta:
                    best_delta = d
                    idx_best = i
            if idx_best >= 0 and best_delta <= self._tol:
                _lst = list(self._pending_results)
                item = _lst.pop(idx_best)
                self._pending_results = deque(_lst)
                return item
            return None

        def _pop_stop_for(ts_img: float) -> Optional[float]:
            # Choose a stop marker within tolerance closest to image ts
            if not self._pending_stops:
                return None
            idx_best = -1
            best_delta = 1e9
            for i, t in enumerate(self._pending_stops):
                d = abs(t - ts_img)
                if d < best_delta:
                    best_delta = d
                    idx_best = i
            if idx_best >= 0 and best_delta <= self._tol:
                t = self._pending_stops[idx_best]
                # remove by index
                self._pending_stops = deque([v for j, v in enumerate(self._pending_stops) if j != idx_best])
                return t
            return None

        def _apply_pair(img_path: str, result: str, ts_res: float, synthetic: bool = False) -> None:
            name = os.path.basename(img_path)
            # Append to CSV and tag file
            try:
                stats_utils.append_result(self._base, name, result, ts_res, season=self._season)
            except Exception as e:
                self._log.log(f"[結果連携] CSV 追記失敗: {e}")
            try:
                tags = [result]
                if self._season:
                    tags.append(f"season:{self._season}")
                stats_utils.add_tags(self._base, name, tags)
            except Exception as e:
                self._log.log(f"[結果連携] タグ付け失敗: {e}")
            self._log.log(f"[結果連携] {name} -> {result}")

            # If this was synthetic, update OBS text source counters from totals
            if synthetic and self._obs is not None:
                try:
                    rows = stats_utils.load_results(self._base)
                    win, lose, dc, _wr = stats_utils.compute_totals(rows)
                    text = f"Win: {win} - Lose: {lose} - DC: {dc}"
                    self._obs.update_text_source(self._text_source, text)
                except Exception:
                    pass

        # Main pairing loop
        while self._pending_images:
            img_path, ts_img = self._pending_images[0]
            res = _pop_best_result_for(ts_img)
            if res is not None:
                # Consume image
                self._pending_images.popleft()
                rname = str(res.get("result") or "win")
                ts_res = float(res.get("timestamp") or ts_img)
                synthetic = bool(res.get("synthetic"))
                _apply_pair(img_path, rname, ts_res, synthetic=synthetic)
                # Drop any stop markers extremely close to this image to prevent double pairing
                _ = _pop_stop_for(ts_img)
                continue

            # No explicit result within tolerance; try stop marker -> default to win
            t_stop = _pop_stop_for(ts_img)
            if t_stop is not None:
                self._pending_images.popleft()
                _apply_pair(img_path, "win", t_stop, synthetic=True)
                continue

            # Cannot pair (yet); wait for more results/stops
            break

        if not self._pending_images:
            self._first_unpaired_ts = None

    def _is_img(self, name: str) -> bool:
        ext = os.path.splitext(name)[1].lower()
        return ext in {".png", ".jpg", ".jpeg", ".webp"}

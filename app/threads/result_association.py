from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional

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
        self._pending_images: Deque[str] = deque()
        self._pending_results: Deque[Dict[str, object]] = deque()
        self._default_win_timeout = float(default_win_timeout or 0)
        self._first_unpaired_ts: Optional[float] = None
        self._obs = obs
        self._text_source = text_source
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
                    self._seen.add(p)
                    self._pending_images.append(p)
                    if self._first_unpaired_ts is None:
                        self._first_unpaired_ts = time.time()
                    self._log.log(f"[結果連携] 新規画像: {os.path.basename(p)}")
            except Exception as e:
                self._log.log(f"[結果連携] スキャンエラー: {e}")

            # 2) Drain any available results quickly (non-blocking)
            try:
                while True:
                    item = self._rq.get_nowait()
                    if isinstance(item, dict) and "result" in item:
                        self._pending_results.append(item)
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
        while self._pending_images and self._pending_results:
            img = self._pending_images.popleft()
            res = self._pending_results.popleft()
            try:
                result = str(res.get("result"))
            except Exception:
                result = "win"
            ts = float(res.get("timestamp") or time.time())
            name = os.path.basename(img)

            # Append to CSV and tag file
            try:
                stats_utils.append_result(self._base, name, result, ts, season=self._season)
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

            # If this was a synthetic assignment (fallback), update OBS text source
            try:
                if bool(res.get("synthetic")) and self._obs is not None:
                    rows = stats_utils.load_results(self._base)
                    win, lose, dc, _wr = stats_utils.compute_totals(rows)
                    text = f"Win: {win} - Lose: {lose} - DC: {dc}"
                    try:
                        self._obs.update_text_source(self._text_source, text)
                    except Exception:
                        pass
            except Exception:
                pass

        if not self._pending_images:
            self._first_unpaired_ts = None

    def _is_img(self, name: str) -> bool:
        ext = os.path.splitext(name)[1].lower()
        return ext in {".png", ".jpg", ".jpeg", ".webp"}

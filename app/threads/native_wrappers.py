from __future__ import annotations

import os
import sys
import ctypes
import threading
import time
from pathlib import Path
from typing import Optional, Callable

from app.obs_client import ObsClient
from app.utils.logging import UiLogger
from app.utils import paths as paths_utils
from app.utils import pairs as pairs_utils

_dll = None
_available = False


def _load_dll() -> None:
    global _dll, _available
    if _dll is not None:
        return
    roots: list[Path] = []
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
        roots.append(Path.cwd())
    # Candidates
    cands: list[Path] = []
    for r in roots:
        cands.append(r / "native" / "build" / "automation.dll")
        cands.append(r / "native" / "automation.dll")
    for p in cands:
        try:
            if p.exists():
                _dll = ctypes.WinDLL(str(p))
                break
        except Exception:
            _dll = None
    _available = _dll is not None
    if not _available:
        return

    # Define prototypes
    # void* start_double_battle_w(const wchar_t* base_dir, const wchar_t* source,
    #   const wchar_t* haisinyou_path, const wchar_t* koutiku_dir, const wchar_t* out_ext,
    #   double interval_sec,
    #   int (*cb_shot)(void*, const wchar_t*, const wchar_t*),
    #   void (*cb_log)(void*, const wchar_t*),
    #   void* ctx)
    _dll.start_double_battle_w.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_double,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _dll.start_double_battle_w.restype = ctypes.c_void_p

    # void stop_double_battle(void*)
    _dll.stop_double_battle.argtypes = [ctypes.c_void_p]

    # void* start_rkaisi_teisi_w(const wchar_t* handan_dir, const wchar_t* source_name, double th,
    #   cb_shot, cb_start, cb_stop, cb_isrec, cb_event, cb_log, void* ctx)
    _dll.start_rkaisi_teisi_w.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_double,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    _dll.start_rkaisi_teisi_w.restype = ctypes.c_void_p
    _dll.stop_rkaisi_teisi.argtypes = [ctypes.c_void_p]


def is_available() -> bool:
    import os as _os
    # Allow disabling native path via env for troubleshooting
    if (_os.getenv("DISABLE_NATIVE", "").strip().lower() in ("1","true","yes","on") or
        _os.getenv("DISABLE_NATIVE_AUTOMATION", "").strip().lower() in ("1","true","yes","on")):
        return False
    _load_dll()
    return bool(_available)


# Callback signatures
_CB_SHOT = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p)
_CB_START = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_CB_STOP = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
_CB_ISREC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
_CB_EVENT = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_double)
_CB_LOG = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_wchar_p)


class DoubleBattleNativeThread(threading.Thread):
    def __init__(self, obs: ObsClient, base_dir: str, logger: Optional[UiLogger] = None, source_name: str = "Capture1", capture_interval_sec: float = 2.0) -> None:
        super().__init__(daemon=True)
        _load_dll()
        if not _available:
            raise RuntimeError("native automation.dll not available")
        self._obs = obs
        self._base_dir = base_dir
        self._log = logger or UiLogger()
        self._source = source_name
        try:
            self._interval = float(capture_interval_sec or 0)
        except Exception:
            self._interval = 0.0
        self._stop = threading.Event()
        self._handle: Optional[int] = None
        # Paths prepared same as Python implementation
        self._haisin_dir = paths_utils.get_haisin_dir(base_dir)
        self._koutiku_dir = paths_utils.get_koutiku_dir(base_dir)
        os.makedirs(self._haisin_dir, exist_ok=True)
        os.makedirs(self._koutiku_dir, exist_ok=True)
        self._haisinyou_path = paths_utils.get_broadcast_output_path(base_dir)
        self._out_ext = paths_utils.get_output_format_ext()

        # Bind callbacks and keep references to avoid GC
        def _cb_shot(_ctx, src, out_path):
            try:
                self._obs.take_screenshot(src, out_path)
                return 0
            except Exception:
                return -1
        def _cb_log(_ctx, msg):
            try:
                self._log.log(msg)
            except Exception:
                pass
        self._cb_shot = _CB_SHOT(_cb_shot)
        self._cb_log = _CB_LOG(_cb_log)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._handle:
                _dll.stop_double_battle(ctypes.c_void_p(self._handle))
        except Exception:
            pass

    def run(self) -> None:
        self._log.log("[ダブルバトル/N] ネイティブ実装を開始")
        try:
            self._handle = _dll.start_double_battle_w(
                ctypes.c_wchar_p(self._base_dir),
                ctypes.c_wchar_p(self._source),
                ctypes.c_wchar_p(self._haisinyou_path),
                ctypes.c_wchar_p(self._koutiku_dir),
                ctypes.c_wchar_p(self._out_ext),
                ctypes.c_double(self._interval),
                ctypes.cast(self._cb_shot, ctypes.c_void_p),
                ctypes.cast(self._cb_log, ctypes.c_void_p),
                None,
            )
            # Wait until stopped
            while not self._stop.wait(0.2):
                pass
        except Exception as e:
            self._log.log(f"[ダブルバトル/N] エラー: {e}")
        finally:
            self._log.log("[ダブルバトル/N] 停止")


class RkaisiTeisiNativeThread(threading.Thread):
    def __init__(self, obs: ObsClient, handan_dir: str, logger: Optional[UiLogger] = None, source_name: str = "Capture1", result_queue: Optional["queue.Queue"] = None, threshold: float = 0.4) -> None:
        super().__init__(daemon=True)
        _load_dll()
        if not _available:
            raise RuntimeError("native automation.dll not available")
        self._obs = obs
        self._handan = handan_dir
        self._log = logger or UiLogger()
        self._source = source_name
        self._stop = threading.Event()
        self._handle: Optional[int] = None
        self._rq = result_queue
        self._threshold = float(threshold)
        self._rec_start_ts: Optional[float] = None

        def _cb_shot(_ctx, src, out_path):
            try:
                self._obs.take_screenshot(src, out_path)
                return 0
            except Exception:
                return -1
        def _cb_start(_ctx):
            try:
                try:
                    method = self._obs.start_recording_diag()
                    self._log.log(f"[録開始/停止/N] 開始メソッド: {method}")
                except Exception:
                    # Fallback to legacy wrapper
                    self._obs.start_recording()
                    self._log.log("[録開始/停止/N] 開始メソッド: legacy")
                return 0
            except Exception:
                return -1
        def _cb_stop(_ctx):
            try:
                try:
                    method = self._obs.stop_recording_diag()
                    self._log.log(f"[録開始/停止/N] 停止メソッド: {method}")
                except Exception:
                    self._obs.stop_recording()
                    self._log.log("[録開始/停止/N] 停止メソッド: legacy")
                return 0
            except Exception:
                return -1
        def _cb_isrec(_ctx, out_state_ptr):
            try:
                st = self._obs.is_recording()
                if st is True:
                    out_state_ptr[0] = 1
                elif st is False:
                    out_state_ptr[0] = 0
                else:
                    out_state_ptr[0] = -1
                return 0
            except Exception:
                out_state_ptr[0] = -1
                return -1
        def _cb_event(_ctx, ev, ts):
            # 1=start, 2=stop marker, 3=stopped on exit
            try:
                if ev == 1:
                    self._rec_start_ts = float(ts)
                elif ev in (2, 3):
                    if self._rq is not None:
                        try:
                            self._rq.put({"timestamp": time.time(), "type": "stop"}, timeout=0.05)
                        except Exception:
                            pass
            except Exception:
                pass
        def _cb_log(_ctx, msg):
            try:
                self._log.log(msg)
            except Exception:
                pass

        self._cb_shot = _CB_SHOT(_cb_shot)
        self._cb_start = _CB_START(_cb_start)
        self._cb_stop = _CB_STOP(_cb_stop)
        self._cb_isrec = _CB_ISREC(_cb_isrec)
        self._cb_event = _CB_EVENT(_cb_event)
        self._cb_log = _CB_LOG(_cb_log)

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._handle:
                _dll.stop_rkaisi_teisi(ctypes.c_void_p(self._handle))
        except Exception:
            pass

    def run(self) -> None:
        self._log.log("[録開始/停止/N] ネイティブ実装を開始")
        try:
            self._handle = _dll.start_rkaisi_teisi_w(
                ctypes.c_wchar_p(self._handan),
                ctypes.c_wchar_p(self._source),
                ctypes.c_double(self._threshold),
                ctypes.cast(self._cb_shot, ctypes.c_void_p),
                ctypes.cast(self._cb_start, ctypes.c_void_p),
                ctypes.cast(self._cb_stop, ctypes.c_void_p),
                ctypes.cast(self._cb_isrec, ctypes.c_void_p),
                ctypes.cast(self._cb_event, ctypes.c_void_p),
                ctypes.cast(self._cb_log, ctypes.c_void_p),
                None,
            )
            while not self._stop.wait(0.2):
                pass
        except Exception as e:
            self._log.log(f"[録開始/停止/N] エラー: {e}")
        finally:
            # Best-effort association of images to the last recording window
            try:
                if self._rec_start_ts is not None:
                    root_base = os.path.dirname(self._handan)
                    rec_dir = os.getenv("RECORDINGS_DIR", "").strip()
                    if not rec_dir:
                        self._log.log("[組合せ/N] RECORDINGS_DIR が未設定のため録画ファイルを特定できません")
                    else:
                        start_ts = float(self._rec_start_ts)
                        end_ts = time.time()
                        video = pairs_utils.find_recording_file(rec_dir, start_ts, end_ts)
                        if not video:
                            self._log.log("[組合せ/N] 録画ファイルが見つかりませんでした（時間範囲/拡張子/マージンを確認）")
                        else:
                            mapping = pairs_utils.associate_recording_window(root_base, start_ts, end_ts)
                            base = os.path.basename(video)
                            if mapping is not None:
                                self._log.log(f"[組合せ/N] 画像と録画を関連付けました -> {base}")
                            else:
                                self._log.log(f"[組合せ/N] 関連付けに失敗しました -> {base}")
            except Exception as e:
                self._log.log(f"[組合せ/N] エラー: {e}")
            self._log.log("[録開始/停止/N] 停止")

from __future__ import annotations

import base64
import os
import shutil
import threading
from typing import Optional
import configparser
import sys
from pathlib import Path

from obswebsocket import obsws, requests  # type: ignore


class ObsClient:
    """Thread-safe wrapper for obs-websocket-py calls used in this app.

    - Serializes access with a provided lock to avoid concurrent calls.
    - Provides helpers for screenshots, recording control, and text updates.
    """

    def __init__(self, host: str, port: int, password: str, lock: Optional[threading.Lock] = None) -> None:
        self._ws = obsws(host, port, password)
        self._lock = lock or threading.Lock()

    def connect(self) -> None:
        with self._lock:
            self._ws.connect()

    def disconnect(self) -> None:
        with self._lock:
            try:
                self._ws.disconnect()
            except Exception:
                pass

    # --- Scenes ---
    def list_scenes(self) -> list[str]:
        """Return a list of scene names."""
        with self._lock:
            res = self._ws.call(requests.GetSceneList())
        scenes = res.getScenes() if hasattr(res, "getScenes") else []  # type: ignore[attr-defined]
        out: list[str] = []
        for s in scenes:
            try:
                name = s.get("name") or s.get("sceneName")  # type: ignore[index]
                if isinstance(name, str):
                    out.append(name)
            except Exception:
                continue
        return out

    def set_current_scene(self, scene_name: str) -> None:
        with self._lock:
            self._ws.call(requests.SetCurrentScene(scene_name))

    # --- Sources ---
    def list_sources(self) -> list[str]:
        """Return a list of source names (OBS v4 API)."""
        with self._lock:
            res = self._ws.call(requests.GetSourcesList())
        sources = res.getSources() if hasattr(res, "getSources") else []  # type: ignore[attr-defined]
        out: list[str] = []
        for s in sources:
            try:
                name = s.get("name")  # type: ignore[index]
                if isinstance(name, str):
                    out.append(name)
            except Exception:
                continue
        return out

    # --- Recording ---
    def start_recording(self) -> None:
        """Start recording with compatibility across OBS websocket variants."""
        # v4
        try:
            with self._lock:
                self._ws.call(requests.StartRecording())
            return
        except Exception:
            pass
        # v5 compat (some forks expose StartRecord)
        try:
            req = getattr(requests, "StartRecord", None)
            if req is not None:
                with self._lock:
                    self._ws.call(req())
                return
        except Exception:
            pass
        # Last resort: try generic call name if available
        try:
            req = getattr(requests, "StartRecording", None)
            if req is not None:
                with self._lock:
                    self._ws.call(req())
                return
        except Exception:
            pass
        # If none succeeded, raise
        raise RuntimeError("Failed to start recording via obs-websocket")

    def stop_recording(self) -> None:
        """Stop recording with compatibility across OBS websocket variants."""
        # v4
        try:
            with self._lock:
                self._ws.call(requests.StopRecording())
            return
        except Exception:
            pass
        # v5 compat (some forks expose StopRecord)
        try:
            req = getattr(requests, "StopRecord", None)
            if req is not None:
                with self._lock:
                    self._ws.call(req())
                return
        except Exception:
            pass
        # Try again v4 in case transient
        try:
            with self._lock:
                self._ws.call(requests.StopRecording())
            return
        except Exception:
            pass
        raise RuntimeError("Failed to stop recording via obs-websocket")

    def is_recording(self) -> Optional[bool]:
        """Return True if recording is active, False if not, or None if unknown."""
        # v5: GetRecordStatus -> {"outputActive": bool}
        try:
            req = getattr(requests, "GetRecordStatus", None)
            if req is not None:
                with self._lock:
                    res = self._ws.call(req())
                d = getattr(res, "datain", {}) or {}
                for k in ("outputActive", "recording", "isRecording"):
                    v = d.get(k)
                    if isinstance(v, bool):
                        return v
        except Exception:
            pass
        # v4: GetStreamingStatus -> {"recording": bool}
        try:
            with self._lock:
                res = self._ws.call(requests.GetStreamingStatus())
            # Some versions expose getters
            for meth in ("getRecording", "getIsRecording"):
                try:
                    fn = getattr(res, meth)
                    v = fn()
                    if isinstance(v, bool):
                        return v
                except Exception:
                    pass
            d = getattr(res, "datain", {}) or {}
            v = d.get("recording")
            if isinstance(v, bool):
                return v
        except Exception:
            pass
        return None

    def get_recordings_dir(self) -> Optional[str]:
        """Best-effort to obtain OBS's recording directory via obs-websocket.

        Tries, in order:
        - GetRecordingFolder (v4 plugin)
        - GetProfileParameter with (Output, RecFilePath)
        - Local OBS config (global.ini + basic.ini) fallback
        - OS default videos directory as last resort
        Returns an absolute path string if available, otherwise None.
        """
        # 1) Try GetRecordingFolder
        try:
            with self._lock:
                req = getattr(requests, "GetRecordingFolder", None)
                if req is not None:
                    res = self._ws.call(req())
                else:
                    res = None
            if res is not None:
                d = getattr(res, "datain", None) or {}
                if isinstance(d, dict):
                    for k in ("rec-folder", "rec_folder", "recordingFolder", "path", "folder"):
                        v = d.get(k)
                        if isinstance(v, str) and v:
                            return v
                # Some library versions expose a getter
                for meth in ("getRecFolder", "getRecordingFolder"):
                    try:
                        fn = getattr(res, meth)
                        v = fn()
                        if isinstance(v, str) and v:
                            return v
                    except Exception:
                        pass
        except Exception:
            pass

        # 2) Try profile parameter
        try:
            with self._lock:
                req = getattr(requests, "GetProfileParameter", None)
                if req is not None:
                    res = self._ws.call(req(parameterCategory="Output", parameterName="RecFilePath"))
                else:
                    res = None
            if res is not None:
                d = getattr(res, "datain", None) or {}
                if isinstance(d, dict):
                    for k in ("parameterValue", "value", "parameter", "path"):
                        v = d.get(k)
                        if isinstance(v, str) and v:
                            return v
        except Exception:
            pass

        # 3) Try local OBS config files (works when OBS is on same machine)
        try:
            root: Optional[Path]
            if sys.platform.startswith("win"):
                appdata = os.getenv("APPDATA") or ""
                root = Path(appdata).joinpath("obs-studio") if appdata else None
            elif sys.platform == "darwin":
                root = Path.home().joinpath("Library", "Application Support", "obs-studio")
            else:
                root = Path.home().joinpath(".config", "obs-studio")
            if root and root.exists():
                global_ini = root.joinpath("global.ini")
                prof_name: Optional[str] = None
                if global_ini.exists():
                    try:
                        cp = configparser.ConfigParser()
                        cp.read(global_ini, encoding="utf-8")
                        # Try common keys across any section
                        for sec in cp.sections():
                            for k, v in cp.items(sec):
                                kl = k.lower()
                                if kl in ("lastprofile", "profile", "activeprofile", "currentprofile"):
                                    if v and isinstance(v, str):
                                        prof_name = v
                                        break
                            if prof_name:
                                break
                    except Exception:
                        pass
                # Fallback to first directory under profiles
                prof_dir = root.joinpath("basic", "profiles")
                if prof_name:
                    p = prof_dir.joinpath(prof_name)
                    if not p.exists():
                        # Some installations store profile dirs without spaces; try replacing spaces with underscores
                        p2 = prof_dir.joinpath(prof_name.replace(" ", "_"))
                        p = p2 if p2.exists() else p
                else:
                    # Pick latest modified profile dir if any
                    try:
                        dirs = [d for d in prof_dir.iterdir() if d.is_dir()]
                        if dirs:
                            dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
                            p = dirs[0]
                        else:
                            p = None  # type: ignore[assignment]
                    except Exception:
                        p = None  # type: ignore[assignment]
                if p:
                    basic_ini = p.joinpath("basic.ini")
                    if basic_ini.exists():
                        try:
                            cp2 = configparser.ConfigParser()
                            cp2.read(basic_ini, encoding="utf-8")
                            candidates: list[str] = []
                            for sec in cp2.sections():
                                for k, v in cp2.items(sec):
                                    kl = k.lower()
                                    if any(x in kl for x in ("rec", "record", "file")) and any(y in kl for y in ("path", "dir")):
                                        if isinstance(v, str) and v:
                                            candidates.append(v)
                            # Normalize and pick an existing directory-like path
                            for c in candidates:
                                try:
                                    c2 = os.path.expandvars(os.path.expanduser(c))
                                    # If file path is given, take its directory
                                    if os.path.splitext(c2)[1]:
                                        d = os.path.dirname(c2)
                                    else:
                                        d = c2
                                    if d and os.path.isabs(d) and os.path.isdir(d):
                                        return d
                                except Exception:
                                    continue
                        except Exception:
                            pass
        except Exception:
            pass

        # 4) OS default videos directory (best guess)
        try:
            if sys.platform.startswith("win"):
                # %USERPROFILE%\Videos
                d = os.path.join(os.path.expanduser("~"), "Videos")
                if os.path.isdir(d):
                    return d
            elif sys.platform == "darwin":
                d = os.path.join(os.path.expanduser("~"), "Movies")
                if os.path.isdir(d):
                    return d
            else:
                d = os.path.join(os.path.expanduser("~"), "Videos")
                if os.path.isdir(d):
                    return d
        except Exception:
            pass

        return None

    # --- Text Source ---
    def update_text_source(self, source_name: str, text: str) -> None:
        with self._lock:
            self._ws.call(
                requests.SetSourceSettings(sourceName=source_name, sourceSettings={"text": text})
            )

    # --- Screenshots ---
    def take_screenshot(self, source_name: str, save_path: str) -> None:
        """Take a screenshot of a source and write it to ``save_path``.

        Tries multiple strategies for compatibility with different OBS versions:
        1) v4: TakeSourceScreenshot (base64 in 'img')
        2) v4: SaveSourceScreenshot (saved to file)
        3) v5 compat: GetSourceScreenshot (base64 in 'imageData') if available
        """

        def _write_b64(data_uri_or_b64: str) -> bool:
            try:
                s = data_uri_or_b64 or ""
                # Strip any data URI prefix
                if "," in s and s.lower().startswith("data:image"):
                    s = s.split(",", 1)[1]
                b = s.encode("utf-8")
                # Fix missing padding if any
                pad = len(b) % 4
                if pad:
                    b += b"=" * (4 - pad)
                img = base64.b64decode(b)
                os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(img)
                return True
            except Exception:
                return False

        # 1) OBS v4: TakeSourceScreenshot (embedded base64)
        try:
            with self._lock:
                res = self._ws.call(
                    requests.TakeSourceScreenshot(
                        sourceName=source_name, embedPictureFormat="png", width=None, height=None
                    )
                )
            d = getattr(res, "datain", {}) or {}
            data = d.get("img") or d.get("imageData")  # some compat layers use 'imageData'
            if data and _write_b64(str(data)):
                return
        except Exception:
            # continue to fallback
            pass

        # 2) OBS v4: SaveSourceScreenshot (saved to file on OBS host)
        try:
            req_cls = getattr(requests, "SaveSourceScreenshot", None)
            if req_cls is not None:
                # Try both common param names across variants
                for kwargs in (
                    {"sourceName": source_name, "imageFormat": "png", "imageFilePath": save_path},
                    {"sourceName": source_name, "imageFormat": "png", "saveToFilePath": save_path},
                ):
                    try:
                        with self._lock:
                            self._ws.call(req_cls(**kwargs))
                        # If OBS saved the file where we can see it, we're done
                        if os.path.exists(save_path) and os.path.getsize(save_path) > 0:
                            return
                    except Exception:
                        continue
        except Exception:
            pass

        # 3) OBS v5: GetSourceScreenshot (compat libraries may expose this)
        try:
            req_cls = getattr(requests, "GetSourceScreenshot", None)
            if req_cls is not None:
                with self._lock:
                    res = self._ws.call(
                        req_cls(sourceName=source_name, imageFormat="png", imageWidth=0, imageHeight=0)
                    )
                d = getattr(res, "datain", {}) or {}
                data = d.get("imageData") or d.get("img")
                if data and _write_b64(str(data)):
                    return
        except Exception:
            pass

        raise ValueError("OBS did not return a screenshot image.")

    # --- Low-level access if needed by advanced flows ---
    @property
    def ws(self) -> obsws:  # type: ignore[name-defined]
        return self._ws

    @property
    def lock(self) -> threading.Lock:
        return self._lock


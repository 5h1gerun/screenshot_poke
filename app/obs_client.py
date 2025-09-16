from __future__ import annotations

import base64
import threading
from typing import Optional

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

    # --- Recording ---
    def start_recording(self) -> None:
        with self._lock:
            self._ws.call(requests.StartRecording())

    def stop_recording(self) -> None:
        with self._lock:
            self._ws.call(requests.StopRecording())

    # --- Text Source ---
    def update_text_source(self, source_name: str, text: str) -> None:
        with self._lock:
            self._ws.call(
                requests.SetSourceSettings(sourceName=source_name, sourceSettings={"text": text})
            )

    # --- Screenshots ---
    def take_screenshot(self, source_name: str, save_path: str) -> None:
        """Take screenshot for a given source, decode base64 and save to file."""
        with self._lock:
            res = self._ws.call(
                requests.TakeSourceScreenshot(
                    sourceName=source_name, embedPictureFormat="png", width=None, height=None
                )
            )
        data = res.datain.get("img")
        if not data:
            raise ValueError("OBS did not return a screenshot image.")
        b64 = data.split(",", 1)[1].encode("utf-8")
        pad = len(b64) % 4
        if pad:
            b64 += b"=" * (4 - pad)
        with open(save_path, "wb") as f:
            f.write(base64.b64decode(b64))

    # --- Low-level access if needed by advanced flows ---
    @property
    def ws(self) -> obsws:  # type: ignore[name-defined]
        return self._ws

    @property
    def lock(self) -> threading.Lock:
        return self._lock


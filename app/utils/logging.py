from __future__ import annotations

from typing import Callable, Optional

try:
    import tkinter as tk  # noqa: F401
except Exception:  # pragma: no cover
    tk = None  # type: ignore


class UiLogger:
    """Thread-aware logger for Tkinter-like widgets.

    - If a Tk widget is provided, always marshal delivery via `widget.after(0, ...)` to
      ensure UI updates occur on the main thread.
    - If no widget is available, call the provided callback or fall back to print.
    """

    def __init__(self, append_cb: Optional[Callable[[str], None]] = None, widget: Optional[object] = None):
        self._append_cb = append_cb
        self._widget = widget

    def log(self, message: str) -> None:
        w = self._widget
        # Prefer scheduling on the UI thread if a widget is available
        if w is not None and hasattr(w, "after"):
            try:
                w.after(0, lambda: self._deliver_on_ui_thread(message))
                return
            except Exception:
                # Fall through to direct callback/print if scheduling fails
                pass

        # No widget: try callback, then print
        if callable(self._append_cb):
            try:
                self._append_cb(message)
                return
            except Exception:
                pass
        print(message)

    def _deliver_on_ui_thread(self, message: str) -> None:
        # On UI thread now: prefer the append callback if present
        if callable(self._append_cb):
            try:
                self._append_cb(message)
                return
            except Exception:
                pass
        # Fallback to inserting directly if widget supports it
        w = self._widget
        if w is not None and hasattr(w, "insert"):
            try:
                w.insert("end", message + "\n")
                if hasattr(w, "see"):
                    w.see("end")
                return
            except Exception:
                pass
        print(message)


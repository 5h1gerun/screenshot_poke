from __future__ import annotations

from typing import Callable, Optional

try:
    import tkinter as tk  # noqa: F401
except Exception:  # pragma: no cover
    tk = None  # type: ignore


class UiLogger:
    """Thread-aware logger that can write to a Tk Text-like widget or fallback to print.

    Pass a callable to override output (e.g., Textual logger) if desired.
    """

    def __init__(self, append_cb: Optional[Callable[[str], None]] = None, widget: Optional[object] = None):
        self._append_cb = append_cb
        self._widget = widget

    def log(self, message: str) -> None:
        if callable(self._append_cb):
            try:
                self._append_cb(message)
                return
            except Exception:
                pass
        w = self._widget
        if w is not None and hasattr(w, "after") and hasattr(w, "insert"):
            try:
                w.after(0, lambda: self._insert(w, message))
                return
            except Exception:
                pass
        print(message)

    @staticmethod
    def _insert(widget, message: str):
        try:
            widget.insert("end", message + "\n")
            widget.see("end")
        except Exception:
            print(message)


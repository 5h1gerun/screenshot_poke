from __future__ import annotations

import os
import hashlib
import re
import sys
import threading
import queue
import concurrent.futures
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
from pathlib import Path
from typing import Optional, Dict, List

import json
import subprocess
import time
import tkinter.simpledialog as sd

import customtkinter as ctk
from PIL import Image
import webbrowser

from app.obs_client import ObsClient
from app.threads.double_battle import DoubleBattleThread
from app.threads.rkaisi_teisi import RkaisiTeisiThread
from app.threads.syouhai import SyouhaiThread
from app.threads.discord_webhook import DiscordWebhookThread
from app.threads.result_association import ResultAssociationThread
from app.utils.logging import UiLogger
from app.version import VERSION as APP_VERSION
from app.utils import stats as stats_utils
from app.utils import paths as paths_utils
try:
    from app.utils.native_thumb import generate_thumbnail_native as _gen_thumb_native, NATIVE_AVAILABLE as _NATIVE_THUMB
except Exception:
    def _gen_thumb_native(*_args, **_kwargs):
        return False
    _NATIVE_THUMB = False


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        # Load appearance/theme from env
        self._appearance = os.getenv("APP_APPEARANCE", "Dark")
        self._accent_theme = os.getenv("APP_THEME", "blue")
        ctk.set_appearance_mode(self._appearance)
        ctk.set_default_color_theme(self._accent_theme)

        self.title(f"OBS Screenshot/Template Tool v{APP_VERSION}")
        self.geometry("1200x800")
        # Try to set window icon when running from source (PNG). In frozen exe, the
        # embedded .ico from PyInstaller is used automatically on Windows.
        try:
            if not getattr(sys, "frozen", False):
                from pathlib import Path as _P
                _png = _P(__file__).resolve().parents[2] / "icon.png"
                if _png.exists():
                    self.iconphoto(True, tk.PhotoImage(file=str(_png)))
        except Exception:
            pass

        # Runtime state
        self._obs: Optional[ObsClient] = None
        self._lock = threading.Lock()
        self._th_double: Optional[DoubleBattleThread] = None
        self._th_rkaisi: Optional[RkaisiTeisiThread] = None
        self._th_syouhai: Optional[SyouhaiThread] = None
        self._th_discord: Optional[DiscordWebhookThread] = None
        self._th_result_assoc: Optional[ResultAssociationThread] = None
        self._results_queue: Optional[queue.Queue] = None

        # Widgets
        self.host_entry: ctk.CTkEntry
        self.port_entry: ctk.CTkEntry
        self.pass_entry: ctk.CTkEntry
        self.base_dir_entry: ctk.CTkEntry
        self.chk_double_var = tk.BooleanVar(value=self._env_bool("ENABLE_DOUBLE", True))
        self.chk_rkaisi_var = tk.BooleanVar(value=self._env_bool("ENABLE_RKAISI", True))
        self.chk_syouhai_var = tk.BooleanVar(value=self._env_bool("ENABLE_SYOUHAI", True))
        self.chk_discord_var = tk.BooleanVar(value=self._env_bool("ENABLE_DISCORD", False))
        self.discord_url_var = tk.StringVar(value=os.getenv("DISCORD_WEBHOOK_URL", ""))
        self.log_text: ctk.CTkTextbox
        self.scene_opt: ctk.CTkOptionMenu
        self.source_opt: ctk.CTkOptionMenu
        self._gallery_search_var = tk.StringVar(value="")
        # Keep a handle to the search entry to detach textvariable on rebuild
        self._gallery_search_entry: Optional[ctk.CTkEntry] = None
        self._gallery_tags_map: Dict[str, List[str]] = {}
        self._gallery_pairs_map: Dict[str, str] = {}
        # Gallery state
        self._thumb_refs: list[ctk.CTkImage] = []
        self._auto_refresh_var = tk.BooleanVar(value=True)
        self._gallery_after_id: Optional[str] = None
        # Live search debounce timer id
        self._search_after_id: Optional[str] = None
        # Search suggestions frame (created in gallery UI)
        self._search_sugg_frame: Optional[ctk.CTkFrame] = None
        # Live tag edit debounce ids per filename
        self._tag_edit_after_ids: Dict[str, str] = {}
        # Async thumbnail loader
        self._thumb_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=int(os.getenv("GALLERY_WORKERS", "4") or 4)
        )
        self._gallery_load_token: Optional[int] = None
        # Gallery UI helpers
        self._gallery_placeholder_img: Optional[ctk.CTkImage] = None
        self._gallery_last_width: int = 0
        self._gallery_resize_after_id: Optional[str] = None
        # Chunked render + scrollregion throttle
        self._gallery_chunk_after_id: Optional[str] = None
        self._gallery_load_files: list[str] = []
        self._gallery_load_cols: int = 1
        self._scrollregion_after_id: Optional[str] = None
        self._scrollregion_pending: bool = False

        self._build_ui()
        # Graceful shutdown on window close
        try:
            self.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

    def _maximize_on_start(self) -> None:
        """Maximize the window on startup without forcing true fullscreen.

        - Windows: use state('zoomed')
        - Linux: try attributes('-zoomed', True) then state('zoomed')
        - macOS: set geometry to screen size as an approximation
        """
        try:
            self.update_idletasks()
            if sys.platform.startswith("win"):
                try:
                    self.state("zoomed")
                    return
                except Exception:
                    pass
            if sys.platform == "darwin":
                try:
                    sw = self.winfo_screenwidth()
                    sh = self.winfo_screenheight()
                    self.geometry(f"{sw}x{sh}+0+0")
                    return
                except Exception:
                    pass
            try:
                self.attributes("-zoomed", True)
            except Exception:
                try:
                    self.state("zoomed")
                except Exception:
                    pass
        except Exception:
            pass

    def _on_tab_changed(self, value: str | None = None) -> None:
        """Adjust layout depending on selected tab.

        - Log: settings (left) wide -> weights (2, 1)
        - Gallery/Stats: content (right) wide -> weights (1, 2)
        """
        try:
            name = value or (self._tabs.get() if getattr(self, "_tabs", None) else "Log")  # type: ignore[attr-defined]
            name_l = (name or "").strip().lower()
        except Exception:
            name_l = "log"
        try:
            if name_l == "log":
                self.grid_columnconfigure(0, weight=2)
                self.grid_columnconfigure(1, weight=1)
            else:
                self.grid_columnconfigure(0, weight=1)
                self.grid_columnconfigure(1, weight=2)
        except Exception:
            pass
        # When switching to gallery/stats, refresh sizing-sensitive views
        try:
            if name_l == "gallery":
                self.after(50, self._reload_gallery)
            elif name_l == "stats":
                self.after(50, self._refresh_stats)
        except Exception:
            pass

    # --- UI ---
    def _build_ui(self) -> None:
        # Default: Log tab selected -> settingsを広め (2:1)
        try:
            self.grid_columnconfigure(0, weight=2)
            self.grid_columnconfigure(1, weight=1)
        except Exception:
            pass
        # Keep the title row compact; let content row expand
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        title = ctk.CTkLabel(self, text=f"OBS Screenshot / Template Tool v{APP_VERSION}", font=ctk.CTkFont(size=18, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 0))

        sidebar = ctk.CTkScrollableFrame(self, corner_radius=10)
        sidebar.grid(row=1, column=0, sticky="nsew", padx=(16, 8), pady=12)
        try:
            sidebar.grid_rowconfigure(99, weight=1)
        except Exception:
            pass

        # Start maximized (best-effort, cross-platform)
        try:
            self.after(100, self._maximize_on_start)
        except Exception:
            pass

        # OBS connection
        obs_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        obs_frame.grid(row=0, column=0, sticky="we", padx=8, pady=(8, 6))
        try:
            obs_frame.grid_columnconfigure(1, weight=1)
        except Exception:
            pass
        ctk.CTkLabel(obs_frame, text="OBS Connection", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 4)
        )

        ctk.CTkLabel(obs_frame, text="Host").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        self.host_entry = ctk.CTkEntry(obs_frame, width=480)
        self.host_entry.insert(0, os.getenv("OBS_HOST", "localhost"))
        self.host_entry.grid(row=1, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Port").grid(row=2, column=0, sticky="e", padx=8, pady=4)
        self.port_entry = ctk.CTkEntry(obs_frame, width=200)
        self.port_entry.insert(0, os.getenv("OBS_PORT", "4444"))
        self.port_entry.grid(row=2, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Password").grid(row=3, column=0, sticky="e", padx=8, pady=4)
        self.pass_entry = ctk.CTkEntry(obs_frame, width=480, show="*")
        self.pass_entry.insert(0, os.getenv("OBS_PASSWORD", ""))
        self.pass_entry.grid(row=3, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Base Directory").grid(row=4, column=0, sticky="e", padx=8, pady=4)
        self.base_dir_entry = ctk.CTkEntry(obs_frame, width=520)
        # Resolve BASE_DIR relative to the app/.env location so it stays relocatable
        self.base_dir_entry.insert(0, self._resolve_base_dir_default())
        self.base_dir_entry.grid(row=4, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkButton(obs_frame, text="Browse", command=self._browse_base_dir).grid(row=4, column=2, padx=8, pady=4)

        # Scene/Source selection
        ctk.CTkLabel(obs_frame, text="Scene").grid(row=5, column=0, sticky="e", padx=8, pady=4)
        default_scene = os.getenv("OBS_SCENE", "")
        self.scene_opt = ctk.CTkOptionMenu(obs_frame, values=[default_scene] if default_scene else [""], width=300)
        self.scene_opt.set(default_scene)
        self.scene_opt.grid(row=5, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Source").grid(row=6, column=0, sticky="e", padx=8, pady=4)
        default_source = os.getenv("OBS_SOURCE", "Capture1")
        self.source_opt = ctk.CTkOptionMenu(obs_frame, values=[default_source], width=300)
        self.source_opt.set(default_source)
        self.source_opt.grid(row=6, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkButton(obs_frame, text="更新", command=self._refresh_obs_lists, width=80).grid(row=6, column=2, padx=8, pady=4)

        # Season (for per-season stats)
        ctk.CTkLabel(obs_frame, text="Season").grid(row=7, column=0, sticky="e", padx=8, pady=4)
        self.season_entry = ctk.CTkEntry(obs_frame, width=300)
        self.season_entry.insert(0, os.getenv("SEASON", ""))
        self.season_entry.grid(row=7, column=1, sticky="w", padx=8, pady=4)

        # Recordings directory (for image-video pairing)
        ctk.CTkLabel(obs_frame, text="Recordings Dir").grid(row=8, column=0, sticky="e", padx=8, pady=4)
        self.recordings_dir_entry = ctk.CTkEntry(obs_frame, width=520)
        self.recordings_dir_entry.insert(0, os.getenv("RECORDINGS_DIR", ""))
        self.recordings_dir_entry.grid(row=8, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkButton(obs_frame, text="Browse", width=80, command=self._browse_recordings_dir).grid(row=8, column=2, padx=8, pady=4)
        ctk.CTkButton(obs_frame, text="From OBS", width=90, command=self._fetch_recordings_dir_from_obs).grid(row=8, column=3, padx=(0,8), pady=4)

        # Quick diagnostics
        diag = ctk.CTkFrame(obs_frame, corner_radius=8)
        diag.grid(row=9, column=0, columnspan=4, sticky="we", padx=8, pady=(2, 8))
        try:
            diag.grid_columnconfigure(0, weight=0)
            diag.grid_columnconfigure(1, weight=0)
            diag.grid_columnconfigure(2, weight=0)
            diag.grid_columnconfigure(3, weight=1)
        except Exception:
            pass
        ctk.CTkLabel(diag, text="Diagnostics:").grid(row=0, column=0, padx=(8,6), pady=6)
        ctk.CTkButton(diag, text="Test Screenshot", width=120, command=self._test_screenshot).grid(row=0, column=1, padx=6, pady=6)
        ctk.CTkButton(diag, text="Test Start Rec", width=120, command=self._test_start_rec).grid(row=0, column=2, padx=6, pady=6)
        ctk.CTkButton(diag, text="Test Stop Rec", width=120, command=self._test_stop_rec).grid(row=0, column=3, padx=6, pady=6)

        # Scripts
        script_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        script_frame.grid(row=1, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(script_frame, text="Scripts", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(8, 4))
        ctk.CTkSwitch(script_frame, text="構築のスクリーンショット", variable=self.chk_double_var).pack(anchor="w", padx=8, pady=2)
        ctk.CTkSwitch(script_frame, text="自動録画開始・停止", variable=self.chk_rkaisi_var).pack(anchor="w", padx=8, pady=2)
        ctk.CTkSwitch(script_frame, text="戦績を自動更新", variable=self.chk_syouhai_var).pack(anchor="w", padx=8, pady=2)

        # Discord webhook controls
        discord_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        discord_frame.grid(row=2, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(discord_frame, text="Discord", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4)
        )
        ctk.CTkSwitch(discord_frame, text="構築をDiscordへ送信", variable=self.chk_discord_var).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=8, pady=2
        )
        ctk.CTkLabel(discord_frame, text="Webhook URL").grid(row=2, column=0, sticky="e", padx=8, pady=(4, 8))
        try:
            discord_frame.grid_columnconfigure(1, weight=1)
        except Exception:
            pass
        self.discord_url_entry = ctk.CTkEntry(discord_frame, width=520, textvariable=self.discord_url_var)
        self.discord_url_entry.grid(row=2, column=1, sticky="we", padx=(0, 8), pady=(4, 8))

        # Output settings
        output_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        output_frame.grid(row=3, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(output_frame, text="Output", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4)
        )
        # Koutiku dir (構築保管)
        ctk.CTkLabel(output_frame, text="構築保管").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        try:
            output_frame.grid_columnconfigure(1, weight=1)
        except Exception:
            pass
        self.koutiku_dir_entry = ctk.CTkEntry(output_frame, width=260)
        self.koutiku_dir_entry.insert(0, os.getenv("OUTPUT_KOUTIKU_DIR", "koutiku"))
        self.koutiku_dir_entry.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        # Haisin dir (配信用)
        ctk.CTkLabel(output_frame, text="配信用").grid(row=2, column=0, sticky="e", padx=8, pady=4)
        self.haisin_dir_entry = ctk.CTkEntry(output_frame, width=260)
        self.haisin_dir_entry.insert(0, os.getenv("OUTPUT_HAISIN_DIR", "haisin"))
        self.haisin_dir_entry.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        # Image format
        ctk.CTkLabel(output_frame, text="Image Format").grid(row=3, column=0, sticky="e", padx=8, pady=(4, 8))
        self.format_opt = ctk.CTkOptionMenu(output_frame, values=["PNG", "JPG", "WEBP"]) 
        self.format_opt.set((os.getenv("OUTPUT_IMAGE_FORMAT", "PNG") or "PNG").upper())
        self.format_opt.grid(row=3, column=1, sticky="w", padx=8, pady=(4, 8))

        # Controls
        control_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        control_frame.grid(row=4, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(control_frame, text="Controls", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4)
        )
        ctk.CTkButton(control_frame, text="Start", command=self._start_threads, height=36).grid(
            row=1, column=0, sticky="we", padx=8, pady=6
        )
        ctk.CTkButton(control_frame, text="Stop", command=self._stop_threads, height=36, fg_color="#8A1C1C").grid(
            row=1, column=1, sticky="we", padx=8, pady=6
        )
        ctk.CTkButton(control_frame, text="Save Settings", command=self._save_settings).grid(
            row=2, column=0, columnspan=2, sticky="we", padx=8, pady=(0, 8)
        )

        # Theme
        theme_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        theme_frame.grid(row=5, column=0, sticky="we", padx=8, pady=(6, 12))
        ctk.CTkLabel(theme_frame, text="Theme", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4)
        )
        ctk.CTkLabel(theme_frame, text="Appearance").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        self.appearance_opt = ctk.CTkOptionMenu(theme_frame, values=["System", "Light", "Dark"], command=self._change_appearance)
        self.appearance_opt.set(self._appearance)
        self.appearance_opt.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(theme_frame, text="Accent").grid(row=2, column=0, sticky="e", padx=8, pady=(4, 12))
        self.theme_opt = ctk.CTkOptionMenu(theme_frame, values=["blue", "dark-blue", "green"], command=self._change_theme)
        self.theme_opt.set(self._accent_theme)
        self.theme_opt.grid(row=2, column=1, sticky="w", padx=8, pady=(4, 12))

        # Right: Tabview with Log / Gallery
        right = ctk.CTkFrame(self, corner_radius=10)
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=12)
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)
        # Keep a handle for width calculations in Gallery/Stats
        self._right_frame = right

        tabs = ctk.CTkTabview(right)
        tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        tab_log = tabs.add("Log")
        tab_gallery = tabs.add("Gallery")
        tab_stats = tabs.add("Stats")
        # Keep reference for tab detection
        self._tabs = tabs
        # Hook tab change to adjust layout (Log: left wide, Gallery/Stats: right wide)
        try:
            def _tab_click(value: str, _tabs=tabs):
                # Ensure the clicked tab is actually selected before adjusting layout
                try:
                    _tabs.set(value)
                except Exception:
                    pass
                self._on_tab_changed(value)
            tabs._segmented_button.configure(command=_tab_click)  # type: ignore[attr-defined]
        except Exception:
            pass

        # Log tab
        tab_log.grid_rowconfigure(0, weight=1)
        tab_log.grid_columnconfigure(0, weight=1)
        log_container = ctk.CTkFrame(tab_log)
        log_container.grid(row=0, column=0, sticky="nsew")
        log_container.grid_rowconfigure(0, weight=1)
        log_container.grid_columnconfigure(0, weight=1)
        self.log_text = ctk.CTkTextbox(log_container, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ctk.CTkScrollbar(log_container, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        # Gallery tab
        self._build_gallery_ui(tab_gallery)
        # Stats tab
        self._build_stats_ui(tab_stats)
        # Initial load
        self.after(200, self._reload_gallery)
        # Auto-refresh if enabled
        self._schedule_gallery_refresh()
        # Optional: check updates in background
        try:
            self.after(1500, self._maybe_check_updates)
        except Exception:
            pass
        # Apply initial layout for selected tab
        try:
            self.after(50, lambda: self._on_tab_changed(self._tabs.get()))  # type: ignore[attr-defined]
        except Exception:
            pass

    # --- callbacks ---
    def _on_search_changed(self, event=None) -> None:
        try:
            if self._search_after_id is not None:
                self.after_cancel(self._search_after_id)
        except Exception:
            pass
        try:
            # Debounce frequent typing to avoid excessive reloads
            # Update suggestions immediately, then reload
            self._update_search_suggestions()
            self._search_after_id = self.after(250, self._reload_gallery)
        except Exception:
            self._search_after_id = None

    # --- search helpers ---
    def _tokenize_search(self, text: str) -> List[str]:
        # Split on spaces, commas, Japanese commas and spaces, semicolons
        if not text:
            return []
        try:
            normalized = text.replace("\u3000", " ")  # full-width space to half-width
            parts = re.split(r"[\s,、，;；]+", normalized)
            return [p for p in parts if p]
        except Exception:
            return [text.strip()] if text.strip() else []

    def _is_tag_token(self, token: str) -> bool:
        try:
            return token.startswith("tag:") or token.startswith("タグ:")
        except Exception:
            return False

    def _update_search_suggestions(self) -> None:
        # Build suggestions for the search box based on current input
        frame = getattr(self, "_search_sugg_frame", None)
        if frame is None or not getattr(frame, "winfo_exists", lambda: False)():
            return
        # Clear
        try:
            for w in list(frame.winfo_children()):
                w.destroy()
        except Exception:
            pass

        query = self._gallery_search_var.get() or ""
        tokens = self._tokenize_search(query)
        # Determine partial (last token being typed)
        partial = ""
        had_sep_end = False
        try:
            had_sep_end = re.search(r"[\s,、，;；]+$", (query or "").replace("\u3000", " ")) is not None
        except Exception:
            had_sep_end = False
        if not had_sep_end and tokens:
            partial = tokens[-1]

        # Build candidate tag list
        all_tags = self._all_existing_tags()
        # If user typed tag:prefix (or タグ:prefix), filter on the part after colon
        prefix = partial
        tag_mode = False
        if self._is_tag_token(partial):
            tag_mode = True
            try:
                prefix = partial.split(":", 1)[1]
            except Exception:
                prefix = ""

        pool = all_tags
        if prefix:
            pool = [t for t in pool if t.lower().startswith(prefix.lower())]
        pool = pool[:8]
        if not pool:
            return

        def apply(tag: str):
            cur = self._gallery_search_var.get() or ""
            if not cur:
                new = f"tag:{tag}"
            else:
                if had_sep_end:
                    new = cur + (" " if not cur.endswith(" ") else "") + f"tag:{tag}"
                else:
                    # replace the last partial token
                    parts = self._tokenize_search(cur)
                    if parts:
                        parts[-1] = f"tag:{tag}"
                        new = " ".join(parts)
                    else:
                        new = f"tag:{tag}"
            self._gallery_search_var.set(new)
            # Refresh suggestions and gallery
            self._update_search_suggestions()
            self._on_search_changed()

        # Render suggestion buttons
        for i, t in enumerate(pool):
            try:
                b = ctk.CTkButton(frame, text=f"tag:{t}", width=1, height=24, command=lambda tag=t: apply(tag))
                b.grid(row=0, column=i, padx=(8 if i == 0 else 6, 0), pady=(0, 6))
            except Exception:
                pass

    def _parse_tags_fixed(self, text: str) -> List[str]:
        """Parse multiple tags separated by spaces/commas (ASCII or Japanese)."""
        try:
            normalized = (text or "").replace("\u3000", " ").strip()
            parts = re.split(r"[\s,、，;；]+", normalized)
        except Exception:
            parts = [(text or "").strip()]
        result: List[str] = []
        seen = set()
        for p in parts:
            t = (p or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            result.append(t)
        return result

    def _browse_base_dir(self) -> None:
        path = fd.askdirectory(title="Choose base directory")
        if path:
            self.base_dir_entry.delete(0, tk.END)
            self.base_dir_entry.insert(0, path)

    def _append_log(self, message: str) -> None:
        # Ensure UI updates are scheduled on the Tk thread
        def _do_append():
            try:
                if getattr(self, "log_text", None) and self.log_text.winfo_exists():
                    self.log_text.insert("end", message + "\n")
                    self.log_text.see("end")
            except Exception:
                pass
        try:
            self.after(0, _do_append)
        except Exception:
            # Fallback (e.g., during shutdown) — best effort
            try:
                if getattr(self, "log_text", None):
                    self.log_text.insert("end", message + "\n")
                    self.log_text.see("end")
            except Exception:
                pass

    def _on_close(self) -> None:
        # Stop background threads and timers safely
        try:
            self._stop_threads()
        except Exception:
            pass
        # Cancel scheduled callbacks
        try:
            if getattr(self, "_search_after_id", None):
                try:
                    self.after_cancel(self._search_after_id)
                except Exception:
                    pass
                self._search_after_id = None
        except Exception:
            pass
        try:
            if getattr(self, "_gallery_after_id", None):
                try:
                    self.after_cancel(self._gallery_after_id)
                except Exception:
                    pass
                self._gallery_after_id = None
        except Exception:
            pass
        # Cancel gallery resize debounce timer
        try:
            if getattr(self, "_gallery_resize_after_id", None):
                try:
                    self.after_cancel(self._gallery_resize_after_id)
                except Exception:
                    pass
                self._gallery_resize_after_id = None
        except Exception:
            pass
        # Cancel chunked render timer
        try:
            if getattr(self, "_gallery_chunk_after_id", None):
                try:
                    self.after_cancel(self._gallery_chunk_after_id)
                except Exception:
                    pass
                self._gallery_chunk_after_id = None
        except Exception:
            pass
        # Cancel scrollregion throttle timer
        try:
            if getattr(self, "_scrollregion_after_id", None):
                try:
                    self.after_cancel(self._scrollregion_after_id)
                except Exception:
                    pass
                self._scrollregion_after_id = None
                self._scrollregion_pending = False
        except Exception:
            pass
        # Shutdown thumbnail executor
        try:
            if getattr(self, "_thumb_executor", None):
                self._thumb_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        # Destroy window
        try:
            self.destroy()
        except Exception:
            pass

    def _change_appearance(self, mode: str) -> None:
        try:
            ctk.set_appearance_mode(mode)
            self._appearance = mode
        except Exception:
            pass

    def _change_theme(self, theme: str) -> None:
        try:
            if self._any_threads_alive():
                mb.showinfo("Theme", "Stop threads before changing theme.")
                return
            ctk.set_default_color_theme(theme)
            self._accent_theme = theme
            # Rebuild UI to apply theme colors across widgets
            self._rebuild_ui_preserving_state()
        except Exception as e:
            mb.showerror("Theme Error", str(e))

    # --- helpers ---
    def _any_threads_alive(self) -> bool:
        for th in (self._th_double, self._th_rkaisi, self._th_syouhai):
            try:
                if th and th.is_alive():
                    return True
            except Exception:
                pass
        return False

    def _rebuild_ui_preserving_state(self) -> None:
        # capture state
        host = getattr(self, "host_entry", None).get() if getattr(self, "host_entry", None) else os.getenv("OBS_HOST", "localhost")
        port = getattr(self, "port_entry", None).get() if getattr(self, "port_entry", None) else os.getenv("OBS_PORT", "4444")
        password = getattr(self, "pass_entry", None).get() if getattr(self, "pass_entry", None) else os.getenv("OBS_PASSWORD", "")
        base_dir = getattr(self, "base_dir_entry", None).get() if getattr(self, "base_dir_entry", None) else os.getcwd()
        chk_double = getattr(self, "chk_double_var", tk.BooleanVar(value=True)).get()
        chk_rkaisi = getattr(self, "chk_rkaisi_var", tk.BooleanVar(value=True)).get()
        chk_syouhai = getattr(self, "chk_syouhai_var", tk.BooleanVar(value=True)).get()
        scene = getattr(self, "scene_opt", None).get() if getattr(self, "scene_opt", None) else os.getenv("OBS_SCENE", "")
        source = getattr(self, "source_opt", None).get() if getattr(self, "source_opt", None) else os.getenv("OBS_SOURCE", "Capture1")
        log_content = ""
        if getattr(self, "log_text", None):
            try:
                log_content = self.log_text.get("1.0", "end-1c")
            except Exception:
                pass

        # Proactively cancel pending timers that might reference destroyed widgets
        try:
            if self._search_after_id is not None:
                try:
                    self.after_cancel(self._search_after_id)
                except Exception:
                    pass
                self._search_after_id = None
        except Exception:
            pass
        try:
            if self._gallery_after_id is not None:
                try:
                    self.after_cancel(self._gallery_after_id)
                except Exception:
                    pass
                self._gallery_after_id = None
        except Exception:
            pass

        # Detach textvariable from search entry to avoid stale trace callbacks
        try:
            if getattr(self, "_gallery_search_entry", None) is not None:
                entry = self._gallery_search_entry
                try:
                    if hasattr(entry, "winfo_exists") and entry.winfo_exists():
                        entry.configure(textvariable=None)
                except Exception:
                    pass
                self._gallery_search_entry = None
        except Exception:
            pass

        # Replace the search StringVar to drop any traces bound by old widgets
        try:
            _cur_search = self._gallery_search_var.get() if self._gallery_search_var is not None else ""
        except Exception:
            _cur_search = ""
        try:
            self._gallery_search_var = tk.StringVar(value=_cur_search)
        except Exception:
            pass

        for child in self.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

        self._build_ui()

        self.host_entry.delete(0, tk.END); self.host_entry.insert(0, host)
        self.port_entry.delete(0, tk.END); self.port_entry.insert(0, port)
        self.pass_entry.delete(0, tk.END); self.pass_entry.insert(0, password)
        self.base_dir_entry.delete(0, tk.END); self.base_dir_entry.insert(0, base_dir)
        self.chk_double_var.set(chk_double)
        self.chk_rkaisi_var.set(chk_rkaisi)
        self.chk_syouhai_var.set(chk_syouhai)
        # Restore scene/source selections
        try:
            self.scene_opt.configure(values=[scene] if scene else [""])
            self.scene_opt.set(scene)
            self.source_opt.configure(values=[source] if source else ["Capture1"])
            self.source_opt.set(source)
        except Exception:
            pass
        # Reset theme selections to current state
        try:
            self.appearance_opt.set(self._appearance)
            self.theme_opt.set(self._accent_theme)
        except Exception:
            pass
        if log_content:
            try:
                self.log_text.insert("1.0", log_content)
            except Exception:
                pass

        # After rebuilding, refresh gallery
        try:
            self._reload_gallery()
        except Exception:
            pass

    # --- start/stop ---
    def _start_threads(self) -> None:
        host = self.host_entry.get()
        try:
            port = int(self.port_entry.get())
        except Exception:
            mb.showerror("Input Error", "Port must be a number")
            return
        password = self.pass_entry.get()
        base_dir = self.base_dir_entry.get()
        os.makedirs(base_dir, exist_ok=True)

        # reconnect OBS
        if self._obs is not None:
            try:
                self._obs.disconnect()
            except Exception:
                pass
            self._obs = None
        try:
            self._obs = ObsClient(host, port, password, self._lock)
            self._obs.connect()
            mb.showinfo("接続", f"OBS WebSocket に接続しました: {host}:{port}")
            self._append_log("[アプリ] OBSに接続しました")
            # Persist settings on successful connect
            self._save_settings()
            # If recordings dir is empty, try to fetch from OBS now
            try:
                if not (self.recordings_dir_entry.get().strip() if getattr(self, "recordings_dir_entry", None) else ""):
                    folder = self._obs.get_recordings_dir()
                    if folder:
                        self.recordings_dir_entry.delete(0, tk.END)
                        self.recordings_dir_entry.insert(0, folder)
                        self._save_settings()
                        self._append_log(f"[OBS] Recording フォルダ取得: {folder}")
            except Exception:
                pass
        except Exception as e:
            mb.showerror("接続エラー", f"OBS への接続に失敗しました。\n{e}")
            self._append_log(f"[アプリ] 接続エラー: {e}")
            return

        logger = UiLogger(self._append_log, self.log_text)

        # Switch scene if selected
        try:
            scene = self.scene_opt.get().strip()
            if scene:
                self._obs.set_current_scene(scene)
                self._append_log(f"[アプリ] シーン切替: {scene}")
        except Exception:
            pass

        src = self.source_opt.get().strip() or "Capture1"
        if self.chk_double_var.get():
            # Capture interval for DoubleBattle (ms). Default = 1000 (slow), 0 = continuous.
            try:
                _dbl_ms = float(os.getenv("DOUBLE_CAPTURE_INTERVAL_MS", "1000") or 1000)
            except Exception:
                _dbl_ms = 1000.0
            self._th_double = DoubleBattleThread(
                self._obs,
                base_dir,
                logger,
                source_name=src,
                capture_interval_sec=max(0.0, _dbl_ms / 1000.0),
            )
            self._th_double.start()
        # Result association queue shared between Syouhai and association thread
        # Create upfront so multiple producers (Syouhai, RkaisiTeisi) can push events
        self._results_queue = queue.Queue()

        if self.chk_rkaisi_var.get():
            handantmp = os.path.join(base_dir, "handantmp")
            os.makedirs(handantmp, exist_ok=True)
            self._th_rkaisi = RkaisiTeisiThread(self._obs, handantmp, logger, source_name=src, result_queue=self._results_queue)
            self._th_rkaisi.start()
        if self.chk_syouhai_var.get():
            self._th_syouhai = SyouhaiThread(self._obs, base_dir, logger, source_name=src, result_queue=self._results_queue)
            self._th_syouhai.start()
            # Start association thread to tie new images to results. Default-win fallback
            # is disabled by default to avoid false +1 on first match.
            # You can enable it via env var ASSOC_DEFAULT_WIN_TIMEOUT (seconds).
            self._th_result_assoc = ResultAssociationThread(
                base_dir,
                self._results_queue,
                logger,
                default_win_timeout=float(os.getenv("ASSOC_DEFAULT_WIN_TIMEOUT", "0") or 0),
                obs=self._obs,
                text_source="sensekiText1",
                season=(self.season_entry.get().strip() if getattr(self, "season_entry", None) else ""),
            )
            self._th_result_assoc.start()
        if self.chk_discord_var.get():
            url = (self.discord_url_var.get() or "").strip()
            if url:
                self._th_discord = DiscordWebhookThread(base_dir, url, logger)
                self._th_discord.start()
            else:
                self._append_log("[Discord] Webhook URL が未設定のため開始しません")

    def _stop_threads(self) -> None:
        # Signal threads to stop
        for th in (self._th_double, self._th_rkaisi, self._th_syouhai, self._th_discord, self._th_result_assoc):
            try:
                if th and th.is_alive():
                    th.stop()  # type: ignore[attr-defined]
            except Exception:
                pass

        # Join with a bit more patience so recording can stop before disconnect
        deadline = time.time() + 5.0
        for th in (self._th_double, self._th_rkaisi, self._th_syouhai, self._th_discord, self._th_result_assoc):
            try:
                if not th:
                    continue
                rem = max(0.1, deadline - time.time())
                th.join(timeout=rem)
            except Exception:
                pass

        # As a last safety, if OBS is still recording, stop it before disconnecting
        if self._obs is not None:
            try:
                st = self._obs.is_recording()
            except Exception:
                st = None
            if st is True:
                try:
                    self._append_log("[アプリ] 録画を停止しています…")
                    # Prefer v5 method/hotkey/toggle with diagnostics
                    method = None
                    try:
                        method = self._obs.stop_recording_diag()
                        self._append_log(f"[アプリ] 停止メソッド: {method}")
                    except Exception:
                        self._obs.stop_recording()
                        self._append_log("[アプリ] 停止メソッド: legacy")
                    # Poll up to ~10s to confirm
                    for _ in range(50):
                        stat = self._obs.is_recording()
                        if stat is False:
                            break
                        time.sleep(0.2)
                except Exception:
                    pass
                # Small grace to let files flush
                try:
                    time.sleep(0.3)
                except Exception:
                    pass

        if self._obs is not None:
            try:
                self._obs.disconnect()
                self._append_log("[アプリ] OBSから切断しました")
            except Exception:
                pass
            self._obs = None

        mb.showinfo("停止", "すべてのスレッドを停止しました。")

    # --- Diagnostics ---
    def _test_screenshot(self) -> None:
        try:
            if not self._obs:
                mb.showerror("OBS", "まずOBSに接続してください")
                return
            src = self.source_opt.get().strip() or os.getenv("OBS_SOURCE", "Capture1")
            base = self.base_dir_entry.get().strip() or os.getcwd()
            handan = os.path.join(base, "handantmp")
            os.makedirs(handan, exist_ok=True)
            path = os.path.join(handan, "test_scene.png")
            self._obs.take_screenshot(src, path)
            ok = os.path.isfile(path) and os.path.getsize(path) > 0
            if ok:
                self._append_log(f"[診断] スクリーンショット成功 -> {path}")
                mb.showinfo("Screenshot", f"OK -> {path}")
            else:
                self._append_log(f"[診断] スクリーンショット失敗 -> {path}")
                mb.showerror("Screenshot", f"失敗 -> {path}")
        except Exception as e:
            self._append_log(f"[診断] スクリーンショット例外: {e}")
            mb.showerror("Screenshot", str(e))

    def _test_start_rec(self) -> None:
        try:
            if not self._obs:
                mb.showerror("OBS", "まずOBSに接続してください")
                return
            method = None
            try:
                method = self._obs.start_recording_diag()
            except Exception:
                self._obs.start_recording()
                method = "legacy"
            self._append_log(f"[診断] 録画開始メソッド: {method}")
            mb.showinfo("Start Recording", f"Method: {method}")
        except Exception as e:
            self._append_log(f"[診断] 録画開始例外: {e}")
            mb.showerror("Start Recording", str(e))

    def _test_stop_rec(self) -> None:
        try:
            if not self._obs:
                mb.showerror("OBS", "まずOBSに接続してください")
                return
            method = None
            try:
                method = self._obs.stop_recording_diag()
            except Exception:
                self._obs.stop_recording()
                method = "legacy"
            self._append_log(f"[診断] 録画停止メソッド: {method}")
            mb.showinfo("Stop Recording", f"Method: {method}")
        except Exception as e:
            self._append_log(f"[診断] 録画停止例外: {e}")
            mb.showerror("Stop Recording", str(e))

    # --- Stats UI ---
    def _build_stats_ui(self, parent: ctk.CTkFrame) -> None:
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        ctrl = ctk.CTkFrame(parent)
        # Align horizontal padding with Gallery (Tabview already has outer 12px)
        ctrl.grid(row=0, column=0, sticky="we", padx=0, pady=8)
        ctrl.grid_columnconfigure(4, weight=1)

        ctk.CTkLabel(ctrl, text="期間 (YYYY-MM-DD)").grid(row=0, column=0, padx=(8, 6))
        self._stats_start = ctk.CTkEntry(ctrl, width=120)
        self._stats_start.grid(row=0, column=1)
        ctk.CTkLabel(ctrl, text="〜").grid(row=0, column=2)
        self._stats_end = ctk.CTkEntry(ctrl, width=120)
        self._stats_end.grid(row=0, column=3)

        # Season filter
        ctk.CTkLabel(ctrl, text="Season").grid(row=0, column=4, padx=(12, 6))
        seasons = self._list_seasons()
        self._stats_season_opt = ctk.CTkOptionMenu(ctrl, values=["[All]"] + seasons, width=120)
        self._stats_season_opt.set(os.getenv("SEASON", "[All]") or "[All]")
        self._stats_season_opt.grid(row=0, column=5)

        ctk.CTkButton(ctrl, text="Reload", width=80, command=self._refresh_stats).grid(row=0, column=6, padx=(8, 6))
        ctk.CTkButton(ctrl, text="Open CSV", width=90, command=self._open_results_csv).grid(row=0, column=7, padx=(0, 6))
        ctk.CTkButton(ctrl, text="Save Chart", width=100, command=self._save_stats_chart).grid(row=0, column=8, padx=(0, 8))

        self._stats_summary = ctk.CTkLabel(parent, text="", anchor="w")
        # Remove extra horizontal padding to match Gallery width
        self._stats_summary.grid(row=1, column=0, sticky="we", padx=0)

        self._stats_chart_label = ctk.CTkLabel(parent, text="")
        # Match Gallery horizontal padding; keep some vertical breathing room
        self._stats_chart_label.grid(row=2, column=0, sticky="nsew", padx=0, pady=12)
        self._stats_chart_img_ref: Optional[ctk.CTkImage] = None

        try:
            self.after(300, self._refresh_stats)
        except Exception:
            pass

    def _parse_date(self, s: str):
        try:
            s2 = (s or "").strip()
            if not s2:
                return None
            import datetime as _dt
            return _dt.datetime.strptime(s2, "%Y-%m-%d").date()
        except Exception:
            return None

    def _refresh_stats(self) -> None:
        base_dir = self.base_dir_entry.get().strip() if getattr(self, "base_dir_entry", None) else self._resolve_base_dir_default()
        rows4 = stats_utils.load_results_with_season(base_dir)
        import datetime as _dt
        start = self._parse_date(self._stats_start.get()) if getattr(self, "_stats_start", None) else None
        end = self._parse_date(self._stats_end.get()) if getattr(self, "_stats_end", None) else None
        # Season filter
        season_sel = None
        try:
            cur = self._stats_season_opt.get()
            season_sel = None if (not cur or cur == "[All]") else cur
        except Exception:
            season_sel = None
        rows4 = [x for x in rows4 if (not season_sel or x[3] == season_sel)]
        rows = [(t, i, r) for (t, i, r, _s) in rows4]
        per_day = stats_utils.aggregate_by_day(rows, start, end)
        win, lose, dc, wr = stats_utils.compute_totals([(t, i, r) for (t, i, r) in rows if (not start or t.date() >= start) and (not end or t.date() <= end)])
        self._stats_summary.configure(text=f"Win: {win}  Lose: {lose}  DC: {dc}  WinRate: {wr:.1f}%")

        # Dynamic chart size based on available width
        try:
            avail_w = self._stats_chart_label.winfo_width()
            if not avail_w or avail_w <= 1:
                avail_w = (self._right_frame.winfo_width() if getattr(self, "_right_frame", None) else self.winfo_width()) - 48
        except Exception:
            avail_w = 900
        # Clamp chart size to a smaller, consistent range
        try:
            _max_w = int(os.getenv("STATS_CHART_MAX_W", "1200") or 1200)
        except Exception:
            _max_w = 1200
        try:
            _min_w = int(os.getenv("STATS_CHART_MIN_W", "700") or 700)
        except Exception:
            _min_w = 700
        try:
            w = max(_min_w, min(_max_w, int(avail_w)))
        except Exception:
            w = max(_min_w, 900)
        try:
            # Slightly flatter aspect ratio to reduce height
            h = max(280, min(500, int(w * 0.30)))
        except Exception:
            h = 320
        img = stats_utils.render_winrate_chart(per_day, size=(w, h))
        try:
            ctki = ctk.CTkImage(light_image=img, dark_image=img, size=img.size)
            self._stats_chart_img_ref = ctki
            self._stats_chart_label.configure(image=ctki, text="")
        except Exception:
            # fallback: save temp and show message
            self._stats_chart_label.configure(text="チャートの描画に失敗")
        # Update season list in option menu
        try:
            seasons = self._list_seasons()
            cur = self._stats_season_opt.get()
            values = ["[All]"] + seasons
            self._stats_season_opt.configure(values=values)
            if cur not in values:
                self._stats_season_opt.set("[All]")
        except Exception:
            pass

    def _list_seasons(self) -> List[str]:
        base_dir = self.base_dir_entry.get().strip() if getattr(self, "base_dir_entry", None) else self._resolve_base_dir_default()
        try:
            return stats_utils.list_seasons(base_dir)
        except Exception:
            return []

    def _open_results_csv(self) -> None:
        base_dir = self.base_dir_entry.get().strip() if getattr(self, "base_dir_entry", None) else self._resolve_base_dir_default()
        path = paths_utils.get_results_csv_path(base_dir)
        try:
            if os.path.exists(path):
                if sys.platform.startswith("win"):
                    os.startfile(path)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            else:
                mb.showinfo("CSV", "まだ結果CSVはありません。")
        except Exception as e:
            mb.showerror("CSV", f"CSV を開けませんでした\n{e}")

    def _save_stats_chart(self) -> None:
        # Save current chart image as PNG
        try:
            from PIL import Image
        except Exception:
            mb.showerror("保存", "Pillow が見つかりません")
            return
        base_dir = self.base_dir_entry.get().strip() if getattr(self, "base_dir_entry", None) else self._resolve_base_dir_default()
        rows = stats_utils.load_results(base_dir)
        start = self._parse_date(self._stats_start.get()) if getattr(self, "_stats_start", None) else None
        end = self._parse_date(self._stats_end.get()) if getattr(self, "_stats_end", None) else None
        per_day = stats_utils.aggregate_by_day(rows, start, end)
        img = stats_utils.render_winrate_chart(per_day, size=(900, 320))
        try:
            out = fd.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", ".png")], title="チャートを保存")
            if out:
                img.save(out, format="PNG")
        except Exception as e:
            mb.showerror("保存", f"保存に失敗しました\n{e}")

    # --- settings persistence ---
    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        v = os.getenv(name)
        if v is None:
            return default
        v = v.strip().lower()
        return v in ("1", "true", "yes", "on")

    def _get_dotenv_path(self) -> str:
        # Prefer alongside the main entry file (combined_app.py), fallback to CWD
        try:
            if getattr(sys, "frozen", False):
                return str(Path(sys.executable).resolve().parent / ".env")
            main_file = getattr(sys.modules.get("__main__"), "__file__", None)
            if main_file:
                return str(Path(main_file).resolve().parent / ".env")
        except Exception:
            pass
        return str(Path.cwd() / ".env")

    def _resolve_base_dir_default(self) -> str:
        """Resolve BASE_DIR from env relative to the app/.env location.

        - If BASE_DIR is unset, treat as '.' (repo root next to combined_app.py).
        - If BASE_DIR is relative, resolve it relative to the .env directory.
        - If BASE_DIR is absolute, normalize it.
        """
        raw = os.getenv("BASE_DIR", ".").strip()
        try:
            env_dir = Path(self._get_dotenv_path()).resolve().parent
        except Exception:
            env_dir = Path.cwd()
        try:
            p = Path(raw)
            if not p.is_absolute():
                p = (env_dir / p).resolve()
            else:
                p = p.resolve()
            return str(p)
        except Exception:
            return str(env_dir)

    def _save_settings(self) -> None:
        dotenv_path = self._get_dotenv_path()
        # Collect values
        # Normalize BASE_DIR for saving: store as relative to .env dir if possible
        base_dir_raw = self.base_dir_entry.get().strip()
        try:
            env_dir = Path(self._get_dotenv_path()).resolve().parent
        except Exception:
            env_dir = Path.cwd()
        try:
            resolved = Path(base_dir_raw).resolve()
            rel = resolved.relative_to(env_dir)
            base_dir_to_save = "." if str(rel) == "." else str(rel)
        except Exception:
            # Outside repo or cannot resolve -> save absolute
            try:
                base_dir_to_save = str(Path(base_dir_raw).resolve())
            except Exception:
                base_dir_to_save = base_dir_raw

        cfg = {
            "OBS_HOST": self.host_entry.get().strip(),
            "OBS_PORT": str(self.port_entry.get()).strip(),
            "OBS_PASSWORD": self.pass_entry.get(),
            "BASE_DIR": base_dir_to_save,
            "SEASON": (self.season_entry.get().strip() if getattr(self, "season_entry", None) else os.getenv("SEASON", "")),
            "APP_APPEARANCE": self._appearance,
            "APP_THEME": self._accent_theme,
            "ENABLE_DOUBLE": "true" if self.chk_double_var.get() else "false",
            "ENABLE_RKAISI": "true" if self.chk_rkaisi_var.get() else "false",
            "ENABLE_SYOUHAI": "true" if self.chk_syouhai_var.get() else "false",
            "OBS_SCENE": self.scene_opt.get().strip() if getattr(self, "scene_opt", None) else os.getenv("OBS_SCENE", ""),
            "OBS_SOURCE": self.source_opt.get().strip() if getattr(self, "source_opt", None) else os.getenv("OBS_SOURCE", "Capture1"),
            "ENABLE_DISCORD": "true" if self.chk_discord_var.get() else "false",
            "DISCORD_WEBHOOK_URL": (self.discord_url_var.get() or "").strip(),
            "RECORDINGS_DIR": (self.recordings_dir_entry.get().strip() if getattr(self, "recordings_dir_entry", None) else os.getenv("RECORDINGS_DIR", "")),
            # Output customization
            "OUTPUT_KOUTIKU_DIR": (self.koutiku_dir_entry.get().strip() if getattr(self, "koutiku_dir_entry", None) else os.getenv("OUTPUT_KOUTIKU_DIR", "koutiku")),
            "OUTPUT_HAISIN_DIR": (self.haisin_dir_entry.get().strip() if getattr(self, "haisin_dir_entry", None) else os.getenv("OUTPUT_HAISIN_DIR", "haisin")),
            "OUTPUT_IMAGE_FORMAT": ((self.format_opt.get().strip().upper() if getattr(self, "format_opt", None) else os.getenv("OUTPUT_IMAGE_FORMAT", "PNG")) or "PNG"),
        }

        # Read existing lines to preserve comments/unknown keys
        existing_lines: list[str] = []
        try:
            existing_lines = Path(dotenv_path).read_text(encoding="utf-8").splitlines(True)
        except Exception:
            existing_lines = []

        keys = set(cfg.keys())
        used = set()
        out_lines: list[str] = []
        for line in existing_lines:
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                out_lines.append(line)
                continue
            k = line.split("=", 1)[0].strip()
            if k in cfg:
                out_lines.append(f"{k}={cfg[k]}\n")
                used.add(k)
            else:
                out_lines.append(line)

        for k in keys - used:
            out_lines.append(f"{k}={cfg[k]}\n")

        try:
            Path(dotenv_path).write_text("".join(out_lines), encoding="utf-8")
            self._append_log(f"[アプリ] 設定を保存しました -> {dotenv_path}")
            # Also update process env so new settings take effect without restart
            try:
                for k, v in cfg.items():
                    os.environ[k] = v
            except Exception:
                pass
        except Exception as e:
            mb.showerror("保存エラー", f"設定の保存に失敗しました: {dotenv_path}\n{e}")
            return

    def _browse_recordings_dir(self) -> None:
        try:
            path = fd.askdirectory(title="Select Recordings Directory", initialdir=self.recordings_dir_entry.get().strip() or os.path.expanduser("~"))
        except Exception:
            path = ""
        if path:
            try:
                self.recordings_dir_entry.delete(0, tk.END)
                self.recordings_dir_entry.insert(0, path)
            except Exception:
                pass

    def _fetch_recordings_dir_from_obs(self) -> None:
        # Use existing connection if available; else create temp client
        client = self._obs
        created_temp = False
        try:
            if client is None:
                try:
                    host = self.host_entry.get().strip()
                    port = int(self.port_entry.get())
                    password = self.pass_entry.get()
                except Exception:
                    mb.showerror("入力エラー", "OBS 接続情報を確認してください")
                    return
                client = ObsClient(host, port, password, self._lock)
                client.connect()
                created_temp = True
            folder = None
            try:
                folder = client.get_recordings_dir()
            except Exception:
                folder = None
            if folder and isinstance(folder, str):
                try:
                    self.recordings_dir_entry.delete(0, tk.END)
                    self.recordings_dir_entry.insert(0, folder)
                except Exception:
                    pass
                self._append_log(f"[OBS] Recording フォルダ取得: {folder}")
                # Optionally persist immediately
                try:
                    self._save_settings()
                except Exception:
                    pass
            else:
                mb.showwarning("未取得", "OBS から録画フォルダを取得できませんでした。手動で指定してください。")
        except Exception as e:
            mb.showerror("取得エラー", f"録画フォルダの取得に失敗しました\n{e}")
        finally:
            if created_temp and client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass

    # --- auto update ---
    def _maybe_check_updates(self) -> None:
        enabled = self._env_bool("AUTO_UPDATE", False)
        feed_url = (os.getenv("UPDATE_FEED_URL", "") or "").strip()
        if not enabled or not feed_url:
            return

        def worker():
            import json, hashlib, tempfile, urllib.request, urllib.error, platform, shutil, time, os, re
            cur = APP_VERSION
            # Manual update style: open latest release page in browser
            try:
                owner_repo = None
                m = re.match(r"^github://([^/]+)/([^/]+)$", feed_url, re.IGNORECASE)
                if m:
                    owner_repo = f"{m.group(1)}/{m.group(2)}"
                if owner_repo is None:
                    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)", feed_url, re.IGNORECASE)
                    if m:
                        owner_repo = f"{m.group(1)}/{m.group(2)}"
                if owner_repo is None:
                    m = re.match(r"^https?://api\.github\.com/repos/([^/]+/[^/]+)/", feed_url, re.IGNORECASE)
                    if m:
                        owner_repo = m.group(1)
                if owner_repo:
                    release_page = f"https://github.com/{owner_repo}/releases/latest"
                    latest_ver = ""
                    try:
                        headers = {
                            "User-Agent": "obs-screenshot-tool",
                            "Accept": "application/vnd.github+json, application/json;q=0.9",
                        }
                        token = (os.getenv("GITHUB_TOKEN", "") or "").strip()
                        if token:
                            headers["Authorization"] = f"Bearer {token}"
                        req = urllib.request.Request(
                            f"https://api.github.com/repos/{owner_repo}/releases/latest", headers=headers
                        )
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            data = resp.read()
                        feed = json.loads(data.decode("utf-8", errors="ignore"))
                        def _extract_version(text: str) -> str:
                            try:
                                if not text:
                                    return ""
                                # Extract 1.2 or 1.2.3 from strings like v1.2.3 / ver1.2.3
                                mm = re.search(r"(?i)(?:^|[^\d])((\d+)\.(\d+)(?:\.(\d+))?)", str(text))
                                return mm.group(1) if mm else ""
                            except Exception:
                                return ""
                        tag = str(feed.get("tag_name") or "")
                        latest_ver = _extract_version(tag) or _extract_version(str(feed.get("name") or "")) or _extract_version(str(feed.get("body") or ""))
                    except Exception as e:
                        self._append_log(f"[更新] バージョン確認に失敗: {e}")
                    def _pver(v: str):
                        try:
                            parts = [int(p) for p in (v or "0").split(".")]
                        except Exception:
                            parts = [0]
                        while len(parts) < 3:
                            parts.append(0)
                        return tuple(parts[:3])
                    if latest_ver:
                        if _pver(latest_ver) <= _pver(cur):
                            self._append_log("[更新] 最新版を利用中です")
                            return
                        self._append_log(f"[更新] 新しいバージョンが見つかりました: {latest_ver} — リリースページを開きます")
                    else:
                        self._append_log("[更新] バージョン確認に失敗: リリースページを開きます")
                    try:
                        webbrowser.open(release_page, new=2)
                    except Exception:
                        try:
                            if sys.platform.startswith("win"):
                                os.startfile(release_page)  # type: ignore[attr-defined]
                            elif sys.platform == "darwin":
                                subprocess.Popen(["open", release_page])
                            else:
                                subprocess.Popen(["xdg-open", release_page])
                        except Exception:
                            pass
                    return
                # Non-GitHub: open provided URL directly
                self._append_log("[更新] リリースURLを開きます")
                try:
                    webbrowser.open(feed_url, new=2)
                except Exception:
                    pass
                return
            except Exception:
                # バージョン確認/ブラウザ起動に失敗した場合は何もしません
                return

        threading.Thread(target=worker, daemon=True).start()

    # --- Gallery ---
    def _build_gallery_ui(self, parent: ctk.CTkFrame) -> None:
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        ctrl = ctk.CTkFrame(parent)
        ctrl.grid(row=0, column=0, sticky="we", pady=(4, 6))
        ctrl.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(ctrl, text="koutiku:").grid(row=0, column=0, sticky="w", padx=(8, 6), pady=6)
        self._gallery_path_label = ctk.CTkLabel(ctrl, text=self._current_koutiku_path(), anchor="w")
        self._gallery_path_label.grid(row=0, column=1, sticky="we", pady=6)

        ctk.CTkButton(ctrl, text="Reload", width=80, command=self._reload_gallery).grid(row=0, column=2, padx=6)
        ctk.CTkSwitch(ctrl, text="Auto Refresh", variable=self._auto_refresh_var, command=self._toggle_auto_refresh).grid(row=0, column=3, padx=(6, 8))

        # Search controls
        ctk.CTkLabel(ctrl, text="検索:").grid(row=1, column=0, sticky="e", padx=(8, 6))
        search_entry = ctk.CTkEntry(ctrl, textvariable=self._gallery_search_var)
        search_entry.grid(row=1, column=1, sticky="we", pady=6)
        # Store reference so we can detach textvariable safely on rebuild
        try:
            self._gallery_search_entry = search_entry
        except Exception:
            pass
        try:
            # Live filter: reload gallery as the user types (debounced)
            search_entry.bind("<KeyRelease>", self._on_search_changed)
        except Exception:
            pass
        # Suggestions holder under the search entry (replaces search/clear buttons)
        self._search_sugg_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        self._search_sugg_frame.grid(row=2, column=0, columnspan=4, sticky="we", padx=(8, 8))

        # Scrollable grid for thumbnails
        self._gallery_scroll = ctk.CTkScrollableFrame(parent, corner_radius=8)
        self._gallery_scroll.grid(row=1, column=0, sticky="nsew")
        # Columns will be adjusted dynamically in _reload_gallery based on available width
        try:
            # Recalculate layout when the available width changes (debounced)
            self._gallery_scroll.bind("<Configure>", self._on_gallery_configure)
        except Exception:
            pass

        # Ensure mouse wheel scroll works even when hovering child widgets
        self._install_gallery_wheel_handler()
        # Make scrollbar slimmer for better aesthetics
        try:
            self._tune_gallery_scrollbar()
        except Exception:
            pass

    def _install_gallery_wheel_handler(self) -> None:
        # Bind a global mouse-wheel handler which only acts when the pointer
        # is over the Gallery tab/scroll area. This improves scrolling
        # reliability on Windows/Mac/Linux and for nested widgets.
        def _gallery_canvas_global():
            # Best-effort to find CTkScrollableFrame's canvas
            try:
                for attr in ("_parent_canvas", "_canvas", "canvas"):
                    c = getattr(self._gallery_scroll, attr, None)
                    if c is not None:
                        return c
            except Exception:
                pass
            try:
                import tkinter as _tk
                for child in self._gallery_scroll.winfo_children():
                    if isinstance(child, _tk.Canvas):
                        return child
            except Exception:
                pass
            return None
        def _is_over_gallery(x: int, y: int) -> bool:
            try:
                w = self.winfo_containing(x, y)
                if w is None:
                    return False
                # Walk up parents to see if we are inside the gallery scroll frame
                cur = w
                target = getattr(self, "_gallery_scroll", None)
                while cur is not None:
                    if cur is target:
                        return True
                    cur = getattr(cur, "master", None)
            except Exception:
                pass
            return False

        def _on_wheel(event):
            try:
                x = self.winfo_pointerx() - self.winfo_rootx()
                y = self.winfo_pointery() - self.winfo_rooty()
                if not _is_over_gallery(x, y):
                    return
                cv = _gallery_canvas_global()
                if cv is None:
                    return
                # Compute direction/amount; platform differences handled here
                if getattr(event, "num", None) in (4, 5):
                    # Linux button events: one step per click
                    steps = -1 if event.num == 4 else 1
                else:
                    d = int(getattr(event, "delta", 0) or 0)
                    if sys.platform == "darwin":
                        # macOS: delta is small but signed; use one step per event
                        steps = -1 if d > 0 else 1
                    else:
                        # Windows: accumulate to 120 units per notch, support high-precision trackpads
                        try:
                            self._gallery_wheel_accum
                        except AttributeError:
                            self._gallery_wheel_accum = 0
                        self._gallery_wheel_accum += d
                        quanta = 120
                        steps = 0
                        if abs(self._gallery_wheel_accum) >= quanta:
                            steps = -int(self._gallery_wheel_accum / quanta)
                            self._gallery_wheel_accum -= -steps * quanta
                if steps:
                    # Acceleration with modifier keys (Shift x2, Ctrl x3)
                    accel = 1
                    try:
                        st = int(getattr(event, "state", 0) or 0)
                        if st & 0x0001:
                            accel *= 2
                        if st & 0x0004:
                            accel *= 3
                    except Exception:
                        pass

                    # Allow unit-based override via env
                    try:
                        import os as _os
                        lines = int((_os.getenv("GALLERY_SCROLL_LINES", "0") or 0))
                    except Exception:
                        lines = 0

                    if lines > 0:
                        move = steps * max(1, lines) * max(1, accel)
                        try:
                            cv.yview_scroll(move, "units")
                        except Exception:
                            pass
                    else:
                        # Fraction-based scrolling; default auto by rows
                        try:
                            import os as _os
                            fenv = (_os.getenv("GALLERY_SCROLL_FRACTION", "auto") or "auto").strip().lower()
                        except Exception:
                            fenv = "auto"
                        if fenv and fenv != "auto":
                            try:
                                frac = float(fenv)
                            except Exception:
                                frac = 0.08
                        else:
                            # Auto: one row per step scaled by optional multiplier
                            try:
                                rows = int(getattr(self, "_gallery_rows_count", 0) or 0)
                            except Exception:
                                rows = 0
                            try:
                                import os as _os
                                mult = float((_os.getenv("GALLERY_ROW_STEP_MULT", "1.0") or 1.0))
                            except Exception:
                                mult = 1.0
                            if rows > 0:
                                frac = max(0.005, min(0.5, (1.0 / float(rows)) * mult))
                            else:
                                frac = 0.08
                        try:
                            y0, y1 = cv.yview()
                        except Exception:
                            y0, y1 = 0.0, 1.0
                        df = steps * frac * max(1, accel)
                        new_y = min(1.0, max(0.0, y0 + df))
                        try:
                            cv.yview_moveto(new_y)
                        except Exception:
                            pass
            except Exception:
                pass

        try:
            # Add global bindings (add="+") so we don't override others
            self.bind_all("<MouseWheel>", _on_wheel, add="+")         # Windows/macOS
            self.bind_all("<Button-4>", _on_wheel, add="+")           # Linux scroll up
            self.bind_all("<Button-5>", _on_wheel, add="+")           # Linux scroll down
        except Exception:
            pass

    def _refresh_gallery_scrollregion(self) -> None:
        # Force-update the canvas scrollregion to include all content
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            # Attempt to find the underlying canvas used by CTkScrollableFrame
            cv = None
            for attr in ("_parent_canvas", "_canvas", "canvas"):
                cv = getattr(self._gallery_scroll, attr, None)
                if cv is not None:
                    break
            if cv is None:
                import tkinter as _tk
                for child in self._gallery_scroll.winfo_children():
                    if isinstance(child, _tk.Canvas):
                        cv = child
                        break
            if cv is not None:
                try:
                    # Prefer inner container request size if available
                    _container = getattr(self._gallery_scroll, "_scrollable_frame", None) or getattr(self._gallery_scroll, "scrollable_frame", None)
                    if _container is not None:
                        self.update_idletasks()
                        w = max(cv.winfo_width(), _container.winfo_reqwidth())
                        h = _container.winfo_reqheight()
                        cv.configure(scrollregion=(0, 0, w, h))
                    else:
                        region = cv.bbox("all")
                        if region is not None:
                            cv.configure(scrollregion=region)
                except Exception:
                    pass
        except Exception:
            pass

    def _request_gallery_scrollregion_refresh(self, delay_ms: int = 50) -> None:
        try:
            if self._scrollregion_pending and self._scrollregion_after_id is not None:
                return
            self._scrollregion_pending = True
            def _do():
                try:
                    self._refresh_gallery_scrollregion()
                finally:
                    self._scrollregion_pending = False
                    self._scrollregion_after_id = None
            self._scrollregion_after_id = self.after(max(0, int(delay_ms)), _do)
        except Exception:
            try:
                self._refresh_gallery_scrollregion()
            except Exception:
                pass

    def _tune_gallery_scrollbar(self) -> None:
        # Try to reduce the scrollbar thickness (width)
        try:
            import os as _os
            w = int((_os.getenv("GALLERY_SCROLLBAR_WIDTH", "14") or 14))
        except Exception:
            w = 14
        targets = []
        try:
            for attr in ("_scrollbar", "scrollbar", "_vertical_scrollbar", "_v_scrollbar"):
                sb = getattr(self._gallery_scroll, attr, None)
                if sb is not None:
                    targets.append(sb)
        except Exception:
            pass
        try:
            for ch in self._gallery_scroll.winfo_children():
                try:
                    import customtkinter as _ctk
                    if isinstance(ch, _ctk.CTkScrollbar):
                        targets.append(ch)
                except Exception:
                    # Fallback: best-effort by class name
                    if ch.__class__.__name__.lower().endswith("scrollbar"):
                        targets.append(ch)
        except Exception:
            pass
        seen = set()
        for sb in targets:
            try:
                if id(sb) in seen:
                    continue
                seen.add(id(sb))
                sb.configure(width=w)
            except Exception:
                pass

    def _on_gallery_configure(self, event=None):
        # Only react while Gallery tab is active
        try:
            name = self._tabs.get() if getattr(self, "_tabs", None) else ""
            if (name or "").strip().lower() != "gallery":
                return
        except Exception:
            pass
        try:
            w = int(getattr(event, "width", 0) or self._gallery_scroll.winfo_width())
        except Exception:
            w = 0
        if w <= 1:
            return
        try:
            if abs(w - self._gallery_last_width) < 40:
                return
            self._gallery_last_width = w
        except Exception:
            self._gallery_last_width = w
        # Debounce rapid size events
        try:
            if self._gallery_resize_after_id is not None:
                self.after_cancel(self._gallery_resize_after_id)
        except Exception:
            pass
        try:
            self._gallery_resize_after_id = self.after(120, self._reload_gallery)
        except Exception:
            self._gallery_resize_after_id = None

    def _current_koutiku_path(self) -> str:
        base_dir = self.base_dir_entry.get().strip() if getattr(self, "base_dir_entry", None) else self._resolve_base_dir_default()
        return paths_utils.get_koutiku_dir(base_dir)

    def _current_haisin_dir(self) -> str:
        base_dir = self.base_dir_entry.get().strip() if getattr(self, "base_dir_entry", None) else self._resolve_base_dir_default()
        return paths_utils.get_haisin_dir(base_dir)

    def _broadcast_image_path(self) -> str:
        base_dir = self.base_dir_entry.get().strip() if getattr(self, "base_dir_entry", None) else self._resolve_base_dir_default()
        return paths_utils.get_broadcast_output_path(base_dir)

    def _reload_gallery(self) -> None:
        # Token to ignore stale async callbacks from prior reloads
        try:
            self._gallery_load_token = (0 if self._gallery_load_token is None else self._gallery_load_token + 1)
        except Exception:
            self._gallery_load_token = 0
        # Update path label
        try:
            self._gallery_path_label.configure(text=self._current_koutiku_path())
        except Exception:
            pass

        koutiku = self._current_koutiku_path()
        os.makedirs(koutiku, exist_ok=True)

        # Clear previous thumbnails (inner container of scrollable frame)
        try:
            _container = getattr(self._gallery_scroll, "_scrollable_frame", None) or getattr(self._gallery_scroll, "scrollable_frame", None) or self._gallery_scroll
            for child in _container.winfo_children():
                child.destroy()
        except Exception:
            pass
        self._thumb_refs.clear()

        # Collect image files (fast): use scandir and prefetch mtime
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        try:
            items: list[tuple[str, float]] = []  # (path, mtime)
            with os.scandir(koutiku) as it:
                for entry in it:
                    try:
                        if not entry.is_file():
                            continue
                        if os.path.splitext(entry.name)[1].lower() not in exts:
                            continue
                        try:
                            mt = entry.stat().st_mtime
                        except Exception:
                            mt = 0.0
                        items.append((entry.path, mt))
                    except Exception:
                        continue
        except Exception:
            items = []

        # Load tags/pairs and filter by search query
        self._load_gallery_tags()
        self._load_gallery_pairs()
        # Keep a copy before filtering for robust fallback
        items_all = list(items)
        query = (self._gallery_search_var.get() or "").strip()
        tokens = [t for t in query.replace("　", " ").split(" ") if t]
        if False and tokens:
            def _match(path: str) -> bool:
                name = os.path.basename(path)
                tags = set(self._gallery_tags_map.get(name, []))
                for t in tokens:
                    if t.startswith("tag:") or t.startswith("タグ:"):
                        key = t.split(":", 1)[1]
                        if key and key in tags:
                            return True
                    else:
                        if t.lower() in name.lower():
                            return True
                return False
            items = [(p, mt) for (p, mt) in items if _match(p)]
        # Additional robust filtering using improved tokenizer (supports Japanese spaces/commas)
        robust_tokens = self._tokenize_search(query)
        if False and robust_tokens:
            def _robust_match(path: str) -> bool:
                name = os.path.basename(path)
                tags = set(t.strip() for t in self._gallery_tags_map.get(name, []) if t)
                for tok in robust_tokens:
                    if self._is_tag_token(tok):
                        key = tok.split(":", 1)[1].strip()
                        if key and key in tags:
                            return True
                    else:
                        if tok.lower() in name.lower():
                            return True
                return False
            items = [(p, mt) for (p, mt) in items_all if _robust_match(p)]

        # Final filtering using effective tokens (ignore empty tag: tokens)
        tokens_eff = []
        try:
            tokens_tmp = self._tokenize_search(query)
        except Exception:
            tokens_tmp = []
        for t in tokens_tmp:
            if self._is_tag_token(t):
                try:
                    key = t.split(":", 1)[1].strip()
                except Exception:
                    key = ""
                if key:
                    tokens_eff.append(f"tag:{key}")
            else:
                if (t or "").strip():
                    tokens_eff.append(t.strip())
        if tokens_eff:
            def _final_match(path: str) -> bool:
                name = os.path.basename(path)
                tags = set(s.strip() for s in self._gallery_tags_map.get(name, []) if s)
                for tok in tokens_eff:
                    if self._is_tag_token(tok):
                        k = tok.split(":", 1)[1].strip()
                        if k and k in tags:
                            return True
                    else:
                        if tok.lower() in name.lower():
                            return True
                return False
            items = [(p, mt) for (p, mt) in items if _final_match(p)]

        # Sort by mtime (desc) using prefetched metadata
        items.sort(key=lambda x: x[1], reverse=True)
        # Default: no upper limit. If GALLERY_MAX is set to a positive integer,
        # limit to that many newest items. "0" or empty means unlimited.
        try:
            _env_max = (os.getenv("GALLERY_MAX", "") or "").strip()
            _limit = int(_env_max) if _env_max else 0
        except Exception:
            _limit = 0
        if _limit > 0:
            items = items[:_limit]
        files = [p for (p, _mt) in items]

        # Layout config (dynamic)
        thumb_w = int(os.getenv("GALLERY_THUMB", "240") or 240)
        pad = 8
        # Determine number of columns from available width
        try:
            cw = self._gallery_scroll.winfo_width()
            if not cw or cw <= 1:
                cw = (self._right_frame.winfo_width() if getattr(self, "_right_frame", None) else self.winfo_width()) - 48
        except Exception:
            cw = 960
        # subtract a small margin for scrollbars/padding to avoid overflow
        try:
            cw = max(1, int(cw) - 24)
        except Exception:
            pass
        try:
            max_cols = int(os.getenv("GALLERY_MAX_COLS", "4") or 4)
        except Exception:
            max_cols = 4
        try:
            cols = max(1, min(max_cols, int(max(thumb_w + 2 * pad, cw) // (thumb_w + 2 * pad))))
        except Exception:
            cols = min(2, max_cols)
        # Reconfigure grid columns on inner container so layout expands
        try:
            _container = getattr(self._gallery_scroll, "_scrollable_frame", None) or getattr(self._gallery_scroll, "scrollable_frame", None) or self._gallery_scroll
            for i in range(cols):
                _container.grid_columnconfigure(i, weight=1)
        except Exception:
            pass

        # Shared placeholder to keep UI responsive while thumbnails load
        placeholder_ctk = None
        placeholder_h = max(80, int(thumb_w * 9 / 16))
        try:
            from PIL import Image as _PILImage
            _ph = _PILImage.new("RGB", (thumb_w, placeholder_h), color=(64, 64, 64))
            # Keep a persistent reference to avoid GC when switching tabs
            self._gallery_placeholder_img = ctk.CTkImage(
                light_image=_ph, dark_image=_ph, size=(thumb_w, placeholder_h)
            )
            placeholder_ctk = self._gallery_placeholder_img
        except Exception:
            pass

        # Thumbnail cache directory
        thumb_dir = os.path.join(koutiku, "_thumbs")
        try:
            os.makedirs(thumb_dir, exist_ok=True)
        except Exception:
            pass

        def _thumb_cache_path(src_path: str, max_w: int) -> str:
            try:
                mt = int(os.path.getmtime(src_path))
            except Exception:
                mt = 0
            try:
                h = hashlib.sha1((src_path + "|" + str(mt) + "|" + str(int(max_w))).encode("utf-8", errors="ignore")).hexdigest()
            except Exception:
                h = os.path.basename(src_path)
            # Default to JPEG for size/speed; PNG if env forces
            ext = ".jpg"
            try:
                if (os.getenv("GALLERY_THUMB_FMT", "jpg") or "jpg").strip().lower() == "png":
                    ext = ".png"
            except Exception:
                pass
            return os.path.join(thumb_dir, f"{h}{ext}")

        def _load_thumb_pil(path: str, max_w: int):
            # Load thumbnail from cache or generate via native DLL or PIL.
            try:
                cache_path = _thumb_cache_path(path, max_w)
                # 1) If cache exists, load and return
                if os.path.exists(cache_path):
                    with Image.open(cache_path) as imc:
                        tw, th = imc.size
                        return (imc.copy(), tw, th)
                # 2) Try native generator if enabled
                use_native_env = (os.getenv("USE_NATIVE_THUMB", "1") or "1").strip().lower()
                use_native = _NATIVE_THUMB and use_native_env not in ("0", "false", "no")
                if use_native:
                    if _gen_thumb_native(path, cache_path, int(max_w)) and os.path.exists(cache_path):
                        with Image.open(cache_path) as imn:
                            tw, th = imn.size
                            return (imn.copy(), tw, th)
                # 3) Fallback: PIL resize from source
                with Image.open(path) as im:
                    w, h = im.size
                    if w <= 0 or h <= 0:
                        return None
                    scale = min(1.0, max_w / float(w))
                    tw = max(1, int(w * scale))
                    th = max(1, int(h * scale))
                    # Faster downscale for thumbnails
                    thumb = im.copy().resize((tw, th), Image.BILINEAR)
                    # Optionally write to cache to speed next time
                    try:
                        cache_on = (os.getenv("CACHE_THUMBS", "1") or "1").strip().lower() not in ("0", "false", "no")
                    except Exception:
                        cache_on = True
                    if cache_on:
                        try:
                            fmt = ("JPEG" if cache_path.lower().endswith(".jpg") or cache_path.lower().endswith(".jpeg") else "PNG")
                            thumb.save(cache_path, format=fmt, quality=85 if fmt == "JPEG" else None)
                        except Exception:
                            pass
                    return (thumb, tw, th)
            except Exception:
                return None

        def _apply_thumb(btn: ctk.CTkButton, fname: str, path: str, token: int, result):
            # Runs on Tk thread
            try:
                if not btn.winfo_exists():
                    return
                if token != self._gallery_load_token:
                    return  # a newer reload happened
                if not result:
                    return
                img_pil, tw, th = result
                tk_img = ctk.CTkImage(light_image=img_pil, dark_image=img_pil, size=(tw, th))
                self._thumb_refs.append(tk_img)
                # Ensure image is never cropped by the button height
                btn.configure(image=tk_img, text="", width=tw, height=th + 2)
                try:
                    # Thumbnails can change the layout height; throttle scrollregion refresh
                    self._request_gallery_scrollregion_refresh(60)
                except Exception:
                    pass
            except Exception:
                pass

        # Chunked rendering of grid
        current_token = self._gallery_load_token
        _container = getattr(self._gallery_scroll, "_scrollable_frame", None) or getattr(self._gallery_scroll, "scrollable_frame", None) or self._gallery_scroll
        self._gallery_load_files = list(files)
        self._gallery_load_cols = max(1, int(cols))

        try:
            from math import ceil as _ceil
            self._gallery_cols_count = self._gallery_load_cols
            self._gallery_rows_count = max(1, int(_ceil(len(self._gallery_load_files) / float(self._gallery_load_cols))))
        except Exception:
            self._gallery_cols_count = self._gallery_load_cols
            self._gallery_rows_count = 1

        try:
            chunk_size = int(os.getenv("GALLERY_CHUNK", "20") or 20)
        except Exception:
            chunk_size = 20
        try:
            chunk_delay = int(os.getenv("GALLERY_CHUNK_DELAY_MS", "0") or 0)
        except Exception:
            chunk_delay = 0

        try:
            if self._gallery_chunk_after_id is not None:
                self.after_cancel(self._gallery_chunk_after_id)
        except Exception:
            pass
        self._gallery_chunk_after_id = None

        def _create_cell(idx: int, path: str):
            try:
                r = idx // self._gallery_load_cols
                c = idx % self._gallery_load_cols
                cell = ctk.CTkFrame(_container, fg_color="transparent")
                cell.grid(row=r, column=c, padx=pad, pady=pad, sticky="n")
                try:
                    cell.grid_columnconfigure(0, weight=1)
                except Exception:
                    pass
                fname = os.path.basename(path)
                handler = lambda e, p=path: self._open_gallery_context_menu(e, p)
                btn = ctk.CTkButton(
                    cell,
                    image=placeholder_ctk,
                    text="",
                    width=thumb_w,
                    height=placeholder_h + 2,
                    command=lambda p=path: self._open_image_viewer(p),
                )
                btn.grid(row=0, column=0, sticky="n")
                try:
                    btn.bind("<Button-3>", handler)
                    cell.bind("<Button-3>", handler)
                except Exception:
                    pass
                try:
                    _name = fname
                    if len(_name) > 52:
                        _name = _name[:23] + "..." + _name[-24:]
                    name_lbl = ctk.CTkLabel(cell, text=_name, anchor="center")
                    name_lbl.grid(row=1, column=0, sticky="n", pady=(4, 0))
                    try:
                        name_lbl.bind("<Button-3>", handler)
                    except Exception:
                        pass
                except Exception:
                    pass
                try:
                    vbtn = ctk.CTkButton(cell, text="動画を見る", width=120)
                    vbtn.grid(row=2, column=0, sticky="n", pady=(6, 0))
                    try:
                        vpath = self._gallery_pairs_map.get(fname)
                    except Exception:
                        vpath = None
                    if vpath and os.path.exists(vpath):
                        vbtn.configure(state="normal", command=lambda vp=vpath: self._open_video(vp))
                    else:
                        vbtn.configure(state="disabled")
                except Exception:
                    pass
                try:
                    tags = self._gallery_tags_map.get(fname, [])
                    txt = ", ".join(tags)
                    if txt:
                        tag_lbl = ctk.CTkLabel(cell, text=txt, anchor="center")
                        tag_lbl.grid(row=3, column=0, sticky="n", pady=(4, 0))
                        try:
                            tag_lbl.bind("<Button-3>", handler)
                        except Exception:
                            pass
                except Exception:
                    pass
                future = self._thumb_executor.submit(_load_thumb_pil, path, thumb_w)
                def _on_done(fut, b=btn, fn=fname, p=path, tok=current_token):
                    try:
                        res = fut.result()
                    except Exception:
                        res = None
                    try:
                        self.after(0, _apply_thumb, b, fn, p, tok, res)
                    except Exception:
                        pass
                future.add_done_callback(_on_done)
            except Exception:
                pass

        idx_box = {"i": 0}
        total = len(self._gallery_load_files)

        def _render_next_chunk():
            if current_token != self._gallery_load_token:
                return
            i = idx_box["i"]
            if i >= total:
                try:
                    self._tune_gallery_scrollbar()
                except Exception:
                    pass
                self._request_gallery_scrollregion_refresh(40)
                self._gallery_chunk_after_id = None
                return
            end = min(total, i + max(1, int(chunk_size)))
            for j in range(i, end):
                _create_cell(j, self._gallery_load_files[j])
            idx_box["i"] = end
            self._request_gallery_scrollregion_refresh(20)
            try:
                self._gallery_chunk_after_id = self.after(max(0, int(chunk_delay)), _render_next_chunk)
            except Exception:
                self._gallery_chunk_after_id = None

        _render_next_chunk()

    def _open_image_viewer(self, path: str) -> None:
        # Optional: use native Direct2D viewer (fast scaled drawing)
        try:
            use_native = (os.getenv("USE_NATIVE_VIEWER", "0") or "0").strip().lower() not in ("0", "false", "no")
        except Exception:
            use_native = False
        if use_native:
            try:
                import sys
                base = None
                try:
                    mp = getattr(sys, "_MEIPASS", None)
                    if mp:
                        base = Path(mp)
                except Exception:
                    base = None
                if base is None:
                    try:
                        if getattr(sys, "frozen", False):
                            base = Path(sys.executable).resolve().parent
                    except Exception:
                        base = None
                if base is None:
                    base = Path(__file__).resolve().parents[2]
                exe = base / "native" / "build" / "image_viewer_d2d.exe"
                exe_alt = base / "native" / "image_viewer_d2d.exe"
                pick = exe if exe.exists() else (exe_alt if exe_alt.exists() else None)
                if pick is not None and pick.exists():
                    subprocess.Popen([str(pick), path])
                    return
            except Exception:
                pass
        try:
            img = Image.open(path)
        except Exception as e:
            mb.showerror("画像を開けません", f"画像の読み込みに失敗しました\n{e}")
            return

        top = ctk.CTkToplevel(self)
        top.title(os.path.basename(path))
        # Ensure viewer appears in front of the main window initially
        try:
            top.transient(self)  # associate with parent for stacking
            top.lift()           # raise above other windows
            top.attributes("-topmost", True)  # force front once
            top.focus_force()    # move keyboard focus to the viewer
            # Drop topmost shortly after so normal stacking resumes
            top.after(200, lambda: top.attributes("-topmost", False))
        except Exception:
            pass
        # Limit size to a reasonable maximum and screen size
        try:
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
        except Exception:
            sw, sh = 1600, 900
        # Show the image slightly larger than before
        max_w = min(1400, int(sw * 0.96))
        max_h = min(1000, int(sh * 0.96))

        w, h = img.size
        scale = min(max_w / float(w), max_h / float(h), 1.0)
        vw = int(w * scale)
        vh = int(h * scale)
        # Interactive editor setup (Canvas based)
        # Keep current image in PIL, display via Tk PhotoImage on a Canvas
        from PIL import ImageTk, ImageDraw, ImageFont  # local import to avoid global dependency noise
        current_pil = img.copy()
        display_scale = scale

        def _compute_view_size(pil_img):
            w0, h0 = pil_img.size
            sc = min(max_w / float(w0), max_h / float(h0), 1.0)
            return int(w0 * sc), int(h0 * sc), sc

        vw, vh, display_scale = _compute_view_size(current_pil)

        def _render_to_canvas():
            nonlocal vw, vh, display_scale
            vw, vh, display_scale = _compute_view_size(current_pil)
            disp = current_pil.copy().resize((vw, vh), Image.LANCZOS)
            imgtk = ImageTk.PhotoImage(disp)
            try:
                canvas.config(width=vw, height=vh)
            except Exception:
                pass
            if hasattr(canvas, "_img_item_id"):
                canvas.itemconfigure(canvas._img_item_id, image=imgtk)  # type: ignore[attr-defined]
            else:
                canvas._img_item_id = canvas.create_image(0, 0, anchor="nw", image=imgtk)  # type: ignore[attr-defined]
            # keep a reference to avoid GC
            top._canvas_imgtk_ref = imgtk  # type: ignore[attr-defined]

        frame = ctk.CTkFrame(top)
        frame.grid(row=0, column=0, padx=12, pady=12, sticky="nsew")
        try:
            frame.grid_columnconfigure(0, weight=0)
            frame.grid_columnconfigure(1, weight=1)
            frame.grid_columnconfigure(2, weight=0)
            # Let the canvas row expand
            frame.grid_rowconfigure(1, weight=1)
        except Exception:
            pass

        # Header: filename on the left, quick actions on the right
        try:
            self._load_gallery_pairs()
        except Exception:
            pass
        name = os.path.basename(path)
        vpath = None
        try:
            vpath = self._gallery_pairs_map.get(name)
        except Exception:
            vpath = None
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=3, sticky="we")
        try:
            header.grid_columnconfigure(0, weight=1)
        except Exception:
            pass
        title_lbl = ctk.CTkLabel(header, text=f"{name}  ({w}x{h})", anchor="w")
        title_lbl.grid(row=0, column=0, sticky="w")
        hdr_btns = ctk.CTkFrame(header, fg_color="transparent")
        hdr_btns.grid(row=0, column=1, sticky="e")
        try:
            col = 0
            vbtn_hdr = ctk.CTkButton(hdr_btns, text="動画を見る", width=96)
            vbtn_hdr.grid(row=0, column=col, padx=(0, 6))
            if vpath:
                vbtn_hdr.configure(state="normal", command=lambda vp=vpath: self._open_video(vp))
            else:
                vbtn_hdr.configure(state="disabled")
            col += 1
            ctk.CTkButton(hdr_btns, text="エクスプローラ", width=96, command=lambda p=path: self._gallery_open_in_explorer(p)).grid(row=0, column=col, padx=(0, 6)); col += 1
            ctk.CTkButton(hdr_btns, text="パスをコピー", width=96, command=lambda p=path: self._gallery_copy_path(p)).grid(row=0, column=col, padx=(0, 6)); col += 1
            # Save… button defined later; resolved at click time
            ctk.CTkButton(hdr_btns, text="保存…", width=80, command=lambda: _save_as_only()).grid(row=0, column=col, padx=(0, 6)); col += 1
            ctk.CTkButton(hdr_btns, text="閉じる", width=70, command=top.destroy).grid(row=0, column=col)
        except Exception:
            pass

        # Canvas to display and edit
        canvas = tk.Canvas(frame, width=vw, height=vh, highlightthickness=1, highlightbackground="#3b3b3b", bd=0, bg="#111111")
        canvas.grid(row=1, column=0, columnspan=3, sticky="n")
        _render_to_canvas()

        # Editing state
        mode = {"value": "view"}  # one of: view, crop, arrow, text_place
        temp_shape_id = {"id": None}
        start_xy = {"x": 0, "y": 0}
        pending_text = {"text": None}

        def _to_orig(x: int, y: int):
            # Canvas coordinates to original image coordinates
            return int(x / max(display_scale, 1e-6)), int(y / max(display_scale, 1e-6))

        def _on_down(event):
            if mode["value"] not in ("crop", "arrow", "text_place"):
                return
            start_xy["x"], start_xy["y"] = event.x, event.y
            if mode["value"] == "crop":
                if temp_shape_id["id"] is not None:
                    try:
                        canvas.delete(temp_shape_id["id"])
                    except Exception:
                        pass
                temp_shape_id["id"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="yellow", width=2)
            elif mode["value"] == "arrow":
                if temp_shape_id["id"] is not None:
                    try:
                        canvas.delete(temp_shape_id["id"])
                    except Exception:
                        pass
                temp_shape_id["id"] = canvas.create_line(event.x, event.y, event.x, event.y, fill="red", width=3, arrow=tk.LAST)
            elif mode["value"] == "text_place":
                # Place text immediately on click
                txt = pending_text["text"] or ""
                if not txt:
                    return
                ox, oy = _to_orig(event.x, event.y)
                draw = ImageDraw.Draw(current_pil)
                # Try truetype font, fallback to default
                font = None
                try:
                    size = max(12, int(22 / max(display_scale, 1e-6)))
                    font = ImageFont.truetype("arial.ttf", size)
                except Exception:
                    try:
                        font = ImageFont.load_default()
                    except Exception:
                        font = None
                try:
                    draw.text((ox, oy), txt, fill=(255, 0, 0), font=font)
                except Exception:
                    # minimal fallback without font
                    try:
                        draw.text((ox, oy), txt, fill=(255, 0, 0))
                    except Exception:
                        pass
                _render_to_canvas()
                # Back to view mode after placing one text
                mode["value"] = "view"

        def _on_move(event):
            if mode["value"] == "crop" and temp_shape_id["id"] is not None:
                try:
                    canvas.coords(temp_shape_id["id"], start_xy["x"], start_xy["y"], event.x, event.y)
                except Exception:
                    pass
            elif mode["value"] == "arrow" and temp_shape_id["id"] is not None:
                try:
                    canvas.coords(temp_shape_id["id"], start_xy["x"], start_xy["y"], event.x, event.y)
                except Exception:
                    pass

        def _on_up(event):
            if mode["value"] == "crop" and temp_shape_id["id"] is not None:
                x0, y0 = start_xy["x"], start_xy["y"]
                x1, y1 = event.x, event.y
                # normalize
                xa, xb = sorted([x0, x1])
                ya, yb = sorted([y0, y1])
                ox1, oy1 = _to_orig(xa, ya)
                ox2, oy2 = _to_orig(xb, yb)
                # clamp
                w0, h0 = current_pil.size
                ox1 = max(0, min(w0 - 1, ox1))
                oy1 = max(0, min(h0 - 1, oy1))
                ox2 = max(ox1 + 1, min(w0, ox2))
                oy2 = max(oy1 + 1, min(h0, oy2))
                try:
                    current_pil = current_pil.crop((ox1, oy1, ox2, oy2))
                except Exception:
                    pass
                # cleanup overlay and re-render
                try:
                    if temp_shape_id["id"] is not None:
                        canvas.delete(temp_shape_id["id"])
                except Exception:
                    pass
                temp_shape_id["id"] = None
                _render_to_canvas()
                mode["value"] = "view"
            elif mode["value"] == "arrow" and temp_shape_id["id"] is not None:
                x0, y0 = start_xy["x"], start_xy["y"]
                x1, y1 = event.x, event.y
                ox0, oy0 = _to_orig(x0, y0)
                ox1, oy1 = _to_orig(x1, y1)
                try:
                    draw = ImageDraw.Draw(current_pil)
                    width = max(1, int(4 / max(display_scale, 1e-6)))
                    draw.line((ox0, oy0, ox1, oy1), fill=(255, 0, 0), width=width)
                    # simple arrow head
                    import math
                    ang = math.atan2(oy1 - oy0, ox1 - ox0)
                    head_len = max(6, int(14 / max(display_scale, 1e-6)))
                    head_ang = math.pi / 6.0
                    xh1 = ox1 - head_len * math.cos(ang - head_ang)
                    yh1 = oy1 - head_len * math.sin(ang - head_ang)
                    xh2 = ox1 - head_len * math.cos(ang + head_ang)
                    yh2 = oy1 - head_len * math.sin(ang + head_ang)
                    draw.polygon([(ox1, oy1), (xh1, yh1), (xh2, yh2)], fill=(255, 0, 0))
                except Exception:
                    pass
                try:
                    if temp_shape_id["id"] is not None:
                        canvas.delete(temp_shape_id["id"])
                except Exception:
                    pass
                temp_shape_id["id"] = None
                _render_to_canvas()
                mode["value"] = "view"

        try:
            canvas.bind("<Button-1>", _on_down)
            canvas.bind("<B1-Motion>", _on_move)
            canvas.bind("<ButtonRelease-1>", _on_up)
        except Exception:
            pass

        # Editor toolbar
        toolbar = ctk.CTkFrame(frame, fg_color="transparent")
        toolbar.grid(row=2, column=0, columnspan=3, sticky="we", pady=(8, 0))
        def _set_mode_crop():
            mode["value"] = "crop"
        def _set_mode_arrow():
            mode["value"] = "arrow"
        def _set_mode_text():
            t = sd.askstring("テキスト", "テキストを入力:")
            if not t:
                return
            pending_text["text"] = t
            mode["value"] = "text_place"
        def _save_as():
            fmt_map = {
                "PNG": (".png", "PNG"),
                "JPG": (".jpg", "JPEG"),
                "WEBP": (".webp", "WEBP"),
            }
            cur_fmt = (self.format_opt.get().strip().upper() if getattr(self, "format_opt", None) else "PNG")
            def_ext, pil_fmt = fmt_map.get(cur_fmt, (".png", "PNG"))
            out = fd.asksaveasfilename(defaultextension=def_ext, filetypes=[("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")], title="画像を保存")
            if not out:
                return
            try:
                current_pil.save(out, format=pil_fmt)
            except Exception as e:
                mb.showerror("保存", f"保存に失敗しました\n{e}")
        def _save_to_broadcast():
            path = self._broadcast_image_path()
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
            except Exception:
                pass
            cur_fmt = (self.format_opt.get().strip().upper() if getattr(self, "format_opt", None) else "PNG")
            pil_fmt = {"PNG": "PNG", "JPG": "JPEG", "WEBP": "WEBP"}.get(cur_fmt, "PNG")
            try:
                current_pil.save(path, format=pil_fmt)
                try:
                    self._append_log(f"[編集] 配信用に保存: {path}")
                except Exception:
                    pass
                mb.showinfo("保存", f"配信用に保存しました:\n{path}")
            except Exception as e:
                mb.showerror("保存", f"保存に失敗しました\n{e}")

        ctk.CTkButton(toolbar, text="トリミング", width=90, command=_set_mode_crop).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(toolbar, text="矢印", width=70, command=_set_mode_arrow).grid(row=0, column=1, padx=(0, 6))
        ctk.CTkButton(toolbar, text="テキスト", width=80, command=_set_mode_text).grid(row=0, column=2, padx=(0, 12))
        ctk.CTkButton(toolbar, text="保存…", width=90, command=_save_as).grid(row=0, column=3, padx=(0, 6))
        ctk.CTkButton(toolbar, text="配信用に保存", width=120, command=_save_to_broadcast).grid(row=0, column=4, padx=(0, 6))

        # Remove all toolbar buttons and keep only Save…
        try:
            for _w in list(toolbar.winfo_children()):
                _w.destroy()
        except Exception:
            pass
        def _save_as_only():
            fmt_map = {"PNG": (".png", "PNG"), "JPG": (".jpg", "JPEG"), "WEBP": (".webp", "WEBP")}
            cur_fmt = (self.format_opt.get().strip().upper() if getattr(self, "format_opt", None) else "PNG")
            def_ext, pil_fmt = fmt_map.get(cur_fmt, (".png", "PNG"))
            out = fd.asksaveasfilename(defaultextension=def_ext, filetypes=[("PNG", ".png"), ("JPEG", ".jpg"), ("WEBP", ".webp")], title="画像を保存")
            if out:
                try:
                    current_pil.save(out, format=pil_fmt)
                except Exception as e:
                    mb.showerror("保存", f"保存に失敗しました\n{e}")
        ctk.CTkButton(toolbar, text="保存…", width=100, command=_save_as_only).grid(row=0, column=0, padx=(0, 6))

        # Live Tag Editor (auto-saves as you type)
        try:
            name = os.path.basename(path)
            # Ensure tags are loaded
            self._load_gallery_tags()
            cur_tags = self._gallery_tags_map.get(name, [])

            ctk.CTkLabel(frame, text="Tags").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=(10, 0))
            tag_var = tk.StringVar(value=", ".join(cur_tags))
            tag_entry = ctk.CTkEntry(frame, textvariable=tag_var)
            tag_entry.grid(row=3, column=1, sticky="we", pady=(10, 0))

            def _on_tags_typing(event=None, fname=name):
                # Debounce saves per file
                try:
                    prev = self._tag_edit_after_ids.get(fname)
                    if prev is not None:
                        try:
                            self.after_cancel(prev)
                        except Exception:
                            pass
                except Exception:
                    pass

                def _commit():
                    text = tag_var.get()
                    tags = self._parse_tags_fixed(text)
                    try:
                        if tags:
                            self._gallery_tags_map[fname] = tags
                        else:
                            self._gallery_tags_map.pop(fname, None)
                        self._save_gallery_tags()
                        # Refresh gallery so searches like tag:xxx reflect immediately
                        self._reload_gallery()
                    except Exception:
                        pass

                try:
                    self._tag_edit_after_ids[fname] = self.after(300, _commit)
                except Exception:
                    _commit()

            # Suggestions area under the entry
            sugg_frame = ctk.CTkFrame(frame, fg_color="transparent")
            sugg_frame.grid(row=4, column=0, columnspan=2, sticky="we")

            def _update_suggestions():
                # Clear previous
                try:
                    for w in list(sugg_frame.winfo_children()):
                        w.destroy()
                except Exception:
                    pass
                text = tag_var.get()
                # Determine existing tokens and current partial
                try:
                    sep = r"[\s,、，]+"
                    tokens = [t for t in re.split(sep, text) if t]
                    trailing_sep = re.search(sep + r"$", text) is not None
                    partial = "" if trailing_sep else (tokens[-1] if tokens else "")
                    existing = set(t.lower() for t in tokens if t)
                except Exception:
                    partial = ""
                    existing = set()
                pool = [t for t in self._all_existing_tags() if t.lower() not in existing]
                if partial:
                    pool = [t for t in pool if t.lower().startswith(partial.lower())]
                pool = pool[:8]
                if not pool:
                    return
                # Render suggestion buttons
                for i, t in enumerate(pool):
                    b = ctk.CTkButton(
                        sugg_frame,
                        text=t,
                        width=1,
                        height=24,
                        command=lambda tag=t: _apply_suggestion(tag),
                    )
                    b.grid(row=0, column=i, padx=(0, 6), pady=(6, 0))

            def _apply_suggestion(tag: str):
                # Merge suggested tag into the field
                try:
                    sep = r"[\s,、，]+"
                    text = tag_var.get()
                    tokens = [t for t in re.split(sep, text) if t]
                    trailing_sep = re.search(sep + r"$", text) is not None
                    if not trailing_sep and tokens:
                        tokens = tokens[:-1]  # drop partial
                    if tag not in tokens:
                        tokens.append(tag)
                    tag_var.set(", ".join(tokens))
                    _on_tags_typing()
                    _update_suggestions()
                except Exception:
                    pass

            def _on_key(event=None):
                _on_tags_typing(event)
                _update_suggestions()

            tag_entry.bind("<KeyRelease>", _on_key)
            # Initial suggestions
            _update_suggestions()
        except Exception:
            pass

        # Keep a reference on the toplevel to avoid GC and set size
        top.geometry(f"{vw+64}x{vh+200}")

        # Close on ESC
        try:
            top.bind("<Escape>", lambda e: top.destroy())
        except Exception:
            pass

    def _toggle_auto_refresh(self) -> None:
        if self._auto_refresh_var.get():
            self._schedule_gallery_refresh()
        else:
            if self._gallery_after_id is not None:
                try:
                    self.after_cancel(self._gallery_after_id)
                except Exception:
                    pass
                self._gallery_after_id = None

    def _schedule_gallery_refresh(self) -> None:
        if not self._auto_refresh_var.get():
            return
        try:
            self._reload_gallery()
        except Exception:
            pass
        # Schedule next refresh
        try:
            # every 20 minutes (20 * 60 * 1000 ms)
            self._gallery_after_id = self.after(1200000, self._schedule_gallery_refresh)
        except Exception:
            self._gallery_after_id = None

    # --- Gallery context menu ---
    def _open_gallery_context_menu(self, event, path: str) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="エクスプローラで開く", command=lambda p=path: self._gallery_open_in_explorer(p))
        menu.add_command(label="パスをコピー", command=lambda p=path: self._gallery_copy_path(p))
        menu.add_separator()
        # Video actions if paired
        try:
            name = os.path.basename(path)
            vpath = self._gallery_pairs_map.get(name)
            if vpath and os.path.exists(vpath):
                menu.add_command(label="動画を開く", command=lambda vp=vpath: self._open_video(vp))
                menu.add_command(label="動画パスをコピー", command=lambda vp=vpath: self._gallery_copy_path(vp))
                menu.add_separator()
        except Exception:
            pass
        menu.add_command(label="タグを追加…", command=lambda p=path: self._gallery_add_tag(p))
        menu.add_command(label="タグを削除…", command=lambda p=path: self._gallery_remove_tag(p))
        menu.add_command(label="タグを表示", command=lambda p=path: self._gallery_show_tags(p))
        menu.add_separator()
        menu.add_command(label="削除", command=lambda p=path: self._gallery_delete_file(p))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _gallery_open_in_explorer(self, path: str) -> None:
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])  # type: ignore[arg-type]
            else:
                folder = os.path.dirname(path)
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
        except Exception as e:
            mb.showerror("エラー", f"エクスプローラを開けませんでした\n{e}")

    def _gallery_copy_path(self, path: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(path)
        except Exception:
            pass

    def _gallery_delete_file(self, path: str) -> None:
        if not mb.askyesno("削除の確認", f"次のファイルを削除しますか？\n{os.path.basename(path)}"):
            return
        try:
            os.remove(path)
        except Exception as e:
            mb.showerror("削除エラー", f"削除に失敗しました\n{e}")
            return
        self._reload_gallery()

    def _tags_json_path(self) -> str:
        return os.path.join(self._current_koutiku_path(), "_tags.json")

    def _pairs_json_path(self) -> str:
        return os.path.join(self._current_koutiku_path(), "_pairs.json")

    def _load_gallery_tags(self) -> None:
        try:
            with open(self._tags_json_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self._gallery_tags_map = {k: list(v) for k, v in data.items() if isinstance(v, list)}
        except Exception:
            self._gallery_tags_map = {}

    def _load_gallery_pairs(self) -> None:
        try:
            with open(self._pairs_json_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # keep only str->str
                    self._gallery_pairs_map = {str(k): str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
                else:
                    self._gallery_pairs_map = {}
        except Exception:
            self._gallery_pairs_map = {}

    def _open_video(self, path: str) -> None:
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            mb.showerror("エラー", f"動画を開けませんでした\n{e}")

    def _save_gallery_tags(self) -> None:
        try:
            with open(self._tags_json_path(), "w", encoding="utf-8") as f:
                json.dump(self._gallery_tags_map, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._append_log(f"[ギャラリー] タグ保存に失敗: {e}")

    def _parse_tags(self, text: str) -> List[str]:
        try:
            parts = re.split(r"[\s,、，]+", (text or "").strip())
        except Exception:
            parts = [(text or "").strip()]
        result: List[str] = []
        seen = set()
        for p in parts:
            t = p.strip()
            if not t:
                continue
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    def _all_existing_tags(self) -> List[str]:
        try:
            all_tags = set()
            for v in self._gallery_tags_map.values():
                try:
                    for t in v:
                        if t:
                            all_tags.add(str(t))
                except Exception:
                    continue
            return sorted(all_tags, key=lambda s: s.lower())
        except Exception:
            return []

    def _gallery_add_tag(self, path: str) -> None:
        name = os.path.basename(path)
        tag = sd.askstring("タグを追加", "タグ名を入力:")
        if not tag:
            return
        self._gallery_tags_map.setdefault(name, [])
        if tag not in self._gallery_tags_map[name]:
            self._gallery_tags_map[name].append(tag)
            self._save_gallery_tags()
            self._reload_gallery()

    def _gallery_remove_tag(self, path: str) -> None:
        name = os.path.basename(path)
        tags = self._gallery_tags_map.get(name, [])
        if not tags:
            mb.showinfo("タグ", "この画像にはタグがありません。")
            return
        tag = sd.askstring("タグを削除", f"削除するタグ名を入力\n既存: {', '.join(tags)}")
        if not tag:
            return
        try:
            self._gallery_tags_map[name] = [t for t in tags if t != tag]
            if not self._gallery_tags_map[name]:
                self._gallery_tags_map.pop(name, None)
            self._save_gallery_tags()
            self._reload_gallery()
        except Exception:
            pass

    def _gallery_show_tags(self, path: str) -> None:
        name = os.path.basename(path)
        tags = self._gallery_tags_map.get(name, [])
        mb.showinfo("タグ", ", ".join(tags) if tags else "タグはありません。")

    # --- OBS helpers ---
    def _refresh_obs_lists(self) -> None:
        host = self.host_entry.get().strip()
        try:
            port = int(self.port_entry.get())
        except Exception:
            mb.showerror("入力エラー", "Port は数値で入力してください")
            return
        password = self.pass_entry.get()

        created_temp = False
        client = self._obs
        try:
            if client is None:
                client = ObsClient(host, port, password, self._lock)
                client.connect()
                created_temp = True
            scenes = []
            sources = []
            try:
                scenes = client.list_scenes()
            except Exception:
                scenes = []
            try:
                sources = client.list_sources()
            except Exception:
                sources = []

            # Update UI lists
            if not scenes:
                scenes = [self.scene_opt.get() or os.getenv("OBS_SCENE", "")]
            if not sources:
                sources = [self.source_opt.get() or os.getenv("OBS_SOURCE", "Capture1")]

            self.scene_opt.configure(values=scenes)
            # keep selection if possible
            cur_scene = self.scene_opt.get()
            self.scene_opt.set(cur_scene if cur_scene in scenes else (scenes[0] if scenes else ""))

            self.source_opt.configure(values=sources)
            cur_source = self.source_opt.get()
            self.source_opt.set(cur_source if cur_source in sources else (sources[0] if sources else "Capture1"))

            self._append_log("[アプリ] シーン/ソース一覧を更新しました")
        except Exception as e:
            mb.showerror("取得エラー", f"シーン/ソースの取得に失敗しました\n{e}")
        finally:
            if created_temp and client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass

def main() -> None:
    app = App()
    app.mainloop()

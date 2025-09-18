from __future__ import annotations

import os
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

        self._build_ui()
        # Graceful shutdown on window close
        try:
            self.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

    # --- UI ---
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        # Keep the title row compact; let content row expand
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        title = ctk.CTkLabel(self, text=f"OBS Screenshot / Template Tool v{APP_VERSION}", font=ctk.CTkFont(size=18, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(12, 0))

        sidebar = ctk.CTkFrame(self, corner_radius=10)
        sidebar.grid(row=1, column=0, sticky="nsw", padx=(16, 8), pady=12)
        sidebar.grid_rowconfigure(99, weight=1)

        # OBS connection
        obs_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        obs_frame.grid(row=0, column=0, sticky="we", padx=8, pady=(8, 6))
        ctk.CTkLabel(obs_frame, text="OBS Connection", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 4)
        )

        ctk.CTkLabel(obs_frame, text="Host").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        self.host_entry = ctk.CTkEntry(obs_frame, width=160)
        self.host_entry.insert(0, os.getenv("OBS_HOST", "localhost"))
        self.host_entry.grid(row=1, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Port").grid(row=2, column=0, sticky="e", padx=8, pady=4)
        self.port_entry = ctk.CTkEntry(obs_frame, width=120)
        self.port_entry.insert(0, os.getenv("OBS_PORT", "4444"))
        self.port_entry.grid(row=2, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Password").grid(row=3, column=0, sticky="e", padx=8, pady=4)
        self.pass_entry = ctk.CTkEntry(obs_frame, width=160, show="*")
        self.pass_entry.insert(0, os.getenv("OBS_PASSWORD", ""))
        self.pass_entry.grid(row=3, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Base Directory").grid(row=4, column=0, sticky="e", padx=8, pady=4)
        self.base_dir_entry = ctk.CTkEntry(obs_frame, width=260)
        # Resolve BASE_DIR relative to the app/.env location so it stays relocatable
        self.base_dir_entry.insert(0, self._resolve_base_dir_default())
        self.base_dir_entry.grid(row=4, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkButton(obs_frame, text="Browse", command=self._browse_base_dir).grid(row=4, column=2, padx=8, pady=4)

        # Scene/Source selection
        ctk.CTkLabel(obs_frame, text="Scene").grid(row=5, column=0, sticky="e", padx=8, pady=4)
        default_scene = os.getenv("OBS_SCENE", "")
        self.scene_opt = ctk.CTkOptionMenu(obs_frame, values=[default_scene] if default_scene else [""], width=200)
        self.scene_opt.set(default_scene)
        self.scene_opt.grid(row=5, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Source").grid(row=6, column=0, sticky="e", padx=8, pady=4)
        default_source = os.getenv("OBS_SOURCE", "Capture1")
        self.source_opt = ctk.CTkOptionMenu(obs_frame, values=[default_source], width=200)
        self.source_opt.set(default_source)
        self.source_opt.grid(row=6, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkButton(obs_frame, text="更新", command=self._refresh_obs_lists, width=80).grid(row=6, column=2, padx=8, pady=4)

        # Season (for per-season stats)
        ctk.CTkLabel(obs_frame, text="Season").grid(row=7, column=0, sticky="e", padx=8, pady=4)
        self.season_entry = ctk.CTkEntry(obs_frame, width=160)
        self.season_entry.insert(0, os.getenv("SEASON", ""))
        self.season_entry.grid(row=7, column=1, sticky="w", padx=8, pady=4)

        # Scripts
        script_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        script_frame.grid(row=1, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(script_frame, text="Scripts", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(8, 4))
        ctk.CTkCheckBox(script_frame, text="構築のスクリーンショット", variable=self.chk_double_var).pack(anchor="w", padx=8, pady=2)
        ctk.CTkCheckBox(script_frame, text="自動録画開始・停止", variable=self.chk_rkaisi_var).pack(anchor="w", padx=8, pady=2)
        ctk.CTkCheckBox(script_frame, text="戦績を自動更新", variable=self.chk_syouhai_var).pack(anchor="w", padx=8, pady=2)

        # Discord webhook controls
        discord_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        discord_frame.grid(row=2, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(discord_frame, text="Discord", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4)
        )
        ctk.CTkCheckBox(discord_frame, text="構築をDiscordへ送信", variable=self.chk_discord_var).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=8, pady=2
        )
        ctk.CTkLabel(discord_frame, text="Webhook URL").grid(row=2, column=0, sticky="e", padx=8, pady=(4, 8))
        self.discord_url_entry = ctk.CTkEntry(discord_frame, width=260, textvariable=self.discord_url_var)
        self.discord_url_entry.grid(row=2, column=1, sticky="we", padx=(0, 8), pady=(4, 8))

        # Controls
        control_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        control_frame.grid(row=3, column=0, sticky="we", padx=8, pady=6)
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
        theme_frame.grid(row=4, column=0, sticky="we", padx=8, pady=(6, 12))
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

        tabs = ctk.CTkTabview(right)
        tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        tab_log = tabs.add("Log")
        tab_gallery = tabs.add("Gallery")
        tab_stats = tabs.add("Stats")

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
        if self.chk_rkaisi_var.get():
            handantmp = os.path.join(base_dir, "handantmp")
            os.makedirs(handantmp, exist_ok=True)
            self._th_rkaisi = RkaisiTeisiThread(self._obs, handantmp, logger, source_name=src)
            self._th_rkaisi.start()
        # Result association queue shared between Syouhai and association thread
        self._results_queue = queue.Queue()
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
        for th in (self._th_double, self._th_rkaisi, self._th_syouhai, self._th_discord, self._th_result_assoc):
            try:
                if th and th.is_alive():
                    th.stop()  # type: ignore[attr-defined]
            except Exception:
                pass

        # Optional join to let threads exit promptly
        for th in (self._th_double, self._th_rkaisi, self._th_syouhai, self._th_discord, self._th_result_assoc):
            try:
                if th:
                    th.join(timeout=1.0)
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

    # --- Stats UI ---
    def _build_stats_ui(self, parent: ctk.CTkFrame) -> None:
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        ctrl = ctk.CTkFrame(parent)
        ctrl.grid(row=0, column=0, sticky="we", padx=8, pady=8)
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
        self._stats_summary.grid(row=1, column=0, sticky="we", padx=12)

        self._stats_chart_label = ctk.CTkLabel(parent, text="")
        self._stats_chart_label.grid(row=2, column=0, sticky="nsew", padx=12, pady=12)
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

        img = stats_utils.render_winrate_chart(per_day, size=(900, 320))
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
        path = os.path.join(base_dir, "koutiku", "_results.csv")
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
        except Exception as e:
            mb.showerror("保存エラー", f"設定の保存に失敗しました: {dotenv_path}\n{e}")
            return

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
        for i in range(4):
            self._gallery_scroll.grid_columnconfigure(i, weight=1)

    def _current_koutiku_path(self) -> str:
        base_dir = self.base_dir_entry.get().strip() if getattr(self, "base_dir_entry", None) else self._resolve_base_dir_default()
        return os.path.join(base_dir, "koutiku")

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

        # Clear previous thumbnails
        try:
            for child in self._gallery_scroll.winfo_children():
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

        # Load tags and filter by search query
        self._load_gallery_tags()
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
        max_items = int(os.getenv("GALLERY_MAX", "100") or 100)
        items = items[:max_items]
        files = [p for (p, _mt) in items]

        # Layout config
        cols = 4
        thumb_w = int(os.getenv("GALLERY_THUMB", "240") or 240)
        pad = 8

        # Shared placeholder to keep UI responsive while thumbnails load
        placeholder_ctk = None
        placeholder_h = max(80, int(thumb_w * 9 / 16))
        try:
            from PIL import Image as _PILImage
            _ph = _PILImage.new("RGB", (thumb_w, placeholder_h), color=(64, 64, 64))
            placeholder_ctk = ctk.CTkImage(light_image=_ph, dark_image=_ph, size=(thumb_w, placeholder_h))
        except Exception:
            pass

        def _load_thumb_pil(path: str, max_w: int):
            # Load and resize in worker thread; return PIL image and size
            try:
                with Image.open(path) as im:
                    w, h = im.size
                    if w <= 0 or h <= 0:
                        return None
                    scale = min(1.0, max_w / float(w))
                    tw = max(1, int(w * scale))
                    th = max(1, int(h * scale))
                    # Faster downscale for thumbnails
                    thumb = im.copy()
                    thumb = thumb.resize((tw, th), Image.BILINEAR)
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
                btn.configure(image=tk_img, width=tw + 8, height=th + 36)
            except Exception:
                pass

        row = 0
        col = 0
        current_token = self._gallery_load_token
        for path in files:
            try:
                # Cell frame per item (button + tags label)
                cell = ctk.CTkFrame(self._gallery_scroll, fg_color="transparent")
                cell.grid(row=row, column=col, padx=pad, pady=pad, sticky="n")
                try:
                    cell.grid_columnconfigure(0, weight=1)
                except Exception:
                    pass

                fname = os.path.basename(path)
                btn = ctk.CTkButton(
                    cell,
                    image=placeholder_ctk,
                    text=fname,
                    compound="top",
                    width=(thumb_w + 8),
                    height=(placeholder_h + 36),
                    command=lambda p=path: self._open_image_viewer(p),
                )
                btn.grid(row=0, column=0, sticky="n")
                try:
                    handler = lambda e, p=path: self._open_gallery_context_menu(e, p)
                    btn.bind("<Button-3>", handler)
                    cell.bind("<Button-3>", handler)
                except Exception:
                    pass

                # Show tags under thumbnail
                try:
                    tags = self._gallery_tags_map.get(fname, [])
                    txt = ", ".join(tags)
                    if txt:
                        tag_lbl = ctk.CTkLabel(cell, text=txt, anchor="center")
                        tag_lbl.grid(row=1, column=0, sticky="n", pady=(4, 0))
                        try:
                            tag_lbl.bind("<Button-3>", handler)
                        except Exception:
                            pass
                except Exception:
                    pass

                # Dispatch background load
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

                col += 1
                if col >= cols:
                    col = 0
                    row += 1
            except Exception:
                continue

    def _open_image_viewer(self, path: str) -> None:
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
        max_w = min(1200, int(sw * 0.9))
        max_h = min(900, int(sh * 0.9))

        w, h = img.size
        scale = min(max_w / float(w), max_h / float(h), 1.0)
        vw = int(w * scale)
        vh = int(h * scale)
        view_img = img.copy().resize((vw, vh), Image.LANCZOS)
        tk_img = ctk.CTkImage(light_image=view_img, dark_image=view_img, size=(vw, vh))

        frame = ctk.CTkFrame(top)
        frame.grid(row=0, column=0, padx=12, pady=12, sticky="nsew")
        try:
            frame.grid_columnconfigure(0, weight=0)
            frame.grid_columnconfigure(1, weight=1)
        except Exception:
            pass

        lbl = ctk.CTkLabel(frame, image=tk_img, text="")
        lbl.grid(row=0, column=0, columnspan=2)

        # Live Tag Editor (auto-saves as you type)
        try:
            name = os.path.basename(path)
            # Ensure tags are loaded
            self._load_gallery_tags()
            cur_tags = self._gallery_tags_map.get(name, [])

            ctk.CTkLabel(frame, text="Tags").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=(10, 0))
            tag_var = tk.StringVar(value=", ".join(cur_tags))
            tag_entry = ctk.CTkEntry(frame, textvariable=tag_var)
            tag_entry.grid(row=1, column=1, sticky="we", pady=(10, 0))

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
            sugg_frame.grid(row=2, column=0, columnspan=2, sticky="we")

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

        # Keep a reference on the toplevel to avoid GC
        top._img_ref = tk_img  # type: ignore[attr-defined]
        top.geometry(f"{vw+40}x{vh+120}")

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

    def _load_gallery_tags(self) -> None:
        try:
            with open(self._tags_json_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self._gallery_tags_map = {k: list(v) for k, v in data.items() if isinstance(v, list)}
        except Exception:
            self._gallery_tags_map = {}

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

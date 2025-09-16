from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
from pathlib import Path
from typing import Optional

import customtkinter as ctk
from PIL import Image

from app.obs_client import ObsClient
from app.threads.double_battle import DoubleBattleThread
from app.threads.rkaisi_teisi import RkaisiTeisiThread
from app.threads.syouhai import SyouhaiThread
from app.utils.logging import UiLogger


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        # Load appearance/theme from env
        self._appearance = os.getenv("APP_APPEARANCE", "Dark")
        self._accent_theme = os.getenv("APP_THEME", "blue")
        ctk.set_appearance_mode(self._appearance)
        ctk.set_default_color_theme(self._accent_theme)

        self.title("OBS Screenshot/Template Tool")
        self.geometry("1200x800")

        # Runtime state
        self._obs: Optional[ObsClient] = None
        self._lock = threading.Lock()
        self._th_double: Optional[DoubleBattleThread] = None
        self._th_rkaisi: Optional[RkaisiTeisiThread] = None
        self._th_syouhai: Optional[SyouhaiThread] = None

        # Widgets
        self.host_entry: ctk.CTkEntry
        self.port_entry: ctk.CTkEntry
        self.pass_entry: ctk.CTkEntry
        self.base_dir_entry: ctk.CTkEntry
        self.chk_double_var = tk.BooleanVar(value=self._env_bool("ENABLE_DOUBLE", True))
        self.chk_rkaisi_var = tk.BooleanVar(value=self._env_bool("ENABLE_RKAISI", True))
        self.chk_syouhai_var = tk.BooleanVar(value=self._env_bool("ENABLE_SYOUHAI", True))
        self.log_text: ctk.CTkTextbox
        # Gallery state
        self._thumb_refs: list[ctk.CTkImage] = []
        self._auto_refresh_var = tk.BooleanVar(value=True)
        self._gallery_after_id: Optional[str] = None

        self._build_ui()

    # --- UI ---
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        title = ctk.CTkLabel(self, text="OBS Screenshot / Template Tool", font=ctk.CTkFont(size=22, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, sticky="we", padx=16, pady=(12, 0))

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

        # Scripts
        script_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        script_frame.grid(row=1, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(script_frame, text="Scripts", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(8, 4))
        ctk.CTkCheckBox(script_frame, text="構築のスクリーンショット", variable=self.chk_double_var).pack(anchor="w", padx=8, pady=2)
        ctk.CTkCheckBox(script_frame, text="自動録画開始・停止", variable=self.chk_rkaisi_var).pack(anchor="w", padx=8, pady=2)
        ctk.CTkCheckBox(script_frame, text="戦績を自動更新", variable=self.chk_syouhai_var).pack(anchor="w", padx=8, pady=(2, 8))

        # Controls
        control_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        control_frame.grid(row=2, column=0, sticky="we", padx=8, pady=6)
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
        theme_frame.grid(row=3, column=0, sticky="we", padx=8, pady=(6, 12))
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
        # Initial load
        self.after(200, self._reload_gallery)
        # Auto-refresh if enabled
        self._schedule_gallery_refresh()

    # --- callbacks ---
    def _browse_base_dir(self) -> None:
        path = fd.askdirectory(title="Choose base directory")
        if path:
            self.base_dir_entry.delete(0, tk.END)
            self.base_dir_entry.insert(0, path)

    def _append_log(self, message: str) -> None:
        try:
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
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
        log_content = ""
        if getattr(self, "log_text", None):
            try:
                log_content = self.log_text.get("1.0", "end-1c")
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
            mb.showinfo("Connected", f"Connected to OBS WebSocket: {host}:{port}")
            self._append_log("[App] Connected to OBS")
            # Persist settings on successful connect
            self._save_settings()
        except Exception as e:
            mb.showerror("Connection Error", f"Failed to connect to OBS.\n{e}")
            self._append_log(f"[App] Connection error: {e}")
            return

        logger = UiLogger(self._append_log, self.log_text)

        if self.chk_double_var.get():
            self._th_double = DoubleBattleThread(self._obs, base_dir, logger)
            self._th_double.start()
        if self.chk_rkaisi_var.get():
            handantmp = os.path.join(base_dir, "handantmp")
            os.makedirs(handantmp, exist_ok=True)
            self._th_rkaisi = RkaisiTeisiThread(self._obs, handantmp, logger)
            self._th_rkaisi.start()
        if self.chk_syouhai_var.get():
            self._th_syouhai = SyouhaiThread(self._obs, base_dir, logger)
            self._th_syouhai.start()

    def _stop_threads(self) -> None:
        for th in (self._th_double, self._th_rkaisi, self._th_syouhai):
            try:
                if th and th.is_alive():
                    th.stop()  # type: ignore[attr-defined]
            except Exception:
                pass

        # Optional join to let threads exit promptly
        for th in (self._th_double, self._th_rkaisi, self._th_syouhai):
            try:
                if th:
                    th.join(timeout=1.0)
            except Exception:
                pass

        if self._obs is not None:
            try:
                self._obs.disconnect()
                self._append_log("[App] OBS disconnected")
            except Exception:
                pass
            self._obs = None

        mb.showinfo("Stopped", "All threads stopped.")

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
            "APP_APPEARANCE": self._appearance,
            "APP_THEME": self._accent_theme,
            "ENABLE_DOUBLE": "true" if self.chk_double_var.get() else "false",
            "ENABLE_RKAISI": "true" if self.chk_rkaisi_var.get() else "false",
            "ENABLE_SYOUHAI": "true" if self.chk_syouhai_var.get() else "false",
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
            self._append_log(f"[App] Saved settings -> {dotenv_path}")
        except Exception as e:
            mb.showerror("Save Error", f"Failed to save settings to {dotenv_path}\n{e}")
            return


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

        # Scrollable grid for thumbnails
        self._gallery_scroll = ctk.CTkScrollableFrame(parent, corner_radius=8)
        self._gallery_scroll.grid(row=1, column=0, sticky="nsew")
        for i in range(4):
            self._gallery_scroll.grid_columnconfigure(i, weight=1)

    def _current_koutiku_path(self) -> str:
        base_dir = self.base_dir_entry.get().strip() if getattr(self, "base_dir_entry", None) else self._resolve_base_dir_default()
        return os.path.join(base_dir, "koutiku")

    def _reload_gallery(self) -> None:
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

        # Collect image files
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        try:
            files = [
                os.path.join(koutiku, f)
                for f in os.listdir(koutiku)
                if os.path.splitext(f)[1].lower() in exts
            ]
        except Exception:
            files = []

        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        max_items = int(os.getenv("GALLERY_MAX", "100") or 100)
        files = files[:max_items]

        # Layout config
        cols = 4
        thumb_w = int(os.getenv("GALLERY_THUMB", "240") or 240)
        pad = 8

        row = 0
        col = 0
        for path in files:
            try:
                img = Image.open(path)
                # Keep aspect ratio and fit width
                w, h = img.size
                if w <= 0 or h <= 0:
                    continue
                scale = min(1.0, thumb_w / float(w))
                tw = int(w * scale)
                th = int(h * scale)
                thumb = img.copy()
                thumb = thumb.resize((tw, th), Image.LANCZOS)
                tk_img = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=(tw, th))
                self._thumb_refs.append(tk_img)

                # Button with image + filename under it
                fname = os.path.basename(path)
                btn = ctk.CTkButton(
                    self._gallery_scroll,
                    image=tk_img,
                    text=fname,
                    compound="top",
                    width=tw + 8,
                    height=th + 36,
                    command=lambda p=path: self._open_image_viewer(p),
                )
                btn.grid(row=row, column=col, padx=pad, pady=pad, sticky="n")

                col += 1
                if col >= cols:
                    col = 0
                    row += 1
            except Exception:
                # Skip problematic file
                continue

    def _open_image_viewer(self, path: str) -> None:
        try:
            img = Image.open(path)
        except Exception as e:
            mb.showerror("Open Image", f"Failed to open image\n{e}")
            return

        top = ctk.CTkToplevel(self)
        top.title(os.path.basename(path))
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
        frame.grid(row=0, column=0, padx=12, pady=12)
        lbl = ctk.CTkLabel(frame, image=tk_img, text="")
        lbl.grid(row=0, column=0)

        # Keep a reference on the toplevel to avoid GC
        top._img_ref = tk_img  # type: ignore[attr-defined]
        top.geometry(f"{vw+40}x{vh+80}")

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

def main() -> None:
    app = App()
    app.mainloop()

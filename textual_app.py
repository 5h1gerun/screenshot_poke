import threading
import time
from typing import Optional

from obswebsocket import obsws

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Label, Input, Button, Checkbox, TextLog

# 既存の処理スレッドを流用
from combined_app import DoubleBattleThread, RkaisiTeisiThread, SyouhaiThread
import os


class TextualObsApp(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    # セクションのスタイル
    .section {
        border: round $accent;
        height: auto;
        padding: 1 2;
        margin: 1;
    }
    .row { height: auto; }
    .grow { width: 1fr; }
    # ログは広めに
    # TextLog expands and scrolls
    # No extra CSS needed beyond container sizing
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self.ws: Optional[obsws] = None
        self.ws_lock = threading.Lock()
        self.thread_double: Optional[DoubleBattleThread] = None
        self.thread_rkaisi: Optional[RkaisiTeisiThread] = None
        self.thread_syouhai: Optional[SyouhaiThread] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        # 接続情報
        with Vertical(classes="section"):
            yield Label("OBS 接続情報")
            with Horizontal(classes="row"):
                yield Label("Host:")
                yield Input(value=os.getenv("OBS_HOST", "localhost"), id="host", classes="grow")
                yield Label("Port:")
                yield Input(value=os.getenv("OBS_PORT", "4444"), id="port", classes="grow")
                yield Label("Password:")
                yield Input(value=os.getenv("OBS_PASSWORD", ""), password=True, id="password", classes="grow")

        # パス設定
        with Vertical(classes="section"):
            yield Label("パス設定")
            with Horizontal(classes="row"):
                yield Label("Tesseract 実行ファイル:")
                yield Input(value=os.getenv("TESSERACT_PATH", r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe"), id="tesseract", classes="grow")
            with Horizontal(classes="row"):
                yield Label("ベースディレクトリ:")
                yield Input(value=os.getenv("BASE_DIR", os.getcwd()), id="base_dir", classes="grow")

        # スクリプト選択
        with Vertical(classes="section"):
            yield Label("実行するスクリプト")
            yield Checkbox("double_battle(ダブルバトル)", value=True, id="chk_double")
            yield Checkbox("rkaisi_teisi(録画開始・停止)", value=True, id="chk_rkaisi")
            yield Checkbox("syouhai(勝敗判定)", value=True, id="chk_syouhai")

        # 実行・停止
        with Horizontal(classes="section"):
            yield Button("Start", id="start")
            yield Button("Stop", id="stop")

        # ログ
        with Vertical(classes="section"):
            yield Label("ログ")
            yield TextLog(id="log", highlight=True, wrap=True)

        yield Footer()

    # ============ ログ関連 ============
    def _append_log(self, message: str) -> None:
        log = self.query_one("#log", TextLog)
        log.write(message)

    def _log_from_thread(self, message: str) -> None:
        # スレッドから安全にUIに反映
        self.call_from_thread(self._append_log, message)

    # ============ ボタンイベント ============
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            await self._handle_start()
        elif event.button.id == "stop":
            await self._handle_stop()

    async def _handle_start(self) -> None:
        host = self.query_one("#host", Input).value.strip()
        port_text = self.query_one("#port", Input).value.strip()
        password = self.query_one("#password", Input).value
        tesseract_path = self.query_one("#tesseract", Input).value.strip()
        base_dir = self.query_one("#base_dir", Input).value.strip()

        try:
            port = int(port_text)
        except ValueError:
            self._append_log(f"[App] Portが数値ではありません: {port_text}")
            return

        # 既存接続があれば閉じる
        if self.ws is not None:
            try:
                self.ws.disconnect()
            except Exception:
                pass
            self.ws = None

        # 接続
        try:
            self.ws = obsws(host, port, password)
            self.ws.connect()
            self._append_log(f"[App] OBS WebSocket 接続成功: {host}:{port}")
        except Exception as e:
            self._append_log(f"[App] OBS 接続失敗: {e}")
            self.ws = None
            return

        # スレッド開始
        if self.query_one("#chk_double", Checkbox).value:
            self.thread_double = DoubleBattleThread(
                self.ws, self.ws_lock, base_dir, tesseract_path, log_text=None, logger=self._log_from_thread
            )
            self.thread_double.start()
        if self.query_one("#chk_rkaisi", Checkbox).value:
            handantmp_dir = os.path.join(base_dir, "handantmp")
            self.thread_rkaisi = RkaisiTeisiThread(
                self.ws, self.ws_lock, handantmp_dir, log_text=None, logger=self._log_from_thread
            )
            self.thread_rkaisi.start()
        if self.query_one("#chk_syouhai", Checkbox).value:
            self.thread_syouhai = SyouhaiThread(
                self.ws, self.ws_lock, base_dir, log_text=None, logger=self._log_from_thread
            )
            self.thread_syouhai.start()

        self._append_log("[App] スレッド開始")

    async def _handle_stop(self) -> None:
        # スレッド停止
        if self.thread_double and self.thread_double.is_alive():
            self.thread_double.stop()
            self.thread_double = None
        if self.thread_rkaisi and self.thread_rkaisi.is_alive():
            self.thread_rkaisi.stop()
            self.thread_rkaisi = None
        if self.thread_syouhai and self.thread_syouhai.is_alive():
            self.thread_syouhai.stop()
            self.thread_syouhai = None

        # 少し待ってから切断
        time.sleep(0.2)
        if self.ws is not None:
            try:
                self.ws.disconnect()
                self._append_log("[App] OBS 切断")
            except Exception:
                pass
            self.ws = None

        self._append_log("[App] 停止指示完了")

    async def on_unmount(self) -> None:
        # アプリ終了時のクリーンアップ
        await self._handle_stop()


if __name__ == "__main__":
    TextualObsApp().run()

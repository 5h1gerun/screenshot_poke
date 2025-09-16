import os
import sys
import time
import signal
import socket
import subprocess
from contextlib import closing

# 標準ライブラリのみで起動待機
from urllib.request import urlopen
from urllib.error import URLError

# pip: pywebview が必要
try:
    import webview
except Exception as e:
    print("pywebview が未インストールです。\n  pip install pywebview", file=sys.stderr)
    raise


def find_free_port(start=8765, end=8899):
    for port in range(start, end + 1):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


def wait_server(url: str, timeout: float = 15.0, interval: float = 0.2) -> bool:
    """指定URLが応答するまで待機。応答したら True。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except URLError:
            pass
        time.sleep(interval)
    return False


def main():
    # ポート確保
    host = os.environ.get("TEXTUAL_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("TEXTUAL_WEB_PORT", find_free_port() or 8765))
    url = f"http://{host}:{port}/"

    cmd = [
        sys.executable,
        "textual_app.py",
        "--web",
        "--host",
        host,
        "--port",
        str(port),
    ]

    print("Launching Textual Web:", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    try:
        # 起動待機（ログにエラーが出たら早めに検知）
        if not wait_server(url, timeout=20):
            print("Textual Web の起動を検知できませんでした。ログを確認します。\n", file=sys.stderr)
            try:
                # 直近の出力を少しだけ表示
                if proc.stdout:
                    for _ in range(50):
                        line = proc.stdout.readline()
                        if not line:
                            break
                        sys.stderr.write(line)
            except Exception:
                pass
            raise SystemExit(1)

        # デスクトップウィンドウを表示
        webview.create_window("Textual OBS App", url, width=1400, height=900)
        webview.start()

    finally:
        # アプリ終了時に Textual Web を停止
        if proc.poll() is None:
            try:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                else:
                    proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


if __name__ == "__main__":
    main()


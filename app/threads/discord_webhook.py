from __future__ import annotations

import mimetypes
import os
import threading
import time
import json
from typing import Optional, Set

from app.utils.logging import UiLogger
from app.utils import paths as paths_utils

try:
    # Use stdlib to avoid extra dependency
    from urllib import request, error
except Exception:  # pragma: no cover
    request = None  # type: ignore
    error = None  # type: ignore


class DiscordWebhookThread(threading.Thread):
    """Watch `koutiku` folder and POST new images to a Discord webhook.

    - Uses polling (every 2s) to detect new files with extensions png/jpg/jpeg/webp.
    - Tracks files seen during the current run to avoid duplicates.
    - Requires `webhook_url` to be a valid Discord webhook URL.
    """

    def __init__(self, base_dir: str, webhook_url: str, logger: Optional[UiLogger] = None) -> None:
        super().__init__(daemon=True)
        self._base = base_dir
        self._koutiku = paths_utils.get_koutiku_dir(base_dir)
        os.makedirs(self._koutiku, exist_ok=True)
        self._url = (webhook_url or "").strip()
        self._log = logger or UiLogger()
        self._stop = threading.Event()
        self._seen: Set[str] = set()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if not self._url:
            self._log.log("[Discord] Webhook URL が未設定のため停止します")
            return
        if request is None:
            self._log.log("[Discord] urllib が利用できないため停止します")
            return

        self._log.log("[Discord] koutiku フォルダ監視を開始")
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        try:
            # Initialize seen with current files to avoid bulk-post on startup
            for name in os.listdir(self._koutiku):
                path = os.path.join(self._koutiku, name)
                if os.path.isfile(path) and os.path.splitext(name)[1].lower() in exts:
                    self._seen.add(path)
        except Exception:
            pass

        while not self._stop.is_set():
            try:
                # Prefer newest-first by mtime so fresh screenshots go out promptly
                try:
                    entries = []
                    with os.scandir(self._koutiku) as it:
                        for e in it:
                            if not e.is_file():
                                continue
                            if os.path.splitext(e.name)[1].lower() not in exts:
                                continue
                            try:
                                mt = e.stat().st_mtime
                            except Exception:
                                mt = 0.0
                            entries.append((mt, e.name))
                    names = [n for (_mt, n) in sorted(entries, key=lambda x: x[0], reverse=True)]
                except Exception:
                    # Fallback to name sort if scandir/stat fails
                    names = sorted(os.listdir(self._koutiku))

                for name in names:
                    if self._stop.is_set():
                        return
                    path = os.path.join(self._koutiku, name)
                    if path in self._seen:
                        continue
                    if not os.path.isfile(path):
                        continue
                    if os.path.splitext(name)[1].lower() not in exts:
                        continue
                    # Basic debounce: ensure the file is fully written (size stable)
                    try:
                        size1 = os.path.getsize(path)
                        time.sleep(0.1)
                        size2 = os.path.getsize(path)
                        if size1 != size2:
                            # Try next tick
                            continue
                    except Exception:
                        continue

                    # Attempt to post
                    ok = self._post_file(path)
                    if ok:
                        self._seen.add(path)
            except Exception as e:
                self._log.log(f"[Discord] 監視ループエラー: {e}")

            if self._stop.wait(2.0):
                return

        self._log.log("[Discord] 監視を停止")

    # --- internals ---
    def _post_file(self, path: str) -> bool:
        name = os.path.basename(path)
        content_text = f"新しいスクリーンショット: {name}"
        try:
            body, ctype = self._build_multipart_request(path, content_text)
        except Exception as e:
            self._log.log(f"[Discord] 送信準備に失敗: {e}")
            return False

        try:
            # Discord may reject requests without a UA; also prefer wait=true for 2xx body
            url = self._url
            if "?" in url:
                if "wait=" not in url:
                    url = url + "&wait=true"
            else:
                url = url + "?wait=true"
            req = request.Request(url, data=body)
            req.add_header("Content-Type", ctype)
            req.add_header("User-Agent", "obs-screenshot-tool")
            req.add_header("Accept", "application/json")
            with request.urlopen(req, timeout=15) as resp:
                code = getattr(resp, "status", None) or getattr(resp, "code", 0)
                if 200 <= int(code) < 300:
                    self._log.log(f"[Discord] 送信しました: {name}")
                    return True
                else:
                    self._log.log(f"[Discord] 送信失敗 (HTTP {code}): {name}")
                    return False
        except Exception as e:  # includes HTTPError/URLError
            # Try to extract HTTP status/body for diagnostics
            try:
                if isinstance(e, error.HTTPError):  # type: ignore[attr-defined]
                    code = getattr(e, "code", None)
                    reason = getattr(e, "reason", "")
                    detail = ""
                    try:
                        data = e.read()
                        if data:
                            detail = data.decode("utf-8", errors="ignore")[:300]
                    except Exception:
                        pass
                    self._log.log(f"[Discord] 送信エラー (HTTP {code} {reason}): {detail}")
                    return False
            except Exception:
                pass
            self._log.log(f"[Discord] 送信エラー: {e}")
            return False

    def _build_multipart_request(self, file_path: str, content: str):
        # Compose a multipart/form-data body compatible with Discord webhook execute
        boundary = f"---------------------------{int(time.time()*1000)}"
        lf = "\r\n".encode("utf-8")

        parts: list[bytes] = []

        # Part 1: payload_json
        payload = {"content": content}
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        parts.append((
            f"--{boundary}\r\n"
            "Content-Disposition: form-data; name=\"payload_json\"\r\n"
            "Content-Type: application/json; charset=utf-8\r\n\r\n"
        ).encode("utf-8") + payload_bytes + lf)

        # Part 2: file (Discord accepts files[0])
        filename = os.path.basename(file_path)
        mime, _ = mimetypes.guess_type(filename)
        if not mime:
            mime = "application/octet-stream"
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        header = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"files[0]\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        parts.append(header + file_bytes + lf)

        # End boundary
        parts.append((f"--{boundary}--\r\n").encode("utf-8"))

        body = b"".join(parts)
        content_type = f"multipart/form-data; boundary={boundary}"
        return body, content_type


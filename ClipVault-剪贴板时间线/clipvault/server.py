"""ClipVault HTTP 服务：时间线页面 + 列表/搜索 + 回贴/置顶/删除/清空/暂停。

全部只读端点为 GET；写操作集中在 POST。store 可注入（测试不碰真实剪贴板）。
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE_PATH = Path(__file__).resolve().parent / "page.html"


def _as_bool(value, default: bool = False) -> bool:
    """严格布尔解析：只认真布尔与 "true"/"false"/"1"/"0"，防字符串真值坑。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
    return default


def _as_id(value):
    """严格条目 id：只认 int（拒绝 bool——SQLite 会把 True 当 1 误操作条目）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def make_server(port: int, store, images_dir: Path):
    """工厂：store/images_dir 可注入。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "ClipVault/0.2"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200) -> None:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, status: int, msg: str) -> None:
            self._json({"error": msg}, status)

        def _read_body(self) -> dict | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 64 * 1024:
                return None
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
                return data if isinstance(data, dict) else None
            except (ValueError, UnicodeDecodeError):
                return None

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            qs = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items() if v}
            if path == "/" or path == "/index.html":
                self._send(200, PAGE_PATH.read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/list":
                entries = store.list(kind=qs.get("kind") or None,
                                     q=qs.get("q") or None,
                                     limit=qs.get("limit", "100"))
                self._json({"entries": entries})
            elif path.startswith("/api/entry/"):
                try:
                    entry = store.get(int(path.rsplit("/", 1)[-1]))
                except ValueError:
                    entry = None
                if entry is None:
                    self._error(404, "not found")
                    return
                self._json(entry)  # 全文（列表响应的 body 是截断版）
            elif path == "/api/stats":
                self._json(store.stats())
            elif path.startswith("/api/image/"):
                try:
                    entry = store.get(int(path.rsplit("/", 1)[-1]))
                except ValueError:
                    entry = None
                if entry is None or not entry["image_path"]:
                    self._error(404, "not found")
                    return
                p = images_dir / entry["image_path"]
                if not p.is_file():
                    self._error(404, "image file missing")
                    return
                self._send(200, p.read_bytes(), "image/png")
            else:
                self._error(404, "not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            data = self._read_body()
            if data is None:
                self._error(400, "请求体不合法")
                return
            if path == "/api/copy":
                entry = store.get(_as_id(data.get("id")) or 0)
                if entry is None:
                    self._error(404, "not found")
                    return
                from . import clip
                if entry["kind"] == "image":
                    png = images_dir / entry["image_path"]
                    if not png.is_file():
                        self._error(404, "image file missing")
                        return
                    ok = clip.write_image(png.read_bytes())
                    what = "图片"
                elif entry["kind"] == "files":
                    ok = clip.write_files(entry["body"].splitlines())
                    what = "文件列表"
                else:
                    ok = clip.write_text(entry["body"])
                    what = "文本"
                self._json({"ok": bool(ok), "what": what})
            elif path == "/api/pin":
                ok = store.pin(_as_id(data.get("id")) or 0,
                               _as_bool(data.get("pinned")))
                self._json({"ok": ok} if ok else {"error": "not found"},
                           200 if ok else 404)
            elif path == "/api/delete":
                ok = store.delete(_as_id(data.get("id")) or 0)
                self._json({"ok": ok} if ok else {"error": "not found"},
                           200 if ok else 404)
            elif path == "/api/clear":
                n = store.clear(keep_pinned=_as_bool(data.get("keep_pinned", True)))
                self._json({"ok": True, "deleted": n})
            elif path == "/api/pause":
                store.set_paused(_as_bool(data.get("paused")))
                self._json({"ok": True, "paused": store.stats()["paused"]})
            else:
                self._error(404, "not found")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)

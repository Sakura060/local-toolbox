"""NoteNest HTTP 服务与 REST API。

路由（全部 JSON，除页面本身）：
  GET  /                    单页应用
  GET  /api/notes?q=...     列表/搜索
  POST /api/notes           新建 {title?, content?} → 201 + note
  GET  /api/notes/<id>      读取（含 content）
  PUT  /api/notes/<id>      更新 {content} → note
  DELETE /api/notes/<id>    删除 → {"deleted": id}（连带删除该笔记的图片）
  POST /api/notes/<id>/assets  上传图片（原始字节）→ 201 + {"url","markdown","size"}
  GET  /assets/<id>/<name>  读取图片（内容寻址，长缓存）
  GET  /api/stats           统计

安全：仅绑定 127.0.0.1；id 白名单校验 + 路径双保险；前端渲染前转义 HTML。
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .store import MAX_ASSET, NoteStore

MAX_BODY = 512 * 1024        # 单篇笔记上限 512KB，足够长文
_PAGE_PATH = Path(__file__).resolve().parent / "page.html"
_ASSET_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".bmp": "image/bmp"}


def _image_ext(data: bytes) -> str | None:
    """按文件头判断图片类型（不信任客户端声明的类型），返回扩展名。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    return None


def _load_page() -> str:
    with open(_PAGE_PATH, encoding="utf-8") as fp:
        return fp.read()


class NoteHandler(BaseHTTPRequestHandler):
    server_version = "NoteNest/0.1"
    protocol_version = "HTTP/1.1"
    store: NoteStore = None      # 类属性，工厂注入
    quiet: bool = False

    # ---- 响应工具 ----
    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _fail(self, status: int, text: str) -> None:
        self._json({"error": text}, status)

    def _drain(self, limit: int = 64 * 1024 * 1024) -> None:
        """丢弃请求体（有上限），避免残包被当成下一个请求。"""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        remaining = min(length, limit)
        while remaining > 0:
            block = self.rfile.read(min(256 * 1024, remaining))
            if not block:
                break
            remaining -= len(block)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            # 排空请求体（有上限）让客户端能收到 400，而不是连接被掐断
            if length <= 16 * 1024 * 1024:
                self._drain(16 * 1024 * 1024)
            else:
                self.close_connection = True
            raise ValueError(f"请求体大小不合法（上限 {MAX_BODY // 1024}KB）")
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def log_message(self, fmt, *args):
        if not self.quiet:
            super().log_message(fmt, *args)

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    # ---- 路由 ----
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._html(_load_page())
        elif path == "/api/notes":
            q = parse_qs(parsed.query).get("q", [""])[0]
            self._json({"notes": self.store.list_notes(q)})
        elif path == "/api/stats":
            self._json(self.store.stats())
        elif path.startswith("/assets/"):
            self._serve_asset(path)
        elif path.startswith("/api/notes/"):
            note_id = path.rsplit("/", 1)[-1]
            note = self.store.get(note_id)
            if note is None:
                self._fail(404, "笔记不存在")
            else:
                self._json(note.to_dict())
        else:
            self._fail(404, "未知路径")

    def _serve_asset(self, path: str) -> None:
        parts = path.split("/")
        if len(parts) != 4 or parts[1] != "assets":
            self._fail(404, "未知路径")
            return
        p = self.store.asset_path(parts[2], parts[3])
        if p is None:
            self._fail(404, "图片不存在")
            return
        try:
            data = p.read_bytes()
        except OSError:
            self._fail(404, "图片不存在")
            return
        self.send_response(200)
        self.send_header("Content-Type",
                         _ASSET_MIME.get(p.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        # 文件名 = 内容哈希 → 可以放心长期缓存
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _upload_asset(self, note_id: str) -> None:
        if self.store.get(note_id) is None:
            self._drain()
            self._fail(404, "笔记不存在")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_ASSET:
            self._drain()
            self._fail(400, f"图片大小不合法（上限 {MAX_ASSET // (1024 * 1024)}MB）")
            return
        data = self.rfile.read(length)
        ext = _image_ext(data)                 # 以文件头为准，不信客户端声明
        if ext is None:
            self._fail(400, "不是支持的图片格式（png/jpg/gif/webp/bmp）")
            return
        url = self.store.save_asset(note_id, data, ext)
        if url is None:
            self._fail(400, "图片保存失败")
            return
        self._json({"url": url, "markdown": f"![]({url})", "size": len(data)}, 201)

    def do_POST(self):
        path = urlparse(self.path).path
        m = re.fullmatch(r"/api/notes/([^/]+)/assets", path)
        if m:
            self._upload_asset(m.group(1))
            return
        if path != "/api/notes":
            self._fail(404, "未知路径")
            return
        try:
            data = self._read_json()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
            self._fail(400, f"请求体不合法: {e}")
            return
        note = self.store.create(str(data.get("title", "")),
                                 str(data.get("content", "")))
        self._json(note.to_dict(), 201)

    def do_PUT(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/notes/"):
            self._fail(404, "未知路径")
            return
        note_id = parsed.path.rsplit("/", 1)[-1]
        try:
            data = self._read_json()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
            self._fail(400, f"请求体不合法: {e}")
            return
        note = self.store.update(note_id, str(data.get("content", "")))
        if note is None:
            self._fail(404, "笔记不存在")
        else:
            self._json(note.to_dict())

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/notes/"):
            self._fail(404, "未知路径")
            return
        note_id = parsed.path.rsplit("/", 1)[-1]
        if self.store.delete(note_id):
            self._json({"deleted": note_id})
        else:
            self._fail(404, "笔记不存在")


def make_server(directory: str, host: str = "127.0.0.1", port: int = 8890,
                quiet: bool = False) -> ThreadingHTTPServer:
    handler = type("H", (NoteHandler,), {"store": NoteStore(directory),
                                         "quiet": quiet})
    return ThreadingHTTPServer((host, port), handler)


def serve(directory: str, port: int = 8890) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
    server = make_server(directory, port=port)
    print(f"NoteNest 笔记目录: {Path(directory).resolve()}")
    print(f"打开 http://127.0.0.1:{port}/  （Ctrl+C 退出）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        server.server_close()

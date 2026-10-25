"""消化器 HTTP 服务：任务页 + API + 媒体流（带 Range，支持时间戳跳转）。"""

from __future__ import annotations

import json
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .db import Database
from .jobs import Runner
from .web import WEB_DIR

_MEDIA_MIME = {
    ".mp4": "video/mp4", ".m4a": "audio/mp4", ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg", ".flv": "video/x-flv", ".webm": "video/webm",
    ".wav": "audio/wav", ".aac": "audio/aac", ".ts": "video/mp2t",
    ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".opus": "audio/ogg",
}


def build_handler(db: Database, runner: Runner | None, web_dir: Path = WEB_DIR):
    web_index = Path(web_dir) / "index.html"

    class Handler(BaseHTTPRequestHandler):
        server_version = "BiliDigest/0.1"

        def log_message(self, fmt: str, *args) -> None:
            pass

        def _json(self, obj, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 1_000_000:
                return {}
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                if route in ("/", "/index.html"):
                    data = (Path(web_dir) / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif route == "/api/jobs":
                    self._json({"jobs": db.list_jobs(), "stats": db.stats()})
                elif m := re.match(r"^/api/job/(\d+)$", route):
                    job = db.get_job(int(m.group(1)))
                    if not job:
                        self._json({"error": "no such job"}, 404)
                        return
                    job["transcripts"] = db.get_transcripts(job["id"])
                    self._json(job)
                elif m := re.match(r"^/media/(\d+)$", route):
                    self._media(int(m.group(1)), qs)
                else:
                    self._json({"error": "not found"}, 404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
                body = self._read_json()
                if parsed.path == "/api/add":
                    inp = str(body.get("input") or "").strip()
                    if not inp:
                        self._json({"ok": False, "error": "输入为空"})
                        return
                    try:
                        job_id = runner.add(inp) if runner else _add_only(db, inp)
                    except Exception as exc:
                        self._json({"ok": False, "error": str(exc)})
                        return
                    self._json({"ok": True, "id": job_id})
                elif parsed.path == "/api/retry":
                    job_id = int(body.get("id") or 0)
                    if runner and db.get_job(job_id):
                        db.set_status(job_id, "pending", "")
                        runner.enqueue(job_id)
                        self._json({"ok": True})
                    else:
                        self._json({"ok": False, "error": "无运行器或任务不存在"})
                elif parsed.path == "/api/clean-audio":
                    # 清理缓存音频（只动 data/media 目录——本地任务引用的用户文件不在此目录）
                    from .config import MEDIA_DIR
                    n, freed = 0, 0
                    if MEDIA_DIR.is_dir():
                        for p in MEDIA_DIR.iterdir():
                            if p.is_file() and p.suffix.lower() in _MEDIA_MIME:
                                try:
                                    freed += p.stat().st_size
                                    p.unlink()
                                    n += 1
                                except OSError:
                                    pass
                    mb = freed / 1048576
                    self._json({"ok": True, "deleted": n,
                                "freed_mb": round(mb, 1)})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def _media(self, job_id: int, qs: dict) -> None:
            job = db.get_job(job_id)
            if not job or not job["media_path"]:
                self._json({"error": "no media"}, 404)
                return
            p = Path(job["media_path"])
            if not p.is_file():
                self._json({"error": "media gone"}, 404)
                return
            size = p.stat().st_size
            mime = _MEDIA_MIME.get(p.suffix.lower(), "application/octet-stream")
            range_header = self.headers.get("Range") or ""
            start, end = 0, size - 1
            code = 200
            if range_header.startswith("bytes="):
                code = 206
                mm = re.match(r"bytes=(\d*)-(\d*)", range_header)
                if mm:
                    if mm.group(1):
                        start = int(mm.group(1))
                    if mm.group(2):
                        end = min(int(mm.group(2)), size - 1)
                    elif not mm.group(1):
                        end = size - 1
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            length = end - start + 1
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with p.open("rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(1024 * 512, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def _add_only(db: Database, input_str: str) -> int:
    """无 Runner 时（测试/只入库）：校验并入库为 pending。"""
    from . import fetch

    kind, value = fetch.classify_input(input_str)
    return db.insert_job(kind, value)


def make_server(port: int, db: Database, runner: Runner | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), build_handler(db, runner))

"""HTTP 服务：上传体检 / 净化下载。仅绑 127.0.0.1。"""

from __future__ import annotations

import json
import shutil
import time
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import KEEP_HOURS, MAX_UPLOAD, UPLOAD_DIR, ensure_dirs
from .inspect import inspect_file, sanitize_file
from .multipart import parse_multipart
from .web import WEB_DIR

_SENSITIVE_EXT = {".docx", ".docm", ".pptx", ".pptm", ".xlsx", ".xlsm",
                  ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff",
                  ".pdf", ".zip"}


class _Store:
    """上传工作区：<id>/original.ext + sanitized.ext + report.json。"""

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    @staticmethod
    def dir_for(uid: str) -> Path:
        return UPLOAD_DIR / uid

    @staticmethod
    def sweep_old() -> int:
        n = 0
        cutoff = time.time() - KEEP_HOURS * 3600
        if UPLOAD_DIR.is_dir():
            for d in UPLOAD_DIR.iterdir():
                if d.is_dir() and d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    n += 1
        return n


def _safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    return name or "file.bin"


def build_handler(web_dir: Path = WEB_DIR):
    web_index = Path(web_dir) / "index.html"

    class Handler(BaseHTTPRequestHandler):
        server_version = "Inspector/0.1"

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

        def _page(self) -> None:
            try:
                data = (Path(web_dir) / "index.html").read_bytes()
            except OSError:
                self._json({"error": "page missing"}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_body(self) -> bytes | None:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_UPLOAD:
                return None
            return self.rfile.read(n)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            try:
                if route in ("/", "/index.html"):
                    self._page()
                elif route.startswith("/api/report/"):
                    uid = route.rsplit("/", 1)[-1]
                    f = _Store.dir_for(uid) / "report.json"
                    if not f.is_file():
                        self._json({"error": "no such id"}, 404)
                        return
                    self._json(json.loads(f.read_text(encoding="utf-8")))
                elif route.startswith("/download/"):
                    uid = route.rsplit("/", 1)[-1]
                    d = _Store.dir_for(uid)
                    files = list(d.glob("sanitized.*")) if d.is_dir() else []
                    if not files:
                        self._json({"error": "no sanitized file"}, 404)
                        return
                    data = files[0].read_bytes()
                    orig = (d / "origname.txt").read_text(encoding="utf-8") if (d / "origname.txt").is_file() else "file"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(data)))
                    quoted = urllib.parse.quote("净化_" + orig)
                    self.send_header("Content-Disposition",
                                     f"attachment; filename*=UTF-8''{quoted}")
                    self.end_headers()
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path == "/api/check":
                    self._check()
                elif parsed.path == "/api/sanitize":
                    self._sanitize()
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def _check(self) -> None:
            ctype = self.headers.get("Content-Type") or ""
            if "multipart/form-data" not in ctype:
                self._json({"ok": False, "error": "需要 multipart 上传"})
                return
            body = self._read_body()
            if body is None:
                self._json({"ok": False, "error": f"请求体为空或超过 {MAX_UPLOAD // 1024 // 1024}MB"})
                return
            parts = parse_multipart(body, ctype)
            if not parts:
                self._json({"ok": False, "error": "未找到上传文件"})
                return
            _, (filename, content) = next(iter(parts.items()))
            filename = _safe_filename(filename)
            ext = Path(filename).suffix.lower()
            if ext not in _SENSITIVE_EXT:
                self._json({"ok": False,
                            "error": f"暂不支持 {ext or '无扩展名'}（docx/pptx/xlsx/图片/pdf/zip）"})
                return
            uid = _Store.new_id()
            work = _Store.dir_for(uid)
            work.mkdir(parents=True, exist_ok=True)
            orig = work / ("original" + ext)
            orig.write_bytes(content)
            (work / "origname.txt").write_text(filename, encoding="utf-8")
            rep = inspect_file(orig)
            (work / "report.json").write_text(
                json.dumps(rep.as_dict(), ensure_ascii=False), encoding="utf-8")
            self._json({"ok": True, "id": uid, "name": filename,
                        "report": rep.as_dict()})

        def _sanitize(self) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            uid = str(body.get("id") or "")
            work = _Store.dir_for(uid)
            origs = list(work.glob("original.*")) if work.is_dir() else []
            if not origs:
                self._json({"ok": False, "error": "no such id"})
                return
            orig = origs[0]
            filename = (work / "origname.txt").read_text(encoding="utf-8")
            result = sanitize_file(orig, work / ("sanitized" + orig.suffix))
            if result is None:
                self._json({"ok": False,
                            "error": "该类型 v0.1 只检不清（PDF 建议重新导出；zip 建议手工整理）"})
                return
            recheck = inspect_file(work / ("sanitized" + orig.suffix))
            remain = [f for f in recheck.findings if f.level in ("high", "med")]
            self._json({"ok": True, "result": result,
                        "recheck": recheck.as_dict(),
                        "remaining": [f.as_dict() for f in remain],
                        "download": f"/download/{uid}"})

    return Handler


def make_server(port: int) -> ThreadingHTTPServer:
    ensure_dirs()
    _Store.sweep_old()
    return ThreadingHTTPServer(("127.0.0.1", port), build_handler())


def open_browser_later(url: str, delay: float = 1.5) -> None:
    import threading

    def _open():
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Timer(delay, _open).start()

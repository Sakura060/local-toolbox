"""HTTP 服务：簇列表 / 谱系视图 / diff。仅绑 127.0.0.1。"""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import default_scan_dirs, ensure_dirs
from .engine import Database, Engine, rebuild_clusters
from .web import WEB_DIR


class ScanState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = False
        self.done = 0
        self.total = 0
        self.current = ""
        self.last_summary = None

    def start(self, total: int) -> None:
        with self._lock:
            self.active, self.done, self.total = True, 0, total

    def update(self, done: int, total: int, current: str) -> None:
        with self._lock:
            self.done, self.total, self.current = done, total, current

    def finish(self, summary) -> None:
        with self._lock:
            self.active = False
            self.last_summary = summary

    def snapshot(self) -> dict:
        with self._lock:
            return {"active": self.active, "done": self.done,
                    "total": self.total, "current": self.current,
                    "last_summary": self.last_summary}


def run_scan(engine: Engine, roots: list[str], state: ScanState) -> dict:
    files = engine.collect_files(roots)
    state.start(len(files))

    def cb(done, total, current):
        state.update(done, total, current)

    summary = {"done": True}
    try:
        summary = engine.scan(roots, progress=cb)
    finally:
        # 存真实摘要（processed/skipped/purged/clusters）——曾误存 {"done":True}
        # 导致前端"上次扫描"永远拿不到结果
        state.finish(summary)
    return summary


def _int(qs: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int((qs.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def build_handler(db: Database, engine: Engine, state: ScanState,
                  web_dir: Path = WEB_DIR):
    web_index = Path(web_dir) / "index.html"

    class Handler(BaseHTTPRequestHandler):
        server_version = "DocLine/0.1"

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
            route, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
            try:
                if route in ("/", "/index.html"):
                    data = (Path(web_dir) / "index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif route == "/api/clusters":
                    clusters = db.list_clusters()
                    for c in clusters:
                        c["members"] = db.docs_by_cluster(c["cid"])
                        c["edges"] = db.edges_for([m["id"] for m in c["members"]])
                    self._json({"clusters": clusters})
                elif route == "/api/stats":
                    st = db.stats()
                    st["indexing"] = state.snapshot()
                    self._json(st)
                elif m := re.match(r"^/api/doc/(\d+)$", route):
                    doc = db.get_doc(int(m.group(1)))
                    if not doc:
                        self._json({"error": "no such doc"}, 404)
                        return
                    doc.pop("sh_json", None)
                    self._json(doc)
                elif route == "/api/diff":
                    a = db.get_doc(_int(qs, "a", 0, 0, 10 ** 9))
                    b = db.get_doc(_int(qs, "b", 0, 0, 10 ** 9))
                    if not a or not b:
                        self._json({"error": "no such doc"}, 404)
                        return
                    from .diff import diff_blocks

                    self._json({
                        "a": {"id": a["id"], "name": a["name"],
                              "mtime": a["mtime"]},
                        "b": {"id": b["id"], "name": b["name"],
                              "mtime": b["mtime"]},
                        "diff": diff_blocks(a["blocks"], b["blocks"]),
                    })
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
                body = self._read_json()
                if parsed.path == "/api/scan":
                    roots = body.get("paths") or db.get_meta("roots", "").split("|") \
                        or default_scan_dirs()
                    roots = [r for r in roots if r and Path(r).is_dir()]
                    if not roots:
                        self._json({"ok": False, "error": "没有可扫描目录"})
                        return
                    db.set_meta("roots", "|".join(roots))
                    if state.snapshot()["active"]:
                        self._json({"ok": True, "started": False, "msg": "扫描进行中"})
                        return
                    threading.Thread(target=run_scan,
                                     args=(engine, roots, state), daemon=True).start()
                    self._json({"ok": True, "started": True, "roots": roots})
                elif parsed.path == "/api/rebuild":
                    n = rebuild_clusters(db)
                    self._json({"ok": True, "clusters": n})
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    return Handler


def make_server(port: int, db: Database, engine: Engine,
                state: ScanState) -> ThreadingHTTPServer:
    ensure_dirs()
    return ThreadingHTTPServer(("127.0.0.1", port),
                               build_handler(db, engine, state))


def start_scan_loop(db: Database, engine: Engine, state: ScanState,
                    interval: int = 60) -> None:
    """后台定时增量重扫（幂等）。"""
    import time

    def loop() -> None:
        while True:
            try:
                roots = [r for r in db.get_meta("roots").split("|") if r]
                if roots and not state.snapshot()["active"]:
                    run_scan(engine, roots, state)
            except Exception:
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()


def open_browser_later(url: str, delay: float = 1.5) -> None:
    import webbrowser

    def _open():
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Timer(delay, _open).start()

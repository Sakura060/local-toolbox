"""户口本 HTTP 服务：查询页 + API。仅绑定 127.0.0.1。"""

from __future__ import annotations

import json
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .db import Database
from .mft import ROOT_FRN, lineage_from_events
from .usn import reason_names
from .web import WEB_DIR


def _int(qs: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int((qs.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def build_handler(db: Database, web_dir: Path = WEB_DIR):
    web_index = Path(web_dir) / "index.html"

    class Handler(BaseHTTPRequestHandler):
        server_version = "Hukou/0.1"

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

        def _item(self, r: dict) -> dict:
            events = db.events_for_name(r["name"], limit=15)
            for e in events:
                e["reason_names"] = reason_names(int(e["reasons"]))
                e["ts_str"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(e["ts"] or 0)
                )
            return {**r, "events": events,
                    "exists": Path(r["path"]).is_file()}

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            route, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
            try:
                if route in ("/", "/index.html"):
                    self._page()
                elif route == "/api/lookup":
                    q = (qs.get("q") or [""])[0].strip()
                    limit = _int(qs, "limit", 50, 1, 200)
                    if not q:
                        rows = db.recent_origins(limit)
                        mode = "recent"
                    else:
                        rows = db.search_origins(q, limit)
                        mode = "search"
                    exact = None
                    p = Path(q)
                    if len(q) > 3 and (":" in q or "\\" in q):
                        rec = db.lookup_origin(str(p))
                        if rec and rec not in rows:
                            rows.insert(0, rec)
                        if rec:
                            exact = str(p)
                    self._json({"mode": mode, "total": len(rows), "items":
                                [self._item(r) for r in rows], "exact": exact})
                elif route == "/api/lineage":
                    q = (qs.get("q") or [""])[0].strip()
                    if not q:
                        self._json({"error": "missing q"}, 400)
                        return
                    p = Path(q)
                    vol = p.drive[:1].upper()
                    if not vol or db.count_files(vol) == 0:
                        self._json({"ok": False, "reason": "该卷还没有快照（cli.py deep）"})
                        return
                    frn = ROOT_FRN
                    for seg in [s for s in p.parts[1:] if s]:
                        nxt = db.find_child(vol, frn, seg)
                        if nxt is None:
                            self._json({"ok": False,
                                        "reason": "路径不在快照中（快照后新建/已删除）"})
                            return
                        frn = nxt
                    events = db.events_for_frn(vol, frn)
                    for e in events:
                        e.setdefault("reason", e.get("reasons", 0))
                        e["ts_str"] = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(e.get("ts") or 0)
                        )
                    self._json({
                        "ok": True, "vol": vol, "frn": frn,
                        "chain": lineage_from_events(events),
                    })
                elif route == "/api/stats":
                    self._json(db.stats())
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/api/scan":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
                    paths = body.get("paths") or []
                    from hukou.config import default_scan_dirs

                    paths = paths or default_scan_dirs()
                    summary = _scan_in_thread_sync(paths, db)
                    self._json({"ok": True, "summary": summary})
                except Exception as exc:
                    self._json({"ok": False, "error": str(exc)}, 500)
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def _scan_in_thread_sync(paths: list[str], db: Database) -> dict:
    """Web 触发的小规模扫描（限下载夹规模，同步执行）。"""
    from .collector import scan_origins

    return scan_origins(paths, db, use_browser=True)


def make_server(port: int, db: Database) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), build_handler(db))

"""HTTP 服务（stdlib http.server）：搜索 API + 静态页 + 缩略图/原图。

安全约束：
- 仅绑定 127.0.0.1；
- /thumb、/img、/api/open 只接受数据库登记的 id，绝不接受客户端传来的路径；
- 前端名称等动态字段一律由前端 escapeHtml 转义后渲染。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import THUMB_DIR
from .db import Database
from .hashes import duplicate_groups
from .indexer import Indexer

MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".bmp": "image/bmp", ".webp": "image/webp", ".gif": "image/gif",
}


class ScanState:
    """索引进度的线程安全快照。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = False
        self.done = 0
        self.total = 0
        self.current = ""
        self.last_summary: dict | None = None

    def start(self, total: int) -> None:
        with self._lock:
            self.active = True
            self.done = 0
            self.total = total
            self.current = ""

    def update(self, done: int, total: int, current: str) -> None:
        with self._lock:
            self.done = done
            self.total = total
            self.current = current

    def finish(self, summary: dict) -> None:
        with self._lock:
            self.active = False
            self.last_summary = summary

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "active": self.active,
                "done": self.done,
                "total": self.total,
                "current": self.current,
                "last_summary": self.last_summary,
            }


def run_scan(indexer: Indexer, roots: list[str], scan_state: ScanState) -> dict:
    files = indexer.collect_files(roots)
    scan_state.start(len(files))

    def cb(done: int, total: int, current: str) -> None:
        scan_state.update(done, total, current)

    summary = {"done": True}
    try:
        summary = indexer.scan(roots, progress=cb)
    finally:
        # 存真实摘要（processed/updated/errors/purged）——曾误存 {"done":True}
        scan_state.finish(summary)
    return summary


def _snippet(ocr_text: str, name: str, q: str) -> str:
    """从原文取命中窗口（客户端再做高亮）。"""
    hay = ocr_text or name or ""
    needle = (q or "").strip().lower()
    pos = -1
    if needle:
        pos = hay.lower().find(needle)
        if pos < 0:
            for ch in needle:
                pos = hay.lower().find(ch)
                if pos >= 0:
                    break
    if pos < 0:
        return hay[:60]
    start = max(0, pos - 20)
    end = min(len(hay), pos + len(needle) + 40)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(hay) else ""
    return prefix + hay[start:end].replace("\n", " ") + suffix


def _int(qs: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int((qs.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _semantic_search(db: Database, semantic, q: str, limit: int) -> dict:
    """语义检索分支：引擎就绪 → 相似度降序；未就绪 → 明确原因的空结果。"""
    if semantic is None or semantic.embedder is None:
        reason = semantic.status().get("reason") if semantic is not None \
            else "服务未开启语义搜索"
        return {"total": 0, "mode": "semantic_unavailable", "q": q,
                "items": [], "error": reason}
    try:
        hits = semantic.search(q, limit)
    except Exception as exc:
        return {"total": 0, "mode": "semantic_unavailable", "q": q,
                "items": [], "error": f"{type(exc).__name__}: {exc}"}
    by_ids = db.items_by_ids([i for i, _ in hits])
    items = []
    for img_id, score in hits:
        it = by_ids.get(img_id)
        if it is None:
            continue  # 库记录刚被清理的竞态
        it["score"] = round(score, 4)
        items.append(it)
    return {"total": len(items), "mode": "semantic", "q": q, "items": items}


def build_handler(db: Database, indexer: Indexer, scan_state: ScanState,
                  web_dir: Path, thumb_dir: Path | None = None,
                  semantic=None):
    """semantic：laotu.semantic.SemanticIndex 或 None（未启用语义搜索）。"""
    web_index = Path(web_dir) / "index.html"
    thumb_root = Path(thumb_dir) if thumb_dir else (
        getattr(indexer, "thumb_dir", None) or THUMB_DIR)

    class Handler(BaseHTTPRequestHandler):
        server_version = "Laotu/0.1"

        def log_message(self, fmt: str, *args) -> None:  # 静默访问日志
            pass

        # ---------- 基础输出 ----------

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

        def _bytes(self, data: bytes, mime: str, cache: bool = False) -> None:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            if cache:
                self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _file(self, p: Path, mime: str, cache: bool = False) -> None:
            try:
                data = p.read_bytes()
            except OSError:
                self._json({"error": "not found"}, 404)
                return
            self._bytes(data, mime, cache)

        def _serve_image(self, img_id: int, thumb: bool) -> None:
            path_str = db.get_path_by_id(img_id)
            if not path_str:
                self._json({"error": "no such id"}, 404)
                return
            if thumb:
                tp = Path(thumb_root) / f"{img_id}.jpg"
                if tp.is_file():
                    self._file(tp, "image/jpeg", cache=True)
                else:
                    self._json({"error": "no thumb"}, 404)
                return
            p = Path(path_str)
            if not p.is_file():
                self._json({"error": "file gone"}, 404)
                return
            self._file(p, MIME.get(p.suffix.lower(), "application/octet-stream"), cache=True)

        # ---------- 路由 ----------

        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                if route in ("/", "/index.html"):
                    self._file(web_index, "text/html; charset=utf-8")
                elif route == "/api/search":
                    q = (qs.get("q") or [""])[0]
                    limit = _int(qs, "limit", 60, 1, 200)
                    offset = _int(qs, "offset", 0, 0, 10**9)
                    t0, t1 = qs.get("t0", [None])[0], qs.get("t1", [None])[0]
                    want_sem = (qs.get("semantic") or ["0"])[0] in ("1", "true", "yes")
                    try:
                        data = db.by_time(float(t0), float(t1), limit, offset)
                    except (TypeError, ValueError):
                        data = None
                    if data is None and want_sem and q.strip():
                        data = _semantic_search(db, semantic, q, limit)
                    if data is None:
                        data = (db.search(q, limit, offset) if q.strip()
                                else db.recent(limit, offset))
                    for item in data["items"]:
                        item["snippet"] = _snippet(
                            item.get("ocr_text", ""), item.get("name", ""), q)
                    self._json(data)
                elif route == "/api/semantic_status":
                    if semantic is not None:
                        self._json(semantic.status())
                    else:
                        self._json({"available": False,
                                    "reason": "服务未开启语义搜索（--no-semantic）"})
                elif route == "/api/dupes":
                    dist = _int(qs, "dist", 8, 0, 32)
                    groups = duplicate_groups(db.all_hashes(), dist)[:100]
                    out = []
                    for g in groups:
                        items = db.items_by_ids(g)
                        keep = max(g, key=lambda i: items.get(i, {}).get("mtime", 0))
                        out.append({"keep": keep, "ids": g, "count": len(g),
                                    "items": [items[i] for i in g if i in items]})
                    self._json({"dist": dist, "groups": out})
                elif route == "/api/timeline":
                    bucket = (qs.get("bucket") or ["day"])[0]
                    if bucket not in ("day", "hour"):
                        bucket = "day"
                    self._json({"bucket": bucket, "clusters": db.timeline(bucket)})
                elif route == "/api/stats":
                    st = db.stats()
                    st["indexing"] = scan_state.snapshot()
                    st["roots"] = db.list_roots()
                    if semantic is not None:
                        st["semantic"] = semantic.status()
                    self._json(st)
                elif route.startswith("/thumb/"):
                    self._serve_image(_id_of(route), thumb=True)
                elif route.startswith("/img/"):
                    self._serve_image(_id_of(route), thumb=False)
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:  # 兜底：任何路由异常不炸线程
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def do_POST(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            route = parsed.path
            try:
                body = self._read_json()
                if route == "/api/open":
                    self._open_image(body)
                elif route == "/api/rescan":
                    self._rescan(body)
                elif route == "/api/dirs":
                    self._add_dir(body)
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as exc:
                self._json({"error": f"{type(exc).__name__}: {exc}"}, 500)

        def _read_json(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 1_000_000:
                return {}
            raw = self.rfile.read(n)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}
            return data if isinstance(data, dict) else {}

        def _open_image(self, body: dict) -> None:
            img_id = int(body.get("id") or 0)
            path_str = db.get_path_by_id(img_id)
            if not path_str:
                self._json({"ok": False, "error": "no such id"}, 404)
                return
            p = Path(path_str)
            if not p.is_file():
                self._json({"ok": False, "error": "文件已不存在（重新扫描可清理）"})
                return
            os.startfile(path_str)  # noqa: S606 - 白名单内的用户文件
            self._json({"ok": True})

        def _rescan(self, body: dict) -> None:
            roots = body.get("dirs") or db.list_roots()
            if not roots:
                self._json({"ok": False, "error": "没有可扫描目录，先用 --dirs 指定"})
                return
            if scan_state.snapshot()["active"]:
                self._json({"ok": True, "started": False, "msg": "扫描进行中"})
                return
            threading.Thread(
                target=run_scan, args=(indexer, roots, scan_state), daemon=True
            ).start()
            self._json({"ok": True, "started": True})

        def _add_dir(self, body: dict) -> None:
            raw = str(body.get("path") or "").strip().strip('"')
            p = Path(raw)
            if not raw or not p.is_dir():
                self._json({"ok": False, "error": "目录不存在"})
                return
            db.add_root(str(p))
            threading.Thread(
                target=run_scan, args=(indexer, [str(p)], scan_state), daemon=True
            ).start()
            self._json({"ok": True, "path": str(p)})

    def _id_of(route: str) -> int:
        tail = route.rsplit("/", 1)[-1]
        try:
            return max(0, int(tail.split(".")[0]))
        except ValueError:
            return 0

    return Handler


def make_server(port: int, db: Database, indexer: Indexer,
                scan_state: ScanState, web_dir: Path,
                semantic=None) -> ThreadingHTTPServer:
    handler = build_handler(db, indexer, scan_state, web_dir, semantic=semantic)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    return server


def start_rescan_loop(db: Database, indexer: Indexer, scan_state: ScanState,
                      interval: int = 30) -> threading.Thread:
    """后台定时重扫（增量、幂等），供 start 模式使用。"""

    def loop() -> None:
        while True:
            try:
                roots = db.list_roots()
                if roots and not scan_state.snapshot()["active"]:
                    run_scan(indexer, roots, scan_state)
            except Exception:
                pass  # 后台循环永不退出
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t

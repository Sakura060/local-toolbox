"""FileFlow Web UI（本地）。

仅绑定 127.0.0.1，供本机浏览器使用：
  python -m fileflow.webui [端口]        # 默认 8635

API（全部 JSON）:
  POST /api/plan  {directory}            预览整理计划（不改动文件）
  POST /api/run   {directory}            重新计算计划并执行
  POST /api/undo  {log?: path}           撤销最近一次/指定一次整理
  GET  /api/logs                         操作日志列表

安全设计：
  - 只监听 127.0.0.1，不暴露局域网
  - POST 体积上限 64KB，JSON 解析失败返回 400
  - 页面渲染一律 textContent，杜绝 XSS
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .executor import apply_plan, list_logs, undo
from .planner import Planner, human_size
from .rules import RuleEngine, RuleError, load_rules
from .scanner import Scanner
from . import dedup

MAX_BODY = 64 * 1024

_PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")


def _load_page() -> str:
    """每次请求实时读取页面文件，便于开发期热更新。"""
    with open(_PAGE_PATH, encoding="utf-8") as fp:
        return fp.read()


class WatchController:
    """管理若干 watch 后台线程（每目录一个），收集事件供页面轮询。"""

    def __init__(self, engine: RuleEngine, scanner: Scanner):
        self.engine = engine
        self.scanner = scanner
        self._lock = threading.Lock()
        self._watches: dict[str, dict] = {}    # abs_root → {thread, stop, watcher, events}

    def start(self, directory: str, interval: float, min_age: float) -> dict:
        from .watcher import Watcher
        root = os.path.abspath(directory)
        if not os.path.isdir(root):
            raise NotADirectoryError(f"不是有效目录: {root}")
        with self._lock:
            if root in self._watches and self._watches[root]["thread"].is_alive():
                return self.status_one(root)
            stop = threading.Event()
            events: deque = deque(maxlen=50)
            watcher = Watcher(root, self.engine, self.scanner,
                              interval=max(1.0, interval), min_age=max(0.0, min_age),
                              on_event=events.append)

            def loop():
                while not stop.is_set():
                    try:
                        watcher.cycle_once()
                    except OSError as e:
                        events.append(f"扫描失败: {e}")
                    stop.wait(watcher.interval)

            t = threading.Thread(target=loop, daemon=True, name=f"ff-watch-{root}")
            self._watches[root] = {"thread": t, "stop": stop,
                                   "watcher": watcher, "events": events}
            t.start()
            return self.status_one(root)

    def stop(self, directory: str) -> dict:
        root = os.path.abspath(directory)
        with self._lock:
            w = self._watches.pop(root, None)
        if w is None:
            raise ValueError(f"该目录未在监控中: {root}")
        w["stop"].set()
        w["thread"].join(timeout=5)
        return {"stopped": root, "stats": {"cycles": w["watcher"].stats.cycles,
                                           "moved_total": w["watcher"].stats.moved_total}}

    def status_one(self, root: str) -> dict:
        w = self._watches.get(os.path.abspath(root))
        if w is None:
            return {"active": False}
        return {"active": True, "directory": root,
                "interval": w["watcher"].interval, "min_age": w["watcher"].min_age,
                "cycles": w["watcher"].stats.cycles,
                "moved_total": w["watcher"].stats.moved_total,
                "events": list(w["events"])}

    def status_all(self) -> dict:
        with self._lock:
            roots = list(self._watches.keys())
        watches = []
        for r in roots:
            st = self.status_one(r)
            if st.get("active") and not self._watches[r]["thread"].is_alive():
                continue
            watches.append(st)
        return {"watches": watches}


class _Handler(BaseHTTPRequestHandler):
    server_version = "FileFlow/0.2"
    protocol_version = "HTTP/1.1"

    # ---------- 基础工具 ----------

    def _send_json(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY:
            raise ValueError(f"请求体大小不合法: {length}")
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def log_message(self, fmt, *args):  # 安静模式
        pass

    # ---------- 路由 ----------

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_html(_load_page())
        elif path == "/api/logs":
            self._send_json({"logs": list_logs()})
        elif path == "/api/watch":
            self._send_json(_watch_controller().status_all())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            data = self._read_json()
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json({"error": f"请求体不合法: {e}"}, 400)
            return

        try:
            if path == "/api/plan":
                self._send_json(self._api_plan(data))
            elif path == "/api/run":
                self._send_json(self._api_run(data))
            elif path == "/api/undo":
                self._send_json(self._api_undo(data))
            elif path == "/api/watch/start":
                self._send_json(self._api_watch_start(data))
            elif path == "/api/watch/stop":
                self._send_json(self._api_watch_stop(data))
            elif path == "/api/dup":
                self._send_json(self._api_dup(data))
            elif path == "/api/dup-clean":
                self._send_json(self._api_dup_clean(data))
            else:
                self._send_json({"error": "not found"}, 404)
        except (NotADirectoryError, RuleError, ValueError) as e:
            self._send_json({"error": str(e)}, 400)
        except OSError as e:
            self._send_json({"error": f"文件系统错误: {e}"}, 500)

    # ---------- API 实现 ----------

    def _engine(self) -> RuleEngine:
        from .config import load_config
        cfg = load_config()
        return RuleEngine(load_rules(cfg.get("rules", []),
                                     use_defaults=bool(cfg.get("use_defaults", True)),
                                     preset=cfg.get("preset") or None))

    def _scanner(self) -> Scanner:
        from .config import load_config
        cfg = load_config()
        return Scanner(exclude_dirs=cfg.get("exclude_dirs", []),
                       exclude_patterns=cfg.get("exclude_patterns", []))

    def _build_plan(self, directory: str):
        scan = self._scanner().scan(directory)
        plan = Planner(self._engine()).build(scan)
        return scan, plan

    @staticmethod
    def _plan_payload(scan, plan) -> dict:
        moves = []
        for a in plan.moves:
            moves.append({
                "src": os.path.relpath(a.src, scan.root).replace(os.sep, "/"),
                "dst": os.path.relpath(a.dst, scan.root).replace(os.sep, "/"),
                "rule": a.rule_name,
                "size": os.path.getsize(a.src) if os.path.exists(a.src) else 0,
            })
        return {
            "root": scan.root,
            "file_count": len(scan.files),
            "total_size": scan.total_size,
            "total_size_text": human_size(scan.total_size),
            "move_count": len(moves),
            "unmatched": len(plan.unmatched),
            "conflicts_resolved": plan.conflicts_resolved,
            "moves": moves,
            "errors": scan.errors[:10],
        }

    def _api_plan(self, data: dict) -> dict:
        directory = str(data.get("directory", "")).strip()
        if not directory:
            raise ValueError("directory 不能为空")
        scan, plan = self._build_plan(directory)
        return self._plan_payload(scan, plan)

    def _api_run(self, data: dict) -> dict:
        directory = str(data.get("directory", "")).strip()
        if not directory:
            raise ValueError("directory 不能为空")
        scan, plan = self._build_plan(directory)
        preview = self._plan_payload(scan, plan)
        report = apply_plan(plan, dry_run=False)
        return {"preview": preview, "report": report.summary(),
                "moved": report.moved, "failed": report.failed,
                "failures": report.failures[:20], "log": report.log_path}

    def _api_undo(self, data: dict) -> dict:
        log = str(data.get("log", "")).strip()
        if not log:
            logs = list_logs()
            if not logs:
                return {"restored": 0, "failed": 0, "failures": [], "message": "没有可撤销的操作"}
            log = logs[0]
        rep = undo(log)
        return {"restored": rep.restored, "failed": rep.failed,
                "failures": rep.failures[:20], "log": log}

    # ---- watch API ----
    def _api_watch_start(self, data: dict) -> dict:
        directory = str(data.get("directory", "")).strip()
        if not directory:
            raise ValueError("directory 不能为空")
        try:
            interval = float(data.get("interval", 10))
        except (TypeError, ValueError):
            interval = 10
        try:
            min_age = float(data.get("min_age", 5))
        except (TypeError, ValueError):
            min_age = 5
        return _watch_controller().start(directory, interval, min_age)

    def _api_watch_stop(self, data: dict) -> dict:
        directory = str(data.get("directory", "")).strip()
        if not directory:
            raise ValueError("directory 不能为空")
        return _watch_controller().stop(directory)

    def _api_dup(self, data: dict) -> dict:
        directory = str(data.get("directory", "")).strip()
        if not directory:
            raise ValueError("directory 不能为空")
        from .config import load_config
        groups = dedup.find_duplicates(
            directory, exclude_dirs=load_config().get("exclude_dirs", []))
        out = []
        for g in groups[:50]:
            out.append({"size": g.size, "size_text": human_size(g.size),
                        "count": len(g.paths), "wasted": g.wasted,
                        "paths": [p.replace(os.sep, "/") for p in g.paths]})
        return {"root": os.path.abspath(directory), "groups": out,
                "group_count": len(groups),
                "wasted_total": sum(g.wasted for g in groups),
                "wasted_total_text": human_size(sum(g.wasted for g in groups))}

    def _api_dup_clean(self, data: dict) -> dict:
        directory = str(data.get("directory", "")).strip()
        if not directory:
            raise ValueError("directory 不能为空")
        from .config import load_config
        groups = dedup.find_duplicates(
            directory, exclude_dirs=load_config().get("exclude_dirs", []))
        plan = dedup.plan_cleanup(groups, keep="oldest")
        import datetime as dt
        import shutil
        root = os.path.abspath(directory)
        dest_dir = os.path.join(root, ".fileflow-trash", "duplicates",
                                dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        moved, failed, failures = 0, 0, []
        for dup, keeper in plan:
            try:
                os.makedirs(dest_dir, exist_ok=True)
                base = os.path.basename(dup)
                dst = os.path.join(dest_dir, base)
                i = 1
                while os.path.exists(dst):
                    stem, dot, suffix = base.rpartition(".")
                    if not dot or stem == "":
                        stem, suffix = base, ""
                    dst = os.path.join(dest_dir, f"{stem} ({i}).{suffix}" if suffix
                                       else f"{stem} ({i})")
                    i += 1
                shutil.move(dup, dst)
                moved += 1
            except OSError as e:
                failed += 1
                failures.append(f"{dup}: {e}")
        return {"moved": moved, "failed": failed, "failures": failures[:20],
                "trash": dest_dir.replace(os.sep, "/")}


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        # 客户端提前断开（浏览器刷新/取消）属正常现象，不刷屏
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


# ---- watch 控制器单例（测试可直接替换 webui._watch_ctrl 注入隔离实例） ----
_watch_ctrl: WatchController | None = None
_watch_ctrl_lock = threading.Lock()


def _watch_controller() -> WatchController:
    global _watch_ctrl
    with _watch_ctrl_lock:
        if _watch_ctrl is None:
            from .config import load_config
            cfg = load_config()
            engine = RuleEngine(load_rules(
                cfg.get("rules", []),
                use_defaults=bool(cfg.get("use_defaults", True)),
                preset=cfg.get("preset") or None))
            scanner = Scanner(exclude_dirs=cfg.get("exclude_dirs", []),
                              exclude_patterns=cfg.get("exclude_patterns", []))
            _watch_ctrl = WatchController(engine, scanner)
        return _watch_ctrl


def make_server(host: str, port: int) -> _Server:
    """构建 Web 服务器实例（便于测试与嵌入）。"""
    return _Server((host, port), _Handler)


def serve(port: int = 8635) -> None:
    server = make_server("127.0.0.1", port)
    print(f"FileFlow Web UI: http://127.0.0.1:{port}/  (Ctrl+C 退出)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        server.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8635
    serve(p)

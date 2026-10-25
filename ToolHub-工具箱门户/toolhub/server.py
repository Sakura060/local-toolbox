"""ToolHub HTTP 服务：仪表盘页面 + 状态探测 + 联邦搜索 + 只读代理 + 启动器。

端点（全部只读，唯一写操作是 POST /api/launch）：
- GET  /                  仪表盘页面（每请求实时读盘，页面可热改）
- GET  /api/tools         注册表静态信息
- GET  /api/status        并行探测全部工具 -> [{id, online, ms}]
- GET  /api/federated?q=  联邦搜索扇出
- POST /api/launch        {id} -> 用新控制台窗口拉起该工具的启动 .bat
- GET  /proxy/<id>/<path> 段边界白名单内的只读透传（捞图缩略图、NoteNest 笔记等）
"""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import federated, proxy
from . import procs
from .registry import TOOLS, Tool, path_allowed

PAGE_PATH = Path(__file__).resolve().parent / "page.html"
LAUNCH_TIMEOUT_S = 15  # 启动后给上游的就绪宽限（仅用于日志提示，不做阻塞等待）


def spawn_bat(bat_path: Path) -> subprocess.Popen:
    """新控制台窗口拉起启动脚本，返回 Popen（.pid 可用）。路径须来自注册表白名单。"""
    if not bat_path.is_file():
        raise FileNotFoundError(f"启动脚本不存在: {bat_path.name}")
    return subprocess.Popen(
        ["cmd", "/c", str(bat_path)],
        cwd=str(bat_path.parent),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def probe_all(tools: tuple[Tool, ...]) -> list[dict]:
    """并行探测（在线工具再并行拉一条待办徽标），总耗时约等于最慢一个而不是求和。"""
    results: dict[str, dict] = {}
    lock = threading.Lock()

    def work(t: Tool) -> None:
        online, ms = proxy.probe(t.port, "/")
        entry = {"id": t.id, "online": online, "ms": ms, "note": None}
        if online:
            note = federated.tool_note(t)
            if note:
                entry["note"] = note
        with lock:
            results[t.id] = entry

    threads = [threading.Thread(target=work, args=(t,), daemon=True) for t in tools]
    for th in threads:
        th.start()
    for th in threads:
        th.join(proxy.PROBE_TIMEOUT + federated.FED_TIMEOUT + 0.5)
    return [results.get(t.id, {"id": t.id, "online": False, "ms": 0, "note": None})
            for t in tools]


def stop_tool(port: int) -> list[int]:
    """按端口反查监听进程并强制结束（进程树）。返回成功结束的 PID。"""
    return procs.kill_pids(procs.find_port_pids(port))


def make_server(port: int, tools: tuple[Tool, ...] = TOOLS,
                launcher=spawn_bat, stopper=stop_tool) -> ThreadingHTTPServer:
    """工厂：tools/launcher/stopper 可注入，测试用假件，不碰真实进程。"""

    by_id: dict[str, Tool] = {t.id: t for t in tools}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ToolHub/0.1"

        def log_message(self, fmt, *args):  # 安静模式：只留错误
            pass

        # ---------- 基础 ----------
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def _error(self, status: int, msg: str) -> None:
            self._json({"error": msg}, status)

        def _query(self) -> dict[str, str]:
            qs = parse_qs(urlparse(self.path).query)
            return {k: v[0] for k, v in qs.items() if v}

        # ---------- 路由 ----------
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/" or path == "/index.html":
                body = PAGE_PATH.read_bytes()
                self._send(200, body, "text/html; charset=utf-8")
            elif path == "/api/tools":
                self._json({"tools": [_tool_view(t) for t in tools]})
            elif path == "/api/status":
                self._json({"status": probe_all(tools)})
            elif path == "/api/federated":
                q = self._query().get("q", "")
                limit = self._query().get("limit", "12")
                try:
                    limit_n = max(1, min(50, int(limit)))
                except ValueError:
                    limit_n = 12
                search_set = tuple(t for t in tools if t.search)
                self._json(federated.federated_search(q, tools=search_set, limit=limit_n))
            elif path == "/api/recent":
                per = self._query().get("per", "5")
                try:
                    per_n = max(1, min(20, int(per)))
                except ValueError:
                    per_n = 5
                search_set = tuple(t for t in tools if t.search)
                self._json(federated.recent(search_set, per_tool=per_n))
            elif path.startswith("/proxy/"):
                self._handle_proxy(path)
            else:
                self._error(404, "not found")

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path not in ("/api/launch", "/api/stop"):
                self._error(404, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 64 * 1024:
                # 排空小残包防连接复用竞态（NoteNest 同款坑）；过大直接断开
                if 0 < length <= 1024 * 1024:
                    self.rfile.read(length)
                self.close_connection = True
                self._error(400, "请求体不合法")
                return
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._error(400, "请求体不合法")
                return
            if path == "/api/stop":
                self._handle_stop(data)
                return
            tid = data.get("id") if isinstance(data, dict) else None
            tool = by_id.get(tid) if isinstance(tid, str) else None
            if tool is None:
                self._error(400, "未知工具 id")
                return
            if not tool.bat:
                self._error(400, "该工具没有启动脚本")
                return
            try:
                proc = launcher(tool.bat_path)
            except FileNotFoundError as exc:
                self._error(500, str(exc))
                return
            except OSError as exc:
                self._error(500, f"启动失败: {type(exc).__name__}")
                return
            self._json({"ok": True, "pid": proc.pid,
                        "url": tool.url,
                        "hint": f"服务就绪一般需要几秒，稍候刷新状态（宽限 {LAUNCH_TIMEOUT_S}s）"})

        def _handle_stop(self, data: dict) -> None:
            tool = by_id.get(data.get("id")) if isinstance(data.get("id"), str) else None
            if tool is None:
                self._error(400, "未知工具 id")
                return
            try:
                killed = stopper(tool.port)
            except OSError as exc:
                self._error(500, f"停止失败: {type(exc).__name__}")
                return
            if killed:
                self._json({"ok": True, "killed": killed})
            else:
                self._json({"ok": False, "error": "该工具没有在运行（或进程无法结束）"})

        def _handle_proxy(self, path: str) -> None:
            rest = path[len("/proxy/"):]
            tid, _, sub = rest.partition("/")
            tool = by_id.get(tid)
            if tool is None or not sub:
                self._error(404, "not found")
                return
            if not path_allowed("/" + sub, tool.proxy) or ".." in sub:
                self._error(403, "路径不在该工具的透传白名单")
                return
            qs = urlparse(self.path).query
            target = "/" + sub + ("?" + qs if qs else "")
            try:
                res = proxy.fetch(tool.port, target)
            except proxy.UpstreamError as exc:
                self._error(502, f"上游不可达: {exc}")
                return
            extra = [("X-ToolHub-Truncated", "1")] if res.truncated else []
            self.send_response(res.status)
            self.send_header("Content-Type", res.content_type)
            self.send_header("Content-Length", str(len(res.body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(res.body)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def _tool_view(t: Tool) -> dict:
    return {"id": t.id, "name": t.name, "desc": t.desc, "port": t.port,
            "url": t.url, "bat": t.bat, "search": t.search, "icon": t.icon}

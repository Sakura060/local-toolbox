"""桌面窗口壳：三档降级，保证"总有一个像样的窗口"。

1. pywebview（WebView2 内核，最像原生应用）
2. Edge --app 模式（独立无标签窗口，零 Python 依赖）
3. 默认浏览器（最后兜底）

壳不承担任何业务：HTTP 服务在 server 线程里跑，壳只负责开窗口和等退出。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

from .server import make_server

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_edge() -> Path | None:
    for raw in EDGE_CANDIDATES:
        p = Path(os.path.expandvars(raw))
        if p.is_file():
            return p
    return None


def _serve(port: int) -> ThreadingHTTPServer:
    httpd = make_server(port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class Bridge:
    """pywebview js_api：原生文件/文件夹选择对话框（导入向导用）。
    仅 webview 窗口模式存在；Edge/浏览器降级时前端自动退回"粘贴路径"。"""

    def pick_file(self, filters=None):
        import webview
        wins = webview.windows
        if not wins:
            return ""
        result = wins[0].create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False)
        if isinstance(result, (list, tuple)) and result:
            return str(result[0])
        return ""

    def pick_folder(self):
        import webview
        wins = webview.windows
        if not wins:
            return ""
        result = wins[0].create_file_dialog(webview.FOLDER_DIALOG)
        if isinstance(result, (list, tuple)) and result:
            return str(result[0])
        return ""


def _log(msg: str) -> None:
    """壳的降级/异常写日志（pythonw 下无控制台，排障全靠它）。"""
    try:
        log = Path(__file__).resolve().parent.parent / "data" / "desktop.log"
        log.parent.mkdir(exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def _webview_window(url: str, title: str) -> bool:
    """真窗口。返回 True = 用户正常关闭窗口（进程该退出了）；
    False = 没起来（降级 Edge/浏览器）。"""
    try:
        import webview  # pywebview
    except Exception as exc:  # noqa: BLE001 ImportError 及其依赖链的任何加载错误
        _log(f"pywebview 导入失败: {type(exc).__name__}: {exc}")
        return False
    try:
        window = webview.create_window(
            title, url, width=1280, height=840, min_size=(960, 620),
            js_api=Bridge())
        sentinel_kill = {"kill": False, "reason": ""}

        # WebView2 首次初始化（建用户数据目录、加载运行时）可能要好几秒：
        # 哨兵给足 10 秒，loaded 事件没来才主动销毁走降级（3 秒曾误杀过）。
        def sentinel():
            for _ in range(100):
                time.sleep(0.1)
                try:
                    if window.events.loaded.is_set():
                        return
                except Exception:  # noqa: BLE001
                    return
            sentinel_kill["kill"] = True
            sentinel_kill["reason"] = "WebView2 10 秒内未完成首次加载"
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass
        threading.Thread(target=sentinel, daemon=True).start()
        webview.start()
        if sentinel_kill["kill"]:
            _log(f"pywebview 降级: {sentinel_kill['reason']}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"pywebview 启动异常: {type(exc).__name__}: {exc}")
        return False


def _edge_window(url: str) -> bool:
    edge = find_edge()
    if edge is None:
        return False
    profile = Path(__file__).resolve().parent.parent / "data" / "edge-profile"
    try:
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [str(edge), "--app=" + url,
             f"--user-data-dir={profile}",
             "--no-first-run", "--no-default-browser-check",
             "--window-size=1280,840"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except OSError:
        return False


def main(port: int = 8650, prefer: str = "auto") -> int:
    title = "ToolHub 工具箱"
    try:
        httpd = _serve(port)
    except OSError as exc:
        _log(f"端口 {port} 起不来（可能已有实例）: {exc}")
        print(f"端口 {port} 起不来（可能桌面版已在运行）：{exc}")
        # 已有实例 → 只是把窗口再拉出来
        url = f"http://127.0.0.1:{port}/?shell=1"
        if not _edge_window(url):
            webbrowser.open(url)
        return 0
    url = f"http://127.0.0.1:{port}/?shell=1"

    mode = None
    if prefer in ("auto", "webview"):
        if _webview_window(url, title):
            mode = "webview-closed"  # 窗口正常打开又正常关闭 → 直接退出
    if mode is None and prefer in ("auto", "edge"):
        if _edge_window(url):
            mode = "edge"
    if mode is None:
        webbrowser.open(url)
        mode = "browser"
    _log(f"窗口模式: {mode}")

    print(f"ToolHub Desktop {url} （窗口模式: {mode}；Ctrl+C 退出）")
    if mode == "webview-closed":
        httpd.shutdown()
        httpd.server_close()
        return 0
    try:
        threading.Event().wait()  # 服务挂住主进程；关窗后用户在此 Ctrl+C
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8650))

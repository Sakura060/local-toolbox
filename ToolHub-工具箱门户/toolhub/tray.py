"""ToolHub 托盘常驻：服务线程 + pystray 托盘图标。

- pythonw 启动时无控制台，日志写 data/tray.log
- pystray 缺失时优雅降级：无图标但服务照常常驻
- 菜单"启动工具"先探测在线：在线直接开浏览器，离线拉起 bat（新控制台）
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

from .server import make_server, spawn_bat
from . import proxy
from .registry import TOOLS, Tool

LOG_NAME = "tray.log"


def _log(root: Path, msg: str) -> None:
    try:
        (root / "data").mkdir(exist_ok=True)
        with (root / "data" / LOG_NAME).open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def open_tool(tool: Tool) -> None:
    online, _ = proxy.probe(tool.port, "/")
    if online:
        webbrowser.open(tool.url)
    elif tool.bat:
        spawn_bat(tool.bat_path)


def build_icon(root: Path, port: int):
    import pystray
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (64, 64), (37, 99, 235))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 61, 61], radius=14, fill=(37, 99, 235))
    d.text((22, 14), "T", fill=(255, 255, 255))

    def open_panel(icon=None, item=None):
        webbrowser.open(f"http://127.0.0.1:{port}/")

    def make_launcher(t: Tool):
        def run(icon=None, item=None):
            open_tool(t)
        return run

    items = [pystray.MenuItem("打开面板", open_panel, default=True),
             pystray.Menu.SEPARATOR]
    items += [pystray.MenuItem(f"{t.icon} {t.name}", make_launcher(t)) for t in TOOLS]
    items += [pystray.Menu.SEPARATOR,
              pystray.MenuItem("退出", lambda icon=None, item=None: icon.stop())]
    return pystray.Icon("ToolHub", img, "ToolHub 工具箱", menu=pystray.Menu(*items))


def main(port: int = 8642) -> None:
    root = Path(__file__).resolve().parent.parent
    try:
        httpd = make_server(port)
    except OSError as exc:
        _log(root, f"[tray] 端口 {port} 起不来（可能已有实例）: {exc}")
        return
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    _log(root, f"[tray] serving at http://127.0.0.1:{port}/")
    try:
        icon = build_icon(root, port)
    except Exception as exc:  # pystray 缺失/初始化失败 → 无图标降级
        _log(root, f"[tray] 托盘不可用（{type(exc).__name__}），服务保持常驻")
        threading.Event().wait()  # 挂住主线程，服务线程继续
        return
    icon.run()  # 阻塞主线程（Windows 托盘要求）
    httpd.shutdown()

"""ToolHub 命令行入口。

  python cli.py serve [--port 8642] [--no-browser]   门户服务（v0.11 保留）
  python cli.py tray [--port 8642]                   托盘常驻（建议 pythonw 跑）
  python cli.py desktop [--port 8650] [--prefer ...] 桌面版窗口（新增）
  python cli.py install-autostart                    写入开机自启（当前用户）
  python cli.py uninstall-autostart                  移除开机自启
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from toolhub import __version__
from toolhub.server import make_server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="toolhub", description="ToolHub 本地工具箱门户")
    p.add_argument("--version", action="version", version=f"ToolHub {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="启动门户服务")
    serve.add_argument("--port", type=int, default=8642)
    serve.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")

    tray = sub.add_parser("tray", help="托盘常驻（无图标则降级为后台服务）")
    tray.add_argument("--port", type=int, default=8642)

    desk = sub.add_parser("desktop", help="桌面版窗口（pywebview，失败降级 Edge/浏览器）")
    desk.add_argument("--port", type=int, default=8650)
    desk.add_argument("--prefer", choices=("auto", "webview", "edge", "browser"),
                      default="auto", help="窗口模式（测试/排障时用）")
    desk.add_argument("--server-only", action="store_true",
                      help="只起 HTTP 服务不开窗口（e2e/测试用）")

    ins = sub.add_parser("install-autostart", help="写入开机自启（当前用户 Startup 文件夹）")
    uni = sub.add_parser("uninstall-autostart", help="移除开机自启")
    insd = sub.add_parser("install-desktop-autostart",
                          help="桌面版开机自启（登录后自动弹工具箱窗口）")
    unid = sub.add_parser("uninstall-desktop-autostart", help="移除桌面版开机自启")

    args = p.parse_args(argv)

    if args.cmd == "tray":
        from toolhub import tray as tray_mod
        tray_mod.main(args.port)
        return 0
    if args.cmd == "desktop":
        if args.server_only:
            from desktop.server import make_server
            httpd = make_server(args.port)
            print(f"ToolHub Desktop serving at http://127.0.0.1:{args.port}/")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nbye")
            finally:
                httpd.server_close()
            return 0
        from desktop import shell
        return shell.main(args.port, prefer=args.prefer)
    if args.cmd == "install-autostart":
        from toolhub import autostart
        root = __import__("pathlib").Path(__file__).resolve().parent
        vbs = autostart.install(root)
        print(f"已写入开机自启: {vbs}")
        print("下次登录时 ToolHub 将以托盘方式常驻；移除：python cli.py uninstall-autostart")
        return 0
    if args.cmd == "uninstall-autostart":
        from toolhub import autostart
        ok = autostart.uninstall()
        print("已移除开机自启" if ok else "本来就没有安装开机自启")
        return 0
    if args.cmd == "install-desktop-autostart":
        from pathlib import Path
        from localcore import autostart as lc_autostart
        root = Path(__file__).resolve().parent
        vbs = lc_autostart.install(root, "toolhub_desktop.vbs", args="desktop")
        print(f"已写入桌面版开机自启: {vbs}")
        print("下次登录将自动打开工具箱窗口；移除：python cli.py uninstall-desktop-autostart")
        return 0
    if args.cmd == "uninstall-desktop-autostart":
        from localcore import autostart as lc_autostart
        ok = lc_autostart.uninstall("toolhub_desktop.vbs")
        print("已移除桌面版开机自启" if ok else "本来就没有安装桌面版开机自启")
        return 0

    httpd = make_server(args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"ToolHub {__version__} serving at {url}")
    print("Ctrl+C 停止")
    if sys.stdout.isatty() and not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

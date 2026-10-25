"""ClipVault 命令行入口。

  python cli.py serve [--port 8643] [--interval 1.5] [--no-browser] [--no-capture]
  python cli.py pause | resume          暂停/恢复采集
  python cli.py status                  条数统计
  python cli.py clear [--all]           清空（默认保留置顶）
  python cli.py read-once [--show]      读一次当前剪贴板（只报类型和长度，--show 显示内容）
  python cli.py install-autostart       开机自启（Startup 放 VBS，隐藏窗口起 serve）
  python cli.py uninstall-autostart     移除开机自启
"""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from clipvault import __version__
from clipvault.store import Store, RETENTION_DEFAULT

ROOT = Path(__file__).resolve().parent


class _NullIO:
    """pythonw（无窗口常驻）下 stdout/stderr 是 None，print 会崩 → 空输出替身。"""

    def write(self, *_a):
        return None

    def flush(self):
        return None

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _NullIO()
if sys.stderr is None:
    sys.stderr = _NullIO()


def make_store() -> Store:
    return Store(ROOT / "data" / "vault.db", ROOT / "data" / "images")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clipvault", description="ClipVault 剪贴板时间线")
    p.add_argument("--version", action="version", version=f"ClipVault {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="采集 + 时间线 Web")
    serve.add_argument("--port", type=int, default=8643)
    serve.add_argument("--interval", type=float, default=1.5)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument("--no-capture", action="store_true", help="不起采集线程（仅查看）")

    sub.add_parser("pause", help="暂停采集")
    sub.add_parser("resume", help="恢复采集")
    sub.add_parser("status", help="统计")

    clear = sub.add_parser("clear", help="清空时间线")
    clear.add_argument("--all", action="store_true", help="连置顶一起删")

    ro = sub.add_parser("read-once", help="读一次当前剪贴板（默认只报类型/长度）")
    ro.add_argument("--show", action="store_true", help="显示文本内容（隐私自负）")

    sub.add_parser("install-autostart", help="开机自启（当前用户 Startup 文件夹）")
    sub.add_parser("uninstall-autostart", help="移除开机自启")

    args = p.parse_args(argv)
    store = make_store()

    if args.cmd == "pause":
        store.set_paused(True)
        print("采集已暂停")
        return 0
    if args.cmd == "resume":
        store.set_paused(False)
        print("采集已恢复")
        return 0
    if args.cmd == "status":
        s = store.stats()
        print(f"共 {s['total']} 条：文本 {s['by_kind'].get('text', 0)} / "
              f"图片 {s['by_kind'].get('image', 0)} / 文件 {s['by_kind'].get('files', 0)}"
              f" · 采集{'已暂停' if s['paused'] else '进行中'}")
        return 0
    if args.cmd == "clear":
        n = store.clear(keep_pinned=not args.all)
        print(f"已删除 {n} 条")
        return 0
    if args.cmd == "read-once":
        from clipvault.clip import read_snapshot
        kind, body, image_bytes = read_snapshot()
        if kind is None:
            print("剪贴板为空或内容不支持")
            return 0
        size = len(body.encode("utf-8")) if kind != "image" else len(image_bytes)
        print(f"类型: {kind} · 大小: {size} 字节")
        if args.show and kind != "image":
            print(body[:500])
        elif args.show and kind == "image":
            print("（图片内容不打印）")
        return 0

    if args.cmd == "install-autostart":
        sys.path.insert(0, str(ROOT.parent))  # localcore 在工作区根（D23 约定）
        from localcore import autostart
        vbs = autostart.install(ROOT, "clipvault_serve.vbs", "serve")
        print(f"已写入开机自启: {vbs}")
        print("下次登录时 ClipVault 将隐藏常驻（暂停状态以库里的为准）；移除：python cli.py uninstall-autostart")
        return 0
    if args.cmd == "uninstall-autostart":
        sys.path.insert(0, str(ROOT.parent))
        from localcore import autostart
        ok = autostart.uninstall("clipvault_serve.vbs")
        print("已移除开机自启" if ok else "本来就没有安装开机自启")
        return 0

    # serve
    from clipvault.clip import Poller
    from clipvault.server import make_server

    if not args.no_capture:
        poller = Poller(store, ROOT / "data" / "images", interval=args.interval)
        poller.start()
        print(f"采集线程已启动（每 {args.interval}s 检查一次）")
    else:
        print("采集线程未启动（--no-capture）")
    httpd = make_server(args.port, store, ROOT / "data" / "images")
    url = f"http://127.0.0.1:{args.port}/"
    print(f"ClipVault {__version__} serving at {url}")
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

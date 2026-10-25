"""捞图 CLI。

用法：
  python cli.py index <目录> [<目录>...]        # 建索引（增量、可重复执行）
  python cli.py serve [--port 8637] [--dirs ...] # 只起服务（不自动扫描）
  python cli.py start [--port 8637] [--dirs ...] [--interval 30]  # 服务 + 后台定时重扫
  python cli.py stats                            # 查看索引统计
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

if not sys.stdout.isatty():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from laotu import config
from laotu.db import Database
from laotu.indexer import Indexer
from laotu.server import ScanState, make_server, run_scan, start_rescan_loop
from laotu.web import WEB_DIR


def _open_browser_later(url: str, delay: float = 1.5) -> None:
    """服务就绪后自动打开浏览器（双击启动场景）。"""
    def _open():
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    threading.Timer(delay, _open).start()


def _open_resources(dirs: list[str] | None) -> tuple[Database, Indexer, ScanState]:
    config.ensure_data_dirs()
    db = Database(config.DB_PATH)
    indexer = Indexer(db)
    state = ScanState()
    if dirs:
        for d in dirs:
            p = Path(d)
            if not p.is_dir():
                print(f"[跳过] 目录不存在：{d}")
                continue
            db.add_root(str(p))
    return db, indexer, state


def _resolve_dirs(dirs: list[str] | None, db: Database) -> list[str]:
    if dirs:
        return [str(Path(d)) for d in dirs if Path(d).is_dir()]
    roots = db.list_roots()
    return roots or config.default_watch_dirs()


def cmd_index(args) -> int:
    db, indexer, state = _open_resources(args.dirs)
    dirs = _resolve_dirs(args.dirs, db)
    if not dirs:
        print("没有可扫描目录。用法：python cli.py index <目录>")
        return 2
    print(f"待扫描目录：{len(dirs)} 个")
    for d in dirs:
        print(f"  - {d}")

    last_print = [0]

    def progress(done: int, total: int, current: str) -> None:
        if done - last_print[0] >= 20 or done == total:
            last_print[0] = done
            print(f"[{done}/{total}] {current}", flush=True)

    summary = indexer.scan(dirs, progress=progress)
    print(
        f"完成：共 {summary['total']} 张 | 处理 {summary['processed']} "
        f"(更新 {summary['updated']}, 失败 {summary['errors']}) | "
        f"跳过 {summary['skipped']} | 清理失效 {summary['purged']}"
    )
    st = db.stats()
    print(f"库中现有 {st['total']} 张（识别成功 {st['ocr_ok']}，失败 {st['errors']}）")
    indexer.close()
    db.close()
    return 0


def cmd_serve(args) -> int:
    db, indexer, state = _open_resources(args.dirs)
    if not db.list_roots() and config.default_watch_dirs():
        for d in config.default_watch_dirs():
            db.add_root(d)
    server = make_server(args.port, db, indexer, state, WEB_DIR)
    if args.dirs:
        threading.Thread(
            target=run_scan, args=(indexer, _resolve_dirs(args.dirs, db), state),
            daemon=True,
        ).start()
    print(f"捞图已启动：http://127.0.0.1:{args.port}  （Ctrl+C 退出）")
    _open_browser_later(f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_start(args) -> int:
    db, indexer, state = _open_resources(args.dirs)
    if not db.list_roots():
        auto = config.default_watch_dirs()
        for d in auto:
            db.add_root(d)
        if auto:
            print(f"未指定目录，自动监控：{auto}")
    dirs = db.list_roots()
    if not dirs:
        print("没有可扫描目录。启动时加 --dirs <目录> 或用网页右上角“＋ 目录”。")
    else:
        threading.Thread(target=run_scan, args=(indexer, dirs, state), daemon=True).start()
    start_rescan_loop(db, indexer, state, interval=args.interval)
    server = make_server(args.port, db, indexer, state, WEB_DIR)
    print(f"捞图已启动：http://127.0.0.1:{args.port}  （后台自动重扫，每 {args.interval}s）")
    _open_browser_later(f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_stats(args) -> int:
    db, _, _ = _open_resources(None)
    st = db.stats()
    print(f"图片总数：{st['total']}")
    print(f"识别成功：{st['ocr_ok']}")
    print(f"识别失败：{st['errors']}")
    print("监控目录：")
    for r in db.list_roots():
        print(f"  - {r}")
    db.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="laotu", description="捞图 — 截图视觉记忆库")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("index", help="建立/增量更新索引")
    p.add_argument("dirs", nargs="*", help="要扫描的目录（默认用已登记目录）")
    p.set_defaults(fn=cmd_index)

    p = sub.add_parser("serve", help="启动 Web 界面")
    p.add_argument("--port", type=int, default=8637)
    p.add_argument("--dirs", nargs="*", default=None)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("start", help="启动 Web 界面 + 后台定时重扫")
    p.add_argument("--port", type=int, default=8637)
    p.add_argument("--dirs", nargs="*", default=None)
    p.add_argument("--interval", type=int, default=30)
    p.set_defaults(fn=cmd_start)

    p = sub.add_parser("stats", help="查看索引统计")
    p.set_defaults(fn=cmd_stats)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

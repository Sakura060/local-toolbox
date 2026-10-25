"""文件户口本 CLI。

用法：
  python cli.py scan [<目录>...] [--no-browser]  # 盘点来源（默认下载夹）
  python cli.py watch [--volumes C D] [--interval 2]  # 持续记账（需管理员）
  python cli.py deep [--volumes C D] [--history]  # 全量 MFT 快照+历史回溯（需管理员）
  python cli.py query <文件路径>                 # 查一个文件的身世（含谱系）
  python cli.py search <关键词>                  # 按文件名搜来源库
  python cli.py serve [--port 8638]              # Web 查询界面
  python cli.py stats
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

if not sys.stdout.isatty():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from hukou import ads, classify, collector, mft, usn
from hukou.config import DB_PATH, default_scan_dirs, ensure_data_dir
from hukou.db import Database
from hukou.server import make_server
from hukou.web import WEB_DIR


def _open_browser_later(url: str, delay: float = 1.5) -> None:
    def _open():
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    threading.Timer(delay, _open).start()


def _db() -> Database:
    ensure_data_dir()
    return Database(DB_PATH)


def cmd_scan(args) -> int:
    db = _db()
    paths = args.paths or default_scan_dirs()
    if not paths:
        print("没有可盘点目录。用法：python cli.py scan <目录>")
        return 2
    print(f"盘点目录：{paths}")

    def progress(n, cur):
        print(f"  已检查 {n} 个文件…", flush=True)

    summary = collector.scan_origins(
        paths, db, use_browser=not args.no_browser, progress=progress
    )
    st = db.stats()
    print(
        f"完成：检查 {summary['files']} 个文件，归因 {summary['attributed']} 个；"
        f"浏览器下载记录 {summary['browser_records']} 条"
    )
    print(f"来源库现有 {st['origins']} 条（{st['by_type']}）")
    db.close()
    return 0


def cmd_watch(args) -> int:
    if not usn.is_admin():
        print("需要管理员权限（USN Journal 是系统级接口）。")
        print("请右键“以管理员身份运行”，或在管理员 cmd 中执行本命令。")
        return 2
    volumes = [v.upper()[0] for v in (args.volumes or [])]
    if not volumes:
        volumes = usn.fixed_ntfs_volumes()
    if not volumes:
        print("没有可用的 NTFS 卷（或权限不足）。")
        return 2
    db = _db()
    print(f"开始记账卷：{volumes}，每 {args.interval}s 读一轮。Ctrl+C 退出。")
    # 先从当前头开始（不回溯）；历史重建请用 snapshot 命令
    for v in volumes:
        if db.get_usn_cursor(v) == 0:
            try:
                h = usn.open_volume(v)
                _, next_usn = usn.get_journal_info(h)
                usn.kernel32.CloseHandle(h)
                db.set_usn_cursor(v, next_usn)
            except usn.UsnAccessError as exc:
                print(f"[{v}] {exc}")
                return 2
    stop = threading.Event()
    try:
        collector.watch_loop(volumes, db, interval=args.interval, stop=stop)
    except KeyboardInterrupt:
        stop.set()
    st = db.stats()
    print(f"退出。事件库共 {st['events']} 条。")
    db.close()
    return 0


def cmd_deep(args) -> int:
    if not usn.is_admin():
        print("需要管理员权限（整卷 MFT 枚举是系统级只读接口）。")
        print("请右键“以管理员身份运行”，或在管理员 cmd 中执行本命令。")
        return 2
    volumes = [v.upper()[0] for v in (args.volumes or [])] or usn.fixed_ntfs_volumes()
    if not volumes:
        print("没有可用的 NTFS 卷。")
        return 2
    db = _db()
    for v in volumes:
        print(f"[{v}] 全量快照中（FSCTL_ENUM_USN_DATA，只读）…", flush=True)
        t0 = time.time()
        n = 0
        buf: list[dict] = []
        for rec in usn.enum_volume_records(v):
            buf.append(rec)
            if len(buf) >= 50000:
                n += db.store_files(v, buf)
                buf.clear()
                print(f"  已入快照 {n} 个文件…", flush=True)
        if buf:
            n += db.store_files(v, buf)
        took = time.time() - t0
        db.set_meta(f"snapshot_{v}", f"{time.time():.0f}|{n}")
        print(f"[{v}] 快照完成：{n} 个文件，{took:.1f}s，{n / max(took, 0.001):.0f} 文件/秒")
        if args.history:
            try:
                h = usn.open_volume(v)
                info = usn.get_journal_info_full(h)
                usn.kernel32.CloseHandle(h)
            except usn.UsnAccessError as exc:
                print(f"[{v}] 历史读取失败：{exc}")
                continue
            start = info["lowest_valid_usn"]
            print(f"[{v}] 回溯日志 {start} → {info['next_usn']} …", flush=True)
            events, cursor = usn.read_journal_range(
                v, start, max_records=args.max_history
            )
            added = db.add_events(v, events)
            db.set_meta(f"history_{v}", f"{time.time():.0f}|{added}")
            print(f"[{v}] 历史事件入库 {added} 条（新增；重复自动忽略）")
    st = db.stats()
    print(f"完成。事件库 {st['events']} 条；快照 {st['files_by_vol']}")
    db.close()
    return 0


def _resolve_frn(db: Database, path: Path) -> tuple[str, int] | None:
    """把完整路径解析为 (卷字母, frn)。需要该卷已有 deep 快照。"""
    vol = path.drive[:1].upper()
    if not vol or db.count_files(vol) == 0:
        return None
    parts = [p for p in path.parts[1:] if p]  # 去掉盘符与空段
    frn = usn_root = 5
    for seg in parts:
        nxt = db.find_child(vol, usn_root, seg)
        if nxt is None:
            return None
        usn_root = nxt
    return vol, usn_root


def cmd_query(args) -> int:
    db = _db()
    p = Path(args.path)
    print(f"文件：{p}")
    if p.is_file():
        st = p.stat()
        print(f"  大小：{st.st_size} 字节；修改时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}")
        zone = ads.read_zone_identifier(str(p))
        if zone:
            src, detail = ads.origin_from_zone(zone)
            print(f"  来源（ADS 实证）：{src} — {detail}")
        else:
            print("  来源（ADS）：无 Zone.Identifier（本机生成/复制的文件）")
        guess = classify.guess_origin(str(p))
        if guess:
            print(f"  来源（目录推测）：{guess[0]} — {guess[1]}")
    else:
        print("  （文件当前不存在）")
    rec = db.lookup_origin(str(p))
    if rec:
        print(f"  来源（历史库）：[{rec['confidence']}] {rec['src_type']} — {rec['detail']}")
    events = db.events_for_name(p.name)
    if events:
        print(f"  事件时间线（自记账起，{len(events)} 条）：")
        for e in events[:10]:
            t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["ts"] or 0))
            names = usn.reason_names(int(e["reasons"]))
            print(f"    {t}  {'/'.join(names) or str(e['reasons'])}")
    else:
        print("  事件时间线：暂无（watch 记账从启动后才开始）")
    resolved = _resolve_frn(db, p)
    if resolved:
        vol, frn = resolved
        events = db.events_for_frn(vol, frn)
        for e in events:
            e.setdefault("reason", e.get("reasons", 0))
        chain = mft.lineage_from_events(events)
        if chain:
            print(f"  谱系链（历史回溯，{len(chain)} 步）：")
            for c in chain:
                t = time.strftime("%Y-%m-%d %H:%M", time.localtime(c["ts"] or 0))
                print(f"    {t}  {c['action']} → {c['name']}")
        else:
            print("  谱系链：日志范围内无该文件（frn）的改名/移动记录")
    db.close()
    return 0


def cmd_search(args) -> int:
    db = _db()
    rows = db.search_origins(args.keyword)
    if not rows:
        print("没有命中。先 scan 盘点，或换个关键词。")
        db.close()
        return 0
    for r in rows:
        mark = {"ads": "★", "browser": "☆", "dir": "？"}.get(r["confidence"], "·")
        print(f"{mark} [{r['src_type']}] {r['path']}")
        if r["detail"]:
            print(f"    {r['detail']}")
    db.close()
    return 0


def cmd_serve(args) -> int:
    db = _db()
    server = make_server(args.port, db)
    print(f"文件户口本已启动：http://127.0.0.1:{args.port}  （Ctrl+C 退出）")
    _open_browser_later(f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    db.close()
    return 0


def cmd_stats(args) -> int:
    db = _db()
    st = db.stats()
    print(f"来源库：{st['origins']} 条  按类型：{st['by_type']}")
    print(f"事件库：{st['events']} 条")
    db.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hukou", description="文件户口本 — 文件身世记录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="盘点目录并归因来源")
    p.add_argument("paths", nargs="*", default=None)
    p.add_argument("--no-browser", action="store_true", help="不读取浏览器下载记录")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("watch", help="持续记账（需管理员）")
    p.add_argument("--volumes", nargs="*", default=None, help="如 --volumes C D")
    p.add_argument("--interval", type=float, default=2.0)
    p.set_defaults(fn=cmd_watch)

    p = sub.add_parser("query", help="查一个文件的身世")
    p.add_argument("path")
    p.set_defaults(fn=cmd_query)

    p = sub.add_parser("search", help="按文件名搜来源库")
    p.add_argument("keyword")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("deep", help="全量 MFT 快照 + 日志历史回溯（需管理员）")
    p.add_argument("--volumes", nargs="*", default=None, help="如 --volumes C D")
    p.add_argument("--history", action="store_true", help="同时回溯 USN 日志历史")
    p.add_argument("--max-history", type=int, default=2_000_000, help="历史事件条数上限")
    p.set_defaults(fn=cmd_deep)

    p = sub.add_parser("serve", help="启动 Web 查询界面")
    p.add_argument("--port", type=int, default=8638)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("stats", help="统计")
    p.set_defaults(fn=cmd_stats)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""文档户口本 CLI。

用法：
  python cli.py scan [<目录>...]      # 扫描并重建谱系（增量、幂等）
  python cli.py serve [--port 8640]   # Web 界面（谱系树 + diff）
  python cli.py clusters              # 控制台列出同源簇
  python cli.py diff <idA> <idB>      # 控制台对比两份
  python cli.py stats
"""

from __future__ import annotations

import argparse
import sys

if not sys.stdout.isatty():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from docline.config import DB_PATH, PORT, default_scan_dirs, ensure_dirs
from docline.engine import Database, Engine
from docline.server import make_server, open_browser_later


def _open() -> tuple[Database, Engine]:
    ensure_dirs()
    db = Database(DB_PATH)
    return db, Engine(db)


def _fmt_time(ts: float) -> str:
    import time as _t

    return _t.strftime("%Y-%m-%d %H:%M", _t.localtime(ts or 0))


def cmd_scan(args) -> int:
    db, engine = _open()
    roots = args.dirs or default_scan_dirs()
    if not roots:
        print("没有可扫描目录。用法：python cli.py scan <目录>")
        return 2
    print(f"扫描：{roots}")

    def progress(done, total, cur):
        if done % 10 == 0 or done == total:
            print(f"  [{done}/{total}] {cur}", flush=True)

    s = engine.scan(roots, progress=progress)
    st = db.stats()
    print(f"完成：{s['total']} 份（处理 {s['processed']}，跳过 {s['skipped']}，"
          f"清理 {s['purged']}）→ {st['clusters']} 个簇，{st['forked']} 个分叉")
    db.close()
    return 0


def cmd_serve(args) -> int:
    db, engine = _open()
    from docline.server import ScanState, run_scan, start_scan_loop

    state = ScanState()
    if not db.get_meta("roots"):
        roots = default_scan_dirs()
        if roots:
            db.set_meta("roots", "|".join(roots))
    saved = [r for r in db.get_meta("roots").split("|") if r]
    if saved:
        import threading

        threading.Thread(target=run_scan, args=(engine, saved, state),
                         daemon=True).start()
    start_scan_loop(db, engine, state, interval=60)
    server = make_server(args.port, db, engine, state)
    print(f"文档户口本已启动：http://127.0.0.1:{args.port}  （Ctrl+C 退出）")
    open_browser_later(f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    db.close()
    return 0


def cmd_clusters(args) -> int:
    db, _ = _open()
    multi = [c for c in db.list_clusters() if c["size"] > 1]
    if not multi:
        print("没有同源副本组。先 scan。")
        db.close()
        return 0
    for c in multi:
        mark = "⚠分叉" if c["fork"] else "      "
        print(f"[{mark}] 簇#{c['cid']} {c['size']} 份 · 最新：{c['latest_name']}")
        print(f"        {c['latest_path']}")
        for m in db.docs_by_cluster(c["cid"]):
            tag = " ←最新" if m["id"] == c["latest_id"] else ""
            print(f"    #{m['id']} {_fmt_time(m['mtime'])} {m['name']}{tag}")
    db.close()
    return 0


def cmd_diff(args) -> int:
    db, _ = _open()
    a, b = db.get_doc(args.ida), db.get_doc(args.idb)
    if not a or not b:
        print("文档不存在。先 clusters 查看编号。")
        db.close()
        return 2
    from docline.diff import diff_blocks

    d = diff_blocks(a["blocks"], b["blocks"])
    older, newer = (a, b) if a["mtime"] <= b["mtime"] else (b, a)
    print(f"旧：{older['name']}  →  新：{newer['name']}")
    print(f"新增 {len(d['added'])} / 删除 {len(d['removed'])} / "
          f"修改 {len(d['changed'])} / 相同 {d['same']}")
    for c in d["changed"]:
        print(f"  ~ {c['old'][:50]}  →  {c['new'][:50]}")
    for r in d["removed"]:
        print(f"  - {r[:70]}")
    for t in d["added"]:
        print(f"  + {t[:70]}")
    db.close()
    return 0


def cmd_stats(args) -> int:
    db, _ = _open()
    print(db.stats())
    db.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="docline", description="文档户口本 — 文档谱系")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="扫描并重建谱系")
    p.add_argument("dirs", nargs="*", default=None)
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("serve", help="Web 界面")
    p.add_argument("--port", type=int, default=PORT)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("clusters", help="列出同源簇")
    p.set_defaults(fn=cmd_clusters)

    p = sub.add_parser("diff", help="对比两份文档")
    p.add_argument("ida", type=int)
    p.add_argument("idb", type=int)
    p.set_defaults(fn=cmd_diff)

    p = sub.add_parser("stats", help="统计")
    p.set_defaults(fn=cmd_stats)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

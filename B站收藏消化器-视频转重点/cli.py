"""收藏消化器 CLI。

用法：
  python cli.py add <B站URL 或 本地文件路径>   # 入队并前台处理完
  python cli.py run                            # 处理所有未完成任务
  python cli.py serve [--port 8639]            # Web 界面（自动恢复未完成任务）
  python cli.py list                           # 任务列表
  python cli.py stats
"""

from __future__ import annotations

import argparse
import sys

if not sys.stdout.isatty():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from bilidigest import asr, fetch
from bilidigest.config import DB_PATH, DEFAULT_MODEL, PORT, ensure_dirs
from bilidigest.db import Database
from bilidigest.jobs import Runner
from bilidigest.server import make_server


def _open_browser_later(url: str, delay: float = 1.5) -> None:
    import threading

    def _open():
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    threading.Timer(delay, _open).start()


def _db() -> Database:
    ensure_dirs()
    return Database(DB_PATH)


def cmd_add(args) -> int:
    db = _db()
    runner = Runner(db, model_size=args.model)
    try:
        kind, value = fetch.classify_input(args.input)
        job_id = db.insert_job(kind, value)
        print(f"任务 #{job_id}（{kind}）：{value}")
        result = runner.process(job_id)
        job = db.get_job(job_id)
        if result["ok"]:
            dg = job["digest"]
            print(f"完成：时长 {dg['duration']}s，句子 {dg['sentence_count']}")
            print("关键词：", "、".join(k["word"] for k in dg["keywords"][:8]))
            print("重点句：")
            for k in dg["key_sentences"]:
                print(f"  [{int(k['t0'])}s] {k['text']}")
            print(f"文字稿：{result['txt']}")
            return 0
        print(f"失败：{result['error']}")
        return 1
    finally:
        db.close()


def cmd_run(args) -> int:
    db = _db()
    runner = Runner(db, model_size=args.model)
    ids = db.pending_jobs()
    if not ids:
        print("没有待处理任务。")
        db.close()
        return 0
    for jid in ids:
        print(f"处理 #{jid} …", flush=True)
        r = runner.process(jid)
        print(f"  {'完成' if r['ok'] else '失败：' + r.get('error','')}")
    db.close()
    return 0


def cmd_serve(args) -> int:
    db = _db()
    runner = Runner(db, model_size=args.model)
    restored = runner.enqueue_pending()
    if restored:
        print(f"已恢复 {restored} 个未完成任务")
    server = make_server(args.port, db, runner)
    print(f"收藏消化器已启动：http://127.0.0.1:{args.port}  （Ctrl+C 退出）")
    _open_browser_later(f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    db.close()
    return 0


def cmd_list(args) -> int:
    db = _db()
    for j in db.list_jobs():
        print(f"#{j['id']} [{j['status']}] {j['kind']}: {j['src'][:80]}"
              + (f"  ({j['err'][:60]})" if j["err"] else ""))
    if not db.list_jobs():
        print("（空）")
    db.close()
    return 0


def cmd_stats(args) -> int:
    db = _db()
    print(db.stats())
    db.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bili", description="B站收藏消化器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="消化一个视频")
    p.add_argument("input")
    p.add_argument("--model", default=DEFAULT_MODEL, choices=asr.MODEL_SIZES)
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("run", help="处理所有待处理任务")
    p.add_argument("--model", default=DEFAULT_MODEL, choices=asr.MODEL_SIZES)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("serve", help="启动 Web 界面")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--model", default=DEFAULT_MODEL, choices=asr.MODEL_SIZES)
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("list", help="任务列表")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("stats", help="统计")
    p.set_defaults(fn=cmd_stats)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""外发卫士 CLI。

用法：
  python cli.py check <文件>              # 控制台体检报告
  python cli.py sanitize <文件> [-o 输出]  # 净化另存（默认同目录加 .净化 后缀）
  python cli.py serve [--port 8641]       # Web 界面（自动开浏览器）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if not sys.stdout.isatty():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from inspector.config import PORT, ensure_dirs
from inspector.inspect import inspect_file, sanitize_file
from inspector.server import make_server, open_browser_later


def cmd_check(args) -> int:
    rep = inspect_file(args.file)
    print(f"体检：{args.file}")
    if not rep.findings:
        print("  未发现敏感项 ✅")
        return 0
    c = rep.counts()
    print(f"  高危 {c['high']} / 注意 {c['med']} / 提示 {c['low']}")
    for f in rep.findings:
        mark = {"high": "★", "med": "☆", "low": "·"}[f.level]
        print(f"  [{mark} {f.level.upper():4}] {f.title}")
        if f.detail:
            print(f"         {f.detail}")
    return 0


def cmd_sanitize(args) -> int:
    src = Path(args.file)
    dst = Path(args.output) if args.output else src.with_name(
        src.stem + ".净化" + src.suffix)
    if dst.resolve() == src.resolve():
        print("净化输出不能覆盖原文件。")
        return 2
    result = sanitize_file(src, dst)
    if result is None:
        print("该类型 v0.1 只检不清（PDF 建议重新导出；zip 建议手工整理）。")
        return 1
    print(f"净化副本：{dst}")
    print(f"  已移除部件：{len(result['removed'])} 个；改写：{len(result['transformed'])} 个；"
          f"操作 {result['ops']} 次")
    if result["manual"]:
        print(f"  需手工处理：{'；'.join(result['manual'])}")
    rep = inspect_file(dst)
    c = rep.counts()
    print(f"  复检：高危 {c['high']} / 注意 {c['med']}（正文内容类问题仍需人工确认）")
    return 0


def cmd_serve(args) -> int:
    ensure_dirs()
    server = make_server(args.port)
    print(f"外发卫士已启动：http://127.0.0.1:{args.port}  （Ctrl+C 退出）")
    open_browser_later(f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="inspector", description="外发卫士 — 发文件前体检")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="体检一个文件")
    p.add_argument("file")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("sanitize", help="净化另存")
    p.add_argument("file")
    p.add_argument("-o", "--output", default=None)
    p.set_defaults(fn=cmd_sanitize)

    p = sub.add_parser("serve", help="启动 Web 界面")
    p.add_argument("--port", type=int, default=PORT)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())

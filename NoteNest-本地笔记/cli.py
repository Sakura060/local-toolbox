"""NoteNest 命令行入口。

用法:
  python cli.py [--dir 笔记目录] [--port 8080]
  默认笔记目录: ~/NoteNest
"""

from __future__ import annotations

import argparse
import os
import sys

from notenest import __version__
from notenest.server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="notenest", description="NoteNest — 本地 Markdown 笔记服务（纯标准库）")
    parser.add_argument("--version", action="version", version=f"NoteNest {__version__}")
    parser.add_argument("--dir", default=os.path.join(os.path.expanduser("~"), "NoteNest"),
                        help="笔记目录（默认 ~/NoteNest）")
    parser.add_argument("--port", type=int, default=8890)
    args = parser.parse_args(argv)
    serve(args.dir, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

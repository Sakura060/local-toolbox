"""全工作区测试跑批：逐项目跑 unittest discover，输出一行摘要。

用法：python tools\\run_all_tests.py
用途：收尾/交接前的整体回归（单项目测试仍进各自目录跑）。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

# 项目目录名 -> 是否把 tests 作为包（有 __init__.py 用 discover 默认即可）
PROJECTS = (
    "localcore",
    "FileFlow-文件整理器",
    "LANDrop-lite-文件传输",
    "NoteNest-本地笔记",
    "捞图-截图记忆库",
    "文件户口本-文件身世",
    "B站收藏消化器-视频转重点",
    "文档户口本-文档谱系",
    "外发卫士-发前体检",
    "ToolHub-工具箱门户",
    "ClipVault-剪贴板时间线",
)


def main() -> int:
    total_fail = 0
    t0 = time.time()
    for name in PROJECTS:
        proj = ROOT / name
        if not (proj / "tests").is_dir():
            print(f"[SKIP] {name}（无 tests 目录）")
            continue
        p = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=str(proj), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        summary = next((ln for ln in reversed(tail) if ln.startswith("Ran ")), "?")
        status = "PASS" if p.returncode == 0 else "FAIL"
        if p.returncode != 0:
            total_fail += 1
            detail = "\n".join(tail[-12:])
            print(f"[{status}] {name}  {summary}\n{detail}")
        else:
            print(f"[{status}] {name}  {summary}")
    print(f"\n{'ALL GREEN' if total_fail == 0 else f'{total_fail} PROJECT(S) FAILED'}"
          f"  in {time.time() - t0:.0f}s")
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())

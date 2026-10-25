"""拷贝前 N 张真实截图到临时目录（真实数据冒烟用）。"""

import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SRC = Path.home() / "Pictures" / "Screenshots"
DST = Path.home() / "AppData" / "Local" / "Temp" / "laotu_real10"
N = 10

DST.mkdir(parents=True, exist_ok=True)
files = sorted(SRC.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:N]
for i, f in enumerate(files, 1):
    shutil.copyfile(f, DST / f.name)
    print(f"copy {i}: {f.name}")
print(f"READY {len(files)} -> {DST}")

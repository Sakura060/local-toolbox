"""生成 OCR 冒烟测试图（独立进程：freetype 与 OCR 推理同进程互斥，实证）。

用法：python tools/make_test_images.py
产出：%TEMP%\laotu_smoke_work\ 下 3 张图（2 张含字 + 1 张纯色）。
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image, ImageDraw, ImageFont

TD = Path(tempfile.gettempdir()) / "laotu_smoke_work"
TD.mkdir(exist_ok=True)


def make_image(text: str, p: Path) -> None:
    img = Image.new("RGB", (560, 160), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 48)
    d.text((30, 50), text, fill="black", font=font)
    img.save(p, "PNG")


make_image("单号 2026", TD / "smoke_1.png")
make_image("测试 88", TD / "smoke_2.png")
Image.new("RGB", (200, 120), (240, 240, 245)).save(TD / "smoke_3.png", "PNG")
broken = TD / "smoke_bad.png"
broken.write_bytes(b"not an image")
print("READY:", TD)

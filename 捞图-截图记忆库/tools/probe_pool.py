"""OcrPool 端到端探针（文件版）。

用法：python tools/probe_pool.py [font]
  无参     —— 父进程不碰 freetype，直接用池识别一张预生成图片
  带 font  —— 父进程先用 ImageFont 画图（复现原故障场景），再走池
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TD = Path(tempfile.gettempdir())


def make_image(with_font: bool) -> Path:
    p = TD / "laotu_probe_file.png"
    if with_font:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (560, 160), "white")
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 48)
        d.text((30, 50), "单号 2026", fill="black", font=f)
        img.save(p, "PNG")
    else:
        from PIL import Image

        Image.new("RGB", (200, 100), "white").save(p, "PNG")
    return p


def main() -> int:
    with_font = "font" in sys.argv
    p = make_image(with_font)
    print(f"image ready (with_font={with_font}): {p}", flush=True)

    from laotu.ocr_pool import OcrPool

    pool = OcrPool(per_image_timeout=60)
    t = time.time()
    text, err = pool.ocr(str(p))
    print(f"ocr#1: {round(time.time()-t,1)}s err={err!r} text={text[:40]!r}", flush=True)
    t = time.time()
    text2, err2 = pool.ocr(str(p))
    print(f"ocr#2: {round(time.time()-t,1)}s err={err2!r} text={text2[:40]!r}", flush=True)
    pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

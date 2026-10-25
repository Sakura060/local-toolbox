"""交叉验证 png_to_dib：DIB 包上 BMP 文件头后用 Pillow 独立解码，逐像素比对。跑完即弃。"""

import struct
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

from clipvault.clip import png_to_dib


def make_case(size, mode, color_fn):
    im = Image.new(mode, size)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = color_fn(x, y)
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def dib_to_bmp(dib: bytes) -> bytes:
    # 像素数据在 文件头(14) + 信息头(40) 之后 → offset=54
    return b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, 54) + dib


cases = [
    ("3x2-rgba", (3, 2), "RGBA", lambda x, y: (x * 40 % 256, y * 90 % 256, (x + y) * 37 % 256, (x * y * 50 + 30) % 256)),
    ("17x9-rgb", (17, 9), "RGB", lambda x, y: (x * 15 % 256, y * 28 % 256, (x * y * 7) % 256)),
    ("1x1", (1, 1), "RGBA", lambda x, y: (250, 251, 252, 253)),
]

ok = True
for name, size, mode, fn in cases:
    png = make_case(size, mode, fn)
    orig = Image.open(BytesIO(png)).convert("RGBA")
    dib = png_to_dib(png)
    back = Image.open(BytesIO(dib_to_bmp(dib))).convert("RGBA")
    # BI_RGB 的 alpha 语义未定义（Pillow 解码丢弃），RGB 通道必须逐像素一致
    orig_rgb = [(r, g, b) for r, g, b, _ in orig.getdata()]
    back_rgb = [(r, g, b) for r, g, b, _ in back.getdata()]
    same = orig_rgb == back_rgb
    print(f"{name}: size={size} dib_bytes={len(dib)} rgb_match={same}")
    ok = ok and same

# 灰度图（单通道）也要能转
gbuf = BytesIO()
Image.new("L", (5, 4), 128).save(gbuf, "PNG")
dib = png_to_dib(gbuf.getvalue())
back = Image.open(BytesIO(dib_to_bmp(dib))).convert("RGBA")
ok = ok and all(p == (128, 128, 128, 255) for p in back.getdata())
print("grayscale: pixels_match=" + str(all(p == (128, 128, 128, 255) for p in back.getdata())))
print("ALL_OK" if ok else "MISMATCH")

"""Seed a temp laotu DB (near-duplicate images + timeline spread) and serve on 8649."""
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw

from laotu.db import Database
from laotu.hashes import dhash_from_pil
from laotu.indexer import Indexer
from laotu.server import ScanState, make_server

tmp = Path(tempfile.mkdtemp(prefix="laotu_ui_"))
data = tmp / "data"
thumbs = data / "thumbs"
thumbs.mkdir(parents=True)
db = Database(data / "laotu.db")

now = time.time()


def stripes(color, phase=0):
    """竖条纹图案：dHash 非零且可微调 phase 生成近邻。"""
    img = Image.new("RGB", (64, 48), color)
    d = ImageDraw.Draw(img)
    for x in range(0, 64, 8):
        d.rectangle([x + phase, 0, x + 3 + phase, 47], fill=(240, 240, 240))
    return img


def add(name, mtime, color=(40, 90, 200), phase=0):
    img = stripes(color, phase)
    img_id = db.upsert_image(
        path=str(tmp / name), name=name, folder=str(tmp), size=999,
        mtime_ns=int(mtime * 1e9), width=64, height=48,
        ocr_text="测试文本 单号12345", ocr_ok=1, err=None, seg_text="测试 文本 单号",
        dhash=dhash_from_pil(img), source=("微信" if "wx" in name else ""))
    img.save(thumbs / f"{img_id}.jpg", "JPEG")
    (tmp / name).write_bytes(b"fake")
    return img_id


# 一组三连拍（同条纹、相位差 1px → dHash 近邻）+ 独立图 + 微信图
add("截图_a.png", now - 100)
add("截图_b.png", now - 80, phase=1)
add("截图_c.png", now - 60, phase=2)
add("旅行.jpg", now - 86400 * 3, color=(200, 120, 40), phase=3)
add("wx_聊天图.png", now - 86400, color=(60, 160, 90), phase=4)

state = ScanState()
web_dir = Path(__file__).resolve().parent.parent / "laotu" / "web"
server = make_server(8649, db, Indexer(db, thumb_dir=thumbs), state, web_dir)
threading.Thread(target=server.serve_forever, daemon=True).start()
print(f"seeded UI preview at http://127.0.0.1:8649/ ({tmp})", flush=True)
time.sleep(900)

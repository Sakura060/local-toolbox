"""真引擎 OCR 冒烟（零 freetype 进程）。

前置：python tools/make_test_images.py（freetype 只允许在那个进程出现）。
用法：python tools/ocr_smoke.py

实证背景：本开发机上 freetype 与 RapidOCR 推理同进程互斥（先来后到都挂），
产品代码零 freetype（缩略图只做缩放），本脚本同样不导入 ImageFont。
"""

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    import tempfile

    work = Path(tempfile.gettempdir()) / "laotu_smoke_work"
    p1 = work / "smoke_1.png"
    p2 = work / "smoke_2.png"
    if not p1.is_file():
        print("请先运行 python tools/make_test_images.py")
        return 2

    from laotu.ocr import ocr_image

    t = time.time()
    text, err = ocr_image(p1)
    assert not err, f"识别报错: {err}"
    assert "2026" in text.replace(" ", ""), f"数字未命中: {text!r}"
    print(f"[1] 往返识别 OK {round(time.time()-t,1)}s text={text[:30]!r}", flush=True)

    t = time.time()
    text2, err2 = ocr_image(p2)
    assert not err2 and "88" in text2.replace(" ", "")
    print(f"[2] 引擎复用 OK {round(time.time()-t,1)}s", flush=True)

    # 小规模索引全链路（真实 Indexer + DB + 缩略图，缩略图不碰 freetype）
    t = time.time()
    from laotu.db import Database
    from laotu.indexer import Indexer

    lib = work / "lib"
    lib.mkdir(exist_ok=True)
    shutil.copyfile(p1, lib / "smoke_1.png")
    shutil.copyfile(p2, lib / "smoke_2.png")
    db = Database(work / "t.db")
    idx = Indexer(db, thumb_dir=work / "thumbs")
    summary = idx.scan([str(lib)])
    r = db.search("2026")
    assert r["total"] >= 1, "索引搜索未命中"
    stats = db.stats()
    db.close()
    print(f"[3] 索引链路 OK {round(time.time()-t,1)}s summary={summary} stats={stats}", flush=True)

    print("OCR_SMOKE_ALL_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

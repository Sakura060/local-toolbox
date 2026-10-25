"""缩略图：Pillow 生成 JPEG 落盘（磁盘文件而非 BLOB，HTTP 直接发）。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .config import THUMB_MAX_SIDE


def dimensions(path: str | Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.size


def make_thumb(path: str | Path, dest: str | Path, max_side: int = THUMB_MAX_SIDE) -> tuple[int, int]:
    """生成缩略图，返回原图 (width, height)。失败抛异常，调用方处理。"""
    img = Image.open(path)
    try:
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = img.convert("RGB")
        w, h = img.size
        img.thumbnail((max_side, max_side))
        dest_path = Path(dest)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest_path, "JPEG", quality=82)
    finally:
        img.close()
    return w, h

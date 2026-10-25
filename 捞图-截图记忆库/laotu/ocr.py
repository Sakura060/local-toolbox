"""OCR 封装：RapidOCR 进程内懒加载单例 + 线程锁（模型非线程安全）。

两条实证纪律（违反任一会在部分环境下死锁，详见 BLUEPRINT 风险表）：
1. 本模块必须在任何 PIL ImageFont/freetype 使用之前完成引擎初始化；
   测试需在 setUp 里先 get_engine() 再画合成图。
2. OMP=1：无控制台(detached)环境下 OpenMP 默认线程池可能挂起。

ocr_image 永不抛异常，返回 (text, err)，由调用方决定如何记录失败。
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import threading
from pathlib import Path

from .config import OCR_MAX_SIDE

_lock = threading.Lock()
_engine = None


def _trace(msg: str) -> None:
    """诊断轨迹（data/ocr_trace.log）：定位 detached 环境推理卡点用。"""
    try:
        import time

        log = Path(__file__).resolve().parent.parent / "data" / "ocr_trace.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with __import__("builtins").open(log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} pid={os.getpid()} {msg}\n")
    except Exception:
        pass


def get_engine():
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        return _engine


def _load_array(path: Path):
    """统一经 Pillow 解码（gif 取首帧），长边压到 OCR_MAX_SIDE，返回 RGB ndarray。"""
    from PIL import Image
    import numpy as np

    img = Image.open(path)
    try:
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = img.convert("RGB")
        w, h = img.size
        scale = OCR_MAX_SIDE / max(w, h, 1)
        if scale < 1:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
        arr = np.array(img)  # 显式拷贝，关闭图像后仍可用
    finally:
        img.close()
    return arr


def ocr_image(path: str | Path) -> tuple[str, str]:
    """识别图中文字。返回 (换行连接的文本, 错误信息)。"""
    _trace(f"ocr begin {Path(path).name}")
    try:
        arr = _load_array(Path(path))
        _trace("load done")
        with _lock:
            result, _ = get_engine()(arr)
        _trace(f"infer done items={len(result or [])}")
        lines = [str(item[1]) for item in (result or []) if len(item) >= 2]
        return "\n".join(lines), ""
    except Exception as exc:  # 单图损坏不中断批次
        _trace(f"ocr error {type(exc).__name__}: {exc}")
        return "", f"{type(exc).__name__}: {exc}"

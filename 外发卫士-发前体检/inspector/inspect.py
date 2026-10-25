"""体检入口：按扩展名分发。"""

from __future__ import annotations

from pathlib import Path

from . import exif, ooxml, pdfzip
from .config import ARCHIVE_ONLY, IMAGE_EXTS, OOXML_EXTS, PDF_EXTS
from .ooxml import Report


def inspect_file(path: str | Path) -> Report:
    p = Path(path)
    rep = Report(path=str(p))
    if not p.is_file():
        rep.add("low", "unsupported", "文件不存在")
        return rep
    ext = p.suffix.lower()
    if ext in OOXML_EXTS:
        return ooxml.inspect_ooxml(p, rep)
    if ext in IMAGE_EXTS:
        return exif.inspect_image(p, rep)
    if ext in PDF_EXTS:
        return pdfzip.inspect_pdf(p, rep)
    if ext in ARCHIVE_ONLY:
        return pdfzip.inspect_zip(p, rep)
    rep.add("low", "unsupported",
            f"暂不支持 {ext or '无扩展名'}（支持：docx/pptx/xlsx/图片/pdf/zip）")
    return rep


def sanitize_file(src: str | Path, dst: str | Path) -> dict | None:
    """净化。返回结果 dict；该类型不支持自动净化时返回 None。

    拒绝原地净化：dst 与 src 相同路径时抛 ValueError（绝不覆盖原件）。
    """
    src_p, dst_p = Path(src), Path(dst)
    if src_p.resolve() == dst_p.resolve():
        raise ValueError("净化输出不能覆盖原文件")
    ext = src_p.suffix.lower()
    if ext in OOXML_EXTS:
        from .sanitizer import sanitize_ooxml

        return sanitize_ooxml(src, dst)
    if ext in IMAGE_EXTS:
        n = exif.strip_exif(src, dst)
        return {"removed": [f"EXIF（{n} 个字段）"], "transformed": [],
                "manual": [], "ops": n}
    return None

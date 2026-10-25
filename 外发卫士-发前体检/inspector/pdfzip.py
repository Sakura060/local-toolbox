"""PDF / ZIP 体检（浅层：字节特征 + 目录项，深度解析列 v0.2）。"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from .ooxml import Report, _local_paths_in

_AUTHOR_RE = re.compile(rb"/Author\s*\(([^)]{0,120})\)")
_TITLE_RE = re.compile(rb"/Title\s*\(([^)]{0,160})\)")
_PRODUCER_RE = re.compile(rb"/Producer\s*\(([^)]{0,120})\)")


def inspect_pdf(path: str | Path, rep: Report) -> Report:
    rep.ftype = "pdf"
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        rep.add("low", "unreadable", "无法读取", str(exc))
        return rep

    author = _author_re_first(data)
    if author:
        rep.add("med", "pdf_author", "PDF 元数据含作者", author)
    title = _TITLE_RE.search(data)
    if title:
        rep.add("low", "pdf_title", "PDF 元数据含标题",
                title.group(1).decode("latin-1", "replace")[:80])
    producer = _PRODUCER_RE.search(data)
    if producer:
        rep.add("low", "pdf_producer", "PDF 生成器信息",
                producer.group(1).decode("latin-1", "replace")[:80])

    if b"/EmbeddedFiles" in data or b"/Filespec" in data:
        rep.add("high", "pdf_attachments", "PDF 内嵌附件",
                "对方可在阅读器附件栏直接提取（v0.1 只检不清）")
    if b"/JavaScript" in data or b"/JS " in data:
        rep.add("high", "pdf_js", "PDF 含 JavaScript", "可能自动执行脚本")
    paths = _local_paths_in(data.decode("latin-1", "replace"))
    if paths:
        rep.add("med", "local_path", "PDF 内容含本机路径", "；".join(paths))
    return rep


def _author_re_first(data: bytes) -> str:
    m = _AUTHOR_RE.search(data)
    if not m:
        return ""
    return m.group(1).decode("latin-1", "replace").strip()[:80]


def inspect_zip(path: str | Path, rep: Report) -> Report:
    rep.ftype = "zip"
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        rep.add("low", "unreadable", "无法按 ZIP 解析", str(exc))
        return rep
    try:
        names = zf.namelist()
        hits = []
        for n in names:
            if _local_paths_in(n) or n.startswith(("Users/", "home/")):
                hits.append(n)
        if hits:
            rep.add("med", "zip_paths", "压缩包内部含个人目录结构",
                    "；".join(hits[:5]))
        inner = [n for n in names if n.lower().endswith((".docx", ".xlsx", ".pptx"))]
        if inner:
            rep.add("low", "zip_office", f"包内含 Office 文档（{len(inner)} 个）",
                    "、".join(inner[:5]))
    finally:
        zf.close()
    return rep

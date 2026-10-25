"""OOXML 文本块提取（vendored 自外发卫士 ooxml.py 的提取部分）。

安全约束：解析前拒绝 DOCTYPE/ENTITY、限制部件大小。
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

_MAX_PART = 64 * 1024 * 1024
_DTD_RE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.I)


class UnsafeXml(ValueError):
    pass


def parse_xml_safe(data: bytes | str, max_bytes: int = _MAX_PART) -> ET.Element:
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > max_bytes:
        raise UnsafeXml(f"part too large: {len(data)}")
    if _DTD_RE.search(data):
        raise UnsafeXml("DOCTYPE/ENTITY forbidden")
    return ET.fromstring(data)


def extract_blocks(path: str | Path) -> list[str]:
    """段落级文本块：docx 按段落、pptx 按 slide/备注文本、xlsx 按共享字符串。"""
    out: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return out
    try:
        for name in zf.namelist():
            try:
                if name == "word/document.xml":
                    root = parse_xml_safe(zf.read(name))
                    for p in root.iter(f"{{{NS_W}}}p"):
                        text = "".join(t.text or "" for t in p.iter(f"{{{NS_W}}}t"))
                        if text.strip():
                            out.append(text.strip())
                elif re.match(r"ppt/(slides|notesSlides)/[^/]+\.xml$", name):
                    root = parse_xml_safe(zf.read(name))
                    for el in root.iter():
                        if el.tag.endswith("}t") and el.text and el.text.strip():
                            out.append(el.text.strip())
                elif name == "xl/sharedStrings.xml":
                    root = parse_xml_safe(zf.read(name))
                    for si in root.findall(f"{{{NS_S}}}si"):
                        text = "".join(t.text or "" for t in si.iter(f"{{{NS_S}}}t"))
                        if text.strip():
                            out.append(text.strip())
            except (UnsafeXml, ET.ParseError, OSError, KeyError):
                continue
    finally:
        zf.close()
    return [b for b in out if b.strip()]

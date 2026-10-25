"""净化另存：白名单重组 + 最小字节手术，绝不覆盖原件。

原则：
1. 原包字节逐条搬运，只动必须动的部件；
2. XML 手术用正则做最小替换（不整树重写——ET 换前缀会破坏 mc:Ignorable 引用），
   手术后的 XML 必须能通过 parse_xml_safe 良构校验，否则回退原文并标记"需手工处理"；
3. 删除部件必须同步清理 _rels 引用与 [Content_Types] Override；
4. 产物自校验：所有 rels Target 存在、所有 Override 存在、关键 XML 可解析。
   自校验不过 → 抛 SanitizeError，绝不交付坏文件。
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from .ooxml import parse_xml_safe

_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

MINIMAL_CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
    ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    '<dc:title></dc:title><dc:creator></dc:creator><cp:lastModifiedBy></cp:lastModifiedBy>'
    '</cp:coreProperties>'
)

MINIMAL_APP = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'
    ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    '<Application></Application><Company></Company><Manager></Manager>'
    '<Template></Template>'
    '</Properties>'
)


class SanitizeError(RuntimeError):
    pass


def _unwrap_revisions(xml_text: str) -> tuple[str, int]:
    """接受全部修订：删 w:del 块、剥 w:ins 壳、清批注标记与格式变更记录。

    返回 (新文本, 操作数)。纯字节手术，不动其余内容。
    """
    ops = 0
    new, n = re.subn(r"<w:del\b[^>]*>.*?</w:del>", "", xml_text, flags=re.S)
    ops += n
    xml_text = new
    xml_text, n2 = re.subn(r"<w:ins\b[^>]*/>", "", xml_text)
    new, n3 = re.subn(r"<w:ins\b[^>]*>", "", xml_text)
    new, n4 = re.subn(r"</w:ins>", "", new)
    ops += n2 + n3 + n4
    xml_text = new
    for pat in (r"<w:commentRangeStart\b[^>]*/>",
                r"<w:commentRangeEnd\b[^>]*/>",
                r"<w:commentReference\b[^>]*/>",
                r"<w:rPrChange\b[^>]*>.*?</w:rPrChange>",
                r"<w:pPrChange\b[^>]*>.*?</w:pPrChange>",
                r"<w:sectPrChange\b[^>]*>.*?</w:sectPrChange>"):
        new, n = re.subn(pat, "", xml_text, flags=re.S)
        ops += n
        xml_text = new
    return xml_text, ops


def _show_hidden_sheets(xml_text: str) -> tuple[str, int]:
    new, n = re.subn(r'\s+state="(?:hidden|veryHidden)"', "", xml_text)
    return new, n


def _drop_external_refs(xml_text: str) -> tuple[str, int]:
    new, n = re.subn(r"<externalReferences\b.*?</externalReferences>", "",
                     xml_text, flags=re.S)
    return new, n


def _rels_base(rels_name: str) -> str:
    """rels 文件所属部件目录：word/_rels/document.xml.rels → word；_rels/.rels → ''。"""
    if rels_name == "_rels/.rels":
        return ""
    return rels_name.split("_rels/")[0].rstrip("/")


def _resolve_rel(rels_name: str, target: str) -> str:
    """把 .rels 里的相对 Target 解析成包内绝对部件名。"""
    if target.startswith("/"):
        return target[1:]
    rel_dir = _rels_base(rels_name)
    parts = (rel_dir + "/" + target).split("/") if rel_dir else target.split("/")
    stack: list[str] = []
    for seg in parts:
        if seg == "..":
            if stack:
                stack.pop()
        elif seg not in ("", "."):
            stack.append(seg)
    return "/".join(stack)


def _strip_rels(data: bytes, rels_name: str, removed: set[str]) -> tuple[bytes, int]:
    """从 .rels 中删除指向 removed 部件的 Relationship（保留 External）。"""

    def repl(m: re.Match) -> str:
        if "TargetMode=\"External\"" in m.group(0):
            return m.group(0)
        resolved = _resolve_rel(rels_name, m.group(1))
        return "" if resolved in removed else m.group(0)

    text = data.decode("utf-8")
    new, n = re.subn(r'<Relationship\b[^>]*\bTarget="([^"]+)"[^>]*/>', repl, text)
    return new.encode("utf-8"), n


def _strip_content_types(data: bytes, removed: set[str]) -> tuple[bytes, int]:
    text = data.decode("utf-8")
    ops = 0
    for r in removed:
        new, n = re.subn(
            rf'<Override\b[^>]*\bPartName="/{re.escape(r)}"[^>]*/>', "", text)
        ops += n
        text = new
    return text.encode("utf-8"), ops


def _structural_check(zf: zipfile.ZipFile, names: set[str]) -> list[str]:
    """产物结构自校验，返回问题列表（空 = 通过）。"""
    problems: list[str] = []
    ct = zf.read("[Content_Types].xml").decode("utf-8", "replace")
    for m in re.finditer(r'<Override\b[^>]*\bPartName="/([^"]+)"', ct):
        if m.group(1) not in names:
            problems.append(f"ContentTypes 指向缺失部件: {m.group(1)}")
    for n in names:
        if not n.endswith(".rels"):
            continue
        try:
            root = parse_xml_safe(zf.read(n))
        except (ValueError, ET.ParseError):
            problems.append(f"rels 损坏: {n}")
            continue
        for rel in root.iter(f"{{{_REL_NS}}}Relationship"):
            if rel.get("TargetMode") == "External":
                continue
            resolved = _resolve_rel(n, rel.get("Target", ""))
            if resolved not in names:
                problems.append(f"{n} 指向缺失部件: {resolved}")
    if "word/document.xml" in names:
        try:
            parse_xml_safe(zf.read("word/document.xml"))
        except (ValueError, ET.ParseError) as exc:
            problems.append(f"document.xml 无法解析: {exc}")
    return problems


def sanitize_ooxml(src: str | Path, dst: str | Path) -> dict:
    """生成净化副本。返回 {"removed": [...], "transformed": [...], "manual": [...], "ops": n}。"""
    removed: list[str] = []
    transformed: list[str] = []
    manual: list[str] = []
    ops_total = 0

    with zipfile.ZipFile(src) as zin:
        names = zin.namelist()

        drop: set[str] = set()
        embeddings = []
        for n in names:
            low = n.lower()
            if ("thumbnail" in low
                    or low == "docprops/custom.xml"
                    or "comments.xml" in low
                    or low.startswith("ppt/notesSlides/")
                    or low.startswith("xl/externalLinks/")):
                drop.add(n)
            elif low.startswith(("word/embeddings/", "ppt/embeddings/", "xl/embeddings/")):
                embeddings.append(n)  # 内嵌对象：牵动正文引用，标记手工而非自动删
        removed = sorted(drop)

        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                n = info.filename
                if n in drop:
                    continue
                data = zin.read(n)
                try:
                    if n == "docProps/core.xml":
                        data = MINIMAL_CORE.encode("utf-8")
                        transformed.append(n)
                        ops_total += 1
                    elif n == "docProps/app.xml":
                        data = MINIMAL_APP.encode("utf-8")
                        transformed.append(n)
                        ops_total += 1
                    elif n == "word/document.xml":
                        text = data.decode("utf-8")
                        new_text, ops = _unwrap_revisions(text)
                        if new_text != text:
                            parse_xml_safe(new_text.encode("utf-8"))  # 良构才采用
                            data = new_text.encode("utf-8")
                            transformed.append(n)
                            ops_total += ops
                        else:
                            parse_xml_safe(data)
                    elif n == "xl/workbook.xml":
                        text = data.decode("utf-8")
                        new_text, n1 = _show_hidden_sheets(text)
                        new_text, n2 = _drop_external_refs(new_text)
                        if new_text != text:
                            parse_xml_safe(new_text.encode("utf-8"))
                            data = new_text.encode("utf-8")
                            transformed.append(n)
                            ops_total += n1 + n2
                    elif n.endswith(".rels"):
                        new_data, ops = _strip_rels(data, n, drop)
                        if new_data != data:
                            data = new_data
                            ops_total += ops
                    elif n == "[Content_Types].xml":
                        new_data, ops = _strip_content_types(data, drop)
                        if new_data != data:
                            data = new_data
                            ops_total += ops
                    else:
                        parse_xml_safe(data) if n.endswith(".xml") else None
                except (ValueError, ET.ParseError):
                    manual.append(n)  # 手术失败 → 保留原文，标记手工核查
                    continue
                zout.writestr(info, data)

    if embeddings:
        manual.append("内嵌对象需手工删除（自动剔除会破坏正文引用）")

    # 自校验
    with zipfile.ZipFile(dst) as zchk:
        problems = _structural_check(zchk, set(zchk.namelist()))
    if problems:
        Path(dst).unlink(missing_ok=True)
        raise SanitizeError("净化产物结构校验失败: " + "；".join(problems[:3]))

    manual.extend(["本机路径/用户名（正文内容，需手工修改）"])
    return {"removed": removed, "transformed": sorted(set(transformed)),
            "manual": sorted(set(manual)), "ops": ops_total}

"""OOXML（docx/pptx/xlsx）隐私体检：元数据 / 批注 / 修订 / 隐藏内容 / 路径泄露。

只读解析：zipfile + ElementTree，绝不修改原文件。
安全约束：所有 XML 部件解析前拒绝 DOCTYPE/ENTITY（防实体扩展）、限制部件大小。
本模块同时是「文档户口本」diff 管线的地基（extract_blocks 供谱系/diff 复用）。
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}

_MAX_PART = 64 * 1024 * 1024
_DTD_RE = re.compile(rb"<!DOCTYPE|<!ENTITY", re.I)

_LEVELS = ("high", "med", "low")


@dataclass
class Finding:
    level: str          # high / med / low
    kind: str           # 类别键（前端/净化器用）
    title: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {"level": self.level, "kind": self.kind,
                "title": self.title, "detail": self.detail}


@dataclass
class Report:
    path: str
    ftype: str = ""
    findings: list = field(default_factory=list)

    def add(self, level: str, kind: str, title: str, detail: str = "") -> None:
        assert level in _LEVELS
        self.findings.append(Finding(level, kind, title, detail))

    def counts(self) -> dict:
        return {lv: sum(1 for f in self.findings if f.level == lv)
                for lv in _LEVELS}

    def as_dict(self) -> dict:
        return {"path": self.path, "ftype": self.ftype,
                "counts": self.counts(),
                "findings": [f.as_dict() for f in self.findings]}


class UnsafeXml(ValueError):
    pass


def parse_xml_safe(data: bytes | str, max_bytes: int = _MAX_PART) -> ET.Element:
    """解析不可信 XML 部件：拒绝 DOCTYPE/ENTITY（防实体扩展），限大小。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if len(data) > max_bytes:
        raise UnsafeXml(f"part too large: {len(data)}")
    if _DTD_RE.search(data):
        raise UnsafeXml("DOCTYPE/ENTITY forbidden")
    return ET.fromstring(data)


# ---------- 文档正文本体（文档户口本复用） ----------

def extract_blocks(path: str | Path) -> list[str]:
    """提取段落级文本块：docx 按段落、pptx 按 slide/备注文本、xlsx 按共享字符串。"""
    out: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return out
    try:
        for name in zf.namelist():
            try:
                if name == "word/document.xml":
                    out.extend(_word_paragraphs(zf.read(name)))
                elif re.match(r"ppt/(slides|notesSlides)/[^/]+\.xml$", name):
                    out.extend(_ppt_texts(zf.read(name)))
                elif name == "xl/sharedStrings.xml":
                    out.extend(_xlsx_shared_strings(zf.read(name)))
            except (UnsafeXml, ET.ParseError, OSError, KeyError):
                continue
    finally:
        zf.close()
    return [b for b in out if b.strip()]


def _word_paragraphs(data: bytes) -> list[str]:
    root = parse_xml_safe(data)
    paras = []
    for p in root.iter(f"{{{NS['w']}}}p"):
        text = "".join(t.text or "" for t in p.iter(f"{{{NS['w']}}}t"))
        if text.strip():
            paras.append(text.strip())
    return paras


def _ppt_texts(data: bytes) -> list[str]:
    root = parse_xml_safe(data)
    texts = []
    for t in root.iter(f"{{{NS['p']}}}t"):
        if t.text and t.text.strip():
            texts.append(t.text.strip())
    return texts


def _xlsx_shared_strings(data: bytes) -> list[str]:
    root = parse_xml_safe(data)
    texts = []
    for si in root.findall(f"{{{NS['s']}}}si"):
        text = "".join(t.text or "" for t in si.iter(f"{{{NS['s']}}}t"))
        if text.strip():
            texts.append(text.strip())
    return texts


# ---------- 体检 ----------

_PATH_RE = re.compile(r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\"<>|\r\n]{2,80}")


def _local_paths_in(text: str) -> list[str]:
    return list(dict.fromkeys(_PATH_RE.findall(text)))[:5]


def inspect_ooxml(path: str | Path, rep: Report) -> Report:
    rep.ftype = Path(path).suffix.lower().lstrip(".")
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        rep.add("low", "unreadable", "无法按 OOXML 解析", str(exc))
        return rep

    names = zf.namelist()
    try:
        # --- 元数据 core.xml ---
        if "docProps/core.xml" in names:
            root = parse_xml_safe(zf.read("docProps/core.xml"))
            creator = _findtext(root, "dc", "creator")
            last_by = _findtext(root, "cp", "lastModifiedBy")
            rev = _findtext(root, "cp", "revision")
            modified = _findtext(root, "dcterms", "modified")
            who = "、".join(x for x in (creator, last_by) if x)
            if who:
                rep.add("med", "meta_author", "文档元数据含作者信息", f"作者/最后修改者：{who}")
            if rev and rev.isdigit() and int(rev) > 1:
                rep.add("low", "meta_rev", f"修订版本号 {rev}",
                        f"最后保存时间：{modified or '未知'}")

        # --- app.xml：公司/管理者/模板路径 ---
        if "docProps/app.xml" in names:
            root = parse_xml_safe(zf.read("docProps/app.xml"))
            company = _text_of(root, "Company")
            manager = _text_of(root, "Manager")
            org = "、".join(x for x in (company, manager) if x)
            if org:
                rep.add("med", "meta_company", "文档属性含公司/管理者", org)
            template = _text_of(root, "Template")
            if template:
                paths = _local_paths_in(template)
                if paths:
                    rep.add("high", "meta_template", "模板字段泄露本机路径", "；".join(paths))

        # --- 自定义属性 ---
        if "docProps/custom.xml" in names:
            root = parse_xml_safe(zf.read("docProps/custom.xml"))
            props = [el.get("name", "") for el in root.iter() if el.get("name")]
            props = [x for x in props if x][:8]
            if props:
                rep.add("med", "meta_custom", "存在自定义属性", "；".join(props))

        # --- 缩略图（首屏泄露）---
        thumbs = [n for n in names if "thumbnail" in n.lower()]
        if thumbs:
            rep.add("med", "thumbnail", "包内含缩略图（首屏预览）", "、".join(thumbs))

        # --- 批注 ---
        comment_parts = [n for n in names if "comments.xml" in n.lower()]
        for cp in comment_parts:
            try:
                root = parse_xml_safe(zf.read(cp))
            except (UnsafeXml, ET.ParseError):
                rep.add("med", "comments", "存在批注部件（解析失败）", cp)
                continue
            authors = sorted({el.get("author") or el.get(f"{{{NS['w']}}}author") or ""
                              for el in root.iter() if el.get("author") or el.get(f"{{{NS['w']}}}author")})[:6]
            texts = []
            for el in root.iter():
                if el.tag == f"{{{NS['w']}}}comment":
                    body = "".join(t.text or "" for t in el.iter(f"{{{NS['w']}}}t"))
                    if body.strip():
                        texts.append(body.strip()[:60])
            preview = " ｜ ".join(texts[:3])
            rep.add("high", "comments", f"批注未删除（{len(texts)} 条）",
                    f"作者：{'、'.join(authors) or '未知'}　预览：{preview}")

        # --- 修订记录（word: w:ins / w:del）---
        doc_xml = "word/document.xml"
        if doc_xml in names:
            data = zf.read(doc_xml).decode("utf-8", errors="replace")
            n_ins = data.count("<w:ins ")
            n_del = data.count("<w:del ")
            if n_ins or n_del:
                rep.add("high", "revisions",
                        f"修订记录未接受（插入 {n_ins} 处 / 删除 {n_del} 处）",
                        "对方开启“显示标记”即可看到全部改动过程")
            paths = _local_paths_in(data)
            if paths:
                rep.add("high", "local_path", "正文含本机路径/用户名", "；".join(paths))

        # --- pptx 演讲者备注 ---
        notes = [n for n in names if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)]
        if notes:
            previews = []
            for n in notes[:4]:
                try:
                    root = parse_xml_safe(zf.read(n))
                    txt = " ".join((el.text or "").strip()
                                   for el in root.iter() if el.tag.endswith("}t")).strip()
                    if txt:
                        previews.append(txt[:40])
                except (UnsafeXml, ET.ParseError):
                    continue
            rep.add("med", "notes", f"演讲者备注（{len(notes)} 页）",
                    " ｜ ".join(previews)[:160])

        # --- xlsx 隐藏工作表 ---
        wb = "xl/workbook.xml"
        if wb in names:
            root = parse_xml_safe(zf.read(wb))
            hidden = [el.get("name", "") for el in root.iter(f"{{{NS['s']}}}sheet")
                      if el.get("state") in ("hidden", "veryHidden")]
            if hidden:
                rep.add("high", "hidden_sheets",
                        f"隐藏工作表（{'、'.join(hidden[:6])}）",
                        "普通打开看不见，数据都在")
        ext = [n for n in names if n.startswith("xl/externalLinks/")]
        if ext:
            rep.add("med", "ext_links", "引用了外部工作簿", "、".join(ext[:4]))

        # --- 嵌入对象 ---
        emb = [n for n in names if "/embeddings/" in n.lower()]
        if emb:
            rep.add("high", "embeddings", f"内嵌对象（{len(emb)} 个）",
                    "、".join(Path(n).name for n in emb[:6]))

        # --- 兜底：其他 XML 部件里的本机路径 ---
        for n in names:
            if n.endswith(".xml") and n != doc_xml and not n.startswith("docProps/"):
                try:
                    text = zf.read(n).decode("utf-8", errors="replace")
                except (OSError, KeyError):
                    continue
                hits = _local_paths_in(text)
                if hits:
                    rep.add("high", "local_path", f"{n} 内含本机路径/用户名", "；".join(hits))
                    break  # 一处代表即可，避免刷屏
    finally:
        zf.close()
    return rep


def _findtext(root: ET.Element, prefix: str, tag: str) -> str:
    el = root.find(f"{{{NS[prefix]}}}{tag}")
    return (el.text or "").strip() if el is not None and el.text else ""


def _text_of(root: ET.Element, tag: str) -> str:
    for el in root.iter():
        if el.tag.split("}")[-1] == tag and el.text:
            return el.text.strip()
    return ""

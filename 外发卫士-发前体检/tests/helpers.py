"""合成 OOXML 测试文件生成器（zipfile 手工构建，不依赖 Office）。"""

from __future__ import annotations

import zipfile
from pathlib import Path

CT_HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
           '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
           '<Default Extension="xml" ContentType="application/xml"/>')
CT_TAIL = "</Types>"

RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        '{extra}</Relationships>')


def _core(creator="张三", last="李四", rev="7") -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
            ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/">'
            f'<dc:creator>{creator}</dc:creator><cp:lastModifiedBy>{last}</cp:lastModifiedBy>'
            f'<cp:revision>{rev}</cp:revision>'
            '<dcterms:modified>2026-09-26T10:00:00Z</dcterms:modified>'
            '</cp:coreProperties>')


def _app(company="绝密科技有限公司", template="") -> str:
    tpl = f"<Template>{template}</Template>" if template else "<Template></Template>"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            f'<Application>Microsoft Office Word</Application><Company>{company}</Company>'
            f'{tpl}</Properties>')


def _doc_body(paras: list[str], ins: bool = False, deled: bool = False,
              comments: bool = False) -> str:
    runs = "".join(
        f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paras)
    if ins:
        runs += ('<w:p><w:ins w:id="1" w:author="王五" w:date="2026-09-01T00:00:00Z">'
                 '<w:r><w:t>这句是修订插入的</w:t></w:r></w:ins></w:p>')
    if deled:
        runs += ('<w:p><w:del w:id="2" w:author="王五" w:date="2026-09-01T00:00:00Z">'
                 '<w:r><w:delText>这句被删除了</w:delText></w:r></w:del></w:p>')
    if comments:
        runs += ('<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>带批注的句子</w:t></w:r>'
                 '<w:commentRangeEnd w:id="0"/>'
                 '<w:r><w:commentReference w:id="0"/></w:r></w:p>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{runs}</w:body></w:document>')


def _comments_xml() -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:comment w:id="0" w:author="王五" w:date="2026-09-01T00:00:00Z">'
            '<w:p><w:r><w:t>这里报价太高了，改一下</w:t></w:r></w:p></w:comment>'
            '</w:comments>')


def build_docx(path: Path, *, creator="张三", company="绝密科技有限公司",
               template="", comments=True, ins=True, deled=True,
               thumbnail=False, custom=False, bomb=False) -> Path:
    """合成最小但结构完整的 docx。"""
    entries: dict[str, str | bytes] = {
        "[Content_Types].xml": (
            CT_HEAD
            + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            + '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
            + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            + '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            + ('<Override PartName="/docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>' if custom else "")
            + CT_TAIL),
        "_rels/.rels": RELS.format(
            extra=('<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" Target="docProps/custom.xml"/>' if custom else "")),
        "word/document.xml": _doc_body(
            ["第一段：项目背景介绍", "第二段：报价 88.50 元"],
            ins=ins, deled=deled, comments=comments),
        "docProps/core.xml": _core(creator=creator),
        "docProps/app.xml": _app(company=company, template=template),
    }
    if comments:
        entries["word/comments.xml"] = _comments_xml()
        entries["word/_rels/document.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>'
            '</Relationships>')
    if thumbnail:
        entries["docProps/thumbnail.jpeg"] = b"\xff\xd8\xff\xe0fakejpeg"
    if custom:
        entries["docProps/custom.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"'
            ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            '<property fmtid="{D5CDD505}" pid="2" name="内部代号"><vt:lpwstr>凤凰计划</vt:lpwstr></property>'
            '</Properties>')
    if bomb:
        entries["word/evil.xml"] = (
            '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
            ']><r>&a;</r>')
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def build_xlsx(path: Path, *, hidden: str | None = "秘密表") -> Path:
    sheets = ('<sheet name="正常数据" sheetId="1" r:id="rId1"/>'
              if hidden is None else
              f'<sheet name="正常数据" sheetId="1" r:id="rId1"/>'
              f'<sheet name="{hidden}" sheetId="2" state="hidden" r:id="rId2"/>')
    entries = {
        "[Content_Types].xml": (
            CT_HEAD
            + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + CT_TAIL),
        "_rels/.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                        '</Relationships>'),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
            ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{sheets}</sheets></workbook>'),
        "xl/sharedStrings.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si><t>客户名单</t></si></sst>'),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def build_pptx(path: Path, *, notes=True) -> Path:
    entries = {
        "[Content_Types].xml": (
            CT_HEAD
            + '<Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            + ('<Override PartName="/ppt/notesSlides/notesSlide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>' if notes else "")
            + CT_TAIL),
        "_rels/.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>'
                        '</Relationships>'),
        "ppt/presentation.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                 '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'),
        "ppt/slides/slide1.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:cSld><p:spTree><p:txBody><a:t xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">首页标题</a:t></p:txBody></p:spTree></p:cSld></p:sld>'),
    }
    if notes:
        entries["ppt/notesSlides/notesSlide1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            '<p:cSld><p:spTree><p:txBody><a:t xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">备注：这页讲给老板的，别外传</a:t></p:txBody></p:spTree></p:cSld></p:notes>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path

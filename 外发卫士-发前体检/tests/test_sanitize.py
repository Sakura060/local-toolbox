"""净化往返测试：净化 → 复检归零 → 结构完整 → 内容保留。"""

import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from helpers import build_docx, build_xlsx

from inspector.inspect import inspect_file, sanitize_file
from inspector.ooxml import extract_blocks, parse_xml_safe


def _structural_ok(path: Path) -> tuple[bool, str]:
    """独立复算结构校验（测试侧不用产品代码的 _structural_check）。"""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return False, "bad zip"
    names = set(zf.namelist())
    ct = zf.read("[Content_Types].xml").decode("utf-8")
    for m in re.finditer(r'<Override\b[^>]*\bPartName="/([^"]+)"', ct):
        if m.group(1) not in names:
            return False, f"CT->missing {m.group(1)}"
    for n in names:
        if n.endswith(".rels"):
            for m in re.finditer(r'Target="([^"]+)"', zf.read(n).decode("utf-8")):
                t = m.group(1)
                if t.startswith("http"):
                    continue
                base = n.split("_rels/")[0].rstrip("/")
                resolved = (base + "/" + t if base else t).lstrip("/")
                while "/../" in resolved:
                    seg = resolved.split("/../")
                    resolved = seg[0].rsplit("/", 1)[0] + "/" + seg[1]
                if resolved not in names:
                    return False, f"{n} -> missing {resolved} ({t})"
    zf.close()
    return True, ""


class TestSanitizeDocx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        src = build_docx(self.d / "a.docx",
                         template=r"C:\Users\zhangsan\AppData\x.dotm")
        rep0 = inspect_file(src)
        self.assertGreater(rep0.counts()["high"], 0)

        dst = self.d / "a_净.docx"
        result = sanitize_file(src, dst)
        self.assertIsNotNone(result)
        self.assertTrue(any("comments" in r for r in result["removed"]))
        self.assertTrue(dst.is_file())

        # 复检：高危与注意应清零（正文路径类为手工项，本样例正文无路径）
        rep1 = inspect_file(dst)
        self.assertEqual(rep1.counts()["high"], 0,
                         f"残留高危: {[f.as_dict() for f in rep1.findings]}")
        self.assertEqual(rep1.counts()["med"], 0,
                         f"残留注意: {[f.as_dict() for f in rep1.findings]}")

    def test_structure_and_content_preserved(self):
        src = build_docx(self.d / "b.docx")
        dst = self.d / "b_s.docx"
        sanitize_file(src, dst)

        ok, why = _structural_ok(dst)
        self.assertTrue(ok, why)

        # 正文保留：原段落 + 修订插入的内容仍在；被删除的文本消失
        blocks = extract_blocks(dst)
        self.assertIn("第一段：项目背景介绍", blocks)
        self.assertIn("这句是修订插入的", blocks)
        self.assertNotIn("这句被删除了", blocks)

        # 元数据已清空
        with zipfile.ZipFile(dst) as zf:
            core = zf.read("docProps/core.xml").decode("utf-8")
            self.assertNotIn("张三", core)
            self.assertNotIn("李四", core)

        # 批注部件已删除，引用标记已清
        with zipfile.ZipFile(dst) as zf:
            self.assertNotIn("word/comments.xml", zf.namelist())
            doc = zf.read("word/document.xml").decode("utf-8")
            self.assertNotIn("commentRangeStart", doc)
            self.assertNotIn("commentReference", doc)
            parse_xml_safe(doc)

    def test_original_untouched(self):
        src = build_docx(self.d / "c.docx")
        before = src.read_bytes()
        sanitize_file(src, self.d / "c_s.docx")
        self.assertEqual(src.read_bytes(), before)

    def test_idempotent(self):
        src = build_docx(self.d / "d.docx")
        dst1 = self.d / "d1.docx"
        dst2 = self.d / "d2.docx"
        sanitize_file(src, dst1)
        r2 = sanitize_file(dst1, dst2)
        rep = inspect_file(dst2)
        self.assertEqual(rep.counts()["high"], 0)
        self.assertEqual(rep.counts()["med"], 0)
        self.assertEqual(r2["removed"], [])

    def test_xlsx_hidden_sheet_visible(self):
        src = build_xlsx(self.d / "a.xlsx")
        dst = self.d / "a_s.xlsx"
        sanitize_file(src, dst)
        rep = inspect_file(dst)
        self.assertNotIn("hidden_sheets", {f.kind for f in rep.findings})
        with zipfile.ZipFile(dst) as zf:
            wb = zf.read("xl/workbook.xml").decode("utf-8")
            self.assertNotIn('state="hidden"', wb)
            parse_xml_safe(wb)
        ok, why = _structural_ok(dst)
        self.assertTrue(ok, why)

    def test_never_overwrites_source(self):
        src = build_docx(self.d / "e.docx")
        before = src.read_bytes()
        with self.assertRaises(ValueError):
            sanitize_file(src, src)  # 同路径必须被拒绝
        self.assertEqual(src.read_bytes(), before)  # 原文件毫发无损


if __name__ == "__main__":
    unittest.main()

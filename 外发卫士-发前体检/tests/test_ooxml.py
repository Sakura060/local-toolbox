"""OOXML 体检测试：合成 docx/xlsx/pptx 断言各类发现。"""

import tempfile
import unittest
from pathlib import Path

from helpers import build_docx, build_pptx, build_xlsx

from inspector.inspect import inspect_file


class TestOoxmlDocx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _kinds(self, rep):
        return {f.kind for f in rep.findings}

    def test_full_danger_docx(self):
        p = build_docx(self.d / "a.docx", custom=True, thumbnail=True,
                       template=r"C:\Users\zhangsan\AppData\Roaming\模板.dotm")
        rep = inspect_file(p)
        kinds = self._kinds(rep)
        for k in ("comments", "revisions", "meta_author", "meta_company",
                  "meta_template", "meta_rev", "meta_custom", "thumbnail"):
            self.assertIn(k, kinds, f"缺发现项 {k}")
        self.assertEqual(rep.counts()["high"] >= 3, True)
        tpl = next(f for f in rep.findings if f.kind == "meta_template")
        self.assertIn("zhangsan", tpl.detail)

    def test_comment_content_preview(self):
        p = build_docx(self.d / "b.docx")
        rep = inspect_file(p)
        cm = next(f for f in rep.findings if f.kind == "comments")
        self.assertIn("王五", cm.detail)
        self.assertIn("报价太高", cm.detail)

    def test_clean_docx_still_has_meta(self):
        p = build_docx(self.d / "c.docx", comments=False, ins=False, deled=False,
                       creator="", company="", template="")
        rep = inspect_file(p)
        kinds = self._kinds(rep)
        self.assertNotIn("comments", kinds)
        self.assertNotIn("revisions", kinds)
        self.assertNotIn("meta_template", kinds)

    def test_extract_blocks(self):
        from inspector.ooxml import extract_blocks

        p = build_docx(self.d / "d.docx")
        blocks = extract_blocks(p)
        self.assertIn("第一段：项目背景介绍", blocks)
        self.assertIn("这句是修订插入的", blocks)  # w:ins 内文本也提取
        self.assertNotIn("这句被删除了", blocks)   # w:delText 不是 w:t

    def test_bomb_xml_does_not_crash(self):
        p = build_docx(self.d / "e.docx", bomb=True)
        rep = inspect_file(p)  # 恶意部件应被安全跳过
        self.assertTrue(len(rep.findings) >= 1)


class TestXlsxPptx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_hidden_sheet(self):
        p = build_xlsx(self.d / "a.xlsx")
        rep = inspect_file(p)
        hs = next(f for f in rep.findings if f.kind == "hidden_sheets")
        self.assertIn("秘密表", hs.title)

    def test_no_hidden_sheet(self):
        p = build_xlsx(self.d / "b.xlsx", hidden=None)
        rep = inspect_file(p)
        self.assertNotIn("hidden_sheets", {f.kind for f in rep.findings})

    def test_pptx_notes(self):
        p = build_pptx(self.d / "a.pptx")
        rep = inspect_file(p)
        nt = next(f for f in rep.findings if f.kind == "notes")
        self.assertIn("别外传", nt.detail)

    def test_pptx_no_notes(self):
        p = build_pptx(self.d / "b.pptx", notes=False)
        rep = inspect_file(p)
        self.assertNotIn("notes", {f.kind for f in rep.findings})


if __name__ == "__main__":
    unittest.main()

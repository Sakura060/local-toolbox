"""diff 纯函数测试。"""

import unittest

from docline.diff import diff_blocks


class TestDiff(unittest.TestCase):
    def test_added_removed_changed(self):
        a = ["项目背景说明文字", "预算总额为一百二十万元", "结尾致谢段落"]
        b = ["项目背景说明文字", "预算总额调整为一百三十五万元",
             "结尾致谢段落", "新增的验收标准章节"]
        d = diff_blocks(a, b)
        self.assertEqual(d["added"], ["新增的验收标准章节"])
        self.assertEqual(d["same"], 2)
        self.assertEqual(len(d["changed"]), 1)
        self.assertIn("一百二十万", d["changed"][0]["old"])
        self.assertIn("一百三十五万", d["changed"][0]["new"])

    def test_pure_delete(self):
        d = diff_blocks(["第一段", "第二段", "第三段"], ["第一段", "第三段"])
        self.assertEqual(d["removed"], ["第二段"])
        self.assertEqual(d["added"], [])
        self.assertEqual(d["same"], 2)

    def test_identical(self):
        d = diff_blocks(["a", "b"], ["a", "b"])
        self.assertEqual(d["same"], 2)
        self.assertFalse(d["added"])
        self.assertFalse(d["removed"])
        self.assertFalse(d["changed"])

    def test_empty(self):
        d = diff_blocks([], ["新的一段"])
        self.assertEqual(d["added"], ["新的一段"])
        d2 = diff_blocks(["旧的一段"], [])
        self.assertEqual(d2["removed"], ["旧的一段"])

    def test_whitespace_insensitive_match(self):
        d = diff_blocks(["内容  带空格"], ["内容 带空格"])
        self.assertEqual(d["same"], 1)


if __name__ == "__main__":
    unittest.main()

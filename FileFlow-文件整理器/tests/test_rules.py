"""规则引擎单元测试。"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fileflow.rules import Rule, RuleEngine, RuleError, load_rules
from fileflow.scanner import FileInfo


def make_file(name: str, size: int = 100, mtime: float = 1700000000.0) -> FileInfo:
    return FileInfo(path=os.path.join(r"D:\x", name), name=name, stem=name, suffix="",
                    size=size, mtime=mtime, depth=0)


def ffile(name: str, **kw) -> FileInfo:
    """构造带正确 suffix 的 FileInfo。"""
    stem, dot, suffix = name.rpartition(".")
    if not dot or stem == "":
        stem, suffix = name, ""
    base = dict(path=os.path.join(r"D:\x", name), name=name, stem=stem,
                suffix=suffix.lower(), size=100, mtime=1700000000.0, depth=0)
    base.update(kw)
    return FileInfo(**base)


class RuleParsingTest(unittest.TestCase):
    def test_valid_extension_rule(self):
        r = Rule.from_dict({"name": "图片", "type": "extension",
                            "pattern": "jpg png", "target": "图片"})
        self.assertEqual(r._extensions(), {"jpg", "png"})

    def test_extension_pattern_normalization(self):
        r = Rule.from_dict({"name": "x", "type": "extension",
                            "pattern": ".JPG  Png ", "target": "t"})
        self.assertEqual(r._extensions(), {"jpg", "png"})

    def test_missing_fields_raise(self):
        with self.assertRaises(RuleError):
            Rule.from_dict({"name": "x", "type": "extension"})
        with self.assertRaises(RuleError):
            Rule.from_dict({"name": "x", "type": "bad", "target": "t"})
        with self.assertRaises(RuleError):
            Rule.from_dict("not a dict")
        with self.assertRaises(RuleError):
            Rule.from_dict({"name": "x", "type": "extension", "target": "t"})  # 无 pattern
        with self.assertRaises(RuleError):
            Rule.from_dict({"name": "x", "type": "size", "target": "t"})       # 无 min/max

    def test_load_rules_defaults_and_priority(self):
        rules = load_rules([{"name": "自定义", "type": "extension",
                             "pattern": "jpg", "target": "我的图"}])
        self.assertEqual(rules[0].name, "自定义")   # 用户规则在前
        self.assertTrue(any(r.name == "图片" for r in rules))

    def test_load_rules_no_defaults(self):
        rules = load_rules([], use_defaults=False)
        self.assertEqual(rules, [])


class RuleMatchTest(unittest.TestCase):
    def test_extension_match(self):
        r = Rule(name="图片", type="extension", target="图片", pattern="jpg png")
        self.assertTrue(r.matches(ffile("a.JPG")))
        self.assertTrue(r.matches(ffile("b.png")))
        self.assertFalse(r.matches(ffile("c.gif")))
        self.assertFalse(r.matches(ffile("jpg")))     # 无扩展名

    def test_name_pattern_match(self):
        r = Rule(name="发票", type="name_pattern", target="财务", pattern="*发票*")
        self.assertTrue(r.matches(ffile("2026 发票-final.pdf")))
        self.assertFalse(r.matches(ffile("invoice.pdf")))

    def test_date_rule_always_matches_and_formats(self):
        r = Rule(name="归档", type="date", target="归档/{date}", date_format="%Y-%m")
        f = ffile("a.txt", mtime=1700000000.0)   # 2023-11-14 UTC+8 ≈ 2023-11
        self.assertTrue(r.matches(f))
        self.assertTrue(r.destination(f).startswith("归档/2023-"))

    def test_size_match(self):
        r = Rule(name="大文件", type="size", target="大", min_mb=1, max_mb=10)
        self.assertTrue(r.matches(ffile("a.bin", size=5 * 1024 * 1024)))
        self.assertFalse(r.matches(ffile("a.bin", size=100)))
        self.assertFalse(r.matches(ffile("a.bin", size=20 * 1024 * 1024)))
        r2 = Rule(name="超大", type="size", target="大", min_mb=100)
        self.assertTrue(r2.matches(ffile("a.bin", size=200 * 1024 * 1024)))

    def test_engine_first_match_wins(self):
        engine = RuleEngine([
            Rule(name="先", type="extension", target="先", pattern="jpg"),
            Rule(name="后", type="extension", target="后", pattern="jpg png"),
        ])
        m = engine.match(ffile("a.jpg"))
        self.assertEqual(m.rule.name, "先")
        m2 = engine.match(ffile("b.png"))
        self.assertEqual(m2.rule.name, "后")
        m3 = engine.match(ffile("c.xyz"))
        self.assertIsNone(m3.rule)


if __name__ == "__main__":
    unittest.main()

"""预设规则模板测试（v0.5）。"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fileflow.rules import PRESETS, Rule, RuleEngine, RuleError, load_preset, load_rules
from fileflow.scanner import FileInfo


def ffile(name: str, **kw) -> FileInfo:
    stem, dot, suffix = name.rpartition(".")
    if not dot or stem == "":
        stem, suffix = name, ""
    base = dict(path=os.path.join(r"D:\x", name), name=name, stem=stem,
                suffix=suffix.lower(), size=100, mtime=1700000000.0, depth=0)
    base.update(kw)
    return FileInfo(**base)


class PresetLoadingTest(unittest.TestCase):
    def test_all_presets_are_valid(self):
        """所有预设都必须能通过规则校验并生成引擎。"""
        for name in PRESETS:
            rules = load_preset(name)
            self.assertTrue(rules, f"预设 {name} 为空")
            engine = RuleEngine([Rule.from_dict(r) for r in rules])
            self.assertTrue(engine.rules)

    def test_unknown_preset_raises(self):
        with self.assertRaises(RuleError):
            load_preset("no-such-preset")

    def test_preset_replaces_defaults(self):
        rules = load_rules(preset="developer")
        names = {r.name for r in rules}
        self.assertIn("代码", names)
        self.assertIn("配置", names)
        self.assertNotIn("音频", names)     # developer 预设没有音频

    def test_user_rules_priority_over_preset(self):
        rules = load_rules(
            [{"name": "我的图", "type": "extension", "pattern": "png", "target": "我的图"}],
            preset="designer",
        )
        self.assertEqual(rules[0].name, "我的图")   # 用户规则在前
        self.assertEqual(rules[0].matches(ffile("a.png")), True)

    def test_preset_none_uses_defaults(self):
        rules = load_rules(preset=None)
        names = {r.name for r in rules}
        self.assertIn("图片", names)
        self.assertIn("音频", names)

    def test_finance_date_rule_format(self):
        rules = load_rules(preset="finance")
        date_rules = [r for r in rules if r.type == "date"]
        self.assertEqual(len(date_rules), 1)
        self.assertIn("{date}", date_rules[0].target)


class PresetMatchTest(unittest.TestCase):
    def test_developer_preset_matches(self):
        engine = RuleEngine(load_rules(preset="developer"))
        self.assertEqual(engine.match(ffile("main.py")).rule.name, "代码")
        self.assertEqual(engine.match(ffile("package.json")).rule.name, "配置")
        self.assertEqual(engine.match(ffile("README.md")).rule.name, "文档")

    def test_finance_pattern_before_extension(self):
        """发票 name_pattern 必须先于扩展名规则命中。"""
        engine = RuleEngine(load_rules(preset="finance"))
        m = engine.match(ffile("2026-03 发票.pdf"))
        self.assertEqual(m.rule.name, "发票")
        self.assertTrue(m.destination.startswith("财务/发票"))


if __name__ == "__main__":
    unittest.main()

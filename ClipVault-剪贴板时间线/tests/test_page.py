"""page：前端资产完整性 —— 纯离线、关键元素齐全、动态值过 esc、隐私红线。"""

import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "clipvault" / "page.html"


class TestPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def test_key_elements_present(self):
        for needle in ('id="q"', 'id="list"', 'id="modal"', 'id="toast"',
                       'id="stat"', 'id="pausebtn"', 'id="clearbtn"',
                       'id="pausednote"'):
            self.assertIn(needle, self.html)

    def test_no_external_assets(self):
        externals = re.findall(r'(?:src|href)\s*=\s*["\']https?://', self.html)
        self.assertEqual(externals, [])

    def test_actions_wired(self):
        for needle in ("/api/copy", "/api/pin", "/api/delete", "/api/clear",
                       "/api/pause", "/api/image/", "data-copy", "data-pin",
                       "data-del", "data-view"):
            self.assertIn(needle, self.html)

    def test_all_innerhtml_writes_use_esc(self):
        writes = self.html.count("innerHTML")
        self.assertGreater(writes, 0)
        self.assertGreaterEqual(self.html.count("esc("), writes)

    def test_dark_mode_support(self):
        self.assertIn("color-scheme: light", self.html)
        self.assertIn("html.dark", self.html)

    def test_escape_closes_modal(self):
        self.assertIn('"Escape"', self.html)

    def test_live_search_debounce(self):
        self.assertIn("setTimeout", self.html)

    def test_privacy_copy_present(self):
        self.assertIn("全部数据只在本机", self.html)
        self.assertIn("置顶的条目不会被", self.html)

    def test_deeplink_support(self):
        # v0.2：接收 ToolHub 联邦搜索深链 ?q=&focus=<id>，定位条目卡并高亮
        self.assertIn("URLSearchParams", self.html)
        self.assertIn('"focus"', self.html)
        self.assertIn('id="entry-', self.html)
        self.assertIn("scrollIntoView", self.html)
        self.assertIn("flashbg", self.html)
        self.assertIn("prefers-reduced-motion", self.html)
        # 焦点条目不在结果里时明确提示，不静默失败
        self.assertIn("不在当前结果里", self.html)


if __name__ == "__main__":
    unittest.main()

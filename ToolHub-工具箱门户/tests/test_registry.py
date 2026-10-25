"""registry：白名单完整性、bat 文件真实存在、路径前缀匹配。"""

import unittest

from toolhub.registry import (TOOLS, WORKSPACE_ROOT, get_tool, path_allowed,
                              search_tools)


class TestRegistry(unittest.TestCase):
    def test_ids_unique_and_ascii(self):
        ids = [t.id for t in TOOLS]
        self.assertEqual(len(ids), len(set(ids)))
        for i in ids:
            self.assertTrue(i.isascii() and i.replace("-", "").isalnum())

    def test_ports_unique_and_loopback_range(self):
        ports = [t.port for t in TOOLS]
        self.assertEqual(len(ports), len(set(ports)))
        for p in ports:
            self.assertTrue(1024 <= p <= 65535)

    def test_every_bat_exists_on_disk(self):
        for t in TOOLS:
            if t.bat:
                self.assertTrue(t.bat_path.is_file(),
                                f"{t.id} 的启动脚本不存在: {t.bat_path}")

    def test_workspace_root_derived_correctly(self):
        self.assertEqual(WORKSPACE_ROOT.name, "项目大全")

    def test_expected_tools_present(self):
        ids = {t.id for t in TOOLS}
        self.assertEqual(
            ids,
            {"fileflow", "landrop-share", "landrop-receive", "laotu", "hukou",
             "bilidigest", "docline", "inspector", "notenest", "clipvault"},
        )

    def test_search_flags(self):
        self.assertEqual({t.id for t in search_tools()},
                         {"laotu", "hukou", "notenest", "clipvault"})

    def test_icons_present_and_unique(self):
        icons = [t.icon for t in TOOLS]
        self.assertTrue(all(i != "🧰" for i in icons), "9 个工具都应有专属图标")
        self.assertEqual(len(icons), len(set(icons)))

    def test_get_tool_whitelist_only(self):
        self.assertIsNone(get_tool("../laotu"))
        self.assertIsNone(get_tool("laotu "))
        self.assertIsNone(get_tool(""))
        self.assertIsNotNone(get_tool("laotu"))

    def test_path_allowed_segment_boundary(self):
        yes = ("/api", "/thumb", "/img")
        self.assertTrue(path_allowed("/api", yes))
        self.assertTrue(path_allowed("/api/search?q=1", yes))
        self.assertTrue(path_allowed("/thumb/12.jpg", yes))
        self.assertFalse(path_allowed("/apix", yes))
        self.assertFalse(path_allowed("/", yes))
        self.assertFalse(path_allowed("api", yes))
        self.assertFalse(path_allowed("", yes))


if __name__ == "__main__":
    unittest.main()

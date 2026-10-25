"""NoteStore 存储层单元测试。"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notenest.store import (MAX_ASSET, NoteStore, extract_title, make_excerpt,
                            strip_color_markup)


class TitleTest(unittest.TestCase):
    def test_heading_title(self):
        self.assertEqual(extract_title("# 我的笔记\n\n正文"), "我的笔记")

    def test_first_nonempty_line_fallback(self):
        self.assertEqual(extract_title("\n\n第一行文字"), "第一行文字")

    def test_no_title(self):
        self.assertEqual(extract_title(""), "无标题")

    def test_excerpt_skips_title(self):
        ex = make_excerpt("# 标题\n\n这是正文内容")
        self.assertNotIn("标题", ex)
        self.assertIn("正文", ex)


class ColorMarkupTest(unittest.TestCase):
    """文字颜色标注：摘要里只留文字，不显示 {色}…{/色} 标记。"""

    def test_strip_named_color(self):
        self.assertEqual(strip_color_markup("{red}重要{/red}内容"), "重要内容")

    def test_strip_chinese_alias_and_hex(self):
        src = "{红}重点{/红}和{#e11d48}警告{/#e11d48}"
        self.assertEqual(strip_color_markup(src), "重点和警告")

    def test_strip_nested_color_markup(self):
        self.assertEqual(strip_color_markup("{red}{orange}重点{/orange}{/red}"), "重点")

    def test_strip_keeps_unmatched_markers(self):
        self.assertEqual(strip_color_markup("{red}没闭合"), "{red}没闭合")
        self.assertEqual(strip_color_markup("{a}文字{/b}"), "{a}文字{/b}")

    def test_excerpt_strips_color_markup(self):
        ex = make_excerpt("# 标题\n\n{red}很重要{/red}的内容")
        self.assertEqual(ex, "很重要的内容")
        self.assertNotIn("{red}", ex)


class AssetTest(unittest.TestCase):
    """图片资产：内容寻址入库 + 路径防护 + 连带删除。"""

    PNG = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="nn-asset-"))
        self.store = NoteStore(self.root)
        self.note = self.store.create("图", "正文")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_save_asset_content_addressed(self):
        url = self.store.save_asset(self.note.id, self.PNG, ".png")
        self.assertRegex(url, r"^assets/%s/[a-f0-9]{16}\.png$" % self.note.id)
        p = self.root / url
        self.assertTrue(p.is_file())
        self.assertEqual(p.read_bytes(), self.PNG)
        self.assertEqual(self.store.save_asset(self.note.id, self.PNG, ".png"), url)  # 去重

    def test_jpeg_alias_normalised(self):
        url = self.store.save_asset(self.note.id, b"jpegdata", ".jpeg")
        self.assertTrue(url.endswith(".jpg"))

    def test_asset_path_resolves_and_guards(self):
        url = self.store.save_asset(self.note.id, self.PNG, ".png")
        name = url.rsplit("/", 1)[-1]
        self.assertIsNotNone(self.store.asset_path(self.note.id, name))
        for bad in ["../x.png", "x.png", name.upper(), "a" * 16 + ".exe", "", ".."]:
            self.assertIsNone(self.store.asset_path(self.note.id, bad))
        self.assertIsNone(self.store.asset_path("../etc", name))
        self.assertIsNone(self.store.asset_path(self.note.id, "a" * 16 + ".png"))

    def test_save_asset_rejects_bad_input(self):
        self.assertIsNone(self.store.save_asset(self.note.id, self.PNG, ".exe"))
        self.assertIsNone(self.store.save_asset(self.note.id, b"", ".png"))
        self.assertIsNone(self.store.save_asset(self.note.id, b"x" * (MAX_ASSET + 1), ".png"))
        self.assertIsNone(self.store.save_asset("zzzzzzzz", self.PNG, ".png"))

    def test_delete_removes_assets(self):
        self.store.save_asset(self.note.id, self.PNG, ".png")
        self.assertTrue((self.root / "assets" / self.note.id).is_dir())
        self.assertTrue(self.store.delete(self.note.id))
        self.assertFalse((self.root / "assets" / self.note.id).exists())

    def test_excerpt_strips_images(self):
        ex = make_excerpt("# 标题\n\n![猫](assets/abc/1234567890abcdef.png) 真好看")
        self.assertIn("猫", ex)
        self.assertIn("真好看", ex)
        self.assertNotIn("assets/", ex)
        self.assertEqual(make_excerpt("# t\n\n![](assets/a/1234567890abcdef.png)"), "🖼")


class NoteStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="nn-store-"))
        self.store = NoteStore(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_and_get(self):
        n = self.store.create("购物清单", "牛奶\n鸡蛋")
        got = self.store.get(n.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.title, "购物清单")
        self.assertIn("牛奶", got.content)
        self.assertTrue(got.content.startswith("# 购物清单"))

    def test_create_empty_content(self):
        n = self.store.create("空笔记", "")
        self.assertTrue((self.root / f"{n.id}.md").exists())

    def test_update(self):
        n = self.store.create("初稿", "v1")
        u = self.store.update(n.id, "# 改题\nv2")
        self.assertEqual(u.title, "改题")
        self.assertIn("v2", u.content)
        got = self.store.get(n.id)
        self.assertEqual(got.title, "改题")

    def test_delete(self):
        n = self.store.create("待删", "x")
        self.assertTrue(self.store.delete(n.id))
        self.assertIsNone(self.store.get(n.id))
        self.assertFalse(self.store.delete(n.id))   # 二次删除 False

    def test_list_sorted_by_updated(self):
        n1 = self.store.create("早", "a")
        n2 = self.store.create("晚", "b")
        import os
        os.utime(self.root / f"{n1.id}.md", (1000, 1000))
        os.utime(self.root / f"{n2.id}.md", (2000, 2000))
        lst = self.store.list_notes()
        self.assertEqual(lst[0]["title"], "晚")
        self.assertEqual(len(lst), 2)
        self.assertIn("excerpt", lst[0])

    def test_search_title_and_content(self):
        self.store.create("Python 笔记", "装饰器用法")
        self.store.create("购物", "买 python 书")      # 正文含关键词
        self.store.create("无关", "hello")
        self.assertEqual(len(self.store.list_notes("python")), 2)
        self.assertEqual(len(self.store.list_notes("PYTHON")), 2)   # 不区分大小写
        self.assertEqual(len(self.store.list_notes("存在的不行")), 0)

    def test_invalid_id_returns_none(self):
        for bad in ["../etc", "abc$%!", "", "a" * 100, ".."]:
            self.assertIsNone(self.store.get(bad))
            self.assertFalse(self.store.delete(bad))
            self.assertIsNone(self.store.update(bad, "x"))
        # 不应产生任何新文件
        self.assertEqual(len(list(self.root.glob("*"))), 0)

    def test_stats(self):
        self.store.create("a", "x" * 100)
        self.store.create("b", "y")
        s = self.store.stats()
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["total_size"], sum(f.stat().st_size for f in self.root.glob("*.md")))


if __name__ == "__main__":
    unittest.main()

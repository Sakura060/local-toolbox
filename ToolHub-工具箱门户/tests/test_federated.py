"""federated：扇出、归一化、失败折叠。全部打到假上游，不碰真实工具。"""

import json
import unittest
import urllib.parse

from toolhub import federated
from toolhub.registry import Tool
from tests.helpers import dead_port, make_fake_upstream


def make_tool(tid: str, port: int) -> Tool:
    return Tool(id=tid, name=tid, desc="", port=port, search=True)


class TestFederated(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.laotu_srv, laotu_port = make_fake_upstream(
            lambda path: (200, {"items": [{
                "id": 1, "name": "hello.png", "mtime": 1700000000.0,
                "source": "微信", "snippet": "命中 关键词 的窗口",
                "ocr_text": "正文",
            }]})
        )
        cls.note_srv, note_port = make_fake_upstream(
            lambda path: (200, {"notes": [{
                "id": "abcd1234", "title": "会议笔记",
                "updated": 1700000001.0, "excerpt": "摘要内容",
            }]})
        )
        cls.hukou_srv, hukou_port = make_fake_upstream(
            lambda path: (200, {"items": [{
                "path": "C:\\data\\report.docx", "detail": "来自微信接收",
                "mtime": 1700000002.0, "exists": True,
            }]})
        )
        cls.tools = (
            make_tool("laotu", laotu_port),
            make_tool("notenest", note_port),
            make_tool("hukou", hukou_port),
        )
        cls.srvs = [cls.laotu_srv, cls.note_srv, cls.hukou_srv]

    @classmethod
    def tearDownClass(cls):
        for s in cls.srvs:
            s.shutdown()
            s.server_close()

    def test_all_groups_ok_and_normalized(self):
        out = federated.federated_search("关键词", tools=self.tools)
        self.assertEqual(out["q"], "关键词")
        self.assertEqual(len(out["groups"]), 3)
        by_id = {g["id"]: g for g in out["groups"]}
        self.assertTrue(all(g["ok"] for g in by_id.values()))
        # 捞图归一化：缩略图走同源代理
        laotu = by_id["laotu"]["items"][0]
        self.assertEqual(laotu["url"], "/proxy/laotu/thumb/1.jpg")
        self.assertEqual(laotu["image_url"], "/proxy/laotu/img/1")
        self.assertEqual(laotu["title"], "hello.png")
        self.assertEqual(laotu["extra"], "微信")
        # NoteNest：详情端点走代理
        note = by_id["notenest"]["items"][0]
        self.assertEqual(note["detail_url"], "/proxy/notenest/api/notes/abcd1234")
        self.assertEqual(note["title"], "会议笔记")
        # 户口本：在盘标注
        hk = by_id["hukou"]["items"][0]
        self.assertEqual(hk["extra"], "在盘")
        self.assertEqual(hk["title"], "C:\\data\\report.docx")

    def test_empty_query_returns_pending_groups(self):
        out = federated.federated_search("   ", tools=self.tools)
        self.assertEqual(out["q"], "")
        self.assertTrue(all(g["ok"] is None for g in out["groups"]))

    def test_offline_tool_folds_into_error_group(self):
        offline = (make_tool("laotu", dead_port()),)
        out = federated.federated_search("x", tools=offline)
        self.assertFalse(out["groups"][0]["ok"])
        self.assertIn("离线", out["groups"][0]["error"])

    def test_upstream_500_reported(self):
        srv, port = make_fake_upstream(lambda path: (500, {"error": "boom"}))
        try:
            out = federated.federated_search("x", tools=(make_tool("laotu", port),))
            self.assertFalse(out["groups"][0]["ok"])
            self.assertIn("500", out["groups"][0]["error"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_malformed_json_reported(self):
        srv, port = make_fake_upstream(lambda path: (200, "not json at all"))
        try:
            out = federated.federated_search("x", tools=(make_tool("laotu", port),))
            self.assertFalse(out["groups"][0]["ok"])
            self.assertIn("解析失败", out["groups"][0]["error"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_notenest_results_sliced_to_limit(self):
        srv, port = make_fake_upstream(lambda path: (200, {"notes": [
            {"id": str(i), "title": f"t{i}", "updated": float(i), "excerpt": ""}
            for i in range(5)
        ]}))
        try:
            out = federated.federated_search("t", tools=(make_tool("notenest", port),), limit=2)
            g = out["groups"][0]
            self.assertTrue(g["ok"])
            self.assertEqual(g["count"], 2)
            self.assertEqual([it["title"] for it in g["items"]], ["t0", "t1"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_unexpected_structure_reported(self):
        srv, port = make_fake_upstream(lambda path: (200, {"unexpected": True}))
        try:
            out = federated.federated_search("x", tools=(make_tool("laotu", port),))
            self.assertFalse(out["groups"][0]["ok"])
            self.assertIn("结构", out["groups"][0]["error"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_clipvault_adapter_normalizes(self):
        srv, port = make_fake_upstream(lambda path: (200, {"entries": [{
            "id": 3, "ts": 1700000003.0, "kind": "text",
            "preview": "复制过的报错信息", "size": 24,
        }]}))
        try:
            out = federated.federated_search("报错", tools=(make_tool("clipvault", port),))
            g = out["groups"][0]
            self.assertTrue(g["ok"])
            self.assertEqual(g["count"], 1)
            it = g["items"][0]
            self.assertEqual(it["title"], "复制过的报错信息")
            self.assertEqual(it["extra"], "文本")
            # 深链：?q= 过滤 + focus 定位条目，宿主是工具自身端口
            self.assertEqual(
                it["open_url"],
                "http://127.0.0.1:%d/?q=%s&focus=3"
                % (port, urllib.parse.quote("报错")))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_clipvault_item_without_id_has_no_deeplink(self):
        srv, port = make_fake_upstream(lambda path: (200, {"entries": [{
            "ts": 1700000003.0, "kind": "image", "preview": "", "size": 9,
        }]}))
        try:
            out = federated.federated_search("x", tools=(make_tool("clipvault", port),))
            it = out["groups"][0]["items"][0]
            self.assertIsNone(it["open_url"])
            self.assertEqual(it["title"], "（空内容）")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_unknown_search_tool_has_no_adapter(self):
        srv, port = make_fake_upstream(lambda path: (200, {}))
        try:
            out = federated.federated_search("x", tools=(make_tool("fileflow", port),))
            self.assertIn("适配器", out["groups"][0]["error"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_query_encoding_reaches_upstream(self):
        seen = {}

        def responder(path):
            seen["p"] = path
            return (200, {"items": []})

        srv, port = make_fake_upstream(responder)
        try:
            federated.federated_search("会 议", tools=(make_tool("laotu", port),), limit=5)
            self.assertIn("/api/search?", seen["p"])
            self.assertIn("limit=5", seen["p"])
            self.assertIn("q=%E4%BC%9A+%E8%AE%AE", seen["p"])
        finally:
            srv.shutdown()
            srv.server_close()


class TestRecent(unittest.TestCase):
    def test_recent_slices_per_tool(self):
        def many(path):
            # 空查询也应拉到最近列表（上游 recent 模式）
            return (200, {"items": [
                {"id": i, "name": f"shot{i}.png", "mtime": 1700000000.0 + i,
                 "source": "", "snippet": "", "ocr_text": ""} for i in range(3)]
            })

        srv, port = make_fake_upstream(many)
        try:
            out = federated.recent((make_tool("laotu", port),), per_tool=2)
            g = out["groups"][0]
            self.assertTrue(g["ok"])
            self.assertEqual(g["count"], 2)
            self.assertEqual([it["title"] for it in g["items"]],
                             ["shot0.png", "shot1.png"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_recent_offline_group_folded(self):
        out = federated.recent((make_tool("hukou", dead_port()),), per_tool=2)
        self.assertFalse(out["groups"][0]["ok"])
        self.assertEqual(out["groups"][0]["items"], [])


class TestToolNote(unittest.TestCase):
    def setUp(self):
        federated._note_cache.clear()

    def test_pending_count_note(self):
        srv, port = make_fake_upstream(
            lambda p: (200, {"total": 134, "ocr_ok": 0, "errors": 0}))
        try:
            note = federated.tool_note(make_tool("laotu", port))
            self.assertIsNotNone(note)
            self.assertIn("待索引 134 张", note)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_no_note_when_fully_indexed(self):
        srv, port = make_fake_upstream(
            lambda p: (200, {"total": 10, "ocr_ok": 10, "errors": 0}))
        try:
            self.assertIsNone(federated.tool_note(make_tool("laotu", port)))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_offline_or_garbage_yields_none(self):
        self.assertIsNone(federated.tool_note(make_tool("laotu", dead_port())))
        srv, port = make_fake_upstream(lambda p: (200, "not json"))
        try:
            self.assertIsNone(federated.tool_note(make_tool("laotu", port)))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_only_laotu_has_note_adapter(self):
        self.assertIsNone(federated.tool_note(make_tool("notenest", dead_port())))

    def test_clipvault_paused_note(self):
        srv, port = make_fake_upstream(
            lambda p: (200, {"total": 4, "paused": True, "by_kind": {}}))
        try:
            note = federated.tool_note(make_tool("clipvault", port))
            self.assertIsNotNone(note)
            self.assertIn("采集已暂停", note)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_clipvault_running_no_note_and_strict_bool(self):
        # paused=False 正常运行无提示；字符串 "false" 也绝不能触发徽标（D24 同族）
        srv, port = make_fake_upstream(
            lambda p: (200, {"total": 4, "paused": False}))
        try:
            self.assertIsNone(federated.tool_note(make_tool("clipvault", port)))
        finally:
            srv.shutdown()
            srv.server_close()
        srv, port = make_fake_upstream(
            lambda p: (200, {"total": 4, "paused": "false"}))
        try:
            self.assertIsNone(federated.tool_note(make_tool("clipvault", port)))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_hukou_deep_not_run_note(self):
        srv, port = make_fake_upstream(
            lambda p: (200, {"origins": 3, "events": 0, "files_by_vol": {}}))
        try:
            note = federated.tool_note(make_tool("hukou", port))
            self.assertIsNotNone(note)
            self.assertIn("deep", note)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_hukou_with_snapshot_no_note(self):
        srv, port = make_fake_upstream(
            lambda p: (200, {"origins": 3, "events": 12, "files_by_vol": {"C": 999}}))
        try:
            self.assertIsNone(federated.tool_note(make_tool("hukou", port)))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_note_survives_non_dict_stats_body(self):
        # /api/stats 返回合法 JSON 但不是对象（如数组/字符串）→ None 不抛
        srv, port = make_fake_upstream(lambda p: (200, ["unexpected"]))
        try:
            self.assertIsNone(federated.tool_note(make_tool("hukou", port)))
        finally:
            srv.shutdown()
            srv.server_close()


class TestToolNoteCache(unittest.TestCase):
    def setUp(self):
        federated._note_cache.clear()

    def test_second_call_hits_cache(self):
        calls = []

        def responder(path):
            calls.append(path)
            return (200, {"total": 9, "ocr_ok": 0})

        srv, port = make_fake_upstream(responder)
        try:
            t = make_tool("laotu", port)
            self.assertIsNotNone(federated.tool_note(t))
            self.assertIsNotNone(federated.tool_note(t))
            self.assertEqual(len(calls), 1, "TTL 内第二次调用不应再打上游")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_none_result_not_cached(self):
        # 失败不缓存：离线期过后恢复在线，下一轮就该出徽标
        dead = dead_port()
        t = make_tool("laotu", dead)
        self.assertIsNone(federated.tool_note(t))
        srv, port = make_fake_upstream(lambda p: (200, {"total": 3, "ocr_ok": 0}))
        try:
            t2 = make_tool("laotu", port)
            self.assertIsNotNone(federated.tool_note(t2))
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()

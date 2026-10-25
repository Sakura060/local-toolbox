"""seg 纯函数测试：分词、查询词、FTS 表达式。"""

import unittest

from laotu import seg


class TestSeg(unittest.TestCase):
    def test_segment_chinese(self):
        out = seg.segment("快递单号2026红色优惠券")
        self.assertIn("单号", out)
        self.assertIn("2026", out)
        self.assertIn("优惠券", out)
        # 空格分隔
        self.assertNotIn("  ", out)

    def test_segment_empty_and_noise(self):
        self.assertEqual(seg.segment(""), "")
        self.assertEqual(seg.segment("。。。！！！"), "")

    def test_query_terms_dedup(self):
        terms = seg.query_terms("红色 红色 优惠券！")
        self.assertEqual(terms, ["红色", "优惠券"])

    def test_query_terms_empty(self):
        self.assertEqual(seg.query_terms("   "), [])
        self.assertEqual(seg.query_terms(None), [])

    def test_fts_expr_quotes_safe(self):
        expr = seg.fts_expr(["红色", "优惠券"])
        self.assertEqual(expr, '"红色" AND "优惠券"')
        self.assertEqual(seg.fts_expr(['a"b']), '"ab"')
        self.assertEqual(seg.fts_expr([]), "")

    def test_roundtrip_query_terms_of_seg_output(self):
        text = "报销发票金额88.50元"
        # 查询侧切分结果应能在入库侧切分结果中找到（词级一致）
        seg_out = seg.segment(text).split(" ")
        for t in seg.query_terms(text):
            self.assertIn(t, seg_out)


if __name__ == "__main__":
    unittest.main()

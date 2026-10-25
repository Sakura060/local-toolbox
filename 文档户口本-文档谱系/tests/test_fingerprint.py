"""指纹与关系判定（纯函数，块级匹配）。"""

import unittest

from docline.fingerprint import block_containment, prepare, relation


def P(blocks):
    return prepare(blocks)


class TestFingerprint(unittest.TestCase):
    def test_identical(self):
        p = P(["第一段内容", "第二段内容"])
        self.assertEqual(block_containment(p, p), 1.0)
        self.assertEqual(relation(p, p)["kind"], "dup")

    def test_subset_is_child(self):
        parent = P(["共同的头部内容一", "共同的主体内容二", "共同的结尾内容三"])
        child = P(["共同的头部内容一", "共同的主体内容二",
                   "共同的结尾内容三", "全新的补充章节内容"])
        self.assertEqual(block_containment(parent, child), 1.0)
        self.assertLessEqual(block_containment(child, parent), 0.75)
        self.assertLess(block_containment(child, parent), 1.0)
        self.assertEqual(relation(parent, child)["kind"], "a_parent_of_b")

    def test_unrelated(self):
        a = P(["今天天气晴朗适合出门散步顺便买了菜"])
        b = P(["数据库连接字符串应当加密存储并定期轮换"])
        self.assertEqual(relation(a, b)["kind"], "unrelated")

    def test_small_edit_pair(self):
        # 同一块的小幅修改：内容方向不可判定 → small_edit_pair
        a = P(["报价清单：项目一 88.50 元", "项目二 120.00 元",
               "项目三 66.00 元", "总计 274.50 元"])
        b = P(["报价清单：项目一 88.50 元", "项目二 128.00 元",
               "项目三 66.00 元", "总计 282.50 元"])
        self.assertEqual(relation(a, b)["kind"], "small_edit_pair")

    def test_changed_para_breaks_exact_hash_keeps_near_match(self):
        a = P(["开头段：介绍项目背景与目标",
               "预算段：总额一百二十万元含税",
               "结尾段：联系人张三"])
        b = P(["开头段：介绍项目背景与目标",
               "预算段：总额一百三十五万元含税",
               "结尾段：联系人张三"])
        # 修改段近似命中（shingle 通道），containment 仍应为 1.0
        self.assertEqual(block_containment(a, b), 1.0)

    def test_empty(self):
        e = P([])
        self.assertEqual(block_containment(e, e), 0.0)


if __name__ == "__main__":
    unittest.main()

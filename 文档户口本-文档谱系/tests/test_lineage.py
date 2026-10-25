"""谱系重建测试：演化链 / 重复副本 / 分叉 / 传递约简。"""

import unittest

from docline.fingerprint import prepare
from docline.lineage import lineage, union_find_clusters


def node(nid, blocks, mtime):
    return {"id": nid, "mtime": mtime, "prep": prepare(blocks)}


A1 = "本项目旨在建设一套面向班级的作业收集系统，支持浏览器上传与自动归档"
A2 = "系统采用浏览器上传方式收集作业文件，教师端可以批量下载"
A3 = "数据存储在教师机的本地目录中，按周自动归档备份"
A4 = "新增功能：支持查重与自动打分模块，减轻教师负担"
A5 = "二期规划：接入消息通知与家长端入口，覆盖家校沟通"
A2X = "系统采用浏览器上传方式收集作业文件，并按班级自动分类整理"
X1 = "分叉版本甲：引入了全新的模板引擎与渲染管线，重写了页面结构"
X2 = "分叉版本甲：同时重构了权限模型和日志系统，支持审计导出"
Y1 = "分叉版本乙：改用了完全不同的数据同步方案，基于差量上传"
Y2 = "分叉版本乙：并新增了离线优先的编辑器组件，弱网可用"
U1 = "今天菜谱：红烧排骨、清炒时蔬、番茄蛋汤，晚餐三人份"


class TestLineage(unittest.TestCase):
    def _chain(self):
        """V1 → V2(含重复副本) → V3。"""
        return [
            node(1, [A1, A2, A3], 1000.0),
            node(2, [A1, A2X, A3, A4], 2000.0),
            node(3, [A1, A2X, A3, A4, A5], 3000.0),
            node(4, [A1, A2X, A3, A4], 2500.0),  # V2 的重复副本
        ]

    def test_cluster_grouped(self):
        nodes = self._chain() + [node(9, [U1], 500.0)]
        assign = union_find_clusters([str(n["id"]) for n in nodes],
                                     {str(n["id"]): n["prep"] for n in nodes})
        self.assertEqual(assign["1"], assign["2"])
        self.assertEqual(assign["2"], assign["3"])
        self.assertEqual(assign["3"], assign["4"])
        self.assertNotEqual(assign["9"], assign["1"])  # 无关文档独立成簇

    def test_chain_lineage(self):
        lin = lineage(self._chain())
        edges = {(e["parent"], e["child"]) for e in lin["edges"]}
        self.assertIn((1, 2), edges)
        self.assertIn((2, 3), edges)
        # 传递约简：1→3 直连应被去掉
        self.assertNotIn((1, 3), edges)
        self.assertEqual(lin["latest"], 3)
        self.assertFalse(lin["fork"])
        # 重复副本分组
        self.assertTrue(any(2 in g and 4 in g for g in lin["dup_groups"]))

    def test_fork(self):
        nodes = [
            node(1, [A1, A2, A3], 1000.0),
            node(2, [A1, A2, A3, X1, X2], 2000.0),
            node(3, [A1, A2, A3, Y1, Y2], 2000.0),
        ]
        lin = lineage(nodes)
        edges = {(e["parent"], e["child"]) for e in lin["edges"]}
        self.assertIn((1, 2), edges)
        self.assertIn((1, 3), edges)
        self.assertNotIn((2, 3), edges)
        self.assertTrue(lin["fork"])
        self.assertEqual(set(lin["sinks"]), {2, 3})

    def test_single_node(self):
        lin = lineage([node(1, [A1], 100.0)])
        self.assertEqual(lin["latest"], 1)
        self.assertFalse(lin["fork"])
        self.assertEqual(lin["edges"], [])


if __name__ == "__main__":
    unittest.main()

"""合并本轮决策/TODO/日志到状态文件（并行会话协同：只追加不覆盖）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DECISIONS_ADD = """

## D18 文档户口本核心算法（2026-09-27）
短文档版本比对禁用全文 shingle（2 段 vs 3 段 Jaccard 仅 0.19，实验证伪）。采用**块级指纹**：每段 = SHA-256 精确 hash + 4-gram shingle + 归一化串，三通道判定"块是否还在"（精确→近似 jaccard→SequenceMatcher）。谱系规则：块包含率 ≥0.75 判后代；双向皆高且 |差|<0.2 为对称小改动（方向由 mtime 定）；传递约简去冗余边；sinks（无后代节点）= 终版候选，多个即标 ⚠分叉（诚实不猜方向）。**dup 语义 = 归一化块集合精确相等**——近似匹配只服务包含率，防止"改两个字的版本"被判成重复副本。

## D19 净化器字节手术原则（2026-09-27）
OOXML 净化不做整树重写（ElementTree 换前缀会破坏 mc:Ignorable 引用），用正则做最小字节手术，手术后 XML 必须过 parse_xml_safe 良构校验，失败回退原文并标"需手工处理"；删除部件必须同步清理 _rels 与 [Content_Types] Override；产物自校验（rels Target 存在 + Override 一致 + 关键 XML 可解析）不过即抛错拒交付。rels 相对 Target 按 rels 文件所在目录解析（word/_rels/document.xml.rels 的基是 word/）。
"""

TODO_ADD = """
## 外发卫士-发前体检（v0.1 完成，30 测试）
- [P2] PDF 深度解析与净化（引入 pikepdf；v0.1 只检不清）
- [P2] Explorer 右键"发前体检"菜单；拖入文件夹批量体检
- [P3] xlsx 批注 vmlDrawing 联动清理

## 文档户口本-文档谱系（v0.1 完成，25 测试）
- [P1] 全盘后台监控（watch 模式 + 开机自启），新副本自动入谱系
- [P2] pptx 表格 cell 级 diff；pdf 支持；与文件户口本入口互相跳转
- [P3] 可选本地小模型给 diff 写人话摘要（用户自带 key）
"""

DEVLOG_ADD = """
[02:10] 第二批双产品交付：外发卫士 + 文档户口本（BLUEPRINT 批准计划执行）
外发卫士 v0.1：OOXML/EXIF/PDF/ZIP 风险分级体检 + 白名单重组净化（修订接受/rels 清理/结构自校验）；30 测试；真实简历冒烟（用户名+AIGC 属性，净化归零）。Mimosa 两度拦截全部采纳：XML 拒 DOCTYPE/ENTITY、MD5→SHA-256
文档户口本 v0.1：块级指纹三通道 + 包含率谱系 + 传递约简 + 分叉/重复副本判定 + 谱系树/diff Web；25 测试；真实数据：简历重复对合并 ✓，python 试题真分叉标警 ✓（diff 实证 854/196/295 差异为真实分歧）
算法教训：全文 shingle 对短文档失效（0.19 Jaccard）→ 块级指纹；sinks 判断曾写反（无后代 vs 无父节点）；对称小改动需 mtime 定向；dup 语义收敛为 hash 精确相等
e2e_server 再立功：文档户口本 cli 缺 get_meta 方法（重写丢失）被真启动抓到
并行会话协同：合并而非覆盖 PROJECT_STATE/TODO；DECISIONS 编号避让到 D18+
"""

for name, add in (("DECISIONS.md", DECISIONS_ADD),
                  ("TODO.md", TODO_ADD),
                  ("DEV_LOG.md", DEVLOG_ADD)):
    p = ROOT / name
    p.write_text(p.read_text(encoding="utf-8") + add, encoding="utf-8")
    print(f"appended {name}")

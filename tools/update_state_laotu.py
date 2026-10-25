"""Final state sync: DECISIONS D18 + DEV_LOG entry for laotu v0.2."""
from pathlib import Path

d = Path(__file__).resolve().parent.parent / "DECISIONS.md"
t = d.read_text(encoding="utf-8")
t += """
## D18 多会话并行协调规则（2026-09-27，重要）
工作区可能同时运行多个自主开发会话。实证：本会话工作期间，另一并行会话新建并活跃开发了
"文档户口本-文档谱系"（8640）与"外发卫士-发前体检"（8641）两个项目（gen_bats.py 预注册表可查认领情况），
且**并行会话不更新共享状态文件**（PROJECT_STATE/TODO/DEV_LOG 里零记录）。
规则：①开工任何新项目前，先 `dir` 检查文件夹是否已存在、查 gen_bats.py 预注册表；②发现别人活跃的项目立即换目标，绝不写入/覆盖对方目录；③自己每完成一步就更新共享状态文件（这是唯一协调信道）；④测试端口避开 8890/8640/8641 及其他项目常驻端口。

## D19 捞图 v0.2 相似查重设计（2026-09-27）
dHash（9×8 灰度相邻比较，纯 Pillow 无 freetype）入库，查询侧并查集分组（汉明距 ≤8 相似）。
两个存储坑：①SQLite INTEGER 上限 2^63-1，64 位哈希入库需转有符号（读出转回无符号）；
②0 不能做"未计算"哨兵——纯色图合法哈希恰为 0，必须用 NULL。旧记录靠重扫回填
（skip 分支检测 dhash IS NULL → 只解码不 OCR，幂等）。keep 策略=组内 mtime 最新。
"""
d.write_text(t, encoding="utf-8")

p = Path(__file__).resolve().parent.parent / "DEV_LOG.md"
t = p.read_text(encoding="utf-8")
t += """
[12:30-13:40] 捞图 v0.2（相似查重 + 时间线 + 来源标注）+ 并行会话协调
完成：
  - 发现"文档户口本-文档谱系""外发卫士-发前体检"被并行会话认领并活跃开发（本想开工版本侦探，立即让位）；制定 D18 协调规则。
  - 捞图 v0.2：hashes.py（dHash/汉明/并查集分组）、sources.py（微信/QQ 路径推测）、db 三列迁移（dhash/source 幂等 ALTER）、索引时算哈希+旧记录重扫回填（hashed 指标）、/api/dupes（保留每组最新）、/api/timeline、/api/search?t0&t1 时间过滤、前端时间线柱图+查重面板。60 测试全绿 + e2e OK + 种子库浏览器实测（柱图 3 天、查重 2 组、点柱过滤 1 张）。
发现（全是真金）：
  1. SQLite INTEGER 上限 2^63-1：64 位 dHash 直接入库会 OverflowError → 有符号转换层（D19）
  2. 纯色图 dHash 恰为 0，与"未计算"哨兵撞车 → 改用 NULL 哨兵（D19）
  3. 测试手算汉明距第三次翻车（7 vs 9 差 3 不是 2；1<<50 离小整数只有 1-4 位）→ 测试常量一律 python bit_count 核算
  4. 增量扫描会让旧记录永远拿不到 dHash → skip 分支回填（hashed 指标、幂等）
测试：捞图 60 ✅；e2e OK；浏览器面板实测 ✅。
"""
p.write_text(t, encoding="utf-8")
print("D18/D19 + DEV_LOG updated")

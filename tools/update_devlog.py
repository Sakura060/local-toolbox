"""Append this session's entry to workspace DEV_LOG.md."""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "DEV_LOG.md"
t = p.read_text(encoding="utf-8")
t += """
## 2026-09-27

[00:05-00:55] 消化器 v0.2 + NoteNest v0.2 + 户口本 v0.2（三线并进）
完成：
  - 消化器 P0 真实 ASR 全流程验证：SAPI 合成 61s 三主题英文语音（tools/tts_gen.ps1+merge_wav.py）→ base 模型全流程跑通。真实数据抓到真 bug：终结标点集合漏英文句号 "." → 整段黏成 2 句；修复后 10 句，关键词干净。另补上 BLUEPRINT 承诺的章节"话题突变"信号（词集 Jaccard≤0.10 且停顿≥1s），60s 合成音频正确切出 2 章。35 测试全绿。
  - NoteNest 自动保存：2s 去抖 + visibilitychange/beforeunload keepalive 兜底 + 失败 8s 重试 + openNote 清定时器防串写。浏览器实测：输入→"已自动保存"→刷新内容持久。20 测试全绿。
  - 户口本 deep 模式：usn.py 加 get_journal_info_full/read_journal_range；新模块 mft.py（frn 索引路径重建、环/孤儿兜底、谱系链纯函数）；db files 表+find_child/frn_index/events_for_frn；cli deep 命令（无管理员明确指引）；server /api/lineage；Web 身世卡谱系链；query 显示谱系。46 测试全绿 + 8648 种子库浏览器实测 + e2e E2E_OK。
发现：
  1. huggingface.co 直连超时（10060）→ HF_ENDPOINT=https://hf-mirror.com 预下载 base 成功（D14）
  2. 本会话 Bash 命令被自动转后台（detached）→ onnxruntime OCR 推理再次挂起（load done 后无输出）；CTranslate2/faster-whisper 推理 detached 下完全正常——两者行为相反
  3. IAB 合成 Enter 不派发 keydown（input 正常）→ 测试改页内 evaluate 调 lookup()；产品给 type=search 补原生 search 事件兜底（D15）
  4. mft.resolve_path 初版索引方向写反（pfrn 索引不能向上走）→ 单测抓住，改 frn 索引
  5. 章节切分旧测试句间实际有 1.0s 间隔、恰好触发新信号 → 测试数据改真正无缝；词频并列标题按字母序，断言改结构化
解决：全部修复并回归。捞图全量索引维持"用户控制台跑"结论，已在 TODO 标注勿重试。
测试：消化器 35 ✅；NoteNest 20 ✅；户口本 46 ✅；E2E_OK（户口本）。
状态文件：PROJECT_STATE/TODO/DECISIONS(D14-D16)/DEV_LOG 已同步。
下一步：用户两动作（捞图索引、户口本 deep）；v0.3 候选见 TODO。
"""
p.write_text(t, encoding="utf-8")
print("DEV_LOG updated")

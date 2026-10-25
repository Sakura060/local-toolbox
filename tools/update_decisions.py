"""Append D14-D16 to workspace DECISIONS.md."""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "DECISIONS.md"
t = p.read_text(encoding="utf-8")
t += """
## D14 模型下载镜像（2026-09-27）
本机直连 huggingface.co 超时（WinError 10060）。faster-whisper 等模型下载统一加 `HF_ENDPOINT=https://hf-mirror.com`（hf-mirror 实测可达，200）。tools/preload_model.py 可预下载，之后离线可用。

## D15 浏览器自动化事件通道限制（2026-09-27）
ZCode 内嵌浏览器（IAB）的 Playwright 合成按键**不派发 keydown**（input/fill 事件正常到达）。Web 测试若依赖 Enter 提交，测试时改在页内直接调函数（evaluate 调 lookup()），产品侧则对 type=search 输入框补 `search` 原生事件兜底（户口本已加）。不要把"合成回车无效"误判为产品 bug。

## D16 消化器分句与章节（2026-09-27，真实 ASR 数据回归修复）
1. Whisper 英文段以 ASCII 句号结尾，终结标点集合必须含 "."（曾漏掉→整段黏成一句）；英文段拼接需补空格（中文不用）。
2. 章节切分双信号：停顿 ≥3.5s（硬边界）+ 前后窗口词集 Jaccard ≤0.10 且间隔 ≥1s（话题突变）。min_sents 防碎章。纯函数、确定性、合成数据可测。
"""
p.write_text(t, encoding="utf-8")
print("DECISIONS updated")

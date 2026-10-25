"""P0-3 卡片文案校对：抽各内置工具 README 的版本/功能要点，人工比对 cfg.BUILTINS。"""
import io
import re

WS = Path(__file__).resolve().parent.parent
PROJECTS = [
    "NoteNest-本地笔记", "B站收藏消化器-视频转重点", "FileFlow-文件整理器",
    "捞图-截图记忆库", "ClipVault-剪贴板时间线", "文件户口本-文件身世",
    "文档户口本-文档谱系", "外发卫士-发前体检", "LANDrop-lite-文件传输",
]
for proj in PROJECTS:
    try:
        t = io.open(WS + "\\" + proj + "\\README.md", encoding="utf-8").read()
    except OSError as e:
        print("#", proj, "NO README", e)
        continue
    # 标题 + 版本行 + 功能清单区（前 40 行里的要点）
    lines = [l for l in t.splitlines()][:40]
    print("#", proj)
    for l in lines:
        if re.match(r"^#{1,3} ", l) or re.search(r"v\d+\.\d+", l) or l.startswith("- "):
            print("  ", l.strip()[:110])
    print()

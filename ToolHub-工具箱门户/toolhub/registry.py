"""工具注册表：ToolHub 门户聚合的全部本地工具。

字段约定：
- id      ASCII slug，用于 /proxy/<id>/... 与 /api/launch 的白名单键
- port    工具默认监听端口（127.0.0.1）
- bat     相对工作区根的启动 .bat 路径；空串表示无启动脚本
- search  是否参与联邦搜索（门户后端扇出该工具的搜索 API）
- proxy   允许被 /proxy/<id> 透传的路径前缀（段边界精确匹配，防 /apix 绕过）

工作区根从包位置向上推导（toolhub/ -> 项目目录 -> 工作区根），不依赖 CWD。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# toolhub/registry.py -> toolhub -> 项目目录 -> 工作区根
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from localcore.net import path_allowed  # noqa: E402,F401 （共享件，兼容再导出）


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    desc: str
    port: int
    bat: str = ""
    search: bool = False
    proxy: tuple[str, ...] = ()
    icon: str = "🧰"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def bat_path(self) -> Path:
        return WORKSPACE_ROOT / self.bat


TOOLS: tuple[Tool, ...] = (
    Tool(
        id="fileflow",
        name="FileFlow 文件整理器",
        desc="按规则自动整理文件夹，支持预览/撤销/监控模式",
        port=8635,
        icon="🧹",
        bat="FileFlow-文件整理器\\启动Web界面.bat",
        proxy=("/api",),
    ),
    Tool(
        id="landrop-share",
        name="LANDrop 局域网共享",
        desc="把文件夹共享给局域网设备下载（手机扫码即取）",
        port=8800,
        icon="📡",
        bat="LANDrop-lite-文件传输\\启动共享.bat",
        proxy=(),
    ),
    Tool(
        id="landrop-receive",
        name="LANDrop 局域网接收",
        desc="接收局域网设备推送的文件，落到指定目录",
        port=8801,
        icon="📥",
        bat="LANDrop-lite-文件传输\\启动接收.bat",
        proxy=(),
    ),
    Tool(
        id="laotu",
        name="捞图 截图记忆库",
        desc="截图 OCR 入库，中文全文搜索 + 时间线 + 相似查重",
        port=8637,
        icon="🖼️",
        bat="捞图-截图记忆库\\启动捞图.bat",
        search=True,
        proxy=("/api", "/thumb", "/img"),
    ),
    Tool(
        id="hukou",
        name="文件户口本 文件身世",
        desc="回答这个文件从哪来：来源归因 + USN 事件 + 谱系链",
        port=8638,
        icon="📔",
        bat="文件户口本-文件身世\\启动户口本.bat",
        search=True,
        proxy=("/api",),
    ),
    Tool(
        id="bilidigest",
        name="B站收藏消化器",
        desc="本地 ASR 把收藏的视频转成关键词/重点句/章节",
        port=8639,
        icon="📺",
        bat="B站收藏消化器-视频转重点\\启动消化器.bat",
        proxy=("/api",),
    ),
    Tool(
        id="docline",
        name="文档户口本 文档谱系",
        desc="同源文档自动聚类成谱系树，任意两版逐段 diff",
        port=8640,
        icon="🌳",
        bat="文档户口本-文档谱系\\启动文档户口本.bat",
        proxy=("/api",),
    ),
    Tool(
        id="inspector",
        name="外发卫士 发前体检",
        desc="发文件前查隐私泄露风险（元数据/GPS/批注），一键净化另存",
        port=8641,
        icon="🛡️",
        bat="外发卫士-发前体检\\启动外发卫士.bat",
        proxy=("/api",),
    ),
    Tool(
        id="notenest",
        name="NoteNest 本地笔记",
        desc="纯本地 Markdown 笔记，自动保存、全文搜索",
        port=8890,
        icon="📝",
        bat="NoteNest-本地笔记\\启动.bat",
        search=True,
        proxy=("/api",),
    ),
    Tool(
        id="clipvault",
        name="ClipVault 剪贴板时间线",
        desc="剪贴板本地时间线：文本/图片/文件全记录，可搜索、一键回贴",
        port=8643,
        icon="📋",
        bat="ClipVault-剪贴板时间线\\启动剪贴板.bat",
        search=True,
        proxy=("/api",),
    ),
)

_BY_ID: dict[str, Tool] = {t.id: t for t in TOOLS}


def get_tool(tool_id: str) -> Tool | None:
    """按 id 取工具；id 必须精确命中白名单，杜绝任何拼接行为。"""
    return _BY_ID.get(tool_id)


def search_tools() -> tuple[Tool, ...]:
    return tuple(t for t in TOOLS if t.search)

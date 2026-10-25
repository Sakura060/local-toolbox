"""ToolHub Desktop：本地桌面应用层（壳 + 新 UI + 插件化注册表 + 环境自检）。

旧网页门户（toolhub/ 包，v0.11，8642）原样保留；本包是新增层，
复用 toolhub 的进程管理/联邦搜索/代理标准件，不改其任何代码。
"""

from __future__ import annotations

import sys
from pathlib import Path

__version__ = "0.3.0"

# desktop/ -> ToolHub-工具箱门户/（= toolhub 包所在目录）-> 工作区根（= localcore 所在目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent
for p in (str(PROJECT_ROOT), str(WORKSPACE_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

DESKTOP_PORT = 8650

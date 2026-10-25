"""ToolHub：本地工具箱统一门户。

依赖工作区共享件 localcore（D:\\项目大全\\localcore）——这里负责把它加进 sys.path。
"""

import sys
from pathlib import Path

__version__ = "0.11"

_WORKSPACE = Path(__file__).resolve().parent.parent.parent
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

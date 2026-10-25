"""上游访问层 —— 实现已抽取到工作区共享件 localcore.net，此处保留兼容导入。

新代码请直接 `from localcore.net import ...`；这个薄壳保证 ToolHub 既有
代码与测试不需要改动。
"""

from localcore.net import (  # noqa: F401
    DEFAULT_TIMEOUT,
    HOST,
    MAX_BODY,
    PROBE_TIMEOUT,
    FetchResult,
    UpstreamError,
    fetch,
    probe,
)

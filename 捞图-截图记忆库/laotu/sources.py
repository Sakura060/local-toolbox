"""目录启发式：从路径推断图片来源（微信/QQ/截图）。纯函数。

与「文件户口本」的归因哲学一致：只做只读路径模式识别，标注"推测"，
不猜测具体发送人（协议红线：不碰聊天软件数据格式，只看文件落在哪）。
"""

from __future__ import annotations

_RULES = [
    ("微信", ("xwechat_files", "wechat files", "weixin")),
    ("QQ", ("tencent files", "filerecv", "qqfile")),
]


def detect_source(path: str) -> str:
    """路径含已知接收目录特征 → 返回来源名；否则空串。大小写不敏感。"""
    p = path.replace("/", "\\").lower()
    for name, needles in _RULES:
        if any(n in p for n in needles):
            return name
    return ""

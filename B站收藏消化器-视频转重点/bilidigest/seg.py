"""中文分词：消化器与捞图同一套策略（jieba，去噪）。"""

import threading

import jieba

_lock = threading.Lock()
_ready = False

_PUNCT = set(
    ' \t\r\n\u3000，。！？；：、·「」『』（）《》〈〉【】…—'
    '"\'`()[]{}{},.?!,.!?;:<>/*-+=~@#$%^&_|\r\\'
)


def _ensure() -> None:
    global _ready
    with _lock:
        if not _ready:
            jieba.setLogLevel(60)
            jieba.initialize()
            _ready = True


def query_terms(q: str) -> list[str]:
    """切成词列表：去噪、去重、保序。"""
    _ensure()
    seen: set[str] = set()
    terms: list[str] = []
    for w in jieba.lcut((q or "").strip()):
        w = w.strip()
        if not w or all(ch in _PUNCT for ch in w) or w in seen:
            continue
        seen.add(w)
        terms.append(w)
    return terms

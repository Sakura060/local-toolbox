"""中文分词：入库与查询双侧使用同一套切分，保证 FTS5 命中一致。

FTS5 unicode61 对中文是"整句一个 token"，必须外部切分后以空格连接写入。
纯函数模块，便于测试。
"""

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


def _clean_word(w: str) -> str:
    return w.strip()


def _is_noise(w: str) -> bool:
    if not w:
        return True
    return all(ch in _PUNCT for ch in w)


def segment(text: str) -> str:
    """把任意文本切成空格连接的词序列（用于写入 FTS5）。"""
    _ensure()
    words = jieba.lcut(text or "")
    return " ".join(w for w in map(_clean_word, words) if not _is_noise(w))


def query_terms(q: str) -> list[str]:
    """把用户查询切成词列表（去重、去噪，保序）。"""
    _ensure()
    seen: set[str] = set()
    terms: list[str] = []
    for w in jieba.lcut((q or "").strip()):
        w = _clean_word(w)
        if _is_noise(w) or w in seen:
            continue
        seen.add(w)
        terms.append(w)
    return terms


def fts_expr(terms: list[str]) -> str:
    """把词列表转成 FTS5 MATCH 表达式（AND 语义，词内引号剔除防注入）。"""
    safe = [t.replace('"', "") for t in terms if t.replace('"', "")]
    return " AND ".join(f'"{t}"' for t in safe)

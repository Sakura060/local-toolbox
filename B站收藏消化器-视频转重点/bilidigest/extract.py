"""抽取式重点提取（纯函数，零 API token）。

管线：分句 → 章节（停顿间隔+话题跳变）→ 关键词（词频+停用词）→ 重点句（TextRank）。
诚实口径：这是"重点提取"，输出是原文摘录，不是生成式摘要。
"""

from __future__ import annotations

import math
import re

from . import seg

# 常用停用词（够用即可，不求全）
STOPWORDS = {
    "一下", "一下", "一个", "一些", "什么", "怎么", "这样", "那样", "这个", "那个",
    "我们", "你们", "他们", "自己", "大家", "就是", "但是", "然后", "所以", "因为",
    "如果", "可以", "没有", "不是", "还是", "这个", "其实", "现在", "时候", "知道",
    "觉得", "应该", "可能", "这种", "那种", "的话", "来说", "对于", "而且", "或者",
    "非常", "特别", "真的", "直接", "简单", "基本", "开始", "最后", "一下", "这个",
    "the", "a", "an", "is", "are", "to", "of", "and", "in", "on", "for", "it",
    "this", "that", "with", "as", "at", "by", "be", "you", "i", "we", "they",
}

_SENT_SPLIT = re.compile(r"[。！？!?；;\n\r]+")
_NUM = re.compile(r"^[0-9.%]+$")


def split_sentences(text: str) -> list[str]:
    """按中英文标点切句，去空句。纯函数。"""
    out = []
    for s in _SENT_SPLIT.split(text or ""):
        s = s.strip()
        if s:
            out.append(s)
    return out


def normalize_segments(segments: list[dict]) -> list[dict]:
    """清洗 ASR 分段：去空文本、按时间排序、确保 t0<=t1。"""
    out = []
    for s in segments or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        t0 = max(0.0, float(s.get("t0", 0.0)))
        t1 = max(t0, float(s.get("t1", t0)))
        out.append({"t0": t0, "t1": t1, "text": text})
    out.sort(key=lambda x: x["t0"])
    return out


def merge_to_sentences(segments: list[dict]) -> list[dict]:
    """把 Whisper 小段合并成句子级单元。

    归并条件（不合并则开新句）：
    - 上一句以终结标点结尾（含英文句号 "."）→ 开新句；
    - 上一句无终结标点，但与下一段间隔 ≥ 1.5s（自然停顿）→ 开新句；
    - 否则视为同一句被切断，合并（英文之间补空格，中文直接连接）。
    """
    segs = normalize_segments(segments)
    out: list[dict] = []
    for s in segs:
        if (out
                and out[-1]["text"][-1:] not in "。！？!?；;….…"
                and (s["t0"] - out[-1]["t1"]) < 1.5):
            prev = out[-1]
            joiner = ""
            tail = prev["text"][-1:]
            head = s["text"][:1]
            if (tail.isascii() and (tail.isalnum() or tail in ".,!?;:")
                    and head.isascii() and head.isalnum()):
                joiner = " "
            prev["text"] += joiner + s["text"]
            prev["t1"] = s["t1"]
        else:
            out.append({"t0": s["t0"], "t1": s["t1"], "text": s["text"]})
    return out


def tokens_of(text: str) -> list[str]:
    """内容词：jieba 分词 + 去停用词 + 去纯数字/单字符（数字串保留）。"""
    toks = []
    for w in seg.query_terms(text or ""):
        if w.lower() in STOPWORDS:
            continue
        if len(w) == 1 and not w.isdigit():
            continue
        if _NUM.match(w) and ("." not in w and "%" not in w and len(w) > 4):
            continue  # 过长的纯数字串多为时间戳/ID
        toks.append(w)
    return toks


def keywords(sents: list[dict], top: int = 10) -> list[dict]:
    """关键词：内容词频次排序。纯函数。"""
    freq: dict[str, int] = {}
    for s in sents:
        for w in tokens_of(s["text"]):
            freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return [{"word": w, "count": c} for w, c in ranked]


def _token_sets(sents: list[dict]) -> list[set[str]]:
    return [set(tokens_of(s["text"])) for s in sents]


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / (len(a) + len(b) - inter)


def key_sentences(sents: list[dict], top: int = 5, graph_cap: int = 300) -> list[dict]:
    """TextRank 重点句：相似度图 + PageRank 幂迭代。

    超长文字稿按等距抽样封顶（graph_cap）保证速度。
    """
    n = len(sents)
    if n == 0:
        return []
    idx_pool = list(range(n))
    if n > graph_cap:
        step = n / graph_cap
        idx_pool = sorted({int(i * step) for i in range(graph_cap)})
    sets = _token_sets(sents)
    m = len(idx_pool)

    # 邻接权重
    weights = [[0.0] * m for _ in range(m)]
    for i in range(m):
        for j in range(i + 1, m):
            w = _jaccard(sets[idx_pool[i]], sets[idx_pool[j]])
            if w > 0:
                weights[i][j] = w
                weights[j][i] = w

    rank = [1.0] * m
    d = 0.85
    for _ in range(30):
        new = [(1 - d) / m] * m
        for i in range(m):
            total = sum(weights[i])
            if total <= 0:
                continue
            for j in range(m):
                if weights[i][j] > 0:
                    new[j] += d * rank[i] * weights[i][j] / total
        rank = new

    picked = sorted(range(m), key=lambda i: -rank[i])[:top]
    picked.sort()  # 按出现顺序输出
    out = []
    for i in picked:
        s = sents[idx_pool[i]]
        out.append({"t0": s["t0"], "t1": s["t1"], "text": s["text"],
                    "score": round(rank[i], 4)})
    return out


def _context_sim(sets: list[set], i: int, win: int = 3) -> float:
    """句子 i 前后各 win 句的词集相似度（话题连贯性信号）。纯函数。"""
    left: set = set()
    for s in sets[max(0, i - win):i]:
        left |= s
    right: set = set()
    for s in sets[i:i + win]:
        right |= s
    return _jaccard(left, right)


def chapters(sents: list[dict], gap: float = 3.5, min_sents: int = 4,
             shift_sim: float = 0.10, shift_gap: float = 1.0) -> list[dict]:
    """章节切分：停顿边界 + 话题突变边界（纯函数、确定性）。

    - 停顿边界：句子间隔 ≥ gap 秒；
    - 话题突变：前后窗口词集相似度 ≤ shift_sim 且间隔 ≥ shift_gap 秒
      （连续语速没有长停顿时，靠关键词分布突变找到话题切换）；
    - 每章至少 min_sents 句，防止碎章。
    章标题 = 该章 top 关键词拼接。
    """
    if not sents:
        return []
    sets = _token_sets(sents)
    bounds = [0]
    for i in range(1, len(sents)):
        gap_i = sents[i]["t0"] - sents[i - 1]["t1"]
        is_gap = gap_i >= gap
        is_shift = (gap_i >= shift_gap
                    and _context_sim(sets, i) <= shift_sim
                    and _context_sim(sets, i, win=1) <= shift_sim)
        if (is_gap or is_shift) and i - bounds[-1] >= min_sents:
            bounds.append(i)
    bounds.append(len(sents))

    out = []
    for ci in range(len(bounds) - 1):
        chunk = sents[bounds[ci]:bounds[ci + 1]]
        if not chunk:
            continue
        kw = keywords(chunk, top=3)
        title = "第{}段 {}".format(ci + 1, "·".join(k["word"] for k in kw[:3]) if kw else "内容")
        out.append({
            "index": ci,
            "t0": chunk[0]["t0"],
            "t1": chunk[-1]["t1"],
            "title": title,
            "count": len(chunk),
        })
    return out


def digest(segments: list[dict]) -> dict:
    """全量重点提取：输入 ASR 分段，输出重点三件套。"""
    sents = merge_to_sentences(segments)
    kw = keywords(sents, top=10)
    keys = key_sentences(sents, top=5)
    chs = chapters(sents)
    total_dur = sents[-1]["t1"] if sents else 0.0
    return {
        "sentence_count": len(sents),
        "duration": round(total_dur, 1),
        "keywords": kw,
        "key_sentences": keys,
        "chapters": chs,
    }


def transcript_text(sents: list[dict]) -> str:
    """导出用：带时间戳的纯文字稿。"""
    lines = []
    for s in sents:
        m1, s1 = divmod(int(s["t0"]), 60)
        h, m1 = divmod(m1, 60)
        stamp = f"{h:02d}:{m1:02d}:{s1:02d}" if h else f"{m1:02d}:{s1:02d}"
        lines.append(f"[{stamp}] {s['text']}")
    return "\n".join(lines)

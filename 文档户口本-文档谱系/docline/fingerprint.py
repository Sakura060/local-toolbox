"""内容指纹：块级匹配（精确 hash + 近似 shingle）。

段落（块）是文档版本的领域单元：版本间的共享度按"块是否还在"计算，
比全文 shingle 对短文档稳健得多。全部纯函数。
"""

from __future__ import annotations

import hashlib
from difflib import SequenceMatcher

from .config import BLOCK_SIM, CLUSTER_SIM, DUP_CONTAIN, LINEAGE_CONTAIN


def _norm(s: str) -> str:
    return "".join(s.split())


def _bsh(text: str, k: int = 4) -> frozenset[str]:
    if len(text) < k:
        return frozenset([text] * bool(text))
    return frozenset(text[i:i + k] for i in range(len(text) - k + 1))


def prepare(blocks: list[str]) -> dict:
    """把块列表编译成可复用的指纹结构（每个文档只算一次）。"""
    items = []
    for b in blocks:
        n = _norm(b)
        if not n:
            continue
        h = hashlib.sha256(n.encode("utf-8")).hexdigest()[:16]
        items.append((h, _bsh(n), n))
    return {
        "blocks": items,                                   # (hash, shingles, norm)
        "hashes": frozenset(h for h, _, _ in items),
        "full": frozenset().union(*[s for _, s, _ in items]) if items else frozenset(),
    }


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / (len(a) + len(b) - inter)


def _block_in(h: str, s: str, sset: frozenset, pb: dict) -> bool:
    """一个块是否在 B 中存在（精确 hash / shingle / 序列相似度三通道）。"""
    if h in pb["hashes"]:
        return True
    for h2, s2, n2 in pb["blocks"]:
        if jaccard(sset, s2) >= BLOCK_SIM:
            return True
    for _, _, n2 in pb["blocks"]:
        if abs(len(s) - len(n2)) <= max(len(s), len(n2)) * (1 - BLOCK_SIM):
            if SequenceMatcher(None, s, n2).ratio() >= BLOCK_SIM:
                return True
    return False


def block_containment(pa: dict, pb: dict) -> float:
    """A 的块出现在 B 中的比例（1.0 = A 的全部段落都在 B 里）。"""
    if not pa["blocks"]:
        return 0.0
    matched = sum(1 for h, sset, s in pa["blocks"] if _block_in(h, s, sset, pb))
    return matched / len(pa["blocks"])


def relation(pa: dict, pb: dict) -> dict:
    """判定两文档关系（纯函数，阈值来自 config）。

    kind：dup（块集合精确相同）/ a_parent_of_b / b_parent_of_a /
    small_edit_pair（互相近包含的对称小改动）/ unrelated
    """
    sim = jaccard(pa["full"], pb["full"])
    ca = block_containment(pa, pb)
    cb = block_containment(pb, pa)
    kind = "unrelated"
    if pa["hashes"] == pb["hashes"]:
        kind = "dup"
    elif ca >= LINEAGE_CONTAIN and cb >= LINEAGE_CONTAIN and abs(ca - cb) < 0.2:
        kind = "small_edit_pair"
    elif ca >= LINEAGE_CONTAIN and ca > cb:
        kind = "a_parent_of_b"
    elif cb >= LINEAGE_CONTAIN and cb > ca:
        kind = "b_parent_of_a"
    return {"sim": round(sim, 4), "contain_a_in_b": round(ca, 4),
            "contain_b_in_a": round(cb, 4), "kind": kind}


def same_cluster(pa: dict, pb: dict) -> bool:
    if jaccard(pa["full"], pb["full"]) >= CLUSTER_SIM:
        return True
    ca = block_containment(pa, pb)
    cb = block_containment(pb, pa)
    return max(ca, cb) >= 0.6 or (ca >= LINEAGE_CONTAIN and cb >= LINEAGE_CONTAIN)

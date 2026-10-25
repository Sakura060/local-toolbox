"""聚类与谱系重建。纯函数，输入均为 (id, mtime, prepare 指纹) 记录。"""

from __future__ import annotations

from .config import LINEAGE_CONTAIN, MTIME_TOL
from .fingerprint import block_containment


def union_find_clusters(ids: list[str], preps: dict[str, dict]) -> dict[str, str]:
    """同源聚类：并查集。返回 id → 簇代表 id。"""
    from .fingerprint import same_cluster

    parent: dict[str, str] = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ids[i], ids[j]
            if same_cluster(preps[a], preps[b]):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra
    return {i: find(i) for i in ids}


def lineage(nodes: list[dict]) -> dict:
    """簇内谱系重建。nodes: [{"id","mtime","prep"}...]（同一簇内）。

    规则：
    - 归一化块集合精确相同 → 同一版本的重复副本（合并 dup 组）
    - A→B（B 是 A 的后代）：A 的块基本都在 B 里，且方向由包含率差异或 mtime 决定
    - 传递约简：A→B 且 B→C 则去掉 A→C
    """
    ids = [n["id"] for n in nodes]
    preps = {n["id"]: n["prep"] for n in nodes}
    mt = {n["id"]: n.get("mtime", 0.0) for n in nodes}

    # 同版本重复组：块集合精确相同
    dup_of: dict[str, str] = {}
    dup_groups: list[list[str]] = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if a in dup_of or b in dup_of:
                continue
            if preps[a]["hashes"] == preps[b]["hashes"]:
                older, newer = (a, b) if mt[a] <= mt[b] else (b, a)
                dup_of[newer] = older
                dup_groups.append([older, newer])

    real = [i for i in ids if i not in dup_of]

    # 祖先边：方向由内容包含率定；对称小改动对按 mtime 定（旧 → 新）
    raw_edges: list[tuple[str, str, float]] = []
    seen_pairs: set[tuple[str, str]] = set()

    def add_edge(a: str, b: str, c: float) -> None:
        if (a, b) not in seen_pairs:
            seen_pairs.add((a, b))
            raw_edges.append((a, b, c))

    for i in range(len(real)):
        for j in range(len(real)):
            if i == j:
                continue
            a, b = real[i], real[j]
            ca = block_containment(preps[a], preps[b])
            cb = block_containment(preps[b], preps[a])
            if ca >= LINEAGE_CONTAIN and cb >= LINEAGE_CONTAIN:
                # 对称小改动：新的那份是旧的那次编辑的后代
                older, newer = (a, b) if mt[a] <= mt[b] else (b, a)
                if mt[newer] >= mt[older] - MTIME_TOL:
                    add_edge(older, newer, round(max(ca, cb), 4))
            elif ca >= LINEAGE_CONTAIN and mt[b] >= mt[a] - MTIME_TOL:
                add_edge(a, b, round(ca, 4))
            elif cb >= LINEAGE_CONTAIN and mt[a] >= mt[b] - MTIME_TOL:
                add_edge(b, a, round(cb, 4))

    # 传递约简
    children: dict[str, list[str]] = {}
    for a, b, _c in raw_edges:
        children.setdefault(a, []).append(b)
    edges: list[tuple[str, str, float]] = []
    for a, b, c in raw_edges:
        transitive = any(x != b and x in children and b in children[x]
                         for x in children.get(a, []))
        if not transitive:
            edges.append((a, b, c))

    edge_sources = {a for a, _, _ in edges}
    # 终点（sinks）= 没有后代的节点 = "最终版本"候选
    sinks = [i for i in real if i not in edge_sources]
    latest = max(sinks, key=lambda i: mt[i]) if sinks else (
        max(real, key=lambda i: mt[i]) if real else None)
    fork = len(sinks) > 1

    return {
        "edges": [{"parent": a, "child": b, "containment": c} for a, b, c in edges],
        "latest": latest,
        "fork": fork,
        "sinks": sinks,
        "dup_of": dup_of,
        "dup_groups": dup_groups,
    }

"""段落级 diff：精确匹配 → 近似配对 → 增/删/改分类。纯函数。"""

from __future__ import annotations

import difflib

from .config import BLOCK_SIM


def _norm(s: str) -> str:
    return "".join(s.split())


def diff_blocks(a: list[str], b: list[str]) -> dict:
    """对比两个块的文本序列。返回：

    {"added": [b文本], "removed": [a文本], "changed": [{"old","new","ratio"}],
     "same": n}
    """
    a_norm = [_norm(x) for x in a]
    b_norm = [_norm(x) for x in b]

    a_left: dict[int, bool] = {i: True for i in range(len(a))}
    b_left: dict[int, bool] = {j: True for j in range(len(b))}
    changed: list[dict] = []
    same = 0

    # 1) 精确匹配
    b_index: dict[str, list[int]] = {}
    for j, nb in enumerate(b_norm):
        b_index.setdefault(nb, []).append(j)
    for i, na in enumerate(a_norm):
        lst = b_index.get(na)
        if lst:
            j = lst[0]
            if b_left.get(j):
                a_left[i] = False
                b_left[j] = False
                same += 1

    # 2) 近似配对（剩余项两两 SequenceMatcher，贪心取最高）
    rem_a = [i for i, keep in a_left.items() if keep]
    rem_b = [j for j, keep in b_left.items() if keep]
    pairs: list[tuple[float, int, int]] = []
    for i in rem_a:
        for j in rem_b:
            if not b_left[j] or not a_left[i]:
                continue
            ratio = difflib.SequenceMatcher(None, a_norm[i], b_norm[j]).ratio()
            if ratio >= BLOCK_SIM:
                pairs.append((ratio, i, j))
    pairs.sort(reverse=True)
    for ratio, i, j in pairs:
        if a_left[i] and b_left[j]:
            a_left[i] = False
            b_left[j] = False
            changed.append({"old": a[i], "new": b[j], "ratio": round(ratio, 3)})

    added = [b[j] for j, keep in b_left.items() if keep]
    removed = [a[i] for i, keep in a_left.items() if keep]
    changed.sort(key=lambda c: c["old"])
    return {"added": added, "removed": removed,
            "changed": changed, "same": same}

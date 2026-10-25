"""感知哈希（dHash）与相似图分组。纯函数 + PIL 解码，无 freetype 依赖。

dHash：缩到 9x8 灰度，比较每行相邻像素（宽 9 → 8 位 × 高 8 行 = 64 位）。
相似判定：汉明距离 ≤ 阈值（默认 8/64）。分组用并查集。
"""

from __future__ import annotations

import io


def dhash_from_pil(img) -> int:
    """PIL Image → 64 位 dHash。彩色转灰度（ITU-R 601），缩放用 LANCZOS。"""
    gray = img.convert("L").resize((9, 8))
    px = list(gray.getdata())
    bits = 0
    bit = 0
    for row in range(8):
        base = row * 9
        for col in range(8):
            if px[base + col] > px[base + col + 1]:
                bits |= 1 << bit
            bit += 1
    return bits


def dhash_bytes(data: bytes) -> int:
    """图片字节 → dHash。解码失败抛 ValueError（调用方自行容错）。"""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            return dhash_from_pil(img)
    except Exception as exc:  # 解码失败统一为 ValueError
        raise ValueError(f"image decode failed: {exc}") from exc


def dhash_file(path: str) -> int:
    from pathlib import Path

    return dhash_bytes(Path(path).read_bytes())


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def duplicate_groups(items: list[tuple[int, int]], max_dist: int = 8,
                     max_group_scan: int = 200_000) -> list[list[int]]:
    """[(img_id, dhash)] → 相似组 [[id, id, ...], ...]（并查集）。

    组内两两距离 ≤ max_dist 即同组；单人组不返回。
    items 顺序任意；输出组按大小降序、组内按 id 升序。
    max_group_scan 限制比较次数上限（n*(n-1)/2 超过则按输入顺序截断处理），
    万级规模单次调用秒级完成，防御性上限防止极端库卡死。
    """
    n = len(items)
    if n < 2:
        return []
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    pairs = 0
    for i in range(n):
        ida, ha = items[i]
        for j in range(i + 1, n):
            if pairs >= max_group_scan:
                break
            pairs += 1
            hb = items[j][1]
            if hamming(ha, hb) <= max_dist:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
        if pairs >= max_group_scan:
            break

    groups: dict[int, list[int]] = {}
    for i, (img_id, _h) in enumerate(items):
        groups.setdefault(find(i), []).append(img_id)
    out = [sorted(g) for g in groups.values() if len(g) > 1]
    out.sort(key=len, reverse=True)
    return out

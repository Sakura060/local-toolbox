"""重复文件检测。

两级检测避免全量哈希的开销：
  1. 按文件大小分组 —— 大小不同的必不重复
  2. 组内再算部分哈希（首/中/尾 64KB），仍相同才整文件哈希确认

输出重复组（每组 ≥2 个文件），支持把多余副本移入回收目录（不直接删除）。
"""

from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, field

CHUNK = 64 * 1024


@dataclass
class DupGroup:
    """一组内容相同的文件（size 相同，哈希相同）。"""
    size: int
    hash: str
    paths: list[str] = field(default_factory=list)

    @property
    def wasted(self) -> int:
        """该组可节省的字节数 = (份数-1) × 单份大小。"""
        return (len(self.paths) - 1) * self.size if len(self.paths) > 1 else 0


def _hash_file(path: str, full: bool = True, quick: bool = False) -> str:
    """quick=True 只哈希首/中/尾三块（用于组内预筛）。"""
    h = hashlib.sha256()
    size = os.path.getsize(path)
    with open(path, "rb") as fp:
        if quick and size > CHUNK:
            fp.read(CHUNK)
            h.update(fp.tell().to_bytes(8, "big"))  # 掺入偏移，避免拼接歧义
            h.update(fp.read(CHUNK))
            mid = size // 2
            fp.seek(mid)
            h.update(fp.read(CHUNK))
            fp.seek(max(0, size - CHUNK))
            h.update(fp.read(CHUNK))
        else:
            remaining = size if full else CHUNK
            while remaining > 0:
                block = fp.read(min(CHUNK, remaining))
                if not block:
                    break
                h.update(block)
                remaining -= len(block)
    return h.hexdigest()


def find_duplicates(root: str, skip_hidden: bool = True,
                    exclude_dirs: list[str] | None = None,
                    progress=None) -> list[DupGroup]:
    """返回按浪费空间降序的重复组列表。progress(done, total) 可选回调。"""
    # 第一级：按大小分组
    by_size: dict[int, list[str]] = defaultdict(list)
    files_all = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if skip_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if exclude_dirs:
            low = {d.lower() for d in exclude_dirs}
            dirnames[:] = [d for d in dirnames if d.lower() not in low]
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if sz == 0:
                continue  # 空文件人人相同，视为噪音不报告
            by_size[sz].append(p)
            files_all += 1
            if progress and files_all % 50 == 0:
                progress(files_all, files_all)

    # 第二级：quick hash 预筛 → 全量确认
    groups: list[DupGroup] = []
    total = sum(len(v) for v in by_size.values() if len(v) > 1)
    done = 0
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        quick_map: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            try:
                quick_map[_hash_file(p, quick=True)].append(p)
            except OSError:
                continue
        for qh, candidates in quick_map.items():
            if len(candidates) < 2:
                continue
            full_map: dict[str, list[str]] = defaultdict(list)
            for p in candidates:
                try:
                    full_map[_hash_file(p, full=True)].append(p)
                except OSError:
                    continue
            for fh, dups in full_map.items():
                if len(dups) > 1:
                    groups.append(DupGroup(size=size, hash=fh[:16], paths=sorted(dups)))
        done += len(paths)
        if progress:
            progress(done, total)

    groups.sort(key=lambda g: -g.wasted)
    return groups


def plan_cleanup(groups: list[DupGroup], keep: str = "oldest") -> list[tuple[str, str]]:
    """生成清理计划: [(要移走的路径, 归属组内保留的路径)]。

    keep: oldest = 保留最旧（修改时间最早），first = 保留列表第一个。
    """
    plan: list[tuple[str, str]] = []
    for g in groups:
        paths = list(g.paths)
        if keep == "oldest":
            paths.sort(key=lambda p: (os.path.getmtime(p), p))
        keeper = paths[0]
        for p in paths[1:]:
            plan.append((p, keeper))
    return plan

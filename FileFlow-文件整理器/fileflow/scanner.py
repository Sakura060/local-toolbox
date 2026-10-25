"""目录扫描器。

用 os.scandir 递归扫描目标目录，输出 FileInfo 列表。
跳过隐藏目录（默认）与 FileFlow 自身的目标子目录（防止把整理结果再次整理）。
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FileInfo:
    """单个文件的扫描结果。"""
    path: str            # 绝对路径
    name: str            # 文件名（含扩展名）
    stem: str            # 主名（不含扩展名）
    suffix: str          # 扩展名（小写，不含点；无扩展名时为 ""）
    size: int            # 字节
    mtime: float         # 修改时间（epoch 秒）
    depth: int           # 相对根目录的深度（根目录下第一层 = 0）

    @property
    def modified(self) -> datetime:
        return datetime.fromtimestamp(self.mtime)

    def relative(self, root: str) -> str:
        """相对根目录的路径（正斜杠风格）。"""
        return os.path.relpath(self.path, root).replace(os.sep, "/")


@dataclass
class ScanResult:
    """一次扫描的汇总。"""
    root: str
    files: list[FileInfo] = field(default_factory=list)
    skipped_dirs: list[str] = field(default_factory=list)   # 被跳过的目录（隐藏/排除）
    errors: list[str] = field(default_factory=list)          # 无法访问等错误

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.files)

    def summary(self) -> str:
        return (
            f"扫描 {self.root}: {len(self.files)} 个文件, "
            f"{self.total_size / 1024:.1f} KB, 跳过 {len(self.skipped_dirs)} 个目录, "
            f"{len(self.errors)} 个错误"
        )


class Scanner:
    """递归扫描目录，产出 FileInfo。"""

    def __init__(
        self,
        skip_hidden: bool = True,
        exclude_dirs: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        max_depth: int = 10,
    ):
        """
        exclude_dirs: 按名称排除的目录（任意层级）。
        exclude_patterns: 按 fnmatch 排除的文件名模式，如 ["*.tmp", "~$*"]。
        max_depth: 递归深度上限，防止符号链接环 / 超深目录。
        """
        self.skip_hidden = skip_hidden
        self.exclude_dirs = [d.lower() for d in (exclude_dirs or [])]
        self.exclude_patterns = exclude_patterns or []
        self.max_depth = max_depth

    def scan(self, root: str) -> ScanResult:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            raise NotADirectoryError(f"不是有效目录: {root}")

        result = ScanResult(root=root)
        self._walk(root, root, 0, result)
        return result

    def _should_skip_dir(self, name: str) -> bool:
        if self.skip_hidden and name.startswith("."):
            return True
        return name.lower() in self.exclude_dirs

    def _should_skip_file(self, name: str) -> bool:
        return any(fnmatch.fnmatch(name, p) for p in self.exclude_patterns)

    def _walk(self, root: str, current: str, depth: int, result: ScanResult) -> None:
        if depth > self.max_depth:
            result.errors.append(f"超过最大深度，跳过: {current}")
            return

        try:
            entries = list(os.scandir(current))
        except OSError as e:
            result.errors.append(f"无法读取目录 {current}: {e}")
            return

        for entry in entries:
            name = entry.name
            if entry.is_dir(follow_symlinks=False):
                if self._should_skip_dir(name):
                    result.skipped_dirs.append(entry.path)
                else:
                    self._walk(root, entry.path, depth + 1, result)
                continue

            if not entry.is_file(follow_symlinks=False):
                continue  # 符号链接、管道等特殊项
            if self._should_skip_file(name):
                continue

            try:
                stat = entry.stat()
            except OSError as e:
                result.errors.append(f"无法获取文件信息 {entry.path}: {e}")
                continue

            stem, dot, suffix = name.rpartition(".")
            # ".gitignore" 这类点开头的无扩展名文件不算 suffix
            if not dot or stem == "" or suffix == "":
                stem, suffix = name, ""

            result.files.append(FileInfo(
                path=entry.path,
                name=name,
                stem=stem,
                suffix=suffix.lower(),
                size=stat.st_size,
                mtime=stat.st_mtime,
                depth=depth,
            ))

"""笔记存储层：一个 .md 文件 = 一条笔记。

文件名 = <id>.md（id 为短随机串，重命名标题不影响文件）；
文件首行 `# 标题` 即笔记标题；其余为正文。
所有操作持有线程锁，可被 ThreadingHTTPServer 并发调用。
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ASSET_DIR = "assets"                 # 图片目录：<笔记目录>/assets/<笔记id>/<哈希>.<ext>
ASSET_EXTS = (".png", ".jpg", ".gif", ".webp", ".bmp")
MAX_ASSET = 10 * 1024 * 1024         # 单张图片上限 10MB
EXT_ALIAS = {".jpeg": ".jpg", ".jpe": ".jpg"}


@dataclass
class Note:
    id: str
    title: str
    content: str        # 含标题行的完整 Markdown
    updated: float
    size: int

    def to_dict(self, with_content: bool = True) -> dict:
        d = {"id": self.id, "title": self.title,
             "updated": self.updated, "size": self.size}
        if with_content:
            d["content"] = self.content
        return d


def extract_title(content: str) -> str:
    """取首行 # 标题；没有则用第一行非空文本；再没有则「无标题」。"""
    for line in content.splitlines():
        t = line.strip()
        if not t:
            continue
        m = re.match(r"^#\s+(.+)$", t)
        return m.group(1).strip() if m else t[:60]
    return "无标题"


_COLOR_MARK_RE = re.compile(r"\{([#\w\u4e00-\u9fa5]{1,20})\}(.*?)\{/\1\}", re.S)


def strip_color_markup(text: str) -> str:
    """去掉文字颜色标注（{色名}文字{/色名}），只留下被标注的文字。

    反复剥离以处理嵌套标记；首尾名字不一致的标记原样保留，笔记本体不做改动。
    仅供摘要等展示文本使用。
    """
    prev = None
    while prev != text:
        prev = text
        text = _COLOR_MARK_RE.sub(r"\2", text)
    return text


_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)\)")


def strip_image_markup(text: str) -> str:
    """把图片语法换成替代文字（没有替代文字就留一个 🖼），用于摘要等展示文本。"""
    return _IMAGE_RE.sub(lambda m: m.group(1) or "🖼", text)


def make_excerpt(content: str, limit: int = 80) -> str:
    """跳过标题行，取正文前 limit 个字符作为摘要（单行化，去掉颜色/图片标记）。"""
    lines = [l.strip() for l in content.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    text = strip_image_markup(strip_color_markup(" ".join(lines)))
    return text[:limit] + ("…" if len(text) > limit else "")


class NoteStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- 内部工具 ----
    def _path(self, note_id: str) -> Path | None:
        """校验 id 合法性后返回文件路径；非法返回 None。"""
        if not re.fullmatch(r"[a-z0-9]{6,16}", note_id):
            return None
        p = self.root / f"{note_id}.md"
        # 防越界（id 白名单已保证，但双保险）
        if p.resolve().parent != self.root.resolve():
            return None
        return p

    @staticmethod
    def _new_id() -> str:
        return secrets.token_hex(4)      # 8 位十六进制

    def _read_note(self, p: Path) -> Note | None:
        try:
            stat = p.stat()
            content = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return Note(id=p.stem, title=extract_title(content), content=content,
                    updated=stat.st_mtime, size=stat.st_size)

    # ---- CRUD ----
    def list_notes(self, q: str = "") -> list[dict]:
        """按更新时间倒序返回笔记摘要；q 非空时做标题+正文子串搜索。"""
        with self._lock:
            notes: list[Note] = []
            for p in self.root.glob("*.md"):
                n = self._read_note(p)
                if n:
                    notes.append(n)
        if q:
            needle = q.lower()
            notes = [n for n in notes
                     if needle in n.title.lower() or needle in n.content.lower()]
        notes.sort(key=lambda n: -n.updated)
        return [{"id": n.id, "title": n.title, "updated": n.updated,
                 "size": n.size, "excerpt": make_excerpt(n.content)}
                for n in notes]

    def get(self, note_id: str) -> Note | None:
        with self._lock:
            p = self._path(note_id)
            if p is None or not p.exists():
                return None
            return self._read_note(p)

    def create(self, title: str = "", content: str = "") -> Note:
        body = content.strip() or ""
        if title.strip():
            body = f"# {title.strip()}\n\n{body}" if not body.startswith("# ") else body
        with self._lock:
            note_id = self._new_id()
            while (self.root / f"{note_id}.md").exists():
                note_id = self._new_id()
            p = self.root / f"{note_id}.md"
            p.write_text(body if body else "无标题\n", encoding="utf-8")
            return self._read_note(p)

    def update(self, note_id: str, content: str) -> Note | None:
        with self._lock:
            p = self._path(note_id)
            if p is None or not p.exists():
                return None
            p.write_text(content, encoding="utf-8")
            return self._read_note(p)

    def delete(self, note_id: str) -> bool:
        """删除笔记。返回是否真的删了。"""
        with self._lock:
            p = self._path(note_id)
            if p is None or not p.exists():
                return False
            p.unlink()
            # 连带清掉这条笔记的图片（assets/<笔记id>/）
            shutil.rmtree(self.root / ASSET_DIR / note_id, ignore_errors=True)
            return True

    # ---- 图片：内容寻址，存 assets/<笔记id>/<内容哈希>.<ext> ----
    def _assets_dir(self, note_id: str) -> Path | None:
        if self._path(note_id) is None:          # 复用同一套 id 白名单
            return None
        return self.root / ASSET_DIR / note_id

    def save_asset(self, note_id: str, data: bytes, ext: str) -> str | None:
        """保存一张图片，返回可写进 Markdown 的相对路径；不合法返回 None。

        文件名取自内容哈希 → 同一张图重复插入不会存两份。
        """
        ext = (ext or "").lower().strip()
        if not ext.startswith("."):
            ext = "." + ext
        ext = EXT_ALIAS.get(ext, ext)
        if ext not in ASSET_EXTS or not data or len(data) > MAX_ASSET:
            return None
        with self._lock:
            p = self._path(note_id)
            if p is None or not p.exists():
                return None
            d = self.root / ASSET_DIR / note_id
            name = hashlib.sha256(data).hexdigest()[:16] + ext
            target = d / name
            if not target.exists():              # 内容寻址：已存在就不重复写
                d.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            return f"{ASSET_DIR}/{note_id}/{name}"

    def asset_path(self, note_id: str, name: str) -> Path | None:
        """校验 id + 文件名后返回图片路径；非法或不存在返回 None。"""
        if not re.fullmatch(r"[a-f0-9]{16}\.(?:png|jpg|jpeg|gif|webp|bmp)", name):
            return None
        d = self._assets_dir(note_id)
        if d is None:
            return None
        p = d / name
        if p.resolve().parent != d.resolve():     # 防越界双保险
            return None
        return p if p.is_file() else None

    def stats(self) -> dict:
        with self._lock:
            files = list(self.root.glob("*.md"))
            total = sum(f.stat().st_size for f in files if f.is_file())
        return {"count": len(files), "total_size": total,
                "directory": str(self.root)}

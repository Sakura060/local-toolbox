"""ClipVault 存储：SQLite 时间线（去重上浮、置顶、保留上限、暂停开关）。

隐私红线：全部数据只落本机 data/ 目录；不联网；测试绝不碰真实剪贴板。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from pathlib import Path

RETENTION_DEFAULT = 300  # 未置顶条目保留上限

_KIND_LABEL = {"text": "文本", "image": "图片", "files": "文件"}

_U64 = 0xFFFFFFFFFFFFFFFF


def _to_signed64(v: int) -> int:
    """64 位无符号 → SQLite 有符号存储（INTEGER 上限 2^63-1，同捞图 D21）。"""
    return v - (1 << 64) if v >= (1 << 63) else v


def _to_unsigned64(v: int) -> int:
    return v + (1 << 64) if v < 0 else v


def perceptual_distance(a: int, b: int) -> int:
    """两个 64 位 dHash 的汉明距。"""
    return bin((a ^ b) & _U64).count("1")


def content_hash(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def kind_label(kind: str) -> str:
    return _KIND_LABEL.get(kind, kind)


class Store:
    """线程安全的时间线存储。capture 由采集线程调用，其余供 Web/CLI。"""

    def __init__(self, db_path: Path, images_dir: Path,
                 retention: int = RETENTION_DEFAULT):
        self.db_path = Path(db_path)
        self.images_dir = Path(images_dir)
        self.retention = max(1, int(retention))
        self.lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS entries(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    kind TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    image_path TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    hash TEXT NOT NULL DEFAULT '',
                    pinned INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(ts DESC);
                CREATE INDEX IF NOT EXISTS idx_entries_hash ON entries(hash);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            # v0.2：图片感知哈希列（旧行 NULL=未计算；64 位入库转有符号，同捞图 D21）
            cols = [r[1] for r in self.conn.execute("PRAGMA table_info(entries)")]
            if "dhash" not in cols:
                self.conn.execute("ALTER TABLE entries ADD COLUMN dhash INTEGER")
            self.conn.commit()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # ---------- 采集 ----------
    # 图片感知去重：只和最近窗口内的图片条目比（回贴回路发生在几秒内），
    # 更久的相似图不再合并——两小时前后两张相似截图是两条记录，不丢内容。
    PERCEPTUAL_WINDOW_S = 600.0
    PERCEPTUAL_HAMMING = 8

    def capture(self, kind: str, body: str = "", image_path: str = "",
                size: int = 0, chash: str = "", dhash: int | None = None) -> dict | None:
        """记一条。hash 已存在 → 原条目上浮（更新 ts）；否则插入。返回条目或 None(暂停)。

        空 chash 跳过精确去重（否则两条空 hash 的不同内容会被误合并）。
        图片再带 dhash（64 位无符号）：与最近窗口内图片条目汉明距 ≤8 判"同图回采"
        （回贴出去的图被重新采集，DIB→PNG 重编码字节不同但视觉相同）→ 上浮旧条目。
        """
        now = time.time()
        with self.lock:
            row = None
            if chash:
                row = self.conn.execute(
                    "SELECT id, pinned FROM entries WHERE hash=? ORDER BY id DESC LIMIT 1",
                    (chash,)).fetchone()
            if row is None and dhash is not None:
                row = self._recent_same_image(dhash, now)
            if row is not None:
                self.conn.execute("UPDATE entries SET ts=? WHERE id=?", (now, row["id"]))
                self.conn.commit()
                return dict(self.conn.execute(
                    "SELECT * FROM entries WHERE id=?", (row["id"],)).fetchone())
            cur = self.conn.execute(
                "INSERT INTO entries(ts, kind, body, image_path, size, hash, dhash, pinned)"
                " VALUES(?,?,?,?,?,?,?,0)",
                (now, kind, body, image_path, size, chash,
                 None if dhash is None else _to_signed64(dhash)))
            self.conn.commit()
            eid = cur.lastrowid
            self._prune()
            return dict(self.conn.execute(
                "SELECT * FROM entries WHERE id=?", (eid,)).fetchone())

    def _recent_same_image(self, dhash: int, now: float):
        """最近窗口内的图片条目里找感知相同的一张。"""
        rows = self.conn.execute(
            "SELECT id, pinned, dhash FROM entries"
            " WHERE kind='image' AND dhash IS NOT NULL AND ts >= ?"
            " ORDER BY id DESC LIMIT 50",
            (now - self.PERCEPTUAL_WINDOW_S,)).fetchall()
        for r in rows:
            if perceptual_distance(_to_unsigned64(r["dhash"]), dhash) <= self.PERCEPTUAL_HAMMING:
                return r
        return None

    def _prune(self) -> int:
        """未置顶条目超过保留上限时删除最旧的（含图片文件）。

        按 id 排序（自增单调）；同一秒内 ts 相同会让 ts 排序不确定。
        """
        cur = self.conn.execute(
            "SELECT id, image_path FROM entries WHERE pinned=0"
            " AND id NOT IN (SELECT id FROM entries WHERE pinned=0"
            "                ORDER BY id DESC LIMIT ?)",
            (self.retention,))
        stale = cur.fetchall()
        for row in stale:
            if row["image_path"]:
                p = self.images_dir / row["image_path"]
                try:
                    p.unlink()
                except OSError:
                    pass
        if stale:
            self.conn.executemany("DELETE FROM entries WHERE id=?",
                                  [(r["id"],) for r in stale])
            self.conn.commit()
        return len(stale)

    # ---------- 查询 ----------
    _BODY_LIST_CAP = 4000  # 列表响应体截断（全文走 /api/entry/<id>）

    def list(self, kind: str | None = None, q: str | None = None,
             limit: int | str = 100) -> list[dict]:
        try:
            n = max(1, min(500, int(limit)))
        except (TypeError, ValueError):
            n = 100
        sql = "SELECT * FROM entries WHERE 1=1"
        args: list = []
        if kind in ("text", "image", "files"):
            sql += " AND kind=?"
            args.append(kind)
        if q:
            # LIKE 通配符转义：搜 "100%" 只命中字面 100%，不放大成前缀匹配
            eq = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            sql += " AND body LIKE ? ESCAPE '\\'"
            args.append(f"%{eq}%")
        sql += " ORDER BY pinned DESC, ts DESC, id DESC LIMIT ?"
        args.append(n)
        with self.lock:
            return [self._with_preview(dict(r), truncate_body=True)
                    for r in self.conn.execute(sql, args)]

    @staticmethod
    def _with_preview(row: dict, truncate_body: bool = False) -> dict:
        if truncate_body and len(row.get("body") or "") > Store._BODY_LIST_CAP:
            row["body"] = row["body"][:Store._BODY_LIST_CAP]
            row["body_truncated"] = True
        if row["kind"] == "text":
            row["preview"] = row["body"][:160]
        elif row["kind"] == "files":
            names = [p.split("\\")[-1].split("/")[-1]
                     for p in row["body"].splitlines() if p]
            head = "、".join(names[:3])
            more = f" 等 {len(names)} 项" if len(names) > 3 else ""
            row["preview"] = head + more
        else:
            row["preview"] = f"图片 {max(1, row['size'] // 1024)} KB"
        return row

    def get(self, entry_id: int) -> dict | None:
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM entries WHERE id=?", (int(entry_id),)).fetchone()
            return self._with_preview(dict(row)) if row else None

    def stats(self) -> dict:
        with self.lock:
            by = {r["kind"]: r["n"] for r in self.conn.execute(
                "SELECT kind, COUNT(*) n FROM entries GROUP BY kind")}
            total = sum(by.values())
            return {"total": total, "by_kind": by, "paused": self._paused()}

    # ---------- 操作 ----------
    def pin(self, entry_id: int, pinned: bool) -> bool:
        with self.lock:
            cur = self.conn.execute("UPDATE entries SET pinned=? WHERE id=?",
                                    (1 if pinned else 0, int(entry_id)))
            self.conn.commit()
            return cur.rowcount > 0

    def delete(self, entry_id: int) -> bool:
        with self.lock:
            row = self.conn.execute("SELECT image_path FROM entries WHERE id=?",
                                    (int(entry_id),)).fetchone()
            if row is None:
                return False
            if row["image_path"]:
                p = self.images_dir / row["image_path"]
                try:
                    p.unlink()
                except OSError:
                    pass
            self.conn.execute("DELETE FROM entries WHERE id=?", (int(entry_id),))
            self.conn.commit()
            return True

    def clear(self, keep_pinned: bool = True) -> int:
        with self.lock:
            if keep_pinned:
                rows = self.conn.execute(
                    "SELECT image_path FROM entries WHERE pinned=0").fetchall()
                n = self.conn.execute("DELETE FROM entries WHERE pinned=0").rowcount
            else:
                rows = self.conn.execute("SELECT image_path FROM entries").fetchall()
                n = self.conn.execute("DELETE FROM entries").rowcount
            self.conn.commit()
        for r in rows:
            if r["image_path"]:
                try:
                    (self.images_dir / r["image_path"]).unlink()
                except OSError:
                    pass
        return n

    # ---------- 暂停 ----------
    def _paused(self) -> bool:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key='paused'").fetchone()
        return bool(row and row["value"] == "1")

    def set_paused(self, paused: bool) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES('paused', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("1" if paused else "0",))
            self.conn.commit()

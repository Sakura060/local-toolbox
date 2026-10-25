"""SQLite 存储：图片元数据 + FTS5 全文索引 + 扫描根目录 + 进度元数据。

线程模型：单连接 + 全局 RLock 串行化（简单且足够快）。
"""

from __future__ import annotations

import os
import sqlite3
import struct
import threading
import time
from pathlib import Path

from . import seg


# 64 位 dHash 超出 SQLite INTEGER 上限（2^63-1），入库转有符号、读出转回无符号
def _pack_u64(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


def _unpack_u64(v: int) -> int:
    return v + (1 << 64) if v < 0 else v


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # ---------- schema ----------

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS images(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  path TEXT UNIQUE NOT NULL,
                  name TEXT NOT NULL DEFAULT '',
                  folder TEXT NOT NULL DEFAULT '',
                  size INTEGER DEFAULT 0,
                  mtime_ns INTEGER DEFAULT 0,
                  width INTEGER DEFAULT 0,
                  height INTEGER DEFAULT 0,
                  ocr_text TEXT DEFAULT '',
                  ocr_ok INTEGER DEFAULT 0,
                  err TEXT,
                  indexed_at REAL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_images_mtime ON images(mtime_ns);
                CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(seg, img_id UNINDEXED);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS roots(path TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS embeddings(
                  img_id INTEGER NOT NULL,
                  model TEXT NOT NULL,
                  dim INTEGER NOT NULL,
                  vec BLOB NOT NULL,
                  mtime_ns INTEGER DEFAULT 0,
                  created_at REAL DEFAULT 0,
                  PRIMARY KEY(img_id, model)
                );
                """
            )
            self._migrate_columns()
            self._conn.commit()

    def _migrate_columns(self) -> None:
        """v0.2：images 增加 dhash/source 列（对旧库幂等）。

        dhash 用 NULL 表示"未计算"——0 是合法哈希值（纯色图），不能当哨兵。
        """
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(images)")}
        if "dhash" not in have:
            self._conn.execute("ALTER TABLE images ADD COLUMN dhash INTEGER")
        if "source" not in have:
            self._conn.execute(
                "ALTER TABLE images ADD COLUMN source TEXT DEFAULT ''")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 图片 ----------

    def upsert_image(
        self,
        path: str,
        name: str,
        folder: str,
        size: int,
        mtime_ns: int,
        width: int,
        height: int,
        ocr_text: str,
        ocr_ok: int,
        err: str | None,
        seg_text: str,
        dhash: int | None = None,
        source: str = "",
    ) -> int:
        with self._lock:
            now = time.time()
            self._conn.execute(
                """
                INSERT INTO images(path, name, folder, size, mtime_ns, width, height,
                                   ocr_text, ocr_ok, err, indexed_at, dhash, source)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                  name=excluded.name, folder=excluded.folder, size=excluded.size,
                  mtime_ns=excluded.mtime_ns, width=excluded.width, height=excluded.height,
                  ocr_text=excluded.ocr_text, ocr_ok=excluded.ocr_ok, err=excluded.err,
                  indexed_at=excluded.indexed_at,
                  dhash=excluded.dhash, source=excluded.source
                """,
                (path, name, folder, size, mtime_ns, width, height,
                 ocr_text, ocr_ok, err, now,
                 None if dhash is None else _pack_u64(dhash), source),
            )
            row = self._conn.execute(
                "SELECT id FROM images WHERE path=?", (path,)
            ).fetchone()
            img_id = int(row["id"])
            self._conn.execute("DELETE FROM fts WHERE img_id=?", (str(img_id),))
            self._conn.execute(
                "INSERT INTO fts(seg, img_id) VALUES(?,?)", (seg_text, str(img_id))
            )
            self._conn.commit()
            return img_id

    def get_fingerprints(self) -> dict[str, tuple[int, int, int]]:
        """path -> (size, mtime_ns, ocr_ok)，供增量扫描跳过未变化文件。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT path, size, mtime_ns, ocr_ok FROM images"
            ).fetchall()
        return {r["path"]: (r["size"], r["mtime_ns"], r["ocr_ok"]) for r in rows}

    def purge_missing(self, roots: list[str], keep_paths: set[str]) -> int:
        """删除位于给定根目录之下、但不在 keep_paths 里的记录。返回删除数。"""
        norm_roots = [os.path.normcase(os.path.normpath(r)) for r in roots]
        with self._lock:
            rows = self._conn.execute("SELECT id, path FROM images").fetchall()
            doomed = []
            for r in rows:
                p = os.path.normcase(os.path.normpath(r["path"]))
                if any(p == rt or p.startswith(rt + os.sep) for rt in norm_roots):
                    if p not in keep_paths:
                        doomed.append(r["id"])
            for img_id in doomed:
                self._conn.execute("DELETE FROM images WHERE id=?", (img_id,))
                self._conn.execute("DELETE FROM fts WHERE img_id=?", (str(img_id),))
                self._conn.execute("DELETE FROM embeddings WHERE img_id=?", (img_id,))
            if doomed:
                self._conn.commit()
            return len(doomed)

    def set_image_error(self, img_id: int, err: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE images SET err=? WHERE id=?", (err, img_id))
            self._conn.commit()

    def add_roots(self, roots: list[str]) -> None:
        for r in roots:
            self.add_root(r)

    def get_path_by_id(self, img_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT path FROM images WHERE id=?", (img_id,)
            ).fetchone()
        return row["path"] if row else None

    def _row_to_item(self, r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "name": r["name"],
            "folder": r["folder"],
            "size": r["size"],
            "mtime": r["mtime_ns"] / 1e9,
            "width": r["width"],
            "height": r["height"],
            "ocr_ok": r["ocr_ok"],
            "ocr_text": r["ocr_text"],
            "err": r["err"],
            "source": r["source"] if "source" in r.keys() else "",
        }

    # ---------- v0.2：相似图去重 / 时间线 ----------

    def missing_hash_paths(self) -> list[str]:
        """还没有 dHash 的文件路径列表（v0.1 旧记录回填用）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT path FROM images WHERE dhash IS NULL").fetchall()
        return [r["path"] for r in rows]

    def set_dhash(self, img_id: int, dhash: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE images SET dhash=? WHERE id=?", (dhash, img_id))
            self._conn.commit()

    def set_dhash_by_path(self, path: str, dhash: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE images SET dhash=? WHERE path=?",
                (_pack_u64(dhash), path))
            self._conn.commit()

    def all_hashes(self) -> list[tuple[int, int]]:
        """[(img_id, dhash)]，仅已计算的记录（纯色图哈希恰为 0 时不参与查重，可忽略）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, dhash FROM images "
                "WHERE dhash IS NOT NULL AND dhash != 0").fetchall()
        return [(int(r["id"]), _unpack_u64(int(r["dhash"]))) for r in rows]

    def items_by_ids(self, ids: list[int]) -> dict[int, dict]:
        """id → item（去重分组展示用）。"""
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM images WHERE id IN ({marks})", ids).fetchall()
        return {r["id"]: self._row_to_item(r) for r in rows}

    def timeline(self, bucket: str = "day", limit: int = 90) -> list[dict]:
        """按天/小时聚簇计数（mtime 本地时区）。"""
        fmt = ("%Y-%m-%d", "%Y-%m-%d %H:00")[bucket == "hour"]
        with self._lock:
            rows = self._conn.execute(
                f"SELECT strftime('{fmt}', mtime_ns/1000000000, "
                "'unixepoch', 'localtime') AS b, COUNT(*) AS n "
                "FROM images GROUP BY b ORDER BY b DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"bucket": r["b"], "count": r["n"]} for r in rows]

    def by_time(self, t0: float, t1: float,
                limit: int = 60, offset: int = 0) -> dict:
        """mtime ∈ [t0, t1]（unix 秒）的图片，新→旧。"""
        t0_ns, t1_ns = int(t0 * 1e9), int(t1 * 1e9)
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM images WHERE mtime_ns BETWEEN ? AND ?",
                (t0_ns, t1_ns)).fetchone()[0]
            rows = self._conn.execute(
                "SELECT * FROM images WHERE mtime_ns BETWEEN ? AND ? "
                "ORDER BY mtime_ns DESC LIMIT ? OFFSET ?",
                (t0_ns, t1_ns, limit, offset)).fetchall()
        return {"total": total, "mode": "time", "q": "",
                "items": [self._row_to_item(r) for r in rows]}

    # ---------- v0.3：语义向量 ----------

    def set_embedding(self, img_id: int, model: str, vec, mtime_ns: int) -> None:
        """存一条向量（float32 小端打包）；同 (img_id, model) 覆盖。"""
        vec = list(vec)
        blob = struct.pack(f"<{len(vec)}f", *vec)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO embeddings(img_id, model, dim, vec, mtime_ns, created_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(img_id, model) DO UPDATE SET
                  dim=excluded.dim, vec=excluded.vec,
                  mtime_ns=excluded.mtime_ns, created_at=excluded.created_at
                """,
                (img_id, model, len(vec), blob, mtime_ns, time.time()),
            )
            self._conn.commit()

    def embedding_mtimes(self, model: str) -> dict[int, int]:
        """已入库向量的 {img_id: 入库时的 images.mtime_ns}，回填时判新鲜度。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT img_id, mtime_ns FROM embeddings WHERE model=?",
                (model,)).fetchall()
        return {int(r["img_id"]): int(r["mtime_ns"]) for r in rows}

    def embedding_rows(self, model: str) -> tuple[list[int], int, bytes]:
        """(ids, dim, 拼接 blob)。ids 升序，blob 按 ids 顺序拼接 float32。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT img_id, dim, vec FROM embeddings WHERE model=? "
                "ORDER BY img_id", (model,)).fetchall()
        ids = [int(r["img_id"]) for r in rows]
        dim = int(rows[0]["dim"]) if rows else 0
        return ids, dim, b"".join(r["vec"] for r in rows)

    def count_embeddings(self, model: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM embeddings WHERE model=?",
                (model,)).fetchone()[0]

    def embedding_backlog(self, model: str) -> list[tuple[int, str, int]]:
        """[(img_id, path, mtime_ns)] 需要新建或刷新向量的图片。

        新建 = 还没有该 model 的向量；刷新 = 图片 mtime 变了（重扫覆盖后向量过期）。
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT i.id, i.path, i.mtime_ns FROM images i
                LEFT JOIN embeddings e ON e.img_id=i.id AND e.model=?
                WHERE e.img_id IS NULL OR e.mtime_ns != i.mtime_ns
                ORDER BY i.id
                """,
                (model,),
            ).fetchall()
        return [(int(r["id"]), r["path"], int(r["mtime_ns"])) for r in rows]

    def delete_embeddings(self, img_ids: list[int]) -> None:
        if not img_ids:
            return
        marks = ",".join("?" * len(img_ids))
        with self._lock:
            self._conn.execute(
                f"DELETE FROM embeddings WHERE img_id IN ({marks})", img_ids)
            self._conn.commit()

    # ---------- 搜索 ----------

    def search(self, q: str, limit: int = 60, offset: int = 0) -> dict:
        """FTS 优先（jieba 切分），空则 LIKE 兜底（原文包含）。"""
        q = (q or "").strip()
        if not q:
            return self.recent(limit=limit, offset=offset)

        with self._lock:
            terms = seg.query_terms(q)
            expr = seg.fts_expr(terms) if terms else ""
            # 二级尝试：jieba 可能把整词切开（如"单号"→"单"+"号"），
            # 与索引侧切分对不上；此时把原始整词作为短语再试一次。
            phrase = f'"{q.replace(chr(34), "")}"' if q.replace('"', "") else ""
            if expr:
                try:
                    total = self._conn.execute(
                        "SELECT COUNT(*) FROM fts WHERE fts MATCH ?", (expr,)
                    ).fetchone()[0]
                    if not total and phrase and phrase != expr:
                        total = self._conn.execute(
                            "SELECT COUNT(*) FROM fts WHERE fts MATCH ?", (phrase,)
                        ).fetchone()[0]
                        if total:
                            expr = phrase
                    if total:
                        rows = self._conn.execute(
                            "SELECT i.* FROM fts JOIN images i ON i.id=CAST(fts.img_id AS INTEGER) "
                            "WHERE fts MATCH ? ORDER BY i.mtime_ns DESC LIMIT ? OFFSET ?",
                            (expr, limit, offset),
                        ).fetchall()
                        return {"total": total, "mode": "fts", "q": q,
                                "items": [self._row_to_item(r) for r in rows]}
                except sqlite3.OperationalError:
                    pass  # MATCH 语法意外失败 → 走 LIKE

            esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{esc}%"
            total = self._conn.execute(
                "SELECT COUNT(*) FROM images WHERE ocr_text LIKE ? ESCAPE '\\' "
                "OR name LIKE ? ESCAPE '\\'",
                (like, like),
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT * FROM images WHERE ocr_text LIKE ? ESCAPE '\\' "
                "OR name LIKE ? ESCAPE '\\' ORDER BY mtime_ns DESC LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
            return {"total": total, "mode": "like", "q": q,
                    "items": [self._row_to_item(r) for r in rows]}

    def recent(self, limit: int = 60, offset: int = 0) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            rows = self._conn.execute(
                "SELECT * FROM images ORDER BY mtime_ns DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return {"total": total, "mode": "recent", "q": "",
                "items": [self._row_to_item(r) for r in rows]}

    # ---------- 根目录 / 元数据 / 统计 ----------

    def add_root(self, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO roots(path) VALUES(?) "
                "ON CONFLICT(path) DO NOTHING",
                (str(path),),
            )
            self._conn.commit()

    def list_roots(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT path FROM roots").fetchall()
        return [r["path"] for r in rows]

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            ok = self._conn.execute(
                "SELECT COUNT(*) FROM images WHERE ocr_ok=1"
            ).fetchone()[0]
            errs = self._conn.execute(
                "SELECT COUNT(*) FROM images WHERE ocr_ok=0"
            ).fetchone()[0]
        return {"total": total, "ocr_ok": ok, "errors": errs}

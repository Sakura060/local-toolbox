"""扫描编排 + SQLite 存储 + 聚类/谱系重建。

存储设计：
- docs 表带冗余 cid 列（每次重建整体覆盖）
- clusters 表存每簇的 latest / fork / sinks / dup_groups
- edges 表存祖先边（parent ⊆ child，containment）
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from . import lineage as lineage_mod
from .config import DOC_EXTS, MAX_DOCS, MAX_FILE_BYTES
from .extract import extract_blocks
from .fingerprint import prepare
from .lineage import union_find_clusters


class Database:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS docs(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  path TEXT UNIQUE NOT NULL,
                  name TEXT NOT NULL,
                  folder TEXT NOT NULL,
                  ftype TEXT DEFAULT '',
                  size INTEGER DEFAULT 0,
                  mtime REAL DEFAULT 0,
                  cid INTEGER DEFAULT -1,
                  blocks_json TEXT DEFAULT '[]',
                  block_count INTEGER DEFAULT 0,
                  chars INTEGER DEFAULT 0,
                  indexed_at REAL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_docs_cid ON docs(cid);
                CREATE TABLE IF NOT EXISTS clusters(
                  cid INTEGER PRIMARY KEY,
                  latest_id INTEGER,
                  fork INTEGER DEFAULT 0,
                  derived_json TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS edges(
                  parent_id INTEGER NOT NULL,
                  child_id INTEGER NOT NULL,
                  containment REAL DEFAULT 0,
                  PRIMARY KEY(parent_id, child_id)
                );
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- docs ----------

    def upsert_doc(self, path: str, name: str, folder: str, ftype: str,
                   size: int, mtime: float, blocks: list[str]) -> int:
        prep = prepare(blocks)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO docs(path, name, folder, ftype, size, mtime,
                                 blocks_json, block_count, chars, indexed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                  name=excluded.name, folder=excluded.folder, ftype=excluded.ftype,
                  size=excluded.size, mtime=excluded.mtime,
                  blocks_json=excluded.blocks_json, block_count=excluded.block_count,
                  chars=excluded.chars, indexed_at=excluded.indexed_at
                """,
                (path, name, folder, ftype, size, mtime,
                 json.dumps(blocks, ensure_ascii=False), len(blocks),
                 sum(len(b) for b in blocks), time.time()),
            )
            row = self._conn.execute(
                "SELECT id FROM docs WHERE path=?", (path,)).fetchone()
            return int(row["id"])

    def get_fingerprints(self) -> dict[str, tuple[float, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path, mtime, size FROM docs").fetchall()
        return {r["path"]: (r["mtime"], r["size"]) for r in rows}

    def purge_missing(self, roots: list[str], keep: set[str]) -> int:
        norm_roots = [os.path.normcase(os.path.normpath(r)) for r in roots]
        with self._lock:
            rows = self._conn.execute("SELECT id, path FROM docs").fetchall()
            doomed = []
            for r in rows:
                p = os.path.normcase(os.path.normpath(r["path"]))
                if any(p == rt or p.startswith(rt + os.sep) for rt in norm_roots):
                    if p not in keep:
                        doomed.append(r["id"])
            for did in doomed:
                self._conn.execute("DELETE FROM docs WHERE id=?", (did,))
            if doomed:
                self._conn.commit()
            return len(doomed)

    def all_docs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, path, name, folder, ftype, size, mtime, blocks_json "
                "FROM docs ORDER BY mtime DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            blocks = json.loads(d.pop("blocks_json"))
            d["blocks"] = blocks
            d["prep"] = prepare(blocks)
            out.append(d)
        return out

    def get_doc(self, doc_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM docs WHERE id=?", (doc_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["blocks"] = json.loads(d.pop("blocks_json"))
        return d

    # ---------- clusters / edges（每次扫描后整体重建） ----------

    def replace_clusters(self, assignments: dict[int, int],
                         lineage_by_root: dict[int, dict]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM clusters")
            self._conn.execute("DELETE FROM edges")
            for cid, lin in lineage_by_root.items():
                self._conn.execute(
                    "INSERT INTO clusters(cid, latest_id, fork, derived_json) "
                    "VALUES(?,?,?,?)",
                    (cid, lin.get("latest"), 1 if lin.get("fork") else 0,
                     json.dumps({"sinks": lin.get("sinks", []),
                                 "dup_groups": lin.get("dup_groups", [])},
                                ensure_ascii=False)))
                for e in lin["edges"]:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO edges(parent_id, child_id, containment) "
                        "VALUES(?,?,?)",
                        (e["parent"], e["child"], e["containment"]))
            for rowid, cid in assignments.items():
                self._conn.execute("UPDATE docs SET cid=? WHERE id=?", (cid, rowid))
            self._conn.commit()

    def list_clusters(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT c.cid, c.latest_id, c.fork, c.derived_json,
                       d.name AS latest_name, d.path AS latest_path,
                       d.mtime AS latest_mtime, d.ftype AS latest_ftype,
                       (SELECT COUNT(*) FROM docs WHERE cid=c.cid) AS size
                FROM clusters c LEFT JOIN docs d ON d.id=c.latest_id
                ORDER BY size DESC, c.cid
                """
            ).fetchall()
        out = []
        for r in rows:
            c = dict(r)
            c["derived"] = json.loads(c.pop("derived_json") or "{}")
            out.append(c)
        return out

    def docs_by_cluster(self, cid: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, folder, path, ftype, size, mtime, block_count "
                "FROM docs WHERE cid=? ORDER BY mtime", (cid,)).fetchall()
        return [dict(r) for r in rows]

    def edges_for(self, doc_ids: list[int]) -> list[dict]:
        if not doc_ids:
            return []
        marks = ",".join("?" * len(doc_ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT parent_id, child_id, containment FROM edges "
                f"WHERE parent_id IN ({marks}) AND child_id IN ({marks})",
                doc_ids + doc_ids).fetchall()
        return [dict(r) for r in rows]

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
            self._conn.commit()

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
            self._conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def stats(self) -> dict:
        with self._lock:
            docs = self._conn.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
            clusters = self._conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
            edges = self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            forked = self._conn.execute(
                "SELECT COUNT(*) FROM clusters WHERE fork=1").fetchone()[0]
        return {"docs": docs, "clusters": clusters, "edges": edges,
                "forked": forked}


# ---------- 聚类/谱系重建 ----------

def rebuild_clusters(db: Database) -> int:
    docs = db.all_docs()
    if not docs:
        db.replace_clusters({}, {})
        return 0
    ids = [str(d["id"]) for d in docs]
    preps = {str(d["id"]): d["prep"] for d in docs}
    mt = {str(d["id"]): d["mtime"] for d in docs}
    assign_str = union_find_clusters(ids, preps)

    by_root: dict[str, list[str]] = {}
    for i, root in assign_str.items():
        by_root.setdefault(root, []).append(i)

    assignments: dict[int, int] = {}
    lineage_by_root: dict[int, dict] = {}
    multi = {root: members for root, members in by_root.items() if len(members) > 1}
    # 单成员簇也要编号：cid 空间统一按 root 分配
    for cid, (root, members) in enumerate(sorted(by_root.items())):
        for m in members:
            assignments[int(m)] = cid
        nodes = [{"id": int(m), "mtime": mt[m], "prep": preps[m]}
                 for m in members]
        if root in multi:
            lin = lineage_mod.lineage(nodes)
        else:
            only = nodes[0]["id"] if nodes else None
            lin = {"edges": [], "latest": only,
                   "fork": False, "sinks": [only] if only is not None else [],
                   "dup_of": {}, "dup_groups": []}
        lineage_by_root[cid] = lin

    db.replace_clusters(assignments, lineage_by_root)
    return len(lineage_by_root)


class Engine:
    """扫描 + 聚类 + 谱系编排。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    def collect_files(self, roots: list[str]) -> list[Path]:
        found: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            rp = Path(root)
            if not rp.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(rp):
                dirnames[:] = [d for d in dirnames
                               if d not in (".git", "__pycache__")
                               and not d.startswith(".")]
                for fn in filenames:
                    p = Path(dirpath) / fn
                    if fn.startswith("~$"):  # Office 锁文件
                        continue
                    if p.suffix.lower() not in DOC_EXTS:
                        continue
                    key = os.path.normcase(str(p))
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(p)
                    if len(found) >= MAX_DOCS:
                        return found
        statted = []
        for p in found:
            try:
                statted.append((p.stat().st_mtime, p))
            except OSError:
                continue
        statted.sort(key=lambda t: t[0], reverse=True)
        return [p for _, p in statted]

    def scan(self, roots: list[str], progress=None) -> dict:
        roots = [str(Path(r)) for r in roots]
        files = self.collect_files(roots)
        fps = self.db.get_fingerprints()
        keep: set[str] = set()
        summary = {"total": len(files), "processed": 0, "skipped": 0,
                   "purged": 0, "clusters": 0}
        done = 0

        pending: list[tuple[Path, object]] = []
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            path_str = str(p)
            keep.add(os.path.normcase(path_str))
            done += 1
            old = fps.get(path_str)
            if old and abs(old[0] - st.st_mtime) < 0.001 and old[1] == st.st_size:
                summary["skipped"] += 1
                if progress:
                    progress(done, len(files), path_str)
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue
            pending.append((p, st))

        for p, st in pending:
            blocks = extract_blocks(str(p))
            self.db.upsert_doc(
                path=str(p), name=p.name, folder=str(p.parent),
                ftype=p.suffix.lower().lstrip("."),
                size=st.st_size, mtime=st.st_mtime, blocks=blocks)
            summary["processed"] += 1
            # done 以"跳过+已读"为准——曾在两个循环各加一次，进度会显示 60/30
            done = summary["skipped"] + summary["processed"]
            if progress:
                progress(done, len(files), str(p))

        summary["purged"] = self.db.purge_missing(roots, keep)
        summary["clusters"] = rebuild_clusters(self.db)
        return summary

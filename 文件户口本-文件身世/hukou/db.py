"""户口本 SQLite 存储：来源表 + USN 事件表 + 游标/元数据。"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

# 来源置信度：数字越大越可信；高置信度不会被低置信度覆盖
CONFIDENCE_RANK = {"dir": 1, "browser": 2, "ads": 3}


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
                CREATE TABLE IF NOT EXISTS origins(
                  path TEXT PRIMARY KEY,
                  name TEXT NOT NULL DEFAULT '',
                  folder TEXT NOT NULL DEFAULT '',
                  size INTEGER DEFAULT 0,
                  mtime REAL DEFAULT 0,
                  src_type TEXT NOT NULL DEFAULT '',
                  detail TEXT NOT NULL DEFAULT '',
                  confidence TEXT NOT NULL DEFAULT 'dir',
                  first_seen REAL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_origins_name ON origins(name);
                CREATE TABLE IF NOT EXISTS events(
                  vol TEXT NOT NULL,
                  usn INTEGER NOT NULL,
                  ts REAL DEFAULT 0,
                  frn INTEGER DEFAULT 0,
                  pfrn INTEGER DEFAULT 0,
                  name TEXT NOT NULL DEFAULT '',
                  reasons TEXT NOT NULL DEFAULT '',
                  PRIMARY KEY(vol, usn)
                );
                CREATE INDEX IF NOT EXISTS idx_events_name ON events(name);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS files(
                  vol TEXT NOT NULL,
                  frn INTEGER NOT NULL,
                  pfrn INTEGER DEFAULT 0,
                  name TEXT NOT NULL DEFAULT '',
                  PRIMARY KEY(vol, frn)
                );
                CREATE INDEX IF NOT EXISTS idx_files_pfrn ON files(vol, pfrn);
                CREATE INDEX IF NOT EXISTS idx_files_name ON files(vol, name);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 来源 ----------

    def upsert_origin(
        self, path: str, src_type: str, detail: str, confidence: str,
        size: int = 0, mtime: float = 0.0,
    ) -> bool:
        """写入来源。已有记录时：仅当新来源置信度更高才覆盖。返回是否写入。"""
        rank = CONFIDENCE_RANK.get(confidence, 0)
        with self._lock:
            row = self._conn.execute(
                "SELECT confidence FROM origins WHERE path=?", (path,)
            ).fetchone()
            if row:
                old_rank = CONFIDENCE_RANK.get(row["confidence"], 0)
                if rank <= old_rank:
                    return False
            p = Path(path)
            self._conn.execute(
                """
                INSERT INTO origins(path, name, folder, size, mtime, src_type,
                                    detail, confidence, first_seen)
                VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                  src_type=excluded.src_type, detail=excluded.detail,
                  confidence=excluded.confidence
                """,
                (path, p.name, str(p.parent), size, mtime,
                 src_type, detail, confidence, time.time()),
            )
            self._conn.commit()
            return True

    def lookup_origin(self, path: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM origins WHERE path=?", (path,)
            ).fetchone()
        return dict(row) if row else None

    def search_origins(self, q: str, limit: int = 50) -> list[dict]:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM origins WHERE name LIKE ? ESCAPE '\\' "
                "OR path LIKE ? ESCAPE '\\' "
                "ORDER BY first_seen DESC LIMIT ?",
                (like, like, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_origins(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM origins ORDER BY first_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 事件 ----------

    def add_events(self, vol: str, records: list[dict]) -> int:
        if not records:
            return 0
        with self._lock:
            cur = self._conn.cursor()
            added = 0
            for r in records:
                cur.execute(
                    "INSERT OR IGNORE INTO events(vol, usn, ts, frn, pfrn, name, reasons) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (vol, r.get("usn", 0), r.get("ts", 0.0), r.get("frn", 0),
                     r.get("pfrn", 0), r.get("name", ""), str(r.get("reason", 0))),
                )
                added += cur.rowcount
            self._conn.commit()
            return added

    def events_for_name(self, name: str, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE name=? ORDER BY ts DESC LIMIT ?",
                (name, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_events(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # ---------- MFT 快照（deep） ----------

    def store_files(self, vol: str, records: list[dict], commit_every: int = 20000) -> int:
        """批量写入快照（INSERT OR REPLACE，幂等）。返回写入行数。"""
        n = 0
        with self._lock:
            cur = self._conn.cursor()
            for r in records:
                cur.execute(
                    "INSERT OR REPLACE INTO files(vol, frn, pfrn, name) VALUES(?,?,?,?)",
                    (vol, r.get("frn", 0), r.get("pfrn", 0), r.get("name", "")),
                )
                n += 1
                if n % commit_every == 0:
                    self._conn.commit()
            self._conn.commit()
        return n

    def get_file(self, vol: str, frn: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM files WHERE vol=? AND frn=?", (vol, frn)
            ).fetchone()
        return dict(row) if row else None

    def find_child(self, vol: str, pfrn: int, name: str) -> int | None:
        """按父 frn + 名字找子 frn（路径下行解析用）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT frn FROM files WHERE vol=? AND pfrn=? AND name=? LIMIT 1",
                (vol, pfrn, name),
            ).fetchone()
        return row["frn"] if row else None

    def frn_index(self, vol: str) -> dict[int, dict]:
        """整卷 frn → {pfrn, name}（路径重建用；快照百万行时慎用，服务端走 find_child）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT frn, pfrn, name FROM files WHERE vol=?", (vol,)
            ).fetchall()
        return {r["frn"]: {"pfrn": r["pfrn"], "name": r["name"]} for r in rows}

    def count_files(self, vol: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM files WHERE vol=?", (vol,)
            ).fetchone()[0]

    def events_for_frn(self, vol: str, frn: int, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE vol=? AND frn=? ORDER BY usn LIMIT ?",
                (vol, frn, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    # ---------- USN 游标 / 元数据 ----------

    def get_usn_cursor(self, vol: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (f"usn_cursor_{vol}",)
            ).fetchone()
        return int(row["value"]) if row else 0

    def set_usn_cursor(self, vol: str, usn: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"usn_cursor_{vol}", str(usn)),
            )
            self._conn.commit()

    def stats(self) -> dict:
        with self._lock:
            origins = self._conn.execute("SELECT COUNT(*) FROM origins").fetchone()[0]
            events = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            by_type = self._conn.execute(
                "SELECT src_type, COUNT(*) AS n FROM origins GROUP BY src_type"
            ).fetchall()
            files = self._conn.execute("SELECT vol, COUNT(*) AS n FROM files "
                                       "GROUP BY vol").fetchall()
        return {
            "origins": origins,
            "events": events,
            "by_type": {r["src_type"]: r["n"] for r in by_type},
            "files_by_vol": {r["vol"]: r["n"] for r in files},
        }

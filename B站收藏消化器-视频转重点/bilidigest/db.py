"""任务库：jobs + transcripts。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


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
                CREATE TABLE IF NOT EXISTS jobs(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  kind TEXT NOT NULL,              -- url | local
                  src TEXT NOT NULL,
                  title TEXT DEFAULT '',
                  status TEXT DEFAULT 'pending',   -- pending|running|done|failed
                  stage TEXT DEFAULT '',
                  progress REAL DEFAULT 0,
                  media_path TEXT DEFAULT '',
                  model TEXT DEFAULT '',
                  duration REAL DEFAULT 0,
                  digest_json TEXT,
                  err TEXT,
                  created REAL DEFAULT 0,
                  finished REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS transcripts(
                  job_id INTEGER NOT NULL,
                  idx INTEGER NOT NULL,
                  t0 REAL, t1 REAL, text TEXT,
                  PRIMARY KEY(job_id, idx)
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- jobs ----------

    def insert_job(self, kind: str, src: str, title: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO jobs(kind, src, title, created) VALUES(?,?,?,?)",
                (kind, src, title, time.time()),
            )
            self._conn.commit()
            return cur.lastrowid

    def set_status(self, job_id: int, status: str, stage: str = "",
                   err: str | None = None) -> None:
        with self._lock:
            finished = time.time() if status in ("done", "failed") else 0
            self._conn.execute(
                "UPDATE jobs SET status=?, stage=?, err=?, finished=CASE "
                "WHEN ? IN ('done','failed') THEN ? ELSE finished END WHERE id=?",
                (status, stage, err, status, finished, job_id),
            )
            self._conn.commit()

    def set_progress(self, job_id: int, stage: str, progress: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET stage=?, progress=? WHERE id=?",
                (stage, progress, job_id),
            )
            self._conn.commit()

    def set_media(self, job_id: int, media_path: str, title: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET media_path=?, title=CASE WHEN ?!='' THEN ? ELSE title END "
                "WHERE id=?",
                (media_path, title, title, job_id),
            )
            self._conn.commit()

    def set_done(self, job_id: int, digest: dict, duration: float,
                 model: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='done', stage='', progress=1, digest_json=?, "
                "duration=?, model=?, finished=? WHERE id=?",
                (json.dumps(digest, ensure_ascii=False), duration, model,
                 time.time(), job_id),
            )
            self._conn.commit()

    def get_job(self, job_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        if not row:
            return None
        job = dict(row)
        if job["digest_json"]:
            try:
                job["digest"] = json.loads(job["digest_json"])
            except ValueError:
                job["digest"] = None
        else:
            job["digest"] = None
        return job

    def list_jobs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            j = dict(r)
            j.pop("digest_json", None)
            out.append(j)
        return out

    def pending_jobs(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM jobs WHERE status IN ('pending','running') ORDER BY id"
            ).fetchall()
        return [r["id"] for r in rows]

    def stats(self) -> dict:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            done = self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='done'"
            ).fetchone()[0]
            failed = self._conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE status='failed'"
            ).fetchone()[0]
            seconds = self._conn.execute(
                "SELECT COALESCE(SUM(duration),0) FROM jobs WHERE status='done'"
            ).fetchone()[0]
        return {"total": total, "done": done, "failed": failed,
                "minutes_digested": round(seconds / 60, 1)}

    # ---------- transcripts ----------

    def save_transcripts(self, job_id: int, segments: list[dict]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM transcripts WHERE job_id=?", (job_id,))
            self._conn.executemany(
                "INSERT INTO transcripts(job_id, idx, t0, t1, text) VALUES(?,?,?,?,?)",
                [(job_id, i, s["t0"], s["t1"], s["text"])
                 for i, s in enumerate(segments)],
            )
            self._conn.commit()

    def get_transcripts(self, job_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT t0, t1, text FROM transcripts WHERE job_id=? ORDER BY idx",
                (job_id,),
            ).fetchall()
        return [dict(r) for r in rows]

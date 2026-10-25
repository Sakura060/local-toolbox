"""浏览器下载记录归因：只读拷贝 History 库再查询（避免锁冲突）。

Chrome/Edge 的 downloads 表记录 target_path 与 url_chains，
可回溯数月，弥补 USN 无法回溯历史的短板。
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path


def chrome_time_to_unix(us: int) -> float:
    """Chrome 时间戳（1601 起的微秒）→ Unix 秒。"""
    if not us:
        return 0.0
    return (us - 11644473600000000) / 1_000_000


def query_downloads(history_file: Path, tmp_dir: Path) -> list[dict]:
    """从单个 History 库读取下载记录（先拷贝，纯只读原文件）。"""
    tmp = Path(tmp_dir) / f"history_{abs(hash(str(history_file)))}.db"
    try:
        shutil.copy2(history_file, tmp)
        conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT target_path, tab_url, start_time, received_bytes "
                "FROM downloads ORDER BY start_time DESC"
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return []
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    out: list[dict] = []
    for target_path, tab_url, start_time, received in rows:
        out.append({
            "path": target_path,
            "url": tab_url or "",
            "time": chrome_time_to_unix(start_time),
            "bytes": received or 0,
            "browser": _browser_name(history_file),
        })
    return out


def collect_download_records(history_files: list[Path] | None = None) -> list[dict]:
    """收集全部浏览器下载记录，按时间倒序。"""
    from .config import browser_history_files

    files = history_files if history_files is not None else browser_history_files()
    records: list[dict] = []
    if not files:
        return records
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        for f in files:
            records.extend(query_downloads(f, tmp_dir))
    records.sort(key=lambda r: r["time"], reverse=True)
    return records


def _browser_name(history_file: Path) -> str:
    parts = history_file.parts
    for name in ("Chrome", "Edge", "Chromium", "Brave-Browser"):
        if name in parts:
            return {"Brave-Browser": "Brave"}.get(name, name)
    return "浏览器"

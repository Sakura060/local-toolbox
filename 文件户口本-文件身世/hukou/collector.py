"""采集编排：来源归因扫描 + USN 事件记账。

归因优先级（高→低）：
  1. ads      Zone.Identifier（浏览器下载自带，含来源网址）
  2. browser  浏览器 downloads 库（先拷贝只读副本查询）
  3. dir      接收目录启发式（标注"推测"）
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from . import ads, browsers, classify, usn
from .config import MAX_SCAN_FILES
from .db import Database


def _iter_files(paths: list[str]):
    count = 0
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            count += 1
            yield p
            continue
        if not p.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "__pycache__")
                           and not d.startswith(".")]
            for fn in filenames:
                count += 1
                if count > MAX_SCAN_FILES:
                    return
                yield Path(dirpath) / fn


def scan_origins(paths: list[str], db: Database, use_browser: bool = True,
                 progress=None) -> dict:
    """对给定路径下的文件做来源归因，写入 origins 表。"""
    summary = {"files": 0, "attributed": 0, "browser_records": 0}

    browser_map: dict[str, dict] = {}
    if use_browser:
        for rec in browsers.collect_download_records():
            key = os.path.normcase(rec["path"])
            existing = browser_map.get(key)
            if not existing or rec["time"] > existing["time"]:
                browser_map[key] = rec
        summary["browser_records"] = len(browser_map)
        # 浏览器已删除的下载历史也有价值：文件仍在的直接入库
        for key, rec in browser_map.items():
            p = Path(rec["path"])
            if p.is_file():
                db.upsert_origin(
                    rec["path"], "网络下载",
                    rec["url"] + f"（{rec['browser']} 下载记录）",
                    "browser", size=p.stat().st_size, mtime=rec["time"],
                )

    for f in _iter_files(paths):
        summary["files"] += 1
        try:
            st = f.stat()
        except OSError:
            continue
        path_str = str(f)

        zone = ads.read_zone_identifier(path_str)
        if zone:
            src_type, detail = ads.origin_from_zone(zone)
            db.upsert_origin(path_str, src_type, detail, "ads",
                             size=st.st_size, mtime=st.st_mtime)
            summary["attributed"] += 1
        elif path_str in browser_map or os.path.normcase(path_str) in browser_map:
            rec = browser_map.get(path_str) or browser_map[os.path.normcase(path_str)]
            db.upsert_origin(path_str, "网络下载",
                             rec["url"] + f"（{rec['browser']} 下载记录）",
                             "browser", size=st.st_size, mtime=st.st_mtime)
            summary["attributed"] += 1
        else:
            guess = classify.guess_origin(path_str)
            if guess:
                db.upsert_origin(path_str, guess[0], guess[1], "dir",
                                 size=st.st_size, mtime=st.st_mtime)
                summary["attributed"] += 1

        if progress and summary["files"] % 200 == 0:
            progress(summary["files"], path_str)

    return summary


def snapshot_volume(vol: str, db: Database, progress=None) -> int:
    """一次性枚举卷上全部 MFT 记录入库（events 表，仅保留关注原因）。

    USN 日志只能从"现在"开始记账；这是唯一能重建"过去改名/新建"历史的只读手段。
    """
    count = 0
    batch: list[dict] = []
    for rec in usn.enum_volume_records(vol):
        if rec["reason"] & usn.INTERESTING_REASONS:
            batch.append({**rec, "vol": vol})
            count += 1
            if len(batch) >= 5000:
                db.add_events(vol, batch)
                batch = []
                if progress:
                    progress(count)
    db.add_events(vol, batch)
    return count


def watch_once(vol: str, db: Database) -> int:
    """读一轮新 USN 记录入库，返回新增条数。"""
    cursor = db.get_usn_cursor(vol)
    records, new_cursor = usn.read_new_records(vol, cursor)
    interesting = [r for r in records if r["reason"] & usn.INTERESTING_REASONS]
    added = db.add_events(vol, interesting)
    db.set_usn_cursor(vol, new_cursor)
    return added


def watch_loop(volumes: list[str], db: Database, interval: float = 2.0,
               stop: threading.Event | None = None) -> None:
    """持续记账循环（需管理员权限）。"""
    while not (stop and stop.is_set()):
        for vol in volumes:
            try:
                watch_once(vol, db)
            except usn.UsnAccessError as exc:
                print(f"[watch] {vol}: {exc}", flush=True)
                return
            except Exception as exc:
                print(f"[watch] {vol}: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(interval)

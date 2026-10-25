"""增量索引器：扫描目录 → 指纹比对 → 批量 OCR → 缩略图 → 入库。

设计约束：
- 幂等：同批重跑不产生重复/丢失；OCR 失败(ocr_ok=0)的记录下次自动重试。
- 隔离：单文件失败只记 err，不中断批次；OCR 在子进程批量执行。
- 可测：OCR/缩略图/尺寸依赖全部注入（测试用假函数跑全流程）。
"""

from __future__ import annotations

import os
from pathlib import Path

from . import seg
from .config import IMAGE_EXTS, MAX_FILE_BYTES, SKIP_DIR_NAMES, THUMB_DIR
from .db import Database
from .hashes import dhash_file
from .ocr import ocr_image
from .sources import detect_source
from .thumbs import dimensions, make_thumb


class Indexer:
    def __init__(
        self,
        db: Database,
        ocr_func=None,
        thumb_func=None,
        dim_func=None,
        thumb_dir: Path | None = None,
        hash_func=None,
    ) -> None:
        self.db = db
        self.ocr_func = ocr_func or ocr_image
        self.thumb_func = thumb_func or make_thumb
        self.dim_func = dim_func or dimensions
        self.thumb_dir = Path(thumb_dir) if thumb_dir else THUMB_DIR
        self.hash_func = hash_func or dhash_file

    def close(self) -> None:
        pass  # 进程内 OCR，无外部资源

    # ---------- 收集 ----------

    def collect_files(self, roots: list[str]) -> list[Path]:
        """收集根目录下全部图片文件，按 mtime 新→旧排序（新截图先索引）。"""
        found: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            root_path = Path(root)
            if not root_path.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root_path):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in SKIP_DIR_NAMES and not d.startswith(".")
                ]
                for fn in filenames:
                    p = Path(dirpath) / fn
                    if p.suffix.lower() not in IMAGE_EXTS:
                        continue
                    key = os.path.normcase(str(p))
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append(p)
        statted: list[tuple[int, Path]] = []
        for p in found:
            try:
                statted.append((p.stat().st_mtime_ns, p))
            except OSError:
                continue
        statted.sort(key=lambda t: t[0], reverse=True)
        return [p for _, p in statted]

    # ---------- 扫描 ----------

    def scan(self, roots: list[str], progress=None) -> dict:
        """增量扫描。progress(done, total, current_path) 可选回调。

        返回摘要 dict：total/processed/skipped/updated/errors/purged。
        """
        roots = [str(Path(r)) for r in roots]
        files = self.collect_files(roots)
        total = len(files)
        fps = self.db.get_fingerprints()
        missing_hash = set(self.db.missing_hash_paths())
        keep: set[str] = set()
        summary = {"total": total, "processed": 0, "skipped": 0,
                   "updated": 0, "errors": 0, "purged": 0, "hashed": 0}
        done = 0

        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            path_str = str(p)
            keep.add(os.path.normcase(path_str))
            done += 1
            old = fps.get(path_str)
            if old and old[0] == st.st_size and old[1] == st.st_mtime_ns and old[2] == 1:
                summary["skipped"] += 1
                if path_str in missing_hash:
                    # v0.1 时代入库的旧记录回填 dHash（跳过 OCR，只解码一次）
                    try:
                        self.db.set_dhash_by_path(
                            path_str, self.hash_func(path_str))
                        summary["hashed"] += 1
                    except Exception:
                        pass
                if progress:
                    progress(done, total, path_str)
                continue
            if st.st_size > MAX_FILE_BYTES:
                continue  # 超大文件跳过（不记 err，下次还跳）

            text, err = self.ocr_func(path_str)
            ocr_ok = 0 if err else 1
            width = height = 0
            try:
                width, height = self.dim_func(path_str)
            except Exception:
                pass
            dhash = None
            try:
                dhash = self.hash_func(path_str)
            except Exception:
                pass  # 解码不了的图不记哈希（OCR 会记 err）

            seg_text = seg.segment(p.name) + " " + seg.segment(text)
            img_id = self.db.upsert_image(
                path=path_str,
                name=p.name,
                folder=str(p.parent),
                size=st.st_size,
                mtime_ns=st.st_mtime_ns,
                width=width,
                height=height,
                ocr_text=text,
                ocr_ok=ocr_ok,
                err=err or None,
                seg_text=seg_text,
                dhash=dhash,
                source=detect_source(path_str),
            )
            try:
                self.thumb_func(path_str, self.thumb_dir / f"{img_id}.jpg")
            except Exception as exc:
                if not err:
                    self.db.set_image_error(img_id, f"thumb {type(exc).__name__}: {exc}")

            summary["processed"] += 1
            if err:
                summary["errors"] += 1
            else:
                summary["updated"] += 1
            if progress:
                progress(done, total, path_str)

        summary["purged"] = self.db.purge_missing(roots, keep)
        self.db.add_roots(roots)
        return summary

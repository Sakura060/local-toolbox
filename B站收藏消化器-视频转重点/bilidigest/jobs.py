"""任务流水线编排：单 worker 队列，逐个消化。

下载（如需要）→ 本地 ASR → 抽取式重点 → 入库 + 导出 txt。
任何阶段失败：任务标记 failed，错误信息落库，不炸 worker。
"""

from __future__ import annotations

import queue
import threading

from . import asr, extract, fetch
from .config import DEFAULT_MODEL, TRANSCRIPT_DIR, ensure_dirs
from .db import Database


class Runner:
    def __init__(self, db: Database, model_size: str = DEFAULT_MODEL) -> None:
        self.db = db
        self.model_size = model_size
        self._q: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---------- 入队 ----------

    def add(self, input_str: str, autostart: bool = True) -> int:
        kind, value = fetch.classify_input(input_str)
        job_id = self.db.insert_job(kind, value)
        if autostart:
            self.enqueue(job_id)
        return job_id

    def enqueue(self, job_id: int) -> None:
        self._ensure_worker()
        self._q.put(job_id)

    def enqueue_pending(self) -> int:
        """服务启动时恢复未完成任务。"""
        n = 0
        for jid in self.db.pending_jobs():
            self.enqueue(jid)
            n += 1
        return n

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker, name="bili-digest-worker", daemon=True
            )
            self._thread.start()

    # ---------- 处理 ----------

    def _worker(self) -> None:
        while True:
            job_id = self._q.get()
            try:
                self.process(job_id)
            except Exception as exc:  # 兜底：worker 永不退出
                try:
                    self.db.set_status(job_id, "failed", "", f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass

    def process(self, job_id: int) -> dict:
        ensure_dirs()
        job = self.db.get_job(job_id)
        if not job:
            return {"ok": False, "error": "no such job"}
        try:
            self.db.set_status(job_id, "running", "准备")

            # 1. 取媒体
            if job["kind"] == "url":
                self.db.set_progress(job_id, "下载音频", 0.02)
                media = fetch.download_audio(job["src"])
                self.db.set_media(job_id, str(media))
            else:
                media = fetch.validate_local(job["src"])
                self.db.set_media(job_id, str(media), title=media.name)

            # 2. 本地转写
            self.db.set_progress(job_id, "本地转写", 0.1)

            def prog(cur: float, total: float) -> None:
                frac = 0.1 + 0.8 * (cur / total if total > 0 else 0)
                self.db.set_progress(job_id, f"本地转写 {int(cur)}s/{int(total)}s",
                                     min(0.9, frac))

            segments, duration = asr.transcribe(
                str(media), self.model_size, progress=prog
            )
            segments = extract.normalize_segments(segments)
            self.db.save_transcripts(job_id, segments)

            # 3. 抽取式重点
            self.db.set_progress(job_id, "提取重点", 0.95)
            dg = extract.digest(segments)

            # 4. 导出 txt（可搜索的资产）
            txt_path = TRANSCRIPT_DIR / f"{job_id}.txt"
            txt_path.write_text(
                extract.transcript_text(extract.merge_to_sentences(segments)) or "（无有效语音内容）",
                encoding="utf-8",
            )

            self.db.set_done(job_id, dg, duration, self.model_size)

            # 5. 清理下载的音频（仅 URL 任务：转写已完成，文字才是资产；
            #    本地文件是用户自己的，绝不动）。删除失败不影响任务成功。
            if job["kind"] == "url":
                try:
                    media.unlink(missing_ok=True)
                    self.db.set_media(job_id, "")  # 音频已不在盘，跳转入口随之隐藏
                except OSError:
                    pass  # 删不掉就留着（下次手动清理）
            return {"ok": True, "segments": len(segments), "txt": str(txt_path)}
        except (asr.AsrUnavailable, fetch.FetchError) as exc:
            self.db.set_status(job_id, "failed", "", str(exc))
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            self.db.set_status(job_id, "failed", "", f"{type(exc).__name__}: {exc}")
            return {"ok": False, "error": str(exc)}

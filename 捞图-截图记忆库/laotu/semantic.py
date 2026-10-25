"""语义搜索编排：Embedder 协议 + 假引擎 + 向量回填 + 余弦检索。

分层：
- Embedder：鸭子类型协议（name / dim / embed_text / embed_image）。
- FakeEmbedder：字符二元组哈希到向量空间（测试用，路径/文本共享同一
  哈希技巧，"红色" 查询能命中路径含 "红色" 的图——可断言排序语义）。
- CLIPEmbedder：真引擎在 clip_model.py（onnxruntime + tokenizers）。
- SemanticIndex：持有 db + embedder，负责回填（缺失/mtime 过期）、
  余弦检索、状态快照与后台循环。

红线：本模块不做任何 ORT 推理调用（沙箱 detached 下推理必挂，见 D 系列），
真引擎只在 clip_model.py 且由可用性探测决定是否实例化。
"""

from __future__ import annotations

import threading
import time
import zlib
from pathlib import Path

import numpy as np

MODEL_NAME = "chinese-clip-vit-base-patch16"
MODEL_DIRNAME = MODEL_NAME


class EmbedUnavailable(Exception):
    """语义引擎不可用（缺模型/缺依赖/初始化失败）。"""


class FakeEmbedder:
    """确定性假引擎：字符串 → 稳定哈希向量（字符二元组计数，L2 归一）。

    文本查询与图片路径过同一个哈希器，所以测试里"红色"能检索到
    路径含"红色"的图——排序断言有意义，且完全不依赖真模型。
    """

    def __init__(self, dim: int = 32) -> None:
        self.name = "fake-embedder"
        self.dim = dim

    def _vec(self, s: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for i in range(len(s) - 1):
            bigram = s[i] + s[i + 1]
            v[zlib.crc32(bigram.encode("utf-8")) % self.dim] += 1.0
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def embed_text(self, text: str) -> np.ndarray:
        return self._vec(text)

    def embed_image(self, path: str) -> np.ndarray:
        return self._vec(Path(path).name)


class SemanticState:
    """回填进度的线程安全快照（结构对齐 server.ScanState）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = False
        self.done = 0
        self.total = 0
        self.current = ""
        self.last_error = ""

    def start(self, total: int) -> None:
        with self._lock:
            self.active, self.done, self.total = True, 0, total

    def update(self, done: int, current: str) -> None:
        with self._lock:
            self.done, self.current = done, current

    def fail(self, err: str) -> None:
        with self._lock:
            self.last_error = err

    def finish(self) -> None:
        with self._lock:
            self.active, self.current = False, ""

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "active": self.active,
                "done": self.done,
                "total": self.total,
                "current": self.current,
                "last_error": self.last_error,
            }


class SemanticIndex:
    """向量回填 + 余弦检索的编排层。

    embedder 可为 None（引擎未就绪）：search 返回空、backfill 抛
    EmbedUnavailable，status 给出 reason——服务层据此降级。
    """

    def __init__(self, db, embedder=None, state: SemanticState | None = None,
                 min_score: float = 0.10, reason: str = "") -> None:
        self.db = db
        self.embedder = embedder
        self.state = state or SemanticState()
        self.min_score = min_score
        self.reason = reason  # embedder 为 None 时给 /api 的不可用原因

    # ---------- 状态 ----------

    def status(self) -> dict:
        name = self.embedder.name if self.embedder else MODEL_NAME
        total = self.db.stats()["total"]
        out = {
            "available": self.embedder is not None,
            "model": name,
            "indexed": self.db.count_embeddings(name) if self.embedder else 0,
            "total": total,
            "indexing": self.state.snapshot(),
        }
        if self.embedder is None:
            out["reason"] = self.reason or (
                "语义引擎未就绪：先跑 tools\\clip_download.py "
                "下载模型，然后重启捞图")
        return out

    # ---------- 回填 ----------

    def backfill(self, limit: int | None = None, batch_size: int = 8) -> int:
        """为缺失/过期图片补向量，返回本次完成的张数。可重复执行（幂等）。

        引擎若带 embed_images（批量接口）则按批推理；单图失败记入
        state.last_error，不阻塞整批。
        """
        if self.embedder is None:
            raise EmbedUnavailable("语义引擎未就绪")
        backlog = self.db.embedding_backlog(self.embedder.name)
        if limit is not None:
            backlog = backlog[:limit]
        if not backlog:
            return 0
        self.state.start(len(backlog))
        done = 0
        try:
            batch_fn = getattr(self.embedder, "embed_images", None)
            if batch_fn is not None:
                done = self._backfill_batched(backlog, batch_fn, batch_size)
            else:
                done = self._backfill_one_by_one(backlog)
        finally:
            self.state.finish()
        return done

    def _store(self, img_id: int, path: str, mtime_ns: int, vec) -> None:
        """落一条向量；引擎返回 NaN/Inf 视为失败，只记录不落库。"""
        arr = np.asarray(vec, dtype=np.float32)
        if arr.size == 0 or not np.all(np.isfinite(arr)):
            self.state.fail(f"#{img_id} {Path(path).name}: 引擎返回无效向量")
            return
        self.db.set_embedding(img_id, self.embedder.name, arr, mtime_ns)

    def _backfill_batched(self, backlog, batch_fn, batch_size: int) -> int:
        done = 0
        for i in range(0, len(backlog), batch_size):
            chunk = backlog[i: i + batch_size]
            paths = [p for _, p, _ in chunk]
            try:
                vecs = batch_fn(paths, batch_size=batch_size)
            except Exception as exc:
                self.state.fail(f"批量推理失败：{exc}")
                done += len(chunk)  # 本批放弃，进度照走（下轮重试）
                self.state.update(done, "")
                continue
            for (img_id, path, mtime_ns), vec in zip(chunk, vecs):
                self._store(img_id, path, mtime_ns, vec)
                done += 1
                self.state.update(done, Path(path).name)
        return done

    def _backfill_one_by_one(self, backlog) -> int:
        done = 0
        for img_id, path, mtime_ns in backlog:
            try:
                vec = self.embedder.embed_image(path)
            except FileNotFoundError:
                done += 1  # 文件已删：等下一次重扫 purge
                continue
            except Exception as exc:
                self.state.fail(f"#{img_id} {Path(path).name}: {exc}")
                done += 1
                continue
            self._store(img_id, path, mtime_ns, vec)
            done += 1
            self.state.update(done, Path(path).name)
        return done

    def start_background(self, interval: int = 30) -> threading.Thread:
        """serve 模式的常驻回填线程：模型就绪且有待回填时持续补齐。"""

        def loop() -> None:
            while True:
                try:
                    if self.embedder is not None and not self.state.snapshot()["active"]:
                        if self.db.embedding_backlog(self.embedder.name):
                            self.backfill()
                except Exception:
                    pass
                time.sleep(interval)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return t

    # ---------- 检索 ----------

    def search(self, query: str, limit: int = 60,
               min_score: float | None = None) -> list[tuple[int, float]]:
        """文本 → 余弦相似检索，[(img_id, score)] 降序。

        向量入库与查询侧都做 L2 归一（真引擎在 embed_* 内做；假引擎
        同样），点积即余弦。
        """
        if self.embedder is None:
            raise EmbedUnavailable("语义引擎未就绪")
        q = (query or "").strip()
        if not q:
            return []
        thr = self.min_score if min_score is None else min_score
        qvec = np.asarray(self.embedder.embed_text(q), dtype=np.float32)
        norm = float(np.linalg.norm(qvec))
        if norm == 0:
            return []
        qvec = qvec / norm
        ids, dim, blob = self.db.embedding_rows(self.embedder.name)
        if not ids:
            return []
        mat = np.frombuffer(blob, dtype="<f4").reshape(len(ids), dim)
        sims = mat @ qvec
        order = np.argsort(-sims)[: limit * 2]  # 多取一倍再按阈值过滤
        out = [(ids[i], float(sims[i])) for i in order if sims[i] >= thr]
        return out[:limit]

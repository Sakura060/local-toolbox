"""本地 ASR：faster-whisper（CPU int8）。

模型首次使用需联网下载一次，之后完全离线。模型缺失/下载失败统一抛
AsrUnavailable，携带可执行的指引文本，上层原样展示给用户。
"""

from __future__ import annotations

import threading

from .config import DEFAULT_MODEL, MODEL_SIZES

_models: dict[str, object] = {}
_lock = threading.Lock()


class AsrUnavailable(RuntimeError):
    pass


def _get_model(size: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AsrUnavailable(
            "缺少 faster-whisper，请执行：pip install faster-whisper"
        ) from exc
    if size not in MODEL_SIZES:
        size = DEFAULT_MODEL
    with _lock:
        if size not in _models:
            try:
                _models[size] = WhisperModel(size, device="cpu", compute_type="int8")
            except Exception as exc:
                raise AsrUnavailable(
                    f"加载模型 {size} 失败：{exc}（首次使用需联网下载模型，"
                    f"之后完全离线；检查网络后重试，或换更小的档位 tiny）"
                ) from exc
        return _models[size]


def transcribe(media_path: str, model_size: str = DEFAULT_MODEL, progress=None):
    """转写音视频。返回 (segments, duration)。

    segments 元素：{"t0": float, "t1": float, "text": str}（秒）。
    progress(cur_seconds, total_seconds) 可选回调。
    """
    model = _get_model(model_size)
    try:
        seg_iter, info = model.transcribe(
            str(media_path), vad_filter=True, beam_size=1
        )
        out = []
        for s in seg_iter:
            out.append({
                "t0": float(s.start),
                "t1": float(s.end),
                "text": (s.text or "").strip(),
            })
            if progress:
                try:
                    progress(float(s.end), float(info.duration or 0))
                except Exception:
                    pass
        return out, float(info.duration or 0)
    except AsrUnavailable:
        raise
    except Exception as exc:
        raise AsrUnavailable(f"转写失败：{type(exc).__name__}: {exc}") from exc


def is_ready(model_size: str = DEFAULT_MODEL) -> bool:
    """模型是否已就绪（已缓存过），用于 UI 判断，不触发下载。"""
    return model_size in _models

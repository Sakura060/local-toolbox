"""输入获取：本地文件校验 + B站 URL 音频下载（yt-dlp 子进程，单并发）。

合规边界：只处理用户明确指定的单条内容；仅拉音频轨；单并发；失败给出可读原因。
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
from pathlib import Path

from .config import MEDIA_DIR, MEDIA_EXTS

_dl_lock = threading.Lock()

_BILI_RE = re.compile(r"(bilibili\.com|b23\.tv)", re.I)


class FetchError(RuntimeError):
    pass


def is_bili_url(s: str) -> bool:
    return bool(_BILI_RE.search(s or ""))


def validate_local(s: str) -> Path:
    p = Path(s.strip().strip('"'))
    if not p.is_file():
        raise FetchError(f"文件不存在：{p}")
    if p.suffix.lower() not in MEDIA_EXTS:
        raise FetchError(
            f"不支持的格式 {p.suffix}（支持：{'/'.join(sorted(MEDIA_EXTS))}）"
        )
    if p.stat().st_size == 0:
        raise FetchError(f"文件为空：{p}")
    return p


def classify_input(s: str) -> tuple[str, str]:
    """返回 (kind, 规范化值)：kind ∈ url / local。"""
    s = (s or "").strip()
    if is_bili_url(s):
        return "url", s
    return "local", str(validate_local(s))


def build_ytdlp_cmd(url: str, out_dir: Path) -> list[str]:
    """纯函数，可测试。仅音频、单视频、固定命名。"""
    return [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio[ext=m4a]/bestaudio",
        "--no-playlist",
        "--no-progress",
        "--socket-timeout", "30",
        "-o", str(Path(out_dir) / "%(id)s.%(ext)s"),
        url,
    ]


def download_audio(url: str, out_dir: Path = MEDIA_DIR, timeout: int = 1800) -> Path:
    """下载 B站音频轨，返回本地文件路径。失败抛 FetchError。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in out_dir.iterdir()}
    cmd = build_ytdlp_cmd(url, out_dir)
    with _dl_lock:  # 单并发：避免触发平台风控
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise FetchError(f"下载超时（>{timeout}s）。可重试，或换更短的视频。") from exc
        except OSError as exc:
            raise FetchError(f"启动 yt-dlp 失败：{exc}（是否已 pip install yt-dlp？）") from exc

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        hint = "\n".join(tail)
        raise FetchError(
            "yt-dlp 下载失败：\n" + hint +
            "\n提示：部分视频需要登录（大会员/地区限制），v0.1 暂不支持 cookies。"
        )

    after = {p.name for p in out_dir.iterdir()} - before
    for name in sorted(after):
        p = out_dir / name
        if p.suffix.lower() in MEDIA_EXTS:
            return p
    raise FetchError("yt-dlp 返回成功但没有产出音频文件。")

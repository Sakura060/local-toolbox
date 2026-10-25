"""配置：目录、模型档位、常量。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "bili.db"
MEDIA_DIR = DATA_DIR / "media"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"

PORT = 8639

# ASR 档位：tiny(75MB)→base(150MB)→small(500MB)，CPU 友好度递减
MODEL_SIZES = ("tiny", "base", "small")
DEFAULT_MODEL = "base"

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}
VIDEO_EXTS = {".mp4", ".mkv", ".flv", ".webm", ".mov", ".avi", ".ts", ".m4v"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

# 章节切分：句子间隔秒数阈值 / 每章最少句数
CHAPTER_GAP = 3.5
CHAPTER_MIN_SENTS = 4


def ensure_dirs() -> None:
    for d in (DATA_DIR, MEDIA_DIR, TRANSCRIPT_DIR):
        d.mkdir(parents=True, exist_ok=True)

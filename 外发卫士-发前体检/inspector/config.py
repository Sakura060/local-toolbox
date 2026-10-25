"""配置：目录、支持类型、常量。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"   # 体检工作区：<id>/original.ext + sanitized.ext
PORT = 8641

MAX_UPLOAD = 200 * 1024 * 1024

OOXML_EXTS = {".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}
ARCHIVE_EXTS = {".zip", ".docx-not"}  # zip 单列
ARCHIVE_ONLY = {".zip"}
SUPPORTED = OOXML_EXTS | IMAGE_EXTS | PDF_EXTS | ARCHIVE_ONLY

KEEP_HOURS = 24  # 上传工作区保留时长


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

"""全局配置：数据目录、支持的图片类型、默认监控目录。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
THUMB_DIR = DATA_DIR / "thumbs"
DB_PATH = DATA_DIR / "laotu.db"
MODELS_DIR = DATA_DIR / "models"
# 中文 CLIP（tools\clip_download.py 下载目标；语义搜索用）
SEMANTIC_MODEL_DIR = MODELS_DIR / "chinese-clip-vit-base-patch16"

# 单文件大小上限：超过则跳过（截图场景极少超过）
MAX_FILE_BYTES = 80 * 1024 * 1024

# 缩略图最长边
THUMB_MAX_SIDE = 360

# OCR 前把长边压到该值（加速且精度几乎无损）
OCR_MAX_SIDE = 2000

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}

# 扫描时跳过的目录名（避免索引无关系统目录）
SKIP_DIR_NAMES = {"node_modules", ".git", ".svn", "__pycache__", "AppData"}


def ensure_data_dirs() -> None:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)


def _wechat_file_dirs() -> list[str]:
    """探测微信接收文件目录（新版 xwechat_files / 旧版 WeChat Files）。

    只读扫描：捞图按扩展名过滤，目录里混着的非图片文件不会入索引。
    """
    out: list[str] = []
    home = Path.home()
    candidates: list[Path] = [
        home / "Documents" / "WeChat Files",       # 旧版微信
        home / "Documents" / "xwechat_files",      # 新版微信（默认装文档目录）
    ]
    for drive in ("C:/", "D:/", "E:/"):
        candidates.append(Path(drive) / "微信聊天" / "xwechat_files")
        candidates.append(Path(drive) / "WeChat Files")
    for base in candidates:
        if not base.is_dir():
            continue
        for wxid in base.iterdir():
            if not wxid.is_dir() or not wxid.name.startswith("wxid_"):
                continue
            new_style = wxid / "msg" / "file"
            old_style = wxid / "FileStorage" / "File"
            if new_style.is_dir():
                out.append(str(new_style))
            elif old_style.is_dir():
                out.append(str(old_style))
    return out[:4]  # 最多 4 个账号目录，防失控


def default_watch_dirs() -> list[str]:
    """探测常见截图目录，存在哪个算哪个；外加微信接收目录。"""
    home = Path.home()
    candidates = [
        home / "Pictures" / "Screenshots",
        home / "OneDrive" / "Pictures" / "Screenshots",
        home / "OneDrive" / "桌面" / "Screenshots",
        home / "Pictures" / "Camera Roll",
    ]
    dirs = [str(p) for p in candidates if p.is_dir()]
    dirs.extend(_wechat_file_dirs())
    return dirs

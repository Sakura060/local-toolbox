"""配置：数据目录、常见来源目录。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "hukou.db"

MAX_SCAN_FILES = 20000  # 单次 scan 防失控上限

# 接收类目录启发式（按优先级）：命中即标注"推测"来源
RECEIVE_DIR_RULES = [
    ("微信接收", ("xwechat_files", "WeChat Files")),
    ("QQ接收", ("Tencent Files",)),
    ("钉钉接收", ("DingTalk",)),
    ("浏览器下载", ("Downloads", "下载")),
]


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def default_scan_dirs() -> list[str]:
    """默认盘点目录：用户下载夹。"""
    home = Path.home()
    cands = [home / "Downloads", home / "Downloads" / "WeChat Files"]
    out = []
    for c in cands:
        if c.is_dir():
            out.append(str(c))
    return out


def browser_history_files() -> list[Path]:
    """定位 Chrome / Edge 的 History 库（所有 Profile）。"""
    home = Path.home()
    local = home / "AppData" / "Local"
    patterns = [
        local / "Google" / "Chrome" / "User Data",
        local / "Microsoft" / "Edge" / "User Data",
        local / "Chromium" / "User Data",
        local / "BraveSoftware" / "Brave-Browser" / "User Data",
    ]
    out: list[Path] = []
    for base in patterns:
        if not base.is_dir():
            continue
        for profile in base.iterdir():
            if not profile.is_dir():
                continue
            if profile.name in ("System Profile", "Guest Profile"):
                continue
            h = profile / "History"
            if h.is_file():
                out.append(h)
    return out

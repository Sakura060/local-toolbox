"""配置。"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "docline.db"
PORT = 8640

DOC_EXTS = {".docx", ".pptx", ".xlsx"}
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_DOCS = 5000

# 聚类：两文档 shingle Jaccard ≥ 此值视为同源
CLUSTER_SIM = 0.55
# 谱系：A 的内容有 ≥ 此比例出现在 B 中 → B 是 A 的后代
LINEAGE_CONTAIN = 0.75
# 双向包含都 ≥ 此值 → 同一版本的重复副本
DUP_CONTAIN = 0.97
# mtime 容差（秒）：在此内不据此分先后
MTIME_TOL = 3.0
# 近似匹配：块相似度 ≥ 此值算"修改"
BLOCK_SIM = 0.62


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def default_scan_dirs() -> list[str]:
    """默认扫描目录：文档 / 下载。"""
    home = Path.home()
    out = []
    for cand in (home / "Documents", home / "Downloads"):
        if cand.is_dir():
            out.append(str(cand))
    return out

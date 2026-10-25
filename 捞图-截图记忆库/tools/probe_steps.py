"""ocr_image 内部阶段二分探针（在 detached 环境下运行才会复现）。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from laotu.ocr_batch import _trace

IMG = r"%USERPROFILE%\AppData\Local\Temp\lb_in.png"

_trace("probe start")
from laotu.ocr import _load_array, get_engine

_trace("imports done")
arr = _load_array(IMG)
_trace(f"array loaded {arr.shape}")
eng = get_engine()
_trace("engine ready")
t = time.time()
res, elapse = eng(arr)
_trace(f"inference done in {round(time.time()-t,2)}s items={len(res or [])}")

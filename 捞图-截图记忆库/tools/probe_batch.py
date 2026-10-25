"""后台环境批处理 OCR 诊断：直接调 OcrBatchClient（会触发子进程）。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

IMG = r"%USERPROFILE%\AppData\Local\Temp\lb_in.png"

from laotu.ocr_client import OcrBatchClient

t = time.time()
client = OcrBatchClient(base_timeout=45, per_image_timeout=5)
text, err = client.ocr_many([IMG])[IMG]
print(f"result in {round(time.time()-t,1)}s err={err!r} text={text[:40]!r}", flush=True)

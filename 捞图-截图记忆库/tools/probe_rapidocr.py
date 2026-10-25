"""统计 RapidOCR 构造器在新进程里的挂死概率（诊断用）。"""

import subprocess
import sys

CODE = (
    "import time; t=time.time(); "
    "from rapidocr_onnxruntime import RapidOCR; e=RapidOCR(); "
    "print(round(time.time()-t,2))"
)

for i in range(6):
    try:
        r = subprocess.run([sys.executable, "-c", CODE],
                           capture_output=True, text=True, timeout=30)
        print(f"try{i+1}: rc={r.returncode} out={r.stdout.strip()!r} "
              f"err_tail={r.stderr.strip()[-80:]!r}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"try{i+1}: HANG (>30s)", flush=True)

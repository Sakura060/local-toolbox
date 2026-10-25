"""cv2 探针：detached 环境下 cv2 计算型操作是否挂起。"""

import subprocess
import sys
import time

CV_DEFAULT = (
    "import numpy as np, cv2, time; "
    "img=np.zeros((2000,2000,3),dtype=np.uint8); "
    "t=time.time(); "
    "img2=cv2.resize(img,(1000,1000)); "
    "b=cv2.GaussianBlur(img2,(31,31),0); "
    "c=cv2.Canny(b,100,200); "
    "print('cv2-ok', round(time.time()-t,2), c.shape)"
)
CV_1THREAD = (
    "import cv2; cv2.setNumThreads(1); cv2.setUseOptimized(False); " + CV_DEFAULT
)

for name, code in (("cv2-default", CV_DEFAULT), ("cv2-1thread", CV_1THREAD)):
    t = time.time()
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=40)
        print(f"{name}: rc={r.returncode} {round(time.time()-t,1)}s "
              f"out={r.stdout.strip()[:60]!r} err={r.stderr.strip()[-100:]!r}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"{name}: HANG (>40s)", flush=True)

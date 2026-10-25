"""ORT 最小推理探针：默认线程 vs 单线程，在 detached 环境下哪个能活。"""

import subprocess
import sys
import time

MODEL = "C:/Python313/Lib/site-packages/rapidocr_onnxruntime/models/ch_PP-OCRv3_det_infer.onnx"

COMMON = (
    "import numpy as np, onnxruntime as ort, time; "
    f"s=ort.InferenceSession('{MODEL}', providers=['CPUExecutionProvider']); "
    "x=np.zeros((1,3,64,64),dtype=np.float32); "
    "t=time.time(); out=s.run(None, {s.get_inputs()[0].name: x}); "
    "print('infer-ok', round(time.time()-t,2), len(out))"
)
CODE_1THREAD = (
    "import onnxruntime as ort; "
    "from onnxruntime import InferenceSession as IS, SessionOptions as SO; "
    "def patched(path, sess_options=None, providers=None, **kw):"
    "    so = sess_options or SO(); so.intra_op_num_threads=1; so.inter_op_num_threads=1;"
    "    return IS(path, sess_options=so, providers=providers, **kw);"
    "ort.InferenceSession = patched; " + COMMON
)
CODE_DEFAULT = COMMON

for name, code in (("1thread", CODE_1THREAD), ("default", CODE_DEFAULT)):
    t = time.time()
    try:
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=45)
        print(f"{name}: rc={r.returncode} {round(time.time()-t,1)}s "
              f"out={r.stdout.strip()[:60]!r} err={r.stderr.strip()[-80:]!r}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"{name}: HANG (>45s)", flush=True)

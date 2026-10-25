"""OcrPool 启动方式变体矩阵：找出哪种 spawn 参数导致 worker 挂死（诊断用）。"""

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
CODE = "import sys; sys.path.insert(0, r'%s'); from laotu.ocr import get_engine; get_engine(); print('ENGINE_OK')" % str(PROJECT_ROOT)


def run_variant(name, cmd, env_extra=None, stdin_mode="pipe"):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    stdin = subprocess.PIPE if stdin_mode == "pipe" else subprocess.DEVNULL
    t = time.time()
    try:
        r = subprocess.run(cmd, stdin=stdin, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=30, env=env)
        dt = round(time.time() - t, 1)
        out = (r.stdout or "").strip()[-60:]
        err = (r.stderr or "").strip()[-120:]
        print(f"{name}: rc={r.returncode} {dt}s out={out!r} err={err!r}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"{name}: HANG (>30s)", flush=True)


run_variant("A_exact_pool(python -m -Xutf8 pipe)",
            [PY, "-X", "utf8", "-m", "laotu.ocr_worker"],
            {"PYTHONPATH": str(PROJECT_ROOT)})
run_variant("B_module_no_xutf8",
            [PY, "-m", "laotu.ocr_worker"],
            {"PYTHONPATH": str(PROJECT_ROOT)})
run_variant("C_direct_engine_c_import",
            [PY, "-c", CODE])
run_variant("D_c_import_plus_xutf8",
            [PY, "-X", "utf8", "-c", CODE])
run_variant("E_module_devnull_stdin",
            [PY, "-X", "utf8", "-m", "laotu.ocr_worker"],
            {"PYTHONPATH": str(PROJECT_ROOT)}, stdin_mode="devnull")

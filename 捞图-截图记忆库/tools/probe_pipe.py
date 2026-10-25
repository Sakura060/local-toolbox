"""stdin 管道最小对照实验。

v1: 单线程 readline 往返（标准姿势）
v2: 复刻 OcrPool 的线程+queue 结构
v3: 单线程，但 bufsize=1（复刻池的缓冲参数）
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV = dict(os.environ)
ENV["PYTHONIOENCODING"] = "utf-8"
ENV["PYTHONPATH"] = str(PROJECT_ROOT) + os.path.sep * 0 + os.pathsep + ENV.get("PYTHONPATH", "")
CMD = [sys.executable, "-X", "utf8", "-m", "laotu.ocr_worker"]
IMG = r"%USERPROFILE%\AppData\Local\Temp\laotu_probe_file.png"


def v1():
    p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
    ready = p.stdout.readline().strip()
    print("v1 ready:", ready[:40], flush=True)
    t = time.time()
    p.stdin.write(json.dumps({"id": 1, "path": IMG}) + "\n")
    p.stdin.flush()
    resp = p.stdout.readline().strip()
    print(f"v1 resp in {round(time.time()-t,1)}s:", resp[:80], flush=True)
    p.kill()


def v3():
    p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                         bufsize=1)
    ready = p.stdout.readline().strip()
    print("v3 ready:", ready[:40], flush=True)
    t = time.time()
    p.stdin.write(json.dumps({"id": 2, "path": IMG}) + "\n")
    p.stdin.flush()
    resp = p.stdout.readline().strip()
    print(f"v3 resp in {round(time.time()-t,1)}s:", resp[:80], flush=True)
    p.kill()


def v2():
    import queue
    import threading

    p = subprocess.Popen(CMD, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                         errors="replace", bufsize=1)
    q = queue.Queue()

    def reader(proc):
        try:
            for line in proc.stdout:
                line = line.strip()
                if line:
                    q.put(json.loads(line))
        except Exception:
            pass
        q.put({"id": None, "err": "__dead__"})

    th = threading.Thread(target=reader, args=(p,), daemon=True)
    th.start()
    first = q.get(timeout=60)
    print("v2 ready:", str(first)[:40], flush=True)
    t = time.time()
    p.stdin.write(json.dumps({"id": 3, "path": IMG}) + "\n")
    p.stdin.flush()
    try:
        while True:
            r = q.get(timeout=60)
            if r.get("id") == 3:
                print(f"v2 resp in {round(time.time()-t,1)}s:", str(r)[:80], flush=True)
                break
            print("v2 skip:", str(r)[:40], flush=True)
    except queue.Empty:
        print("v2 TIMEOUT waiting response", flush=True)
    p.kill()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "v1"):
        v1()
    if which in ("all", "v3"):
        v3()
    if which in ("all", "v2"):
        v2()

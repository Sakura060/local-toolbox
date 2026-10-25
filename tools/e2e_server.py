"""端到端启动验证：真起 cli.py 服务 → 请求页面 → 校验内容 → 关闭进程。

用法：python tools\\e2e_server.py <项目目录> <端口> <页面必须包含的文本> [start|serve] [cli 额外参数...]

端口须与服务实际监听端口一致（默认端口的 cli 不用传额外参数；
若该端口被常驻实例占用，透传 --port <探测端口> 让 cli 改端口）。
"""

import http.client
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    proj, port, needle = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    mode = sys.argv[4] if len(sys.argv) > 4 else "serve"
    extra = sys.argv[5:]

    p = subprocess.Popen(
        [sys.executable, "cli.py", mode, *extra],
        cwd=proj, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    deadline = time.time() + 30
    ok, last = False, ""
    while time.time() < deadline and p.poll() is None:
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            c.request("GET", "/")
            r = c.getresponse()
            body = r.read().decode("utf-8", "replace")
            c.close()
            if r.status == 200 and needle in body:
                ok = True
                break
            last = f"status={r.status}"
        except OSError as exc:
            last = repr(exc)
        time.sleep(0.5)

    print("E2E_OK" if ok else f"E2E_FAIL last={last} server_alive={p.poll() is None}",
          flush=True)
    p.terminate()
    try:
        out, _ = p.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
    tail = "\n".join((out or "").splitlines()[-6:])
    if tail:
        print("server output tail:")
        print(tail)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

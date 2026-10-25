"""便携目录真机验证：自动找 发布包-桌面版 下最新 v* 版本目录，
自带 runtime 起服务 → 版本/新端点断言 → 关闭。"""
import http.client
import json
import re
import subprocess
import sys
import time
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "发布包-桌面版"
cands = sorted(
    (p for p in BUILD.iterdir()
     if p.is_dir() and re.fullmatch(r"v[\d.]+", p.name)),
    key=lambda p: [int(x) for x in p.name[1:].split(".")])
assert cands, "没有 v* 版本目录"
V = cands[-1]
PKG = V / "ToolHub桌面版"
PY = PKG / "AppToolHub" / "runtime" / "python.exe"
TH = PKG / "AppToolHub" / "workspace" / "ToolHub-工具箱门户"
PORT = 8656

print("verify:", V.name, "| runtime:", PY.is_file())
proc = subprocess.Popen(
    [str(PY), "cli.py", "desktop", "--server-only", "--port", str(PORT)],
    cwd=str(TH), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace")


def req(method, path, payload=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    conn.request(method, path, body=body,
                 headers={"Content-Type": "application/json"} if body else {})
    with conn.getresponse() as r:
        return json.loads(r.read().decode("utf-8"))


try:
    deadline = time.time() + 30
    d = None
    while time.time() < deadline:
        try:
            d = req("GET", "/api/tools")
            break
        except OSError:
            time.sleep(0.5)
    assert d, "便携包服务 30 秒没起来"
    ver = d.get("app", {}).get("version")
    print("version =", ver)
    assert ver == V.name[1:], f"版本不符: {ver} != {V.name[1:]}"
    assert len(d.get("tools", [])) >= 10

    sc = req("GET", "/api/scenes")
    assert isinstance(sc.get("scenes"), list), sc
    st = req("GET", "/api/plugins/storeapps")
    assert st.get("ok"), st
    print("scenes/storeapps endpoints ok; storeapps =", len(st.get("items", [])))
    print("PORTABLE_VERIFY_OK", V.name)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

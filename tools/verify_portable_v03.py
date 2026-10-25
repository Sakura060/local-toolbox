"""便携目录真机验证 v0.3.0：自带 runtime 起服务 → 版本/新端点断言 → 关闭。"""
import http.client
import json
import subprocess
import time
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "发布包-桌面版" / "ToolHub桌面版"
PY = PKG / "AppToolHub" / "runtime" / "python.exe"
TH = PKG / "AppToolHub" / "workspace" / "ToolHub-工具箱门户"
PORT = 8656

print("runtime:", PY.is_file())
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
    assert ver == "0.3.0", f"版本不是 0.3.0: {ver}"
    assert len(d.get("tools", [])) >= 10

    sc = req("GET", "/api/scenes")
    assert isinstance(sc.get("scenes"), list), sc
    print("scenes endpoint ok, n =", len(sc["scenes"]))

    st = req("GET", "/api/plugins/storeapps")
    assert st.get("ok"), st
    print("storeapps endpoint ok, n =", len(st.get("items", [])))

    sv = req("POST", "/api/scenes/save",
             {"scene": {"id": "verify-scene", "name": "验证", "items": []}})
    assert sv.get("ok"), sv
    rm = req("POST", "/api/scenes/p/verify-scene/remove", {})
    assert rm.get("ok"), rm
    print("scene save/remove roundtrip ok")

    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    conn.request("GET", "/")
    with conn.getresponse() as r:
        html = r.read().decode("utf-8")
    for needle in ("renderScenes", "storeapps", "shareexport"):
        assert needle in html, f"页面缺 {needle}"
        print("page has", needle)
    print("PORTABLE_V03_VERIFY_OK")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

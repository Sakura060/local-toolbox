"""分块上传真机验证脚本：模拟浏览器行为上传一个 10MB 文件。"""
import http.client
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, ".")

HOST, PORT, PIN = "127.0.0.1", 8895, "ck99"
SRC = Path(r"D:\temp-ff\landrop-big\source.bin")
OUT = Path(r"D:\temp-ff\landrop-rx-big")


def request(method, path, body=None, headers=None, timeout=30):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read(), dict(resp.getheaders())
    finally:
        conn.close()


SRC.parent.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
payload = os.urandom(10 * 1024 * 1024)          # 10MB 随机数据
SRC.write_bytes(payload)
print(f"源文件: {SRC} ({len(payload)} 字节)")

# 1) init
body = json.dumps({"filename": "大文件 测试.bin", "size": len(payload)}).encode()
st, raw, _ = request("POST", f"/upload-init?pin={PIN}", body,
                     {"Content-Type": "application/json",
                      "Content-Length": str(len(body))})
assert st == 200, (st, raw)
info = json.loads(raw)
uid, cs, total = info["id"], info["chunk_size"], info["total_chunks"]
print(f"init ok: chunk={cs}, total_chunks={total}")

# 2) chunks
for n in range(total):
    part = payload[n * cs:(n + 1) * cs]
    st, raw, _ = request("POST", f"/upload-chunk?id={uid}&n={n}&pin={PIN}", part,
                         {"Content-Length": str(len(part))})
    assert st == 200, (st, raw, n)
print("chunks ok:", total)

# 3) finish
body = json.dumps({"id": uid}).encode()
st, raw, _ = request("POST", f"/upload-finish?pin={PIN}", body,
                     {"Content-Type": "application/json",
                      "Content-Length": str(len(body))})
assert st == 200, (st, raw)
saved = json.loads(raw)
print("finish ok:", saved)

# 4) 校验字节一致（saved["saved"] 是服务端清洗后的文件名）
final = OUT / saved["saved"]
got = final.read_bytes()
assert got == payload, "内容不一致！"
print(f"字节校验通过: {len(got)} 字节完全一致 → {final}")

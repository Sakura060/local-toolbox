"""Serve hukou web UI with a seeded temp DB (for UI verification only)."""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hukou.db import Database
from hukou.server import make_server

tmp = tempfile.mkdtemp(prefix="hukou_ui_")
db = Database(Path(tmp) / "ui.db")
db.store_files("C", [
    {"frn": 100, "pfrn": 5, "name": "Users", "usn": 0, "ts": 0, "reason": 0},
    {"frn": 101, "pfrn": 100, "name": "t", "usn": 0, "ts": 0, "reason": 0},
    {"frn": 102, "pfrn": 101, "name": "setup.exe", "usn": 0, "ts": 0, "reason": 0},
])
db.upsert_origin(r"C:\Users\t\setup.exe", "网络下载",
                 "https://example.com/setup（Chrome 下载记录）", "ads")
db.add_events("C", [
    {"usn": 10, "ts": time.time() - 86400, "frn": 102, "pfrn": 101,
     "name": "setup-old.exe", "reason": "4096"},
    {"usn": 30, "ts": time.time() - 43200, "frn": 102, "pfrn": 101,
     "name": "setup.exe", "reason": "8192"},
])
server = make_server(8648, db)
threading.Thread(target=server.serve_forever, daemon=True).start()
print("UI server ready at http://127.0.0.1:8648/ (temp db)", flush=True)
time.sleep(600)

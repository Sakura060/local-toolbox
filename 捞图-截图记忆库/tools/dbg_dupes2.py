"""Reproduce server dupes-route scenario exactly."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from laotu.db import Database
from laotu.hashes import duplicate_groups, hamming

tmp = tempfile.TemporaryDirectory()
db = Database(Path(tmp.name) / "t.db")
i1 = db.upsert_image("C:\\x\\1.png", "1.png", "C:\\x", 1,
                     int(1000 * 1e9), 2, 2, "", 1, None, "", 7)
i2 = db.upsert_image("C:\\x\\2.png", "2.png", "C:\\x", 1,
                     int(2000 * 1e9), 2, 2, "", 1, None, "", 9)
i3 = db.upsert_image("C:\\x\\3.png", "3.png", "C:\\x", 1,
                     int(3000 * 1e9), 2, 2, "", 1, None, "", 1 << 50)
hs = db.all_hashes()
print("all_hashes:", hs)
print("hamming 7,9 =", hamming(7, 9))
print("hamming 7,1<<50 =", hamming(7, 1 << 50))
print("hamming 9,1<<50 =", hamming(9, 1 << 50))
g = duplicate_groups(hs, 2)
print("groups:", g)
db.close()

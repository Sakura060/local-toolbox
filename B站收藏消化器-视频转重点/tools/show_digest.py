"""Show digest of a job (chapters etc.) from the DB."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bilidigest.config import DB_PATH
from bilidigest.db import Database

jid = int(sys.argv[1]) if len(sys.argv) > 1 else 2
db = Database(DB_PATH)
job = db.get_job(jid)
dg = job["digest"]
print("job", jid, "sentences:", dg["sentence_count"], "duration:", dg["duration"])
for c in dg["chapters"]:
    print(f"  ch{c['index']} [{c['t0']:.0f}s-{c['t1']:.0f}s] {c['title']} ({c['count']}句)")
db.close()

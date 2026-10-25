"""起一个带种子数据的临时 ClipVault 实例（浏览器验收用；不动真实剪贴板、不开 Poller）。

用法：python tools\seed_preview.py <port>
跑完由验收方杀进程（或 Ctrl+C）。
"""

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from io import BytesIO

from PIL import Image

from clipvault.server import make_server
from clipvault.store import Store, content_hash


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8644
    data = ROOT / "data" / "preview_tmp"
    store = Store(data / "preview.db", data / "images")
    store.clear(keep_pinned=False)

    store.capture("text", "共建一带一路倡议提出十周年",
                  chash=content_hash("共建一带一路倡议提出十周年"))
    store.capture("text", "meeting notes about deploy pipeline",
                  chash=content_hash("deploy-notes"))
    store.capture("files", r"C:\Users\public\报表.xlsx" + "\n" + r"D:\说明.txt",
                  chash=content_hash("two-files"))
    buf = BytesIO()
    Image.new("RGB", (320, 200), (13, 148, 138)).save(buf, "PNG")
    png = buf.getvalue()
    (data / "images" / "seed.png").write_bytes(png)
    store.capture("image", "", image_path="seed.png",
                  size=len(png), chash=content_hash(png))

    httpd = make_server(port, store, data / "images")
    print(f"preview serving at http://127.0.0.1:{port}", flush=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
        store.close()


if __name__ == "__main__":
    main()

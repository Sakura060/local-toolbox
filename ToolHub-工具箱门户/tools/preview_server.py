"""ToolHub 预览实例：只挂 clipvault（指向 8644 种子库），供浏览器验收联邦深链。

用法：python tools\preview_server.py 8645
"""

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # toolhub 包在项目目录下

from toolhub.registry import Tool
from toolhub.server import make_server


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8645
    tools = (Tool(id="clipvault", name="ClipVault", desc="剪贴板时间线",
                  port=8644, search=True),)
    httpd = make_server(port, tools=tools)
    print(f"toolhub preview at http://127.0.0.1:{port}", flush=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()

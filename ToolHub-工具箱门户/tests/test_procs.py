"""procs：netstat 端口反查 + taskkill 进程树杀（真件验证，杀的是自家测试子进程）。"""

import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from toolhub import procs


class TestFindPortPids(unittest.TestCase):
    def test_finds_own_listening_pid(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: None)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            time.sleep(0.2)  # 等 socket 进入 LISTENING
            pids = procs.find_port_pids(port)
            self.assertIn(os.getpid(), pids)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_dead_port_empty(self):
        s = __import__("socket").socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        time.sleep(0.1)
        self.assertEqual(procs.find_port_pids(port), [])


class TestKillPids(unittest.TestCase):
    def test_kills_spawned_child(self):
        # 起一个自家的 sleep 子进程，杀它（不碰任何真实工具）
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.5)  # 等它起来
            killed = procs.kill_pids([p.pid])
            self.assertEqual(killed, [p.pid])
        finally:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()

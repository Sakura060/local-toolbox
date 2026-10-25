"""launch：真实拉起验证 —— spawn 一个临时 bat，靠哨兵文件确认真的执行了。

这是唯一会真开控制台窗口的测试；bat 只写一个文件然后退出。
"""

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from toolhub.server import spawn_bat


class TestRealSpawn(unittest.TestCase):
    def test_spawn_bat_writes_sentinel(self):
        with tempfile.TemporaryDirectory(prefix="toolhub_spawn_") as tmp:
            tmp_path = Path(tmp)
            sentinel = tmp_path / "sentinel.txt"
            bat = tmp_path / "go.bat"
            # ASCII 正文 + CRLF，路径加引号（cmd 规范）
            bat.write_bytes(
                b"@echo off\r\n"
                b"type nul > \"" + str(sentinel).encode("ascii") + b"\"\r\n"
                b"exit\r\n"
            )
            proc = spawn_bat(bat)
            self.assertGreater(proc.pid, 0)
            deadline = time.time() + 10
            while time.time() < deadline and not sentinel.exists():
                time.sleep(0.2)
            self.assertTrue(sentinel.exists(), "bat 在 10s 内未产生哨兵文件，spawn 路径失效")
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.fail("cmd 进程未按预期退出")

    def test_spawn_missing_bat_raises(self):
        with tempfile.TemporaryDirectory(prefix="toolhub_spawn_") as tmp:
            missing = Path(tmp) / "nope.bat"
            with self.assertRaises(FileNotFoundError):
                spawn_bat(missing)


if __name__ == "__main__":
    unittest.main()

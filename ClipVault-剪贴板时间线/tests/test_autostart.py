"""autostart 接线：cli 子命令用的 localcore 通用件契约（注入临时目录，不碰真实 Startup）。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ClipVaultAutostartTest(unittest.TestCase):
    def test_localcore_reachable_and_roundtrip(self):
        sys.path.insert(0, str(ROOT.parent))  # 与 cli.py 相同的 bootstrap
        from localcore import autostart

        with tempfile.TemporaryDirectory() as tmp:
            vbs = autostart.install(ROOT, "clipvault_serve.vbs", "serve",
                                    target_dir=Path(tmp))
            text = vbs.read_bytes().decode("mbcs")
            self.assertIn("cli.py", text)
            self.assertIn(" serve", text)  # 隐藏窗口起 serve（采集+Web）
            self.assertTrue(autostart.is_installed("clipvault_serve.vbs", Path(tmp)))
            self.assertTrue(autostart.uninstall("clipvault_serve.vbs", Path(tmp)))
            self.assertFalse(autostart.is_installed("clipvault_serve.vbs", Path(tmp)))

    def test_cli_uses_same_vbs_name_and_args(self):
        # cli.py 子命令与测试里的字面量必须同步（防一处改名一处忘）
        src = (ROOT / "cli.py").read_text(encoding="utf-8")
        self.assertIn('autostart.install(ROOT, "clipvault_serve.vbs", "serve")', src)
        self.assertIn('autostart.uninstall("clipvault_serve.vbs")', src)


if __name__ == "__main__":
    unittest.main()

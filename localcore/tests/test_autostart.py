"""localcore.autostart 自测：VBS 内容、安装/卸载（注入临时目录，不碰真实 Startup）。"""

import sys
import tempfile
import unittest
from pathlib import Path

# localcore/tests/test_autostart.py -> localcore -> 工作区根
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from localcore import autostart  # noqa: E402


class TestVbsContent(unittest.TestCase):
    def test_content_quotes_paths(self):
        pw = r"C:\Python313\pythonw.exe"
        cli = Path(__file__).resolve().parent.parent.parent / "某项目" / "cli.py"
        s = autostart.vbs_content(pw, cli, "serve")
        self.assertIn(f'"""{pw}"" ""{cli}', s)
        self.assertIn(" serve", s)
        self.assertIn("WScript.Shell", s)

    def test_content_without_args(self):
        pw = r"C:\Python313\pythonw.exe"
        s = autostart.vbs_content(pw, Path(r"D:\x\cli.py"))
        self.assertTrue(s.endswith('""", 0, False'))
        self.assertNotIn(" serve", s)

    def test_pythonw_preferred(self):
        pw = autostart.pythonw_path()
        self.assertTrue(pw.endswith(("pythonw.exe", "python.exe")))


class TestInstallUninstall(unittest.TestCase):
    def test_roundtrip_in_tmp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Startup"
            root = Path(tmp) / "proj"
            root.mkdir()
            (root / "cli.py").write_text("print('hi')\n", encoding="utf-8")
            self.assertFalse(autostart.is_installed("x_tray.vbs", target))
            vbs = autostart.install(root, "x_tray.vbs", "serve", target_dir=target)
            self.assertTrue(vbs.is_file())
            self.assertEqual(vbs.name, "x_tray.vbs")
            # mbcs 可解码（GBK 中文 Windows 上内容可读）
            text = vbs.read_bytes().decode("mbcs")
            self.assertIn("cli.py", text)
            self.assertIn("serve", text)
            self.assertTrue(autostart.is_installed("x_tray.vbs", target))
            self.assertTrue(autostart.uninstall("x_tray.vbs", target))
            self.assertFalse(autostart.is_installed("x_tray.vbs", target))
            self.assertFalse(autostart.uninstall("x_tray.vbs", target))  # 幂等

    def test_install_requires_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "noproj"
            fake_root.mkdir()
            with self.assertRaises(FileNotFoundError):
                autostart.install(fake_root, "x.vbs", target_dir=Path(tmp) / "Startup")


if __name__ == "__main__":
    unittest.main()

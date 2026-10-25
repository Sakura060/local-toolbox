"""autostart：VBS 内容、安装/卸载（注入临时目录，不碰真实 Startup）。"""

import tempfile
import unittest
from pathlib import Path

from toolhub import autostart

ROOT = Path(__file__).resolve().parent.parent


class TestVbsContent(unittest.TestCase):
    def test_content_quotes_paths(self):
        pw = r"C:\Python313\pythonw.exe"
        cli = ROOT / "cli.py"
        s = autostart.vbs_content(pw, cli, "tray")
        self.assertIn(f'"""{pw}"" ""{cli}', s)
        self.assertIn('tray', s)
        self.assertIn("WScript.Shell", s)

    def test_pythonw_preferred(self):
        pw = autostart.pythonw_path()
        self.assertTrue(pw.endswith(("pythonw.exe", "python.exe")))


class TestInstallUninstall(unittest.TestCase):
    def test_roundtrip_in_tmp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "Startup"
            self.assertFalse(autostart.is_installed(target))
            vbs = autostart.install(ROOT, target_dir=target)
            self.assertTrue(vbs.is_file())
            self.assertEqual(vbs.name, autostart.VBS_NAME)
            # mbcs 可解码（GBK 中文 Windows 上内容可读）
            raw = vbs.read_bytes()
            text = raw.decode("mbcs")
            self.assertIn("cli.py", text)
            self.assertIn("tray", text)
            self.assertTrue(autostart.is_installed(target))
            self.assertTrue(autostart.uninstall(target))
            self.assertFalse(autostart.is_installed(target))
            self.assertFalse(autostart.uninstall(target))  # 幂等

    def test_install_requires_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_root = Path(tmp) / "notoolhub"
            fake_root.mkdir()
            with self.assertRaises(FileNotFoundError):
                autostart.install(fake_root, target_dir=Path(tmp) / "Startup")


if __name__ == "__main__":
    unittest.main()

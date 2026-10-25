"""OCR 集成测试（仅环境无关部分跑 unittest）。

真引擎的往返测试移到 tools/ocr_smoke.py（shell 直启模式，实证稳定）。
原因：本开发沙箱内，unittest 进程里引擎初始化与先前的 PIL/freetype
使用顺序偶发死锁（详见 BLUEPRINT 风险表与 tools/ocr_smoke.py 注释）。
"""

import tempfile
import unittest
from pathlib import Path

from laotu import ocr as ocr_mod


class TestOcrModule(unittest.TestCase):
    def test_broken_file_returns_err_no_engine(self):
        # 损坏文件在 _load_array 阶段就失败，不触发引擎初始化 → 环境无关
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.png"
            p.write_bytes(b"not an image")
            text, err = ocr_mod.ocr_image(p)
            self.assertEqual(text, "")
            self.assertTrue(err)

    def test_module_sets_thread_env(self):
        # 纪律 2：OMP=1 必须在模块导入时就位（detached 环境防挂）
        import os

        self.assertEqual(os.environ.get("OMP_NUM_THREADS"), "1")

    def test_ocr_image_signature(self):
        self.assertTrue(callable(ocr_mod.ocr_image))
        self.assertTrue(callable(ocr_mod.get_engine))


if __name__ == "__main__":
    unittest.main()

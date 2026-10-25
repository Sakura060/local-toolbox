"""回贴字节构造与 ctypes 层：png_to_dib / build_hdrop / write_* 全假件。

隐私红线（D24）：测试绝不碰真实剪贴板——user32/kernel32 换成记录调用的假件，
GlobalLock 返回真实缓冲区地址以便 memmove 落进可检查的内存。
"""

import ctypes
import struct
import unittest
from io import BytesIO
from unittest.mock import patch

from clipvault import clip


def make_rgba_png():
    from PIL import Image

    im = Image.new("RGBA", (3, 2))
    px = im.load()
    px[0, 0] = (255, 0, 0, 255)
    px[1, 0] = (0, 255, 0, 128)
    px[2, 0] = (0, 0, 255, 0)
    px[0, 1] = (10, 20, 30, 255)
    px[1, 1] = (40, 50, 60, 200)
    px[2, 1] = (70, 80, 90, 255)
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


class FakeUser32:
    def __init__(self):
        self.open_ok = True
        self.set_ok = True
        self.opened = self.emptied = self.closed = 0
        self.sets = []

    def OpenClipboard(self, _):
        self.opened += 1
        return 1 if self.open_ok else 0

    def EmptyClipboard(self):
        self.emptied += 1

    def SetClipboardData(self, fmt, handle):
        if self.set_ok:
            self.sets.append((fmt, handle))
            return 100
        return 0

    def CloseClipboard(self):
        self.closed += 1


class FakeKernel32:
    def __init__(self):
        self.buf = None
        self.freed = []

    def GlobalAlloc(self, flags, size):
        self.buf = ctypes.create_string_buffer(max(1, size))
        return 1234

    def GlobalLock(self, handle):
        return ctypes.addressof(self.buf)

    def GlobalUnlock(self, handle):
        return 1

    def GlobalFree(self, handle):
        self.freed.append(handle)
        return 1


class PasteFakeTest(unittest.TestCase):
    """write_* 的完整流程（假 OS 层）：验证格式号与落进全局内存的字节。"""

    def setUp(self):
        self.u = FakeUser32()
        self.k = FakeKernel32()
        p1 = patch.object(clip, "user32", self.u)
        p2 = patch.object(clip, "kernel32", self.k)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def test_write_text_format_and_bytes(self):
        self.assertTrue(clip.write_text("你好 hi"))
        self.assertEqual(self.u.sets, [(clip.CF_UNICODETEXT, 1234)])
        expect = "你好 hi".encode("utf-16-le") + b"\x00\x00"
        self.assertEqual(self.k.buf.raw[:len(expect)], expect)
        self.assertEqual(self.u.opened, 1)
        self.assertEqual(self.u.closed, 1)

    def test_write_image_dib_bytes(self):
        png = make_rgba_png()
        self.assertTrue(clip.write_image(png))
        self.assertEqual(self.u.sets, [(clip.CF_DIB, 1234)])
        dib = clip.png_to_dib(png)
        self.assertEqual(self.k.buf.raw[:len(dib)], dib)

    def test_write_files_hdrop_bytes(self):
        self.assertTrue(clip.write_files([r"C:\a\报告.docx", r"D:\b\c.txt"]))
        self.assertEqual(self.u.sets, [(clip.CF_HDROP, 1234)])
        drop = clip.build_hdrop([r"C:\a\报告.docx", r"D:\b\c.txt"])
        self.assertEqual(self.k.buf.raw[:len(drop)], drop)

    def test_write_files_empty_list_fails_without_clipboard(self):
        self.assertFalse(clip.write_files([]))
        self.assertFalse(clip.write_files(["", "  "]))
        self.assertEqual(self.u.opened, 0)  # 根本不该开剪贴板

    def test_open_clipboard_failure_returns_false(self):
        self.u.open_ok = False
        self.assertFalse(clip.write_text("x"))
        self.assertEqual(self.u.emptied, 0)

    def test_set_clipboard_data_failure_frees_memory(self):
        self.u.set_ok = False
        self.assertFalse(clip.write_text("x"))
        self.assertEqual(self.k.freed, [1234])

    def test_global_alloc_failure_returns_false(self):
        def boom(flags, size):
            return 0
        with patch.object(self.k, "GlobalAlloc", boom):
            self.assertFalse(clip.write_text("x"))


class PngToDibTest(unittest.TestCase):
    def test_header_fields(self):
        dib = clip.png_to_dib(make_rgba_png())  # 3x2
        (bi_size, w, h, planes, bpp, compression,
         size_image, _, _, _, _) = struct.unpack("<IiiHHIIiiII", dib[:40])
        self.assertEqual(bi_size, 40)
        self.assertEqual((w, h), (3, 2))
        self.assertEqual(planes, 1)
        self.assertEqual(bpp, 32)
        self.assertEqual(compression, 0)  # BI_RGB
        self.assertEqual(size_image, 3 * 2 * 4)
        self.assertEqual(len(dib), 40 + 3 * 2 * 4)

    def test_bottom_up_bgra_pixels(self):
        png = make_rgba_png()
        dib = clip.png_to_dib(png)
        pixels = dib[40:]
        # DIB 行自下而上：第一行是原图 y=1（底行）
        # 原图 (0,1)=(10,20,30,255) → BGRA (30,20,10,255)
        self.assertEqual(pixels[0:4], bytes([30, 20, 10, 255]))
        # 原图 (2,1)=(70,80,90,255)
        self.assertEqual(pixels[8:12], bytes([90, 80, 70, 255]))
        # 顶行 y=0 从第 12 字节开始：(0,0)=(255,0,0,255) → BGRA (0,0,255,255)
        self.assertEqual(pixels[12:16], bytes([0, 0, 255, 255]))
        # (1,0)=(0,255,0,128) 半透明绿
        self.assertEqual(pixels[16:20], bytes([0, 255, 0, 128]))
        # (2,0)=(0,0,255,0) 全透明
        self.assertEqual(pixels[20:24], bytes([255, 0, 0, 0]))

    def test_single_pixel_and_wide_image(self):
        from PIL import Image

        for size in [(1, 1), (16, 1), (1, 16), (17, 9)]:
            buf = BytesIO()
            Image.new("RGB", size, (1, 2, 3)).save(buf, "PNG")
            dib = clip.png_to_dib(buf.getvalue())
            w, h = size
            self.assertEqual(len(dib), 40 + w * h * 4)
            # 每像素 BGRA=(3,2,1,255)：抽查最后一个字节（顶行最右像素 alpha）
            self.assertEqual(dib[-1], 255)


class BuildHdropTest(unittest.TestCase):
    def test_header_layout(self):
        drop = clip.build_hdrop([r"C:\x.txt"])
        p_files, pt_x, pt_y, f_nc, f_wide = struct.unpack("<IIiiI", drop[:20])
        self.assertEqual(p_files, 20)  # 文件串紧跟 20 字节头
        self.assertEqual((pt_x, pt_y, f_nc), (0, 0, 0))
        self.assertEqual(f_wide, 1)  # Unicode 路径

    def test_path_list_double_null_terminated(self):
        paths = [r"C:\a.txt", r"D:\b\报告.docx"]
        drop = clip.build_hdrop(paths)
        body = drop[20:].decode("utf-16-le")
        expect = "C:\\a.txt" + "\x00" + "D:\\b\\报告.docx" + "\x00\x00"
        self.assertEqual(body, expect)


if __name__ == "__main__":
    unittest.main()

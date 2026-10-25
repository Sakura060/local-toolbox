"""NTFS USN Journal 只读采集与解析。

- parse_records / filetime_to_unix / reason_names 是纯函数（合成 buffer 测试）。
- 系统调用层只使用只读控制码：
    FSCTL_GET_USN_JOURNAL / FSCTL_ENUM_USN_DATA / FSCTL_READ_USN_JOURNAL
  绝不调用 FSCTL_CREATE_USN_JOURNAL / FSCTL_DELETE_USN_JOURNAL，不写卷。
- 枚举整卷 MFT 通常需要管理员权限；无权限时抛 UsnAccessError 并给出指引。
"""

from __future__ import annotations

import ctypes
import string
import struct
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.DeviceIoControl.restype = wintypes.BOOL
kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

GENERIC_READ = 0x80000000
FILE_SHARE_ALL = 0x7
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

FSCTL_GET_USN_JOURNAL = 0x000900F4
FSCTL_ENUM_USN_DATA = 0x000900B3
FSCTL_READ_USN_JOURNAL = 0x000900BB

ERROR_HANDLE_EOF = 38
ERROR_ACCESS_DENIED = 5

USN_RECORD_V2_SIZE = 60  # 记录头固定部分（不含文件名）

# USN_REASON 位标志 → 中文名（用于展示）
REASONS: dict[int, str] = {
    0x00000001: "数据覆盖",
    0x00000002: "数据扩展",
    0x00000004: "数据截断",
    0x00000100: "新建",
    0x00000200: "删除",
    0x00000400: "EA变更",
    0x00000800: "安全变更",
    0x00001000: "改名(旧名)",
    0x00002000: "改名(新名)",
    0x00004000: "索引变更",
    0x00008000: "基本信息变更",
    0x00010000: "硬链接变更",
    0x00020000: "压缩变更",
    0x00040000: "加密变更",
    0x00080000: "对象ID变更",
    0x00100000: "重解析点变更",
    0x00200000: "流变更",
    0x80000000: "关闭",
}

INTERESTING_REASONS = (
    0x00000100 | 0x00000200 | 0x00001000 | 0x00002000  # 新建/删除/改名旧/改名新
)


class UsnAccessError(OSError):
    """卷句柄打不开（通常是需要管理员权限）。"""


def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def filetime_to_unix(ft: int) -> float:
    """Windows FILETIME(1601 起的 100ns) → Unix 秒。"""
    if ft <= 0:
        return 0.0
    return (ft - 116444736000000000) / 10_000_000


def reason_names(mask: int) -> list[str]:
    return [name for bit, name in REASONS.items() if mask & bit]


def parse_records(buf: bytes, skip_header: bool = True) -> list[dict]:
    """解析 ENUM/READ 控制码的输出缓冲。

    两种输出同构：前 8 字节是游标（NextUsn），其后是连续的 USN_RECORD_V2。
    纯函数：坏数据只导致提前停止，绝不抛异常。
    """
    off = 8 if skip_header else 0
    n = len(buf)
    out: list[dict] = []
    while off + 8 <= n:
        try:
            rec_len = struct.unpack_from("<I", buf, off)[0]
        except struct.error:
            break
        if rec_len < USN_RECORD_V2_SIZE or off + rec_len > n:
            break
        try:
            major, _minor = struct.unpack_from("<HH", buf, off + 4)
            if major != 2:
                off += rec_len
                continue
            frn, pfrn, usn = struct.unpack_from("<QQQ", buf, off + 8)
            ts, reason = struct.unpack_from("<QI", buf, off + 32)
            fn_len, fn_off = struct.unpack_from("<HH", buf, off + 56)
            end = off + fn_off + fn_len
            if end > n:
                off += rec_len
                continue
            name = buf[off + fn_off:end].decode("utf-16-le", errors="replace")
            out.append({
                "usn": usn,
                "frn": frn,
                "pfrn": pfrn,
                "ts": filetime_to_unix(ts),
                "reason": reason,
                "name": name,
            })
        except struct.error:
            break
        off += rec_len
    return out


def open_volume(volume_letter: str):
    """打开卷句柄（如 'C'），只读共享。失败抛 UsnAccessError。"""
    path = f"\\\\.\\{volume_letter}:"
    h = kernel32.CreateFileW(
        path, GENERIC_READ, FILE_SHARE_ALL, None, OPEN_EXISTING, 0, None
    )
    if not h or h == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        if err == ERROR_ACCESS_DENIED:
            raise UsnAccessError(
                f"打开卷 {volume_letter}: 需要管理员权限（右键“以管理员身份运行”）"
            )
        raise UsnAccessError(f"打开卷 {volume_letter}: 失败，WinError {err}")
    return h


def get_journal_info(h) -> tuple[int, int]:
    """返回 (UsnJournalID, NextUsn)。非 NTFS 卷会失败。"""
    buf = ctypes.create_string_buffer(48)
    ret = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        h, FSCTL_GET_USN_JOURNAL, None, 0, buf, 48, ctypes.byref(ret), None
    )
    if not ok:
        raise UsnAccessError(f"FSCTL_GET_USN_JOURNAL 失败，WinError {ctypes.get_last_error()}")
    journal_id, next_usn = struct.unpack_from("<QQ", buf, 0)
    return journal_id, next_usn


def get_journal_info_full(h) -> dict:
    """返回日志完整信息（含可回溯范围）。USN_JOURNAL_DATA: id, first, next, lowest, max, size, delta。"""
    buf = ctypes.create_string_buffer(48)
    ret = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        h, FSCTL_GET_USN_JOURNAL, None, 0, buf, 48, ctypes.byref(ret), None
    )
    if not ok:
        raise UsnAccessError(f"FSCTL_GET_USN_JOURNAL 失败，WinError {ctypes.get_last_error()}")
    journal_id, first_usn, next_usn, lowest, max_usn, max_size, delta = \
        struct.unpack_from("<QQQQQQQ", buf, 0)
    return {
        "journal_id": journal_id,
        "first_usn": first_usn,
        "next_usn": next_usn,
        "lowest_valid_usn": lowest,
        "max_usn": max_usn,
        "journal_max_size": max_size,
        "allocation_delta": delta,
    }


def _device_io(h, ctrl: int, in_buf, in_size, out_buf):
    ret = wintypes.DWORD()
    ok = kernel32.DeviceIoControl(
        h, ctrl, in_buf, in_size, out_buf, len(out_buf), ctypes.byref(ret), None
    )
    if ok:
        return ret.value, None
    return 0, ctypes.get_last_error()


def enum_volume_records(volume_letter: str, chunk: int = 1 << 20):
    """枚举整卷 USN 记录（生成器，逐条产出 dict）。"""
    h = open_volume(volume_letter)
    try:
        get_journal_info(h)  # 确认是 NTFS 且有日志
        med = struct.pack("<QQQ", 0, 0, 0xFFFFFFFFFFFFFFFF)
        out_buf = ctypes.create_string_buffer(chunk)
        while True:
            ret, err = _device_io(h, FSCTL_ENUM_USN_DATA, med, len(med), out_buf)
            if err == ERROR_HANDLE_EOF:
                break
            if err:
                raise UsnAccessError(f"FSCTL_ENUM_USN_DATA 失败，WinError {err}")
            data = out_buf.raw[:ret]
            # 输出前 8 字节是续读游标（标准用法：作为 StartFileReferenceNumber 继续枚举）
            med = data[:8] + struct.pack("<QQ", 0, 0xFFFFFFFFFFFFFFFF)
            for rec in parse_records(data, skip_header=True):
                yield rec
    finally:
        kernel32.CloseHandle(h)


class _READ_USN_JOURNAL_DATA(ctypes.Structure):
    _fields_ = [
        ("StartUsn", ctypes.c_uint64),
        ("ReasonMask", ctypes.c_uint32),
        ("ReturnOnlyOnClose", ctypes.c_uint32),
        ("Timeout", ctypes.c_uint64),
        ("BytesToWaitFor", ctypes.c_uint64),
        ("UsnJournalID", ctypes.c_uint64),
    ]


def read_new_records(volume_letter: str, start_usn: int, chunk: int = 1 << 20):
    """从 start_usn 增量读取新记录，返回 (新记录列表, 新的 NextUsn)。"""
    h = open_volume(volume_letter)
    try:
        journal_id, next_usn = get_journal_info(h)
        if start_usn == 0:
            start_usn = next_usn  # 首次：从当前头开始（不回溯历史）
        if start_usn < next_usn - (1 << 30):
            start_usn = next_usn  # 日志被复用过，跳到头部，避免读出海量旧数据
        ruj = _READ_USN_JOURNAL_DATA(start_usn, 0xFFFFFFFF, 0, 0, 0, journal_id)
        records: list[dict] = []
        cursor = start_usn
        out_buf = ctypes.create_string_buffer(chunk)
        while True:
            ret, err = _device_io(
                h, FSCTL_READ_USN_JOURNAL,
                ctypes.byref(ruj), ctypes.sizeof(ruj), out_buf,
            )
            if err:
                raise UsnAccessError(f"FSCTL_READ_USN_JOURNAL 失败，WinError {err}")
            if ret <= 8:
                break
            data = out_buf.raw[:ret]
            cursor = struct.unpack_from("<Q", data, 0)[0]
            batch = parse_records(data, skip_header=True)
            records.extend(batch)
            if ret < chunk // 2:
                break
        return records, cursor
    finally:
        kernel32.CloseHandle(h)


def read_journal_range(volume_letter: str, start_usn: int,
                       chunk: int = 1 << 20, max_records: int | None = None):
    """从指定 USN 顺序读到日志头（历史回溯专用，不做任何钳位）。

    与 read_new_records 不同：不把过旧的 start_usn 跳到日志头——
    深度回溯本来就是要读旧数据（start_usn 应取 lowest_valid_usn）。
    返回 (记录列表, 结束游标)。
    """
    h = open_volume(volume_letter)
    try:
        info = get_journal_info_full(h)
        journal_id = info["journal_id"]
        cursor = start_usn
        records: list[dict] = []
        out_buf = ctypes.create_string_buffer(chunk)
        while True:
            ruj = _READ_USN_JOURNAL_DATA(cursor, 0xFFFFFFFF, 0, 0, 0, journal_id)
            ret, err = _device_io(
                h, FSCTL_READ_USN_JOURNAL,
                ctypes.byref(ruj), ctypes.sizeof(ruj), out_buf,
            )
            if err:
                raise UsnAccessError(f"FSCTL_READ_USN_JOURNAL 失败，WinError {err}")
            if ret <= 8:
                break
            data = out_buf.raw[:ret]
            cursor = struct.unpack_from("<Q", data, 0)[0]
            records.extend(parse_records(data, skip_header=True))
            if max_records is not None and len(records) >= max_records:
                break
            if ret < chunk // 2:
                break
        return records, cursor
    finally:
        kernel32.CloseHandle(h)


def fixed_ntfs_volumes() -> list[str]:
    """探测本机固定磁盘中的 NTFS 卷（能读到 journal 的才算）。"""
    out: list[str] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if not ctypes.windll.kernel32.GetDriveTypeW(root) == 3:  # DRIVE_FIXED
            continue
        try:
            h = open_volume(letter)
        except UsnAccessError:
            continue
        try:
            get_journal_info(h)
            out.append(letter)
        except UsnAccessError:
            continue
        finally:
            kernel32.CloseHandle(h)
    return out

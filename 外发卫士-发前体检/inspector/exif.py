"""图片 EXIF 体检与剥离（Pillow 已有依赖）。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .ooxml import Report

_GPS_IFD = 0x8825
_EXIF_IFD = 0x8769


def inspect_image(path: str | Path, rep: Report) -> Report:
    rep.ftype = Path(path).suffix.lower().lstrip(".")
    try:
        img = Image.open(path)
    except Exception as exc:
        rep.add("low", "unreadable", "无法按图片解析", str(exc))
        return rep
    try:
        exif = img.getexif()
        if not exif:
            return rep

        # GPS
        gps = exif.get_ifd(_GPS_IFD) if hasattr(exif, "get_ifd") else {}
        if gps:
            lat = _gps_coord(gps.get(2), gps.get(1))
            lon = _gps_coord(gps.get(4), gps.get(3))
            if lat and lon:
                rep.add("high", "exif_gps", "EXIF 含 GPS 定位",
                        f"约 {lat}, {lon}（精确到秒级）")
            else:
                rep.add("high", "exif_gps", "EXIF 含 GPS 数据块", "含定位信息")

        # 设备
        make = exif.get(271) or ""
        model = exif.get(272) or ""
        device = " ".join(x for x in (str(make), str(model)) if x and x != "None")
        if device:
            rep.add("med", "exif_device", "EXIF 含拍摄设备", device)

        # 拍摄时间（EXIF 子 IFD）
        try:
            sub = exif.get_ifd(_EXIF_IFD) if hasattr(exif, "get_ifd") else {}
            dt = sub.get(36867) or sub.get(36868) or exif.get(306) or ""
        except Exception:
            dt = ""
        if dt:
            rep.add("low", "exif_time", "EXIF 含拍摄时间", str(dt))

        # 软件与作者注释
        software = exif.get(305) or ""
        artist = exif.get(315) or ""
        who = "、".join(x for x in (str(artist), str(software)) if x and x != "None")
        if who:
            rep.add("med", "exif_author", "EXIF 含作者/软件", who)
    finally:
        img.close()
    return rep


def _gps_coord(values, ref) -> str | None:
    if not values or ref is None:
        return None
    try:
        d, m, s = (float(v) for v in values)
        deg = d + m / 60 + s / 3600
        if str(ref).upper() in ("S", "W"):
            deg = -deg
        return f"{deg:.5f}°{'N' if str(ref).upper()=='N' else 'S' if str(ref).upper()=='S' else 'E' if str(ref).upper()=='E' else 'W'}"
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def strip_exif(src: str | Path, dst: str | Path) -> int:
    """另存为无 EXIF 副本（重编码，保留画质）。返回原 EXIF 字段数。"""
    img = Image.open(src)
    try:
        n = len(img.getexif())
        img = img.convert("RGB") if img.mode not in ("RGB", "L") else img.copy()
        dst_p = Path(dst)
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        if dst_p.suffix.lower() in (".jpg", ".jpeg"):
            img.save(dst_p, "JPEG", quality=95)
        else:
            img.save(dst_p)
    finally:
        img.close()
    return n

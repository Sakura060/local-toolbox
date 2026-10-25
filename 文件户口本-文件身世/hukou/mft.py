"""全量 MFT 快照与谱系重建（v0.2 deep 模式）。

数据来源：
- 快照：usn.enum_volume_records（FSCTL_ENUM_USN_DATA，只读枚举整卷 MFT，
  每个当前存在的文件一条记录：frn / pfrn / name）。
- 历史：usn.read_journal_range（从 lowest_valid_usn 回溯到日志头，
  rename 新旧名共享同一 frn，pfrn 变化即移动）。

纯函数（合成数据可测）：build_path_map / resolve_path / lineage_from_events。
FRN 5 是卷根目录（"\\"）。孤儿（父链断裂）与环都由深度上限兜底。
"""

from __future__ import annotations

ROOT_FRN = 5
_MAX_DEPTH = 64


def build_parent_index(records: list[dict]) -> dict[int, dict[int, str]]:
    """frn → {pfrn, name} 索引（向上走的依据）。同 frn 重复记录后者覆盖。"""
    idx: dict[int, dict[int, str]] = {}
    for r in records:
        idx[r["frn"]] = {"pfrn": r["pfrn"], "name": r["name"]}
    return idx


def resolve_path(frn_index: dict[int, dict], vol: str,
                 frn: int, max_depth: int = _MAX_DEPTH) -> str | None:
    """从 frn 沿 pfrn 链向上重建完整路径。孤儿/环返回 None。

    frn_index 由 build_parent_index 产生；ROOT_FRN(5) 是卷根。
    """
    parts: list[str] = []
    cur = frn
    for _ in range(max_depth):
        if cur == ROOT_FRN:
            return (vol + ":\\" + "\\".join(reversed(parts))) if parts else (vol + ":\\")
        rec = frn_index.get(cur)
        if rec is None:
            return None
        parts.append(rec["name"])
        cur = rec["pfrn"]
    return None  # 超过深度上限：视为环


def build_path_map(records: list[dict], vol: str) -> dict[int, str]:
    """一次性为快照里所有可解析的文件建 frn → 完整路径表（带记忆化）。"""
    frn2rec = {r["frn"]: r for r in records}
    cache: dict[int, str | None] = {}

    def path_of(frn: int) -> str | None:
        if frn in cache:
            return cache[frn]
        cache[frn] = None  # 先占位防环
        parts: list[str] = []
        cur = frn
        for _ in range(_MAX_DEPTH):
            if cur == ROOT_FRN:
                break
            rec = frn2rec.get(cur)
            if rec is None:
                return None  # 链上有文件不在快照中 → 整条链不可解析
            parts.append(rec["name"])
            cur = rec["pfrn"]
        else:
            return None  # 深度超限
        full = vol + ":\\" + "\\".join(reversed(parts)) if parts else vol + ":\\"
        cache[frn] = full
        return full

    out: dict[int, str] = {}
    for r in records:
        p = path_of(r["frn"])
        if p is not None:
            out[r["frn"]] = p
    return out


def lineage_from_events(events: list[dict]) -> list[dict]:
    """按 frn 排序的事件序列 → 谱系链（改名/移动摘要）。

    输入：该文件同一 frn 的 USN 记录（按 usn 升序）。
    输出：[{ts, name, pfrn, action}]，action ∈ 新建/改名/移动/删除/事件。
    相邻同名同位去重（数据覆盖等高频事件不进谱系）。
    """
    out: list[dict] = []
    prev_name = None
    prev_pfrn = None
    for e in sorted(events, key=lambda x: x.get("usn", 0)):
        reason = int(e.get("reason", 0) or 0)
        name = e.get("name") or ""
        pfrn = e.get("pfrn") or 0
        if reason & 0x00000200:  # 删除
            action = "删除"
        elif reason & 0x00000100:  # 新建
            action = "新建"
        elif reason & 0x00002000:  # 改名新名
            action = "移动" if (prev_pfrn is not None and pfrn != prev_pfrn) else "改名"
        elif prev_name is None:
            action = "事件"
        else:
            action = None  # 与前一条同名同位：跳过
        if action is not None:
            out.append({"ts": e.get("ts", 0.0), "name": name,
                        "pfrn": pfrn, "action": action})
            prev_name, prev_pfrn = name, pfrn
    return out

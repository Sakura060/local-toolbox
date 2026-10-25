"""执行器与撤销。

apply_plan：按 Action 逐条移动文件，写 JSON 操作日志。
undo：逆序回滚一次 run 的所有移动。
设计原则：任何单条失败不影响其余；undo 同样逐条尽力而为并汇报。

安全约束：
1. 所有移动的目标路径必须位于计划根目录之内（防规则配置中的 `..`
   或绝对路径把文件搬出预期范围），逐条校验后才移动。
2. 操作日志文件名只含白名单字符，最终路径必须位于日志目录内。
   日志写入采用临时文件 + 原子替换，避免半写状态。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime

from .planner import Plan

LOG_DIR = os.path.join(os.path.expanduser("~"), ".fileflow", "logs")
_LOG_NAME_RE = re.compile(r"[A-Za-z0-9_\-]{1,60}\.json")


@dataclass
class RunReport:
    log_path: str
    moved: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)
    skipped: int = 0   # src 不存在等

    def summary(self) -> str:
        return (f"执行完成: 移动 {self.moved}, 失败 {self.failed}, 跳过 {self.skipped}\n"
                f"操作日志: {self.log_path}")


@dataclass
class UndoReport:
    restored: int = 0
    failed: int = 0
    failures: list[str] = field(default_factory=list)


def _is_within(child: str, parent: str) -> bool:
    """child 是否位于 parent 目录内（含相等）。用真实路径防符号链接绕过。"""
    try:
        c = os.path.realpath(child)
        p = os.path.realpath(parent)
        return os.path.commonpath([c, p]) == p
    except (OSError, ValueError):
        return False


def log_path_for(root: str) -> str:
    """为一次 run 生成日志路径。文件名只含 [A-Za-z0-9_-]，无用户可控分隔符。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.basename(root or "dir")
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", base)[:40] or "dir"
    name = f"{safe}-{ts}.json"
    if not _LOG_NAME_RE.fullmatch(name):
        raise ValueError(f"生成的日志文件名不合法: {name!r}")
    path = os.path.join(LOG_DIR, name)
    if not _is_within(path, LOG_DIR):
        raise ValueError(f"日志路径越界: {path}")
    return path


def apply_plan(plan: Plan, dry_run: bool = False) -> RunReport:
    """执行整理计划。dry_run=True 时只验证不改盘。"""
    report = RunReport(log_path="(dry-run)")
    if dry_run:
        for a in plan.moves:
            if not os.path.exists(a.src):
                report.skipped += 1
        report.moved = len(plan.moves) - report.skipped
        return report

    log_path = log_path_for(plan.root)
    os.makedirs(LOG_DIR, exist_ok=True)
    entries: list[dict] = []

    for a in plan.moves:
        if not os.path.exists(a.src):
            report.skipped += 1
            continue
        # 安全约束：目标必须留在计划根目录内
        if not _is_within(a.dst, plan.root):
            report.failed += 1
            report.failures.append(f"拒绝越界移动: {a.src} -> {a.dst}")
            continue
        try:
            os.makedirs(os.path.dirname(a.dst), exist_ok=True)
            shutil.move(a.src, a.dst)
            report.moved += 1
            entries.append({
                "op": "move",
                "src": a.src,
                "dst": a.dst,
                "rule": a.rule_name,
                "ts": datetime.now().isoformat(timespec="seconds"),
            })
        except OSError as e:
            report.failed += 1
            report.failures.append(f"{a.src} -> {a.dst}: {e}")

    payload = {
        "version": 1,
        "root": plan.root,
        "time": datetime.now().isoformat(timespec="seconds"),
        "actions": entries,
    }
    _write_log_atomically(log_path, payload)
    report.log_path = log_path
    return report


def _write_log_atomically(log_path: str, payload: dict) -> None:
    """先写同目录临时文件，校验后原子替换，避免半写日志。"""
    log_dir = os.path.dirname(log_path)
    fd, tmp = tempfile.mkstemp(prefix=".fflog-", suffix=".tmp", dir=log_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        # 临时文件与最终路径都必须落在日志目录内
        if not _is_within(tmp, LOG_DIR) or not _is_within(log_path, LOG_DIR):
            raise ValueError(f"日志路径越界: {log_path}")
        os.replace(tmp, log_path)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def load_log(path: str) -> dict:
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict) or data.get("version") != 1 or "actions" not in data:
        raise ValueError(f"无效的操作日志: {path}")
    return data


def undo(log_path: str) -> UndoReport:
    """按逆序回滚一次 run 的全部移动。"""
    data = load_log(log_path)
    report = UndoReport()
    root = data.get("root", "")

    for entry in reversed(data["actions"]):
        if entry.get("op") != "move":
            continue
        src, dst = entry["src"], entry["dst"]
        # 回滚目标（原位置）必须仍在原根目录内，防被篡改的日志指到任意路径
        if root and not _is_within(src, root):
            report.failed += 1
            report.failures.append(f"拒绝越界回滚: {dst} -> {src}")
            continue
        if not os.path.exists(dst):
            report.failures.append(f"目标已不在，跳过: {dst}")
            continue
        # 原位置被占用时，回滚到旁边的 (restored-N) 名字
        final_src = src
        if os.path.exists(src):
            d = os.path.dirname(src)
            stem, dot, suffix = os.path.basename(src).rpartition(".")
            if not dot or stem == "":
                stem, suffix = os.path.basename(src), ""
            for i in range(1, 10_000):
                cand = os.path.join(d, f"{stem} (restored-{i}).{suffix}" if suffix
                                    else f"{stem} (restored-{i})")
                if not os.path.exists(cand):
                    final_src = cand
                    break
        try:
            os.makedirs(os.path.dirname(final_src), exist_ok=True)
            shutil.move(dst, final_src)
            report.restored += 1
        except OSError as e:
            report.failed += 1
            report.failures.append(f"{dst} -> {final_src}: {e}")

    _remove_empty_dirs(root, report)
    return report


def _remove_empty_dirs(root: str, report: UndoReport) -> None:
    """回滚后自底向上删除变空的子目录（根目录本身不动）。"""
    if not root or not os.path.isdir(root):
        return
    for dirpath, dirnames, _ in list(os.walk(root, topdown=False)):
        if os.path.samefile(dirpath, root):
            continue
        try:
            if not dirnames and not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass  # 删不掉就留着，不影响回滚结果


def list_logs() -> list[str]:
    """按时间倒序列出操作日志。"""
    if not os.path.isdir(LOG_DIR):
        return []
    logs = [os.path.join(LOG_DIR, n) for n in os.listdir(LOG_DIR) if n.endswith(".json")]
    return sorted(logs, key=os.path.getmtime, reverse=True)

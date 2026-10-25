"""整理计划生成器（dry-run 核心）。

把扫描结果 + 规则引擎的匹配结果转成一份可预览、可执行、可撤销的 Plan。
所有目标路径都会做冲突消解：重名文件追加 (1) (2) ……
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .rules import MatchResult, RuleEngine
from .scanner import ScanResult


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


@dataclass
class Action:
    """一次移动操作。"""
    src: str              # 绝对路径
    dst: str              # 绝对路径（已消解冲突）
    rule_name: str        # 命中的规则名（"" = 未命中，不动）

    @property
    def will_move(self) -> bool:
        return bool(self.rule_name) and os.path.normpath(self.src) != os.path.normpath(self.dst)


@dataclass
class Plan:
    root: str
    actions: list[Action] = field(default_factory=list)
    unmatched: list[MatchResult] = field(default_factory=list)
    conflicts_resolved: int = 0   # 因重名而改名的数量

    @property
    def moves(self) -> list[Action]:
        return [a for a in self.actions if a.will_move]

    def summary(self) -> str:
        total = len(self.moves)
        size = sum(os.path.getsize(a.src) for a in self.moves if os.path.exists(a.src))
        lines = [f"整理计划（{self.root}）:"]
        by_target: dict[str, list[Action]] = {}
        for a in self.moves:
            by_target.setdefault(os.path.dirname(a.dst), []).append(a)
        for target in sorted(by_target):
            acts = by_target[target]
            lines.append(f"  → {os.path.relpath(target, self.root)}/  ({len(acts)} 个文件)")
        lines.append(f"共 {total} 个文件待移动, {human_size(size)}; "
                     f"{len(self.unmatched)} 个文件无匹配规则将保持原位")
        return "\n".join(lines)


class Planner:
    """扫描结果 → Plan。"""

    def __init__(self, engine: RuleEngine):
        self.engine = engine

    def build(self, scan: ScanResult) -> Plan:
        plan = Plan(root=scan.root)
        # 记录目标目录里已占用的名字：目标中真实存在的 + 本计划内已分配的
        occupied: dict[str, set[str]] = {}

        for f in scan.files:
            m = self.engine.match(f)
            if not m.matched:
                plan.unmatched.append(m)
                plan.actions.append(Action(src=f.path, dst=f.path, rule_name=""))
                continue

            target_dir = os.path.join(scan.root, m.destination.replace("/", os.sep))
            # 防自锁：如果文件本来就在目标目录，跳过
            if os.path.normpath(os.path.dirname(f.path)) == os.path.normpath(target_dir):
                plan.actions.append(Action(src=f.path, dst=f.path, rule_name=m.rule.name))
                continue

            key = target_dir.lower()
            if key not in occupied:
                taken = occupied.setdefault(key, set())
                if os.path.isdir(target_dir):
                    try:
                        taken.update(n.lower() for n in os.listdir(target_dir))
                    except OSError:
                        pass
            else:
                taken = occupied[key]

            dst = os.path.join(target_dir, f.name)
            final = self._resolve_conflict(dst, taken, plan)
            taken.add(os.path.basename(final).lower())
            plan.actions.append(Action(src=f.path, dst=final, rule_name=m.rule.name))

        return plan

    def _resolve_conflict(self, dst: str, taken: set[str], plan: Plan) -> str:
        """重名时追加 (1) (2) …。"""
        if os.path.basename(dst).lower() not in taken:
            return dst
        plan.conflicts_resolved += 1
        stem, dot, suffix = os.path.basename(dst).rpartition(".")
        if not dot or stem == "":
            stem, suffix = os.path.basename(dst), ""
        base_dir = os.path.dirname(dst)
        for i in range(1, 10_000):
            cand = f"{stem} ({i}).{suffix}" if suffix else f"{stem} ({i})"
            if cand.lower() not in taken:
                return os.path.join(base_dir, cand)
        raise RuntimeError(f"冲突消解失败: {dst}")

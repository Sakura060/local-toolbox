"""watch 监控模式：定时扫描目录并自动整理新文件。

设计要点：
- min_age 秒数保护：修改时间太新的文件不整理（下载器还在写入）
- 每次整理作为独立 run 记录日志，undo 可按次撤销
- 未命中规则的文件保持原位；只打印新增移动，避免刷屏
- dry_run 模式可供测试与 --preview 使用
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .executor import apply_plan
from .planner import Planner
from .rules import RuleEngine
from .scanner import Scanner


@dataclass
class WatchStats:
    cycles: int = 0
    moved_total: int = 0
    skipped_fresh: int = 0          # 因 min-age 跳过的次数
    runs: list[str] = field(default_factory=list)   # 各次 run 的日志路径


class Watcher:
    """对 root 反复 扫描→计划→执行。interval 与 min_age 单位为秒。"""

    def __init__(self, root: str, engine: RuleEngine, scanner: Scanner,
                 interval: float = 10.0, min_age: float = 5.0,
                 on_event=None):
        self.root = root
        self.engine = engine
        self.scanner = scanner
        self.interval = interval
        self.min_age = min_age
        self.on_event = on_event        # callable(str) 可选，用于 CLI 打印/测试收集
        self.stats = WatchStats()

    def _log(self, msg: str) -> None:
        if self.on_event:
            self.on_event(msg)

    def cycle_once(self, now: float | None = None) -> int:
        """执行一轮，返回本轮移动的文件数。now 参数便于测试注入时钟。"""
        now = time.time() if now is None else now
        self.stats.cycles += 1
        scan = self.scanner.scan(self.root)

        # min-age 保护：太新的文件本轮不动（模拟为从扫描结果剔除）
        fresh = [f for f in scan.files if now - f.mtime < self.min_age]
        self.stats.skipped_fresh += len(fresh)
        scan.files = [f for f in scan.files if now - f.mtime >= self.min_age]

        plan = Planner(self.engine).build(scan)
        moved = len(plan.moves)
        if moved == 0:
            return 0

        report = apply_plan(plan, dry_run=False)
        self.stats.moved_total += report.moved
        if report.log_path and report.log_path != "(dry-run)":
            self.stats.runs.append(report.log_path)
        self._log(f"[cycle {self.stats.cycles}] 整理 {report.moved} 个文件"
                  + (f"（另跳过 {len(fresh)} 个写入中的文件）" if fresh else ""))
        for f in report.failures[:5]:
            self._log(f"  失败: {f}")
        return report.moved

    def run_forever(self) -> None:
        """阻塞循环；由 CLI 的 KeyboardInterrupt 中断。"""
        self._log(f"监控 {self.root}（每 {self.interval:g}s 扫描，"
                  f"文件静置 {self.min_age:g}s 后整理，Ctrl+C 停止）")
        while True:
            try:
                self.cycle_once()
            except OSError as e:
                self._log(f"本轮扫描失败: {e}")
            time.sleep(self.interval)

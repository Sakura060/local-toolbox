"""FileFlow 命令行入口。

用法:
  python cli.py scan  <目录>                 扫描并显示概览
  python cli.py plan  <目录> [-r 规则.json]   生成整理计划（只预览，不改盘）
  python cli.py run   <目录> [-r 规则.json] [-y]  执行整理
  python cli.py undo  [日志.json]            撤销最近一次整理（无参数=最近）
  python cli.py logs                         列出历史操作日志
  python cli.py config [--init]              查看/初始化用户配置
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sys

from fileflow import __version__
from fileflow.config import CONFIG_PATH, ensure_config, load_config
from fileflow.dedup import find_duplicates, plan_cleanup
from fileflow.executor import apply_plan, list_logs, load_log, undo
from fileflow.planner import Planner, human_size
from fileflow.rules import PRESETS, RuleError, RuleEngine, load_rules
from fileflow.scanner import Scanner


def _build_engine(args) -> RuleEngine:
    cfg = load_config()
    user_rules = list(cfg.get("rules", []))
    use_defaults = bool(cfg.get("use_defaults", True))
    preset = getattr(args, "preset", None) or cfg.get("preset") or None
    if getattr(args, "rules_file", None):
        with open(args.rules_file, encoding="utf-8") as fp:
            extra = json.load(fp)
        if isinstance(extra, dict):
            extra = extra.get("rules", [])
        if not isinstance(extra, list):
            raise RuleError("规则文件必须是数组或 {\"rules\": [...]}")
        user_rules = extra + user_rules
    if getattr(args, "no_defaults", False):
        # --no-defaults = 只用用户规则，预设与内置默认全部禁用
        use_defaults = False
        preset = None
    return RuleEngine(load_rules(user_rules, use_defaults=use_defaults, preset=preset))


def _build_scanner() -> Scanner:
    cfg = load_config()
    return Scanner(
        exclude_dirs=cfg.get("exclude_dirs", []),
        exclude_patterns=cfg.get("exclude_patterns", []),
    )


def _make_plan(args):
    engine = _build_engine(args)
    scan = _build_scanner().scan(args.directory)
    planner = Planner(engine)
    return scan, planner.build(scan)


def cmd_scan(args) -> int:
    scan = _build_scanner().scan(args.directory)
    print(scan.summary())
    by_ext: dict[str, int] = {}
    for f in scan.files:
        key = f.suffix or "(无扩展名)"
        by_ext[key] = by_ext.get(key, 0) + 1
    top = sorted(by_ext.items(), key=lambda kv: -kv[1])[:15]
    if top:
        print("扩展名分布:", ", ".join(f"{k}×{v}" for k, v in top))
    return 0


def cmd_plan(args) -> int:
    scan, plan = _make_plan(args)
    print(scan.summary())
    print()
    print(plan.summary())
    if args.json:
        print(json.dumps([a.__dict__ for a in plan.moves], ensure_ascii=False, indent=2))
    return 0


def cmd_run(args) -> int:
    scan, plan = _make_plan(args)
    print(scan.summary())
    print()
    print(plan.summary())
    if not plan.moves:
        print("没有需要移动的文件。")
        return 0
    if not args.yes:
        try:
            answer = input(f"\n确认执行？将移动 {len(plan.moves)} 个文件 [y/N]: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("已取消。")
            return 1
    report = apply_plan(plan, dry_run=False)
    print()
    print(report.summary())
    if report.failures:
        for f in report.failures[:20]:
            print(f"  失败: {f}")
    return 0 if report.failed == 0 else 2


def cmd_undo(args) -> int:
    log = args.logfile
    if not log:
        logs = list_logs()
        if not logs:
            print("没有可撤销的操作日志。")
            return 1
        log = logs[0]
        print(f"撤销最近一次: {log}")
    data = load_log(log)
    n = len(data.get("actions", []))
    if n == 0:
        print("该日志没有任何操作，无需撤销。")
        return 0
    rep = undo(log)
    print(f"撤销完成: 恢复 {rep.restored}/{n}, 失败 {rep.failed}")
    for f in rep.failures[:20]:
        print(f"  失败: {f}")
    return 0 if rep.failed == 0 else 2


def cmd_logs(_args) -> int:
    logs = list_logs()
    if not logs:
        print("暂无操作日志。")
        return 0
    for p in logs[:20]:
        print(p)
    return 0


def cmd_dup(args) -> int:
    """查找重复文件；--clean 把多余副本移入根下 .fileflow-trash 回收目录。"""
    root = os.path.abspath(args.directory)
    print(f"正在扫描重复文件: {root}")
    groups = find_duplicates(root, exclude_dirs=load_config().get("exclude_dirs", []))
    if not groups:
        print("未发现重复文件。")
        return 0
    wasted = sum(g.wasted for g in groups)
    for g in groups[:30]:
        print(f"\n[{human_size(g.size)} × {len(g.paths)} 份]")
        for p in g.paths:
            print(f"  {p}")
    if len(groups) > 30:
        print(f"\n… 以及另外 {len(groups) - 30} 组")
    print(f"\n共 {len(groups)} 组重复，可释放 {human_size(wasted)}")

    if not args.clean:
        print("（加 --clean 把多余副本移入回收目录）")
        return 0

    plan = plan_cleanup(groups, keep=args.keep)
    trash = os.path.join(root, ".fileflow-trash", "duplicates")
    moved, failed = 0, 0
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest_dir = os.path.join(trash, stamp)
    for dup, keeper in plan:
        try:
            os.makedirs(dest_dir, exist_ok=True)
            dst = os.path.join(dest_dir, os.path.basename(dup))
            i = 1
            while os.path.exists(dst):
                stem, dot, suffix = os.path.basename(dup).rpartition(".")
                if not dot or stem == "":
                    stem, suffix = os.path.basename(dup), ""
                dst = os.path.join(dest_dir, f"{stem} ({i}).{suffix}" if suffix
                                   else f"{stem} ({i})")
                i += 1
            shutil.move(dup, dst)
            moved += 1
        except OSError as e:
            failed += 1
            print(f"  失败: {dup}: {e}")
    print(f"清理完成: 移入回收目录 {moved} 个, 失败 {failed} 个")
    print(f"回收目录: {dest_dir}  （确认无误后可手动删除）")
    return 0 if failed == 0 else 2


def cmd_watch(args) -> int:
    """监控目录，自动整理新文件（Ctrl+C 停止）。"""
    from fileflow.watcher import Watcher

    engine = _build_engine(args)
    scanner = _build_scanner()
    watcher = Watcher(args.directory, engine, scanner,
                      interval=args.interval, min_age=args.min_age,
                      on_event=lambda m: print(m, flush=True))
    try:
        watcher.run_forever()
    except KeyboardInterrupt:
        print(f"\n监控结束：共 {watcher.stats.cycles} 轮，"
              f"整理 {watcher.stats.moved_total} 个文件。")
        if watcher.stats.runs:
            print("最近的操作日志（可用 undo 撤销）:")
            for p in watcher.stats.runs[-3:]:
                print(f"  {p}")
    return 0


def cmd_config(args) -> int:
    if args.init:
        print(f"配置已就绪: {ensure_config()}")
        return 0
    ensure_config()
    print(f"配置文件: {CONFIG_PATH}")
    print(open(CONFIG_PATH, encoding="utf-8").read())
    return 0


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台编码兜底：输出包含中文时避免 UnicodeEncodeError 崩溃
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
    parser = argparse.ArgumentParser(
        prog="fileflow",
        description="FileFlow 智能文件整理器 — 按规则整理目录，可预览、可撤销",
    )
    parser.add_argument("--version", action="version", version=f"FileFlow {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="扫描目录并显示概览")
    p.add_argument("directory")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("plan", help="生成整理计划（只预览）")
    p.add_argument("directory")
    p.add_argument("-r", "--rules-file", help="自定义规则 JSON 文件")
    p.add_argument("--preset", choices=sorted(PRESETS), help="使用预设规则模板替代内置默认")
    p.add_argument("--no-defaults", action="store_true", help="不使用内置默认规则")
    p.add_argument("--json", action="store_true", help="以 JSON 输出移动清单")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run", help="执行整理")
    p.add_argument("directory")
    p.add_argument("-r", "--rules-file", help="自定义规则 JSON 文件")
    p.add_argument("--preset", choices=sorted(PRESETS), help="使用预设规则模板替代内置默认")
    p.add_argument("--no-defaults", action="store_true", help="不使用内置默认规则")
    p.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("undo", help="撤销一次整理")
    p.add_argument("logfile", nargs="?", help="操作日志路径（默认最近一次）")
    p.set_defaults(func=cmd_undo)

    p = sub.add_parser("logs", help="列出操作日志")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("dup", help="查找重复文件")
    p.add_argument("directory")
    p.add_argument("--clean", action="store_true", help="把多余副本移入回收目录")
    p.add_argument("--keep", choices=["oldest", "first"], default="oldest",
                   help="保留策略: oldest=最旧(默认), first=列表第一个")
    p.set_defaults(func=cmd_dup)

    p = sub.add_parser("watch", help="监控目录自动整理新文件")
    p.add_argument("directory")
    p.add_argument("--interval", type=float, default=10.0, help="扫描间隔秒数（默认 10）")
    p.add_argument("--min-age", type=float, default=5.0,
                   help="文件静置秒数，防止整理未写完的下载（默认 5）")
    p.add_argument("-r", "--rules-file", help="自定义规则 JSON 文件")
    p.add_argument("--preset", choices=sorted(PRESETS), help="使用预设规则模板")
    p.add_argument("--no-defaults", action="store_true", help="不使用内置默认规则")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("config", help="查看/初始化配置")
    p.add_argument("--init", action="store_true", help="初始化默认配置")
    p.set_defaults(func=cmd_config)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (NotADirectoryError, RuleError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

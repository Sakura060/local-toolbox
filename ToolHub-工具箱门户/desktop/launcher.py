"""工具启动器：无窗口静默拉起 + 输出捕获到日志文件 + 进程存活跟踪。

与旧门户 spawn_bat（新控制台窗口）的区别：
- 不弹黑窗（CREATE_NO_WINDOW），stdout/stderr 管道捕获 → data/logs/<id>.log
- {python} 替换为打包自带运行时（TOOLHUB_PYTHON > 随包 runtime > 当前解释器）
- {param:x} 替换为界面传入的参数（字符串，长度受限；仅本机使用，仍从严校验）
- 子进程环境注入 PYTHONUTF8=1（中文 Windows 下日志统一 UTF-8）
停止仍走 toolhub.procs（端口反查 PID 树杀），与旧门户语义一致。
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import WORKSPACE_ROOT
from .cfg import DesktopTool

LOG_DIR = WORKSPACE_ROOT / "ToolHub-工具箱门户" / "data" / "logs"
LOG_MAX_BYTES = 512 * 1024
LOG_KEEP_BYTES = 256 * 1024
PARAM_MAX_LEN = 500


class LaunchError(Exception):
    """人话版启动失败（信息直接展示给用户）。"""


def resolve_python() -> Path:
    r"""三档：环境变量 TOOLHUB_PYTHON → 随包 runtime\python.exe → 当前解释器。"""
    env = os.environ.get("TOOLHUB_PYTHON")
    if env and Path(env).is_file():
        return Path(env)
    bundled = WORKSPACE_ROOT.parent / "runtime" / "python.exe"
    if bundled.is_file():
        return bundled
    return Path(sys_executable())


def sys_executable() -> str:
    import sys
    return sys.executable


def _truncate_log(path: Path) -> None:
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            data = path.read_bytes()[-LOG_KEEP_BYTES:]
            path.write_bytes(data)
    except OSError:
        pass


class Launcher:
    """跟踪每个工具的子进程；线程把输出写进日志文件。"""

    def __init__(self, python_exe: Path | None = None, log_dir: Path | None = None):
        self.python_exe = python_exe or resolve_python()
        self.log_dir = Path(log_dir) if log_dir else LOG_DIR
        self._procs: dict[str, subprocess.Popen] = {}
        self._started_at: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---- 存活 ----
    def alive(self, tool_id: str) -> bool:
        with self._lock:
            p = self._procs.get(tool_id)
        return bool(p and p.poll() is None)

    def started_at(self, tool_id: str) -> float | None:
        with self._lock:
            return self._started_at.get(tool_id)

    def forget(self, tool_id: str) -> None:
        with self._lock:
            self._procs.pop(tool_id, None)
            self._started_at.pop(tool_id, None)

    # ---- 日志 ----
    def log_path(self, tool_id: str) -> Path:
        return self.log_dir / f"{tool_id}.log"

    def read_log(self, tool_id: str, lines: int = 200) -> str:
        path = self.log_path(tool_id)
        if not path.exists():
            return ""
        try:
            data = path.read_bytes()[-64 * 1024:]
            out = data.decode("utf-8", "replace").splitlines()
            return "\n".join(out[-lines:])
        except OSError:
            return ""

    # ---- 启动 ----
    def launch(self, tool: DesktopTool, params: dict[str, str] | None = None) -> dict:
        if self.alive(tool.id):
            return {"ok": True, "already": True,
                    "pid": self._procs[tool.id].pid,
                    "hint": "该工具已经在运行（由桌面版拉起的实例）"}
        params = params or {}

        # 参数校验与展开
        resolved: dict[str, str] = {}
        for name, default in tool.params:
            raw = str(params.get(name) or default or "")
            raw = os.path.expandvars(os.path.expanduser(raw)).strip()
            if not raw:
                raise LaunchError(f"参数「{name}」不能为空")
            if len(raw) > PARAM_MAX_LEN:
                raise LaunchError(f"参数「{name}」过长（上限 {PARAM_MAX_LEN} 字符）")
            resolved[name] = raw

        argv: list[str] = []
        for a in tool.argv:
            if a == "{python}":
                argv.append(str(self.python_exe))
                continue
            m = _PARAM_PLH.fullmatch(a)
            if m:
                argv.append(resolved.get(m.group(1), ""))
                continue
            argv.append(a)

        workdir = tool.dir_path
        if not workdir.is_dir():
            raise LaunchError(f"启动目录不存在：{tool.launch_dir}")

        script = argv[1] if len(argv) > 1 else ""
        if script == "cli.py" and not (workdir / "cli.py").is_file():
            raise LaunchError(f"目录里找不到 cli.py：{tool.launch_dir}")

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.log_path(tool.id)
        _truncate_log(log_file)

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("PYTHONOPTIMIZE", None)

        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(workdir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                env=env,
            )
        except OSError as exc:
            raise LaunchError(f"启动进程失败：{exc}") from exc

        with self._lock:
            self._procs[tool.id] = proc
            self._started_at[tool.id] = time.time()
        threading.Thread(target=self._pump, args=(tool.id, proc, log_file),
                         daemon=True).start()
        return {"ok": True, "pid": proc.pid,
                "hint": "服务就绪一般需要几秒，界面会自动等它上线"}

    def _pump(self, tool_id: str, proc: subprocess.Popen, log_file: Path) -> None:
        """把子进程输出写进日志；进程退出后从跟踪表移除。"""
        try:
            with log_file.open("a", encoding="utf-8", errors="replace") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 启动 =====\n")
                f.flush()
                assert proc.stdout is not None
                for line in proc.stdout:
                    f.write(line if line.endswith("\n") else line + "\n")
                f.write(f"===== 进程退出（代码 {proc.wait()}）=====\n")
        except OSError:
            pass
        finally:
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except OSError:
                pass
            self.forget(tool_id)


_PARAM_PLH = re.compile(r"\{param:([a-z0-9_]+)\}")

"""进程查找与结束（Windows 自带 netstat/taskkill，零依赖）。

停止语义：按端口反查监听进程——不管工具是门户拉起的还是用户自己双击
bat 启动的，谁占着这个端口就停谁。
"""

from __future__ import annotations

import subprocess


def find_port_pids(port: int) -> list[int]:
    """返回正在监听该 TCP 端口的进程 PID 列表（可能为空；理论上可多于一个）。"""
    out = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True, text=True, timeout=15,
        encoding="utf-8", errors="replace",  # 中文 Windows 下 netstat 头部是 GBK
    ).stdout or ""
    pids: list[int] = []
    suffix = f":{port}"
    for line in out.splitlines():
        parts = line.split()
        # 形如：TCP  127.0.0.1:8642  0.0.0.0:0  LISTENING  12345
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING" \
                and parts[1].endswith(suffix):
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            if pid > 0 and pid not in pids:
                pids.append(pid)
    return pids


def kill_pids(pids: list[int]) -> list[int]:
    """强制结束进程树（/T 连子进程，/F 强制）。返回成功结束的 PID。"""
    killed: list[int] = []
    for pid in pids:
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        if r.returncode == 0:
            killed.append(pid)
    return killed

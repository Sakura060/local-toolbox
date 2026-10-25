"""配置管理。

用户配置放在 ~/.fileflow/config.json：
{
  "rules": [ ...自定义规则（优先于内置）... ],
  "use_defaults": true,
  "exclude_dirs": [".git", "node_modules"],
  "exclude_patterns": ["*.tmp", "~$*"]
}

提供默认配置生成、读取（容错：文件损坏时备份并重建）、保存（临时文件 + 原子替换）。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".fileflow")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_CONFIG: dict = {
    "use_defaults": True,
    "preset": None,          # 可选: developer / designer / student / finance
    "rules": [],
    "exclude_dirs": [".git", "node_modules", "__pycache__", "$RECYCLE.BIN"],
    "exclude_patterns": ["*.tmp", "~$*", "Thumbs.db", "desktop.ini"],
}


def ensure_config() -> str:
    """确保配置存在，返回配置路径。首次运行时写入默认配置。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
    return CONFIG_PATH


def load_config() -> dict:
    """读取配置；损坏时把原文件备份为 .broken-<ts> 并重建默认配置。"""
    ensure_config()
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fp:
            data = json.load(fp)
        if not isinstance(data, dict):
            raise ValueError("配置根必须是对象")
        return {**DEFAULT_CONFIG, **data}
    except (json.JSONDecodeError, ValueError, OSError) as e:
        backup = os.path.join(CONFIG_DIR,
                              f"config.json.broken-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        try:
            shutil.copy2(CONFIG_PATH, backup)
        except OSError:
            backup = "(备份失败)"
        save_config(DEFAULT_CONFIG)
        print(f"[fileflow] 配置损坏({e})，已备份到 {backup} 并重建默认配置")
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> None:
    """保存配置：同目录临时文件 + 原子替换。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=CONFIG_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
        final = os.path.abspath(os.path.join(CONFIG_DIR, "config.json"))
        if os.path.dirname(os.path.abspath(tmp)) != CONFIG_DIR:
            raise ValueError("临时文件位置异常")
        os.replace(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise

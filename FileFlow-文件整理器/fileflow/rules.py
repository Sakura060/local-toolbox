"""规则引擎。

规则为声明式 JSON/dict：
  {"name": "图片", "type": "extension", "pattern": "jpg png gif", "target": "图片"}
  {"name": "按月归档", "type": "date", "format": "%Y-%m", "target": "归档/{date}"}
  {"name": "发票", "type": "name_pattern", "pattern": "*发票*", "target": "财务/发票"}
  {"name": "大文件", "type": "size", "min_mb": 100, "target": "大文件"}

type 取值：
  extension    — pattern 为空格分隔的扩展名列表（不带点，大小写不敏感）
  date         — 按文件修改时间归档，target 中的 {date} 会被 format 结果替换
  name_pattern — fnmatch 通配符匹配文件名
  size         — 文件大小在 [min_mb, max_mb) 范围内（任一端可省略）

多条规则按顺序求值，首个命中的生效；未命中任何规则的文件留在原地（或进 target="_review"）。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from fnmatch import fnmatch as _fnmatch
from typing import Any

from .scanner import FileInfo

RULE_TYPES = ("extension", "date", "name_pattern", "size")


class RuleError(ValueError):
    """规则定义非法。"""


@dataclass
class Rule:
    name: str
    type: str
    target: str
    pattern: str = ""            # extension / name_pattern
    date_format: str = "%Y-%m"   # date
    min_mb: float | None = None  # size
    max_mb: float | None = None  # size

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Rule":
        if not isinstance(raw, dict):
            raise RuleError(f"规则必须是对象: {raw!r}")
        missing = [k for k in ("name", "type", "target") if k not in raw]
        if missing:
            raise RuleError(f"规则缺少字段 {missing}: {raw!r}")
        if raw["type"] not in RULE_TYPES:
            raise RuleError(f"未知规则类型 {raw['type']!r}，可选: {RULE_TYPES}")
        rule = cls(
            name=str(raw["name"]),
            type=raw["type"],
            target=str(raw["target"]),
            pattern=str(raw.get("pattern", "")),
            date_format=str(raw.get("format", "%Y-%m")),
            min_mb=raw.get("min_mb"),
            max_mb=raw.get("max_mb"),
        )
        if rule.type == "extension" and not rule.pattern.strip():
            raise RuleError(f"规则 {rule.name!r}: extension 类型需要 pattern")
        if rule.type == "size":
            if rule.min_mb is None and rule.max_mb is None:
                raise RuleError(f"规则 {rule.name!r}: size 类型需要 min_mb 或 max_mb")
        return rule

    def _extensions(self) -> set[str]:
        return {e.strip().lstrip(".").lower() for e in self.pattern.split() if e.strip()}

    def matches(self, f: FileInfo) -> bool:
        if self.type == "extension":
            return f.suffix in self._extensions()
        if self.type == "name_pattern":
            return _fnmatch(f.name, self.pattern) or _fnmatch(f.stem, self.pattern)
        if self.type == "date":
            return True  # date 规则永远命中（用于兜底归档）
        if self.type == "size":
            mb = f.size / (1024 * 1024)
            if self.min_mb is not None and mb < self.min_mb:
                return False
            if self.max_mb is not None and mb >= self.max_mb:
                return False
            return True
        return False

    def destination(self, f: FileInfo) -> str:
        """返回目标子目录（相对根目录，正斜杠风格）。"""
        if self.type == "date":
            ds = f.modified.strftime(self.date_format)
            return self.target.replace("{date}", ds)
        return self.target

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "type": self.type, "target": self.target}
        if self.pattern:
            d["pattern"] = self.pattern
        if self.type == "date":
            d["format"] = self.date_format
        if self.type == "size":
            if self.min_mb is not None:
                d["min_mb"] = self.min_mb
            if self.max_mb is not None:
                d["max_mb"] = self.max_mb
        return d


# 内置默认规则（可被用户规则覆盖顺序）
DEFAULT_RULES: list[dict[str, Any]] = [
    {"name": "图片", "type": "extension", "pattern": "jpg jpeg png gif bmp webp svg ico heic", "target": "图片"},
    {"name": "文档", "type": "extension", "pattern": "pdf doc docx xls xlsx ppt pptx txt md csv rtf", "target": "文档"},
    {"name": "音频", "type": "extension", "pattern": "mp3 wav flac aac ogg m4a", "target": "音频"},
    {"name": "视频", "type": "extension", "pattern": "mp4 mkv avi mov wmv flv webm", "target": "视频"},
    {"name": "压缩包", "type": "extension", "pattern": "zip rar 7z tar gz bz2 xz iso", "target": "压缩包"},
    {"name": "安装包", "type": "extension", "pattern": "exe msi apk dmg pkg", "target": "安装包"},
    {"name": "代码", "type": "extension", "pattern": "py js ts tsx jsx c cpp h java cs go rs html css json xml yml yaml sh bat", "target": "代码"},
    {"name": "字体", "type": "extension", "pattern": "ttf otf woff woff2", "target": "字体"},
]

# 预设规则模板：针对不同人群的整理偏好（--preset / config 的 "preset" 键）
PRESETS: dict[str, list[dict[str, Any]]] = {
    "default": DEFAULT_RULES,
    "developer": [
        {"name": "代码", "type": "extension", "pattern": "py js ts tsx jsx c cpp h hpp java cs go rs rb php html css scss vue sql sh bat ps1", "target": "代码"},
        {"name": "配置", "type": "extension", "pattern": "json yaml yml toml ini env cfg xml lock", "target": "配置"},
        {"name": "文档", "type": "extension", "pattern": "md txt pdf doc docx", "target": "文档"},
        {"name": "压缩包", "type": "extension", "pattern": "zip rar 7z tar gz bz2 xz", "target": "压缩包"},
        {"name": "安装包", "type": "extension", "pattern": "exe msi pkg", "target": "安装包"},
        {"name": "图片", "type": "extension", "pattern": "png jpg jpeg gif svg ico", "target": "图片"},
    ],
    "designer": [
        {"name": "设计源文件", "type": "extension", "pattern": "psd ai sketch fig xd indesign", "target": "设计源文件"},
        {"name": "位图", "type": "extension", "pattern": "png jpg jpeg webp gif bmp tiff heic", "target": "位图"},
        {"name": "矢量与图标", "type": "extension", "pattern": "svg eps ai pdf", "target": "矢量与图标"},
        {"name": "字体", "type": "extension", "pattern": "ttf otf woff woff2", "target": "字体"},
        {"name": "3D与视频", "type": "extension", "pattern": "obj fbx blend mp4 mov", "target": "3D与视频"},
        {"name": "成品交付", "type": "extension", "pattern": "zip rar 7z", "target": "成品交付"},
    ],
    "student": [
        {"name": "课件文档", "type": "extension", "pattern": "pdf ppt pptx doc docx txt md", "target": "课件文档"},
        {"name": "表格", "type": "extension", "pattern": "xls xlsx csv", "target": "表格"},
        {"name": "课程视频", "type": "extension", "pattern": "mp4 mkv avi mov wmv", "target": "课程视频"},
        {"name": "音频", "type": "extension", "pattern": "mp3 wav m4a flac", "target": "音频"},
        {"name": "图片", "type": "extension", "pattern": "jpg jpeg png gif", "target": "图片"},
        {"name": "资料压缩包", "type": "extension", "pattern": "zip rar 7z tar gz", "target": "资料压缩包"},
        {"name": "安装包", "type": "extension", "pattern": "exe msi apk dmg", "target": "安装包"},
    ],
    "finance": [
        {"name": "发票", "type": "name_pattern", "pattern": "*发票*", "target": "财务/发票"},
        {"name": "报销单", "type": "name_pattern", "pattern": "*报销*", "target": "财务/报销"},
        {"name": "对账单", "type": "name_pattern", "pattern": "*对账*", "target": "财务/对账"},
        {"name": "表格", "type": "extension", "pattern": "xls xlsx csv", "target": "财务/表格"},
        {"name": "文档", "type": "extension", "pattern": "pdf doc docx", "target": "财务/文档"},
        {"name": "按月归档", "type": "date", "format": "%Y-%m", "target": "财务/归档/{date}"},
    ],
}


def load_preset(name: str) -> list[dict[str, Any]]:
    """按名称取预设模板；未知名称抛 RuleError。"""
    if name not in PRESETS:
        raise RuleError(f"未知预设 {name!r}，可选: {sorted(PRESETS)}")
    return PRESETS[name]


def load_rules(user_rules: list[dict[str, Any]] | None = None,
               use_defaults: bool = True,
               preset: str | None = None) -> list[Rule]:
    """组装规则列表：用户规则在前（优先），预设/内置规则在后。

    preset 指定时用对应模板替代内置默认规则；user_rules 显式给出时
    通常配合 preset 使用（用户规则 > 预设规则）。
    """
    raw = list(user_rules or [])
    if preset:
        raw = raw + load_preset(preset)
    elif use_defaults:
        raw = raw + DEFAULT_RULES
    return [Rule.from_dict(r) for r in raw]


@dataclass
class MatchResult:
    """单个文件的规则匹配结果。"""
    file: FileInfo
    rule: Rule | None      # 命中的规则；None = 无规则命中
    destination: str       # 相对根目录的目标目录；无命中时为 ""

    @property
    def matched(self) -> bool:
        return self.rule is not None


class RuleEngine:
    """按顺序求值规则，输出每个文件的归宿。"""

    def __init__(self, rules: list[Rule]):
        self.rules = rules

    def match(self, f: FileInfo) -> MatchResult:
        for rule in self.rules:
            if rule.matches(f):
                return MatchResult(file=f, rule=rule, destination=rule.destination(f))
        return MatchResult(file=f, rule=None, destination="")

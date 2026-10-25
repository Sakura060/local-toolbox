"""工作场景组合（总提示词阶段 1）：把多个工具/文件夹/文件/网址组成一键启动的场景。

设计要点（docs/万物皆插件 同款铁律的延续）：
- 场景只"引用"（工具 id / 路径 / 网址），绝不复制软件本体。
- data/scenes.json 原子写入（tmp + os.replace）；损坏时备份 .corrupt-<ts> 后从空恢复，
  绝不让一个坏文件拖垮整个功能。
- 停止语义（安全红线）：只停"本次场景启动且可可靠识别"的项——
  · 内置工具：启动前离线、由本会话拉起 → 停 = 按端口杀（启动前离线保证端口上只有我们的实例）；
  · 插件：runner 独立进程句柄 → stop(pid) 精确；
  · 文件夹/文件/网址：交给系统打开后无法可靠追踪 → 保守策略，不提供停止（提示词第五节第 10 条）。
- 已运行判定不猜进程名：内置工具看端口应答，插件看 runner 存活句柄。
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import WORKSPACE_ROOT

SCENES_PATH = WORKSPACE_ROOT / "ToolHub-工具箱门户" / "data" / "scenes.json"
SCENES_FORMAT = "toolhub-scenes"
SCENES_VERSION = 1

MAX_ITEMS = 50
MAX_SCENES = 50

ITEM_KINDS = ("tool", "folder", "file", "url")
_URL_RE = re.compile(r"^https?://[^\s]+$", re.I)
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

# 场景字段白名单（保存时未知字段拒绝——与插件清单同一哲学）
_SCENE_KEYS = {"id", "name", "icon", "items", "created_at", "last_launched"}
_ITEM_KEYS = {"kind", "ref"}


class SceneError(Exception):
    """人话版场景错误（直接展示给用户）。"""


# ---------------------------------------------------------------------------
# 存取（原子写 + 损坏自愈）
# ---------------------------------------------------------------------------

def load_scenes(path: Path | None = None) -> list[dict]:
    p = Path(path) if path else SCENES_PATH
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        scenes = data.get("scenes") if isinstance(data, dict) else None
        if not isinstance(scenes, list):
            raise ValueError("scenes 不是数组")
        out = []
        for s in scenes:
            # 读侧宽容：单条不合法跳过，不让一条坏记录吞掉全部场景
            if isinstance(s, dict) and _ID_RE.match(str(s.get("id", ""))) \
                    and isinstance(s.get("items"), list):
                out.append(s)
        return out
    except (OSError, ValueError) as exc:
        # 损坏 → 备份后从空恢复（用户的旧内容留档，不丢）
        backup = p.with_suffix(p.suffix + ".corrupt-" + time.strftime("%Y%m%d-%H%M%S"))
        try:
            p.replace(backup)
        except OSError:
            pass
        _ = exc  # 诊断信息不展示给用户（恢复才是目的）
        return []


def save_scenes(scenes: list[dict], path: Path | None = None) -> None:
    p = Path(path) if path else SCENES_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"format": SCENES_FORMAT, "version": SCENES_VERSION,
               "scenes": scenes}
    tmp = p.with_suffix(".json.tmp")
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp.write_text(body, encoding="utf-8")
    try:
        os.replace(tmp, p)      # 原子替换：中途断电/崩溃不留半截文件
    except OSError:
        tmp.unlink(missing_ok=True)   # 失败必须清理半成品，不留垃圾
        raise


def validate_scene(scene: dict, existing_ids: set[str] | None = None) -> list[str]:
    """保存前校验；返回错误列表（空=合格）。未知字段/重复 ID/超限全部拒绝。"""
    errors: list[str] = []
    if not isinstance(scene, dict):
        return ["场景必须是 JSON 对象"]
    unknown = set(scene) - _SCENE_KEYS
    if unknown:
        errors.append(f"场景包含不允许的字段: {', '.join(sorted(unknown))}")
    sid = str(scene.get("id", ""))
    if not _ID_RE.match(sid):
        errors.append("id 必须是小写字母/数字/连字符（2-40 位）")
    elif existing_ids and sid in existing_ids:
        errors.append(f"id「{sid}」已存在（编辑请用保存，不要重复新建）")
    name = str(scene.get("name", "")).strip()
    if not (1 <= len(name) <= 40):
        errors.append("名称需要 1-40 个字符")
    icon = str(scene.get("icon", "🎬"))
    if len(icon) > 8:
        errors.append("图标只能是一个 emoji")
    items = scene.get("items")
    if not isinstance(items, list):
        errors.append("items 必须是数组")
        return errors
    if len(items) > MAX_ITEMS:
        errors.append(f"条目太多（上限 {MAX_ITEMS}）")
    seen_ref: set[str] = set()
    for i, it in enumerate(items):
        if not isinstance(it, dict) or set(it) - _ITEM_KEYS:
            errors.append(f"第 {i + 1} 条包含未知字段")
            continue
        kind = str(it.get("kind", ""))
        ref = str(it.get("ref", "")).strip()
        if kind not in ITEM_KINDS:
            errors.append(f"第 {i + 1} 条类型不合法（只能是 {'/'.join(ITEM_KINDS)}）")
            continue
        if not (1 <= len(ref) <= 500):
            errors.append(f"第 {i + 1} 条内容为空或过长")
            continue
        if kind == "tool" and not _ID_RE.match(ref):
            errors.append(f"第 {i + 1} 条引用的工具 id 不合法: {ref!r}")
        if kind == "url" and not _URL_RE.match(ref):
            errors.append(f"第 {i + 1} 条网址必须以 http(s):// 开头")
        if (kind, ref) in seen_ref:
            errors.append(f"第 {i + 1} 条与前面的条目重复")
        seen_ref.add((kind, ref))
    return errors


def normalize_scene(scene: dict) -> dict:
    """保存前归一化（只保留白名单字段 + 补默认值）。"""
    return {
        "id": str(scene["id"]),
        "name": str(scene["name"]).strip()[:40],
        "icon": str(scene.get("icon") or "🎬")[:8],
        "items": [{"kind": str(it["kind"]), "ref": str(it["ref"]).strip()}
                  for it in scene.get("items", [])],
        "created_at": scene.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_launched": scene.get("last_launched"),
    }


# ---------------------------------------------------------------------------
# 预检与启动（执行器注入：与 server 现有 launcher/runner/stopper 复用）
# ---------------------------------------------------------------------------

def preview_scene(scene: dict, tools_by_id: dict, *, probe_port=None,
                  plugin_alive=None, expand=None) -> list[dict]:
    """逐项预检：将启动 / 已运行（跳过）/ 失效（原因）。不执行任何东西。

    probe_port(port) -> bool  内置工具在线判定（端口应答）
    plugin_alive(pid) -> bool 插件运行判定（runner 进程句柄）
    """
    expand = expand or (lambda s: os.path.expandvars(os.path.expanduser(s)))
    out: list[dict] = []
    for i, it in enumerate(scene.get("items", [])):
        kind, ref = str(it.get("kind", "")), str(it.get("ref", ""))
        row = {"index": i, "kind": kind, "ref": ref,
               "status": "ok", "reason": "", "label": ""}
        if kind == "tool":
            tool = tools_by_id.get(ref)
            if tool is None:
                row.update(status="invalid", reason="引用的工具不存在（可能已被移除）")
            else:
                row["label"] = tool.name
                running = (plugin_alive(ref) if getattr(tool, "kind", "") == "plugin"
                           else (bool(probe_port(tool.port)) if (probe_port and tool.port) else False))
                if running:
                    row.update(status="skip_running", reason="已在运行——将跳过（不是本次启动的，停止时不会碰它）")
            out.append(row)
            continue
        real = expand(ref)
        row["ref_expanded"] = real
        if kind == "url":
            row["label"] = ref[:60]
        else:
            row["label"] = Path(real).name or real
            if not Path(real).exists():
                row.update(status="invalid",
                           reason="路径不存在（搬家了？编辑场景换一个）")
        out.append(row)
    return out


def launch_scene(scene: dict, tools_by_id: dict, *, launcher=None,
                 runner=None, probe_port=None, plugin_alive=None,
                 start_file=None, expand=None,
                 plugin_manifests: dict | None = None) -> dict:
    """顺序启动。逐项独立（单项失败继续）；返回 {results, session}。

    session 只记录"本会话启动且可可靠识别"的停止句柄：
      tool_port=内置工具端口（启动前离线 → 端口上必是我们的实例）
      plugin=插件 id（runner 进程句柄精确）
    文件夹/文件/网址记 untracked——不提供停止（保守策略，防误杀用户自己的窗口）。
    plugin_manifests = {插件id: manifest}，由调用方（server）用 load_plugin 提供。
    """
    expand = expand or (lambda s: os.path.expandvars(os.path.expanduser(s)))
    start_file = start_file or os.startfile  # noqa: S606  交给系统默认处理程序
    plugin_manifests = plugin_manifests or {}
    results: list[dict] = []
    stoppable: list[dict] = []
    untracked = 0
    for i, it in enumerate(scene.get("items", [])):
        kind, ref = str(it.get("kind", "")), str(it.get("ref", ""))
        row = {"index": i, "kind": kind, "ref": ref, "status": "ok", "detail": ""}
        try:
            if kind == "tool":
                tool = tools_by_id.get(ref)
                if tool is None:
                    raise SceneError("工具不存在（可能已被移除）")
                if getattr(tool, "kind", "") == "plugin":
                    if plugin_alive and plugin_alive(ref):
                        row.update(status="skip", detail="已在运行，跳过")
                    else:
                        manifest = plugin_manifests.get(ref)
                        if manifest is None:
                            raise SceneError("插件清单读不到（可能已被移除）")
                        if runner is not None:
                            runner.launch(manifest)
                        row["detail"] = "已启动"
                        stoppable.append({"type": "plugin", "id": ref})
                else:
                    online = bool(probe_port(tool.port)) if (probe_port and tool.port) else False
                    if online:
                        row.update(status="skip", detail="已在运行，跳过")
                    else:
                        if launcher is not None:
                            launcher.launch(tool, {})
                        row["detail"] = "已拉起（等几秒上线）"
                        stoppable.append({"type": "tool_port", "port": tool.port,
                                          "id": ref})
            elif kind in ("folder", "file", "url"):
                real = expand(ref)
                if kind != "url" and not Path(real).exists():
                    raise SceneError("路径不存在")
                start_file(real)
                row["detail"] = "已交给系统打开"
                untracked += 1
            else:
                raise SceneError(f"未知条目类型: {kind}")
        except SceneError as exc:
            row.update(status="fail", detail=str(exc))
        except Exception as exc:  # noqa: BLE001  单项失败继续（提示词第五节第 5 条）
            detail = str(exc).strip() or type(exc).__name__
            row.update(status="fail", detail=f"启动失败：{detail[:200]}")
        results.append(row)
    return {"results": results,
            "session": {"scene_id": scene.get("id"),
                        "started_at": time.time(),
                        "stoppable": stoppable, "untracked": untracked}}

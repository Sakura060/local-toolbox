"""联邦搜索：一次查询，并行扇出到各工具的搜索 API，结果归一化聚合。

各上游端点（2026-09-27 侦察结论，改动上游时同步这里）：
- 捞图   GET /api/search?q=&limit=    -> {items:[{id,name,mtime,source,snippet,ocr_text,...}]}
- NoteNest GET /api/notes?q=          -> {notes:[{id,title,updated,excerpt}]}
- 户口本 GET /api/lookup?q=&limit=    -> {items:[{path,detail,mtime,exists,...}]}

归一化条目：{title, snippet, url, detail_url, when, extra}
- url        可直接展示的媒体（捞图缩略图，经 /proxy 同源透传）
- detail_url 点开可拉取详情的 JSON 端点（NoteNest 笔记全文预览）
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import proxy
from .registry import Tool, search_tools

FED_TIMEOUT = 2.5


def _norm_laotu(items: list) -> list[dict]:
    out = []
    for it in items:
        snippet = it.get("snippet") or (it.get("ocr_text") or "")[:120]
        thumb = f"/proxy/laotu/thumb/{it['id']}.jpg" if it.get("id") is not None else None
        full = f"/proxy/laotu/img/{it['id']}" if it.get("id") is not None else None
        out.append({
            "title": it.get("name") or "（未命名截图）",
            "snippet": snippet,
            "url": thumb,
            "image_url": full,
            "detail_url": None,
            "when": it.get("mtime"),
            "extra": it.get("source") or it.get("folder") or "",
        })
    return out


def _norm_notenest(notes: list) -> list[dict]:
    return [
        {
            "title": n.get("title") or "（无标题笔记）",
            "snippet": n.get("excerpt") or "",
            "url": None,
            "detail_url": f"/proxy/notenest/api/notes/{n['id']}" if n.get("id") else None,
            "when": n.get("updated"),
            "extra": "",
        }
        for n in notes
    ]


def _norm_hukou(items: list) -> list[dict]:
    out = []
    for it in items:
        exists = it.get("exists")
        extra = "在盘" if exists else ("已不在盘" if exists is False else "")
        out.append({
            "title": it.get("path") or it.get("name") or "",
            "snippet": it.get("detail") or "",
            "url": None,
            "detail_url": None,
            "when": it.get("mtime"),
            "extra": extra,
        })
    return out


_KIND_LABEL = {"text": "文本", "image": "图片", "files": "文件"}


def _norm_clipvault(items: list, q: str, port: int) -> list[dict]:
    """open_url = 工具页深链（?q= 过滤 + focus 定位到条目卡并高亮）。"""
    out = []
    for it in items:
        preview = (it.get("preview") or "").strip()
        kind = it.get("kind") or ""
        eid = it.get("id")
        open_url = None
        if eid is not None:
            open_url = ("http://127.0.0.1:%d/?%s" % (
                port, urllib.parse.urlencode({"q": q, "focus": eid})))
        out.append({
            "title": preview[:60] or "（空内容）",
            "snippet": preview,
            "url": None,
            "detail_url": None,
            "open_url": open_url,
            "when": it.get("ts"),
            "extra": _KIND_LABEL.get(kind, kind),
        })
    return out


def _query_group(tool: Tool, q: str, limit: int) -> dict:
    """查询单个工具，永不抛异常——失败折叠成 ok=False 的组。"""
    group = {"id": tool.id, "name": tool.name, "ok": False, "error": None,
             "count": 0, "items": []}
    if tool.id == "laotu":
        path = "/api/search?" + urllib.parse.urlencode({"q": q, "limit": limit})
        pick, norm = "items", _norm_laotu
    elif tool.id == "notenest":
        path = "/api/notes?" + urllib.parse.urlencode({"q": q})
        pick, norm = "notes", _norm_notenest
    elif tool.id == "hukou":
        path = "/api/lookup?" + urllib.parse.urlencode({"q": q, "limit": limit})
        pick, norm = "items", _norm_hukou
    elif tool.id == "clipvault":
        path = "/api/list?" + urllib.parse.urlencode({"q": q, "limit": limit})
        pick = "entries"
        norm = lambda lst: _norm_clipvault(lst, q, tool.port)  # noqa: E731
    else:  # 注册表里新增 search 工具时必须补分支
        group["error"] = "未实现的搜索适配器"
        return group
    try:
        res = proxy.fetch(tool.port, path, timeout=FED_TIMEOUT)
        if res.status != 200:
            group["error"] = f"上游返回 {res.status}"
            return group
        data = json.loads(res.body.decode("utf-8", "replace"))
        raw = data.get(pick)
        if not isinstance(raw, list):
            group["error"] = "响应结构不符合预期"
            return group
        items = norm(raw)[:limit]  # 上游不限流的端点（NoteNest）在此收敛，防长列表
        group.update(ok=True, items=items, count=len(items))
    except proxy.UpstreamError:
        group["error"] = "离线或无响应"
    except (ValueError, KeyError, TypeError) as exc:
        group["error"] = f"解析失败: {type(exc).__name__}"
    return group


def federated_search(q: str, tools: tuple[Tool, ...] | None = None,
                     limit: int = 12) -> dict:
    """并行扇出搜索。tools 参数供测试注入假端点；q 为空返回空组集合。"""
    q = (q or "").strip()
    tools = tools if tools is not None else search_tools()
    if not q:
        return {"q": "", "groups": [
            {"id": t.id, "name": t.name, "ok": None, "error": None,
             "count": 0, "items": []} for t in tools]}
    with ThreadPoolExecutor(max_workers=max(1, len(tools))) as pool:
        groups = list(pool.map(lambda t: _query_group(t, q, limit), tools))
    return {"q": q, "groups": groups}


def recent(tools: tuple[Tool, ...] | None = None, per_tool: int = 5) -> dict:
    """最近动态：三个上游的空查询本身就是 recent 模式（捞图/户口本 / NoteNest 按 updated）。"""
    tools = tools if tools is not None else search_tools()
    with ThreadPoolExecutor(max_workers=max(1, len(tools))) as pool:
        groups = list(pool.map(lambda t: _query_group(t, "", per_tool), tools))
    for g in groups:
        g["items"] = g["items"][:per_tool]
        g["count"] = len(g["items"])
    return {"groups": groups}


_NOTE_TTL_S = 30.0
_note_cache: dict[tuple[str, int], tuple[float, str]] = {}
_note_lock = threading.Lock()


def _note_laotu(data: dict) -> str | None:
    total, ocr_ok = data.get("total"), data.get("ocr_ok")
    if (isinstance(total, int) and isinstance(ocr_ok, int)
            and total > ocr_ok):
        return f"待索引 {total - ocr_ok} 张 · 在控制台启动一次即自动回填"
    return None


def _note_clipvault(data: dict) -> str | None:
    # 严格 is True：paused 万一以字符串出现不误报（D24 家族教训）
    if data.get("paused") is True:
        return "采集已暂停 · 暂停期间复制不会入库（到 ClipVault 点「恢复采集」）"
    return None


def _note_hukou(data: dict) -> str | None:
    # files_by_vol 为空 = 还没跑过 deep 快照（MFT 谱系链不可用）
    vols = data.get("files_by_vol")
    if isinstance(vols, dict) and not vols:
        return "deep 快照未跑 · 管理员控制台执行一次 cli.py deep --history 后谱系链可用"
    return None


_NOTE_EXTRACTORS = {
    "laotu": _note_laotu,
    "clipvault": _note_clipvault,
    "hukou": _note_hukou,
}


def tool_note(tool: Tool) -> str | None:
    """工具卡"待办徽标"文案：从该工具的 /api/stats 提炼一条行动提示。

    提取器按工具 id 注册（_NOTE_EXTRACTORS），新工具要出徽标就在那里加一个
    纯函数（吃 stats dict，回 str | None）。永不抛异常——上游离线/坏 JSON/
    字段缺失一律返回 None。成功结果缓存 30s（/api/status 每 5s 轮询，不必
    每次都打上游）；失败（None）不缓存，工具状态变化后下一轮即可更新。
    """
    extract = _NOTE_EXTRACTORS.get(tool.id)
    if extract is None:
        return None
    key = (tool.id, tool.port)
    now = time.monotonic()
    with _note_lock:
        hit = _note_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    note: str | None = None
    try:
        res = proxy.fetch(tool.port, "/api/stats", timeout=2.0)
        if res.status == 200:
            data = json.loads(res.body.decode("utf-8", "replace"))
            if isinstance(data, dict):
                note = extract(data)
    except (proxy.UpstreamError, ValueError, TypeError):
        note = None
    if note:
        with _note_lock:
            _note_cache[key] = (time.monotonic() + _NOTE_TTL_S, note)
    return note

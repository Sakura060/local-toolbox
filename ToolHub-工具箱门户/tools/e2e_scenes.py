"""工作场景浏览器 E2E：新建→编辑→预览→启动（真拉番茄钟+无效路径失败项）→停止→删除。

复用 e2e_stage_b 的服务模式：临时插件目录 + 临时 scenes.json（不碰真实 data/）。
"""
import http.client
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT.parent))
sys.path.insert(0, str(PROJECT))

from desktop import server as desktop_server   # noqa: E402
from desktop import plugins as P               # noqa: E402
from desktop import scenes as scenes_mod       # noqa: E402

PORT = 8655
HOST = "127.0.0.1"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))


def api(method, path, payload=None):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=30)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    conn.request(method, path, body=body,
                 headers={"Content-Type": "application/json"} if body else {})
    with conn.getresponse() as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    plugins_dir = Path(tmp.name) / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    scenes_mod.SCENES_PATH = Path(tmp.name) / "scenes.json"   # 隔离：不写真实 data/
    httpd = desktop_server.make_server(
        PORT, tools=None, warnings=[],
        plugin_runner=P.PluginRunner(plugins_dir))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"server on {PORT}")

    from playwright.sync_api import sync_playwright
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        base = f"http://{HOST}:{PORT}"

        # 1. 空状态 + 新建
        page.goto(base + "/#/scenes")
        page.wait_for_selector("#scnew", timeout=15000)         # 空状态页的按钮
        check("scenes empty state", True)
        page.click("#scnew")
        page.wait_for_selector("#sename", timeout=8000)
        page.fill("#sename", "E2E 场景")
        page.select_option("#setool", "pomodoro")               # 番茄钟（apps 真工具）
        page.click("#seaddtool")
        page.select_option("#sekind", "folder")
        page.fill("#seref", r"D:\不存在-e2e-xyz")
        page.click("#seaddref")
        page.wait_for_timeout(300)
        print("  [diag] after addref: rows=",
              page.locator("#seitems .paramrow").count(),
              "| toast:", page.locator("#toast").inner_text(),
              "| seref:", page.locator("#seref").input_value())
        check("editor lists 2 items",
              page.locator("#seitems .paramrow").count() == 2)
        page.click("#sesave")
        page.wait_for_timeout(1500)
        print("  [diag] toast:", page.locator("#toast").inner_text(),
              "| url:", page.url)
        page.wait_for_selector("[data-sclaunch]", timeout=10000)
        check("scene card appears", page.locator("[data-sclaunch]").count() == 1)

        # 2. 启动流：预览 → 确认 → 结果
        page.click("[data-sclaunch]")
        page.wait_for_selector("[data-r='1']", timeout=8000)
        prev_text = page.locator("body").inner_text()
        check("preview shows invalid folder", "失效" in prev_text, prev_text[:80])
        check("preview shows will-launch tool", "将启动" in prev_text)
        page.click("[data-r='1']")
        page.wait_for_selector("[data-scstop]", timeout=20000)  # 结果面板
        body = page.locator("body").inner_text()
        # 真机上 8661 可能已有番茄钟在线 → 走"已在运行，跳过"分支（同样是成功处理）；
        # 离线则"已拉起"。两种都算通过——分支行为都有单测覆盖。
        ok_item = ("已拉起" in body or "已启动" in body or "已在运行，跳过" in body)
        check("result handles tool item (start or skip)", ok_item, body[-160:])
        check("result has failed item", "路径不存在" in body)
        skipped = "已在运行，跳过" in body
        session = page.evaluate("window.__lastSceneSession || null")
        # 3. 停止本次启动的工具，断言代码契约（不依赖外部进程存活状态）：
        #    skip 分支 → stopped 必须为空（绝不碰不是本会话启动的实例）
        #    启动分支 → stopped 含 pomodoro
        page.click("[data-scstop]")
        page.wait_for_timeout(1200)
        stopres = page.evaluate("window.__lastSceneStop || null")
        check("scene stop endpoint responded", stopres and stopres.get("ok") is True,
              str(stopres)[:120])
        if skipped:
            check("stop list empty when tool was skipped (user's instance untouched)",
                  stopres and stopres.get("stopped") == [], str(stopres))
        else:
            check("stop list contains scene-started tool",
                  stopres and "pomodoro" in (stopres.get("stopped") or []), str(stopres))

        # 4. 删除场景 → 回到空状态
        page.click("[data-scdel]")
        page.wait_for_selector("[data-r='1']", timeout=8000)    # uiConfirm 确认
        page.click("[data-r='1']")
        page.wait_for_selector("#scnew", timeout=8000)
        check("scene deleted back to empty", True)

        js_errors = [e for e in errors if "favicon" not in e.lower()]
        check("no js errors", not js_errors, "; ".join(js_errors[:3]))
        browser.close()

    httpd.shutdown()
    httpd.server_close()
    tmp.cleanup()
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

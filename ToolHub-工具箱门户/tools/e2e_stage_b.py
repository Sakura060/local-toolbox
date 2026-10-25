"""阶段 B 浏览器 E2E：拖放区/批量扫描/开始菜单/参数模板全链路抽检。

起临时桌面服务（8651，插件目录=临时目录，不碰真实 data/plugins），
Playwright + 系统 Edge 无头验证：
  1. 首页加载无 JS 错误
  2. 向导第 1 步：dropzone + 批量入口渲染；probe(python.exe)→第 3 步占位符按钮可插入
  3. #/scan：临时目录扫描→黑名单过滤→导入→补说明→插件列表出现
  4. #/startmenu：开始菜单列表渲染
  5. 参数模板启动：带 {arg:} 的插件点启动→页内输入表单→填参→启动成功
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

from desktop import server as desktop_server          # noqa: E402
from desktop import plugins as P                       # noqa: E402

PORT = 8651
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
    httpd = desktop_server.make_server(
        PORT, tools=None, warnings=[],
        plugin_runner=P.PluginRunner(plugins_dir))
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    print(f"server on {PORT}, plugins dir = {plugins_dir}")

    # 造一个"绿色软件目录"：1 个正常 exe + 1 个卸载器 + 子目录 1 个
    scan_dir = Path(tmp.name) / "Tools"
    (scan_dir / "sub").mkdir(parents=True, exist_ok=True)
    (scan_dir / "GoodTool.exe").write_bytes(b"MZ" + b"x" * 1000)
    (scan_dir / "unins000.exe").write_bytes(b"MZ" + b"x" * 10)
    (scan_dir / "sub" / "Nested.exe").write_bytes(b"MZ" + b"x" * 100)

    from playwright.sync_api import sync_playwright
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        base = f"http://{HOST}:{PORT}"

        # 1. 首页
        page.goto(base + "/")
        page.wait_for_selector(".card", timeout=15000)
        check("home renders", page.locator(".card").count() >= 10)

        # 2. 向导第 1 步
        page.goto(base + "/#/import")
        page.wait_for_selector("#wdrop", timeout=8000)
        check("wizard dropzone", page.locator("#wdrop").count() == 1)
        check("wizard batch entries", page.locator("#wstartmenu").count() == 1
              and page.locator("#wscan").count() == 1)

        # probe python.exe → 第 2 步（识别卡）→ 下一步 → 第 3 步参数模板
        # 先直接打一次 API 预热（probe 内部要起 PowerShell 拿版本信息，冷启动可能 20s+）
        api("POST", "/api/plugins/probe", {"path": sys.executable})
        page.fill("#wpath", sys.executable)
        page.click("#wprobe")
        page.wait_for_function("window.W && W.step === 2", timeout=30000)
        page.click("text=下一步：确认启动方式 →")
        page.wait_for_function("window.W && W.step === 3", timeout=8000)
        page.check("#wconfirm")
        page.click('[data-ph="{file}"]')
        args_val = page.locator("#wargs").input_value()
        check("step3 placeholder button inserts", "{file}" in args_val, args_val)
        check("step3 accept select", page.locator("#waccept").count() == 1)
        page.select_option("#waccept", "file")

        # 3. 开始菜单页
        page.goto(base + "/#/startmenu")
        page.wait_for_selector("#smq", timeout=15000)
        page.wait_for_timeout(1500)     # 等 fetch
        rows = page.locator("[data-smpath]").count()
        check("startmenu lists lnks", rows >= 5, f"{rows} rows")

        # 4. 批量扫描
        page.goto(base + "/#/scan")
        page.fill("#scandir", str(scan_dir))
        page.click("#scango")
        page.wait_for_selector("[data-idx]", timeout=20000)
        n = page.locator("[data-idx]").count()
        check("scan lists 3 items", n == 3, f"{n} items")
        unchecked = sum(1 for i in range(n)
                        if not page.locator(f"[data-idx='{i}']").is_checked())
        check("blocked unchecked by default", unchecked == 1, f"{unchecked} unchecked")
        page.click("#scanimport")
        page.wait_for_selector("[data-desc-id]", timeout=90000)
        ok_n = page.locator("[data-desc-id]").count()
        check("scan imported ok items", ok_n == 2, f"{ok_n} imported")
        page.fill("[data-desc-id]", "E2E 导入的测试工具")     # 第一个输入框
        page.click("#scansave")
        page.wait_for_url("**/#/plugins", timeout=15000)
        page.wait_for_selector(".plugrow", timeout=10000)
        check("plugins list after scan", page.locator(".plugrow").count() >= 2)
        body_text = page.locator("#pluglist").inner_text()
        check("kind label is chinese", "程序" in body_text)

        # 5. 参数模板启动表单：先 API commit 一个带 {arg:} 的脚本插件
        script_path = Path(tmp.name) / "shout.py"
        script_path.write_text(
            "import sys\nprint('GOT', sys.argv[1])\n", encoding="utf-8")
        probe = api("POST", "/api/plugins/probe", {"path": str(script_path)})
        if probe.get("ok"):
            m = probe["manifest"]
            m["launch"]["args"] = ["{arg:喊话}"]
            m["input"] = {"accept": "text", "via": "argument"}
            m["desc"] = "E2E 参数模板插件"
            r2 = api("POST", "/api/plugins/commit",
                     {"manifest": m, "confirmed_launch": True,
                      "icon_tmp": probe.get("icon_tmp") or "",
                      "allow_duplicate": True})
            check("commit arg-template plugin", r2.get("ok") is True, str(r2))
            pid = r2.get("id")
            page.reload()
            page.wait_for_selector(".plugrow", timeout=10000)
            btn = page.locator(f'.plugrow[data-pid="{pid}"] [data-act="launch"]')
            btn.click()
            page.wait_for_selector("[data-arg]", timeout=10000)
            check("inputs form appears", True)
            page.fill("[data-arg]", "你好世界")
            page.locator('[data-r="1"]').click()
            deadline = time.time() + 20
            got = ""
            while time.time() < deadline:
                log = api("GET", f"/api/plugins/{pid}/log").get("log", "")
                if "GOT" in log:
                    got = log
                    break
                time.sleep(0.5)
            check("arg-template run stdout", "GOT 你好世界" in got, got[-120:])
        else:
            check("commit arg-template plugin", False, str(probe.get("error")))

        # 6. 清单分享（阶段 C 一期）：导出 → 粘贴导入 → 预览 → 确认改名导入
        share_pid = None
        page.goto(base + "/#/plugins")
        page.wait_for_selector(".plugrow", timeout=10000)
        export_btn = page.locator("#shareexport")
        if export_btn.count() == 1:
            # 先造一个可分享插件（API），再走 UI 导出拦截下载
            r_exp = api("POST", "/api/plugins/export", {"ids": []})   # 空 ids 应报错
            check("export rejects empty ids", r_exp.get("ok") is False)
            # 用之前扫描导入的第一个插件做导出源
            list_all = api("GET", "/api/plugins")
            candidates = [p["id"] for p in list_all.get("plugins", []) if p["state"] != "draft"]
            if candidates:
                r_text = api("POST", "/api/plugins/export", {"ids": candidates[:1]})
                check("export via api", r_text.get("ok") is True, str(r_text.get("error")))
                if r_text.get("ok"):
                    text = r_text["text"]
                    r_prev = api("POST", "/api/plugins/import-preview", {"text": text})
                    item0 = (r_prev.get("items") or [{}])[0]
                    check("import-preview flags id_taken on same machine",
                          (item0.get("conflict") or {}).get("type") == "id_taken",
                          str(item0.get("conflict")))
                    r_imp = api("POST", "/api/plugins/import",
                                {"items": [item0["manifest"]],
                                 "rename": {item0["manifest"]["id"]: "e2e-shared"}})
                    ok_imp = r_imp.get("ok") and r_imp["results"][0]["ok"]
                    check("import with rename", ok_imp is True, str(r_imp)[:120])
                    share_pid = "e2e-shared"
            else:
                check("export via api", False, "no plugin to export")
        else:
            check("share buttons on plugins page", False, "shareexport button missing")

        # UI 冒烟：导入分享对话框能打开并预检坏 JSON
        if page.locator("#shareimport").count() == 1:
            page.click("#shareimport")
            page.wait_for_selector("#sharetext", timeout=8000)
            page.fill("#sharetext", "{broken json")
            page.click('[data-next="1"]')
            page.wait_for_timeout(600)
            toast_txt = page.locator("#toast").inner_text()
            check("import dialog rejects broken json", "JSON" in toast_txt, toast_txt)
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        else:
            check("import dialog opens", False, "shareimport missing")

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

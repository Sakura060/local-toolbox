"""ClipVault 深链 E2E（无头 Edge，8644 种子实例）。

验证：?q=&focus= 深链 → 搜索框预填、条目卡滚入视野并高亮 flash；
focus 不存在 → toast 明确提示；无参数 → 无 flash。
红线：全程不点任何「回贴」按钮（D24）。
"""

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8644"
FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" | {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", headless=True)
    pg = b.new_page(viewport={"width": 1100, "height": 800})

    # 1) 深链：过滤词 + 焦点条目
    pg.goto(f"{BASE}/?q=meeting&focus=2")
    pg.wait_for_selector("#entry-2", timeout=5000)
    check("搜索框预填 q", pg.input_value("#q") == "meeting", pg.input_value("#q"))
    check("焦点条目在结果中", pg.locator("#entry-2").count() == 1)
    check("结果被 q 过滤（只有 1 条）", pg.locator(".entry").count() == 1)
    check("条目卡带 flash 高亮", "flash" in (pg.get_attribute("#entry-2", "class") or ""))
    box = pg.locator("#entry-2").bounding_box()
    check("条目卡滚入视野", box is not None and 0 <= box["y"] <= 800 - box["height"], str(box))
    check("flash 动画已注入 CSS", pg.evaluate(
        "getComputedStyle(document.getElementById('entry-2')).animationName") == "flashbg")

    # 2) 深链 focus 不存在 → toast 提示（且 q 过滤仍生效）
    pg.goto(f"{BASE}/?q=一带一路&focus=999")
    pg.wait_for_selector("#toast.show", timeout=5000)
    check("缺条目 toast 提示", "不在当前结果里" in pg.text_content("#toast"),
          pg.text_content("#toast"))
    check("toast 有内容可读", pg.evaluate(
        "document.getElementById('toast').classList.contains('show')"))

    # 3) 图片条目深链：缩略图 + flash
    pg.goto(f"{BASE}/?focus=4")
    pg.wait_for_selector("#entry-4", timeout=5000)
    check("图片条目 flash", "flash" in (pg.get_attribute("#entry-4", "class") or ""))
    check("搜索框未被污染", pg.input_value("#q") == "")

    # 4) 无参数普通加载：无 flash、无 toast
    pg.goto(f"{BASE}/")
    pg.wait_for_selector(".entry", timeout=5000)
    pg.wait_for_timeout(400)
    check("普通加载无 flash", pg.evaluate(
        "[...document.querySelectorAll('.entry.flash')].length") == 0)
    check("普通加载无 toast", pg.evaluate(
        "!document.getElementById('toast').classList.contains('show')"))
    check("4 条种子全渲染", pg.locator(".entry").count() == 4)

    pg.goto(f"{BASE}/?q=meeting&focus=2")
    pg.wait_for_selector("#entry-2", timeout=5000)
    pg.screenshot(path="data/preview_tmp/deeplink_light.png")
    print("screenshot saved")

    b.close()

print("ALL_OK" if not FAILS else f"FAILED: {FAILS}")

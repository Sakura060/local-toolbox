"""ToolHub 联邦深链 E2E（无头 Edge，8645 预览 → 8644 种子库）。

验证：搜索后 clipvault 组条目带 data-act="tool"；点击弹新标签；
新标签 URL = 深链（?q=&focus=）；落点页条目卡高亮。
"""

import urllib.parse

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8645"
Q = "meeting"
EXPECT_HREF = "http://127.0.0.1:8644/?" + urllib.parse.urlencode(
    {"q": Q, "focus": 2})
FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" | {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="msedge", headless=True)
    ctx = b.new_context(viewport={"width": 1200, "height": 850})
    pg = ctx.new_page()
    pg.goto(BASE)
    pg.wait_for_selector("#q", timeout=5000)

    pg.fill("#q", Q)
    # 联邦结果异步扇出（有 debounce），轮询等 clipvault 条目出现
    # 注意：最近动态面板也用 itemHtml 渲染 tool 动作（空 q）→ 必须 #results 作用域限定
    pg.wait_for_selector('#results [data-act="tool"]', timeout=10000)
    items = pg.locator('#results [data-act="tool"]')
    n = items.count()
    check("clipvault 条目出现且带 tool 动作", n >= 1, f"count={n}")
    href = items.first.get_attribute("data-href")
    check("href 是深链", href == EXPECT_HREF, f"{href!r} vs {EXPECT_HREF!r}")
    check("最近动态面板的深链 q 为空（recent 语义）",
          pg.locator('#recent [data-act="tool"]').count() >= 0)

    with ctx.expect_page(timeout=8000) as pop_info:
        items.first.click()
    pop = pop_info.value
    pop.wait_for_load_state("domcontentloaded")
    check("新标签 URL=深链", pop.url == EXPECT_HREF, pop.url)
    pop.wait_for_selector("#entry-2", timeout=5000)
    check("落点条目高亮 flash",
          "flash" in (pop.get_attribute("#entry-2", "class") or ""))

    # Ctrl+K 命令面板里的联邦结果也应带 tool 动作（同一 itemHtml 渲染）
    pg.keyboard.press("Control+k")
    pg.wait_for_selector("#pinput", timeout=5000)
    pg.fill("#pinput", Q)
    pg.wait_for_selector('#plist [data-act="tool"]', timeout=8000)
    check("命令面板同样出 tool 动作",
          pg.locator('#plist [data-act="tool"]').count() >= 1)
    pg.screenshot(path="data/preview/federated_deeplink.png")
    print("screenshot saved")

    b.close()

print("ALL_OK" if not FAILS else f"FAILED: {FAILS}")

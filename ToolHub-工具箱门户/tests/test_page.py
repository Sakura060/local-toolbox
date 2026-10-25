"""page：前端资产完整性 —— 纯离线、关键元素齐全、动态值都过 esc、v0.5 交互件。"""

import re
import unittest
from pathlib import Path

PAGE = Path(__file__).resolve().parent.parent / "toolhub" / "page.html"


class TestPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def test_key_elements_present(self):
        for needle in ('id="q"', 'id="grid"', 'id="results"', 'id="modal"',
                       'id="toast"', 'id="netstat"', 'id="recent"',
                       'id="chips"', 'id="hist"', 'id="themebtn"', 'id="qclear"'):
            self.assertIn(needle, self.html)

    def test_no_external_assets(self):
        # 页面必须完全离线可用：不许引用外部 http(s) 资源
        externals = re.findall(r'(?:src|href)\s*=\s*["\']https?://', self.html)
        self.assertEqual(externals, [])

    def test_search_event_and_enter_fallback(self):
        # D15 教训：合成 Enter 不派发 keydown，search 事件 + keydown 双通道
        self.assertIn('addEventListener("search"', self.html)
        self.assertIn('"Enter"', self.html)

    def test_all_innerhtml_writes_use_esc(self):
        writes = self.html.count("innerHTML")
        self.assertGreater(writes, 0)
        self.assertGreaterEqual(self.html.count("esc("), writes)

    def test_launch_posts_json(self):
        self.assertIn('"/api/launch"', self.html)
        self.assertIn("JSON.stringify", self.html)

    def test_status_polling(self):
        self.assertIn("/api/status", self.html)
        self.assertIn("setInterval", self.html)

    def test_recent_panel_and_iframe_preview(self):
        self.assertIn("/api/recent", self.html)
        self.assertIn("loadRecent", self.html)
        self.assertIn("openFrame", self.html)
        self.assertIn("<iframe", self.html)
        # 关闭弹窗必须摘掉 iframe/大图，停掉内嵌页
        self.assertIn('querySelector("iframe, img")', self.html)

    def test_todo_badge_renders_note(self):
        self.assertIn("st.note", self.html)
        self.assertIn('class="todo"', self.html)

    def test_render_dedup_and_launch_debounce(self):
        self.assertIn("statusSig", self.html)
        self.assertIn("lastSig", self.html)
        self.assertIn("data-ms", self.html)
        self.assertIn("启动中…", self.html)

    def test_stop_button_and_endpoint(self):
        self.assertIn('"/api/stop"', self.html)
        self.assertIn("stopTool", self.html)
        self.assertIn("data-stop", self.html)
        self.assertIn("强制结束", self.html)  # 停止前确认
        self.assertIn('class="btn danger"', self.html)
        # 在线才给停止、离线才给启动
        self.assertIn("(online && t.bat)", self.html)
        self.assertIn("(!online && t.bat)", self.html)
        # 面板里也有停止动作
        self.assertIn("⏹️", self.html)

    def test_recent_reloads_when_cleared(self):
        # input 清空恢复最近动态时必须重新拉取
        self.assertIn("loadRecent();  // 恢复显示时刷新", self.html)

    def test_a11y_and_favicon(self):
        self.assertIn('aria-label="跨工具搜索"', self.html)
        self.assertIn('rel="icon"', self.html)

    def test_theme_toggle(self):
        self.assertIn("toolhub-theme", self.html)
        self.assertIn("prefers-color-scheme", self.html)
        self.assertIn("cycleTheme", self.html)
        self.assertIn("html.dark", self.html)

    def test_live_search_debounce(self):
        self.assertIn("searchTimer", self.html)
        self.assertIn(", 300)", self.html)
        self.assertIn("searchSeq", self.html)  # 作废在途请求

    def test_search_history(self):
        self.assertIn("toolhub-history", self.html)
        self.assertIn("pushHistory", self.html)
        self.assertIn("renderHistory", self.html)

    def test_filter_chips(self):
        self.assertIn("setFilter", self.html)
        self.assertIn('id="chips"', self.html)

    def test_image_preview(self):
        self.assertIn("openImage", self.html)
        self.assertIn('data-act="' , self.html)
        self.assertIn("image_url", self.html)
        self.assertIn('class="bigimg"', self.html)

    def test_clipvault_deep_link_action(self):
        # v0.9/v0.2 联动：open_url 条目 = 第三类动作 tool，新标签打开工具页深链
        self.assertIn('"tool"', self.html)
        self.assertIn("open_url", self.html)
        self.assertIn('window.open(href, "_blank")', self.html)

    def test_markdown_preview(self):
        self.assertIn("mdRender", self.html)
        self.assertIn('class="md"', self.html)
        # 先转义再结构替换（安全顺序）：mdRender 内部第一处正文处理必须是 esc(
        body = self.html[self.html.index("function mdRender"):]
        self.assertIn("esc(src)", body[:300])

    def test_relative_time(self):
        self.assertIn("relTime", self.html)

    def test_keyboard_shortcuts(self):
        self.assertIn('e.key === "/"', self.html)
        self.assertIn('"Escape"', self.html)

    def test_tool_icons(self):
        self.assertIn("t.icon", self.html)

    def test_skeleton_loading(self):
        self.assertIn('class="skel"', self.html)

    def test_cursor_follow_and_ripple(self):
        # v0.8：粒子拖尾换成 dot+ring 跟随 + 点击涟漪（搜索确认的简洁风最佳实践）
        self.assertIn("initCursorFollower", self.html)
        self.assertIn('id = "cdot"', self.html)
        self.assertIn('id = "cring"', self.html)
        self.assertIn('r.className = "ripple"', self.html)
        self.assertIn(".ripple {", self.html)
        self.assertIn("pointermove", self.html)
        self.assertIn("pointerdown", self.html)
        self.assertIn("0.14", self.html)  # ring lerp 系数
        self.assertIn("scale(", self.html)  # hover 放大
        # 尊重系统"减少动态效果"/触屏
        self.assertIn("prefers-reduced-motion", self.html)
        self.assertIn("(hover: none)", self.html)
        self.assertNotIn("initTrail", self.html)  # 旧粒子拖尾已移除
        self.assertNotIn('id="trail"', self.html)

    def test_command_palette(self):
        self.assertIn('id="pal"', self.html)
        self.assertIn('id="pinput"', self.html)
        self.assertIn("palOpen", self.html)
        self.assertIn("palFilter", self.html)
        self.assertIn("palMove", self.html)
        # Ctrl+K 打开 / Esc 优先关面板
        self.assertIn('String(e.key).toLowerCase() === "k"', self.html)
        self.assertIn("ArrowDown", self.html)
        self.assertIn('data-f', self.html)  # 过滤器仍在

    def test_no_dark_trap(self):
        # D13 复盘教训：两套主题都给显式配色，避免暗色下深底深字
        self.assertIn("color-scheme: light", self.html)
        self.assertIn("color-scheme: dark", self.html)


if __name__ == "__main__":
    unittest.main()

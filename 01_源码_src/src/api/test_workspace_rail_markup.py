"""P1-4：左侧栏快捷区与「显示与主题」弹层的静态契约。

为什么用静态契约测试：主题卡、滑杆、动效开关都是纯前端行为，
单元测试跑不到；但“元素被搬到别处但旧的 JS 绑定还指向原处”
这类回归一旦发生，页面会在真实浏览器里静默失效。
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


class TestRailQuickActions:
    @classmethod
    def setup_class(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "assets" / "app.js").read_text(encoding="utf-8")
        cls.css = (WEB / "assets" / "app.css").read_text(encoding="utf-8")

    def test_five_quick_buttons_exist(self):
        block = self.html[self.html.index('<div class="rail-quick"'):]
        block = block[: block.index("</div>\n    <!-- P1-4: 主题")]
        for button_id in ("guideRailBtn", "ctxRailBtn", "memoryRailBtn", "mascotRailBtn", "displayRailBtn"):
            assert f'id="{button_id}"' in block, button_id
        assert block.count("<svg") >= 5

    def test_display_menu_holds_theme_mascot_motion(self):
        start = self.html.index('<div class="display-menu"')
        block = self.html[start:]
        block = block[: block.index("\n    </div>", block.index('id="reduceMotionToggle"'))]
        assert 'id="themePicker"' in block
        assert block.count('class="theme-card"') == 3
        assert 'id="mascotToggleBtn"' in block
        assert 'id="mascotScale"' in block
        assert 'id="reduceMotionToggle"' in block

    def test_account_menu_is_slimmed(self):
        start = self.html.index('<div class="account-menu" id="accountMenu"')
        block = self.html[start: self.html.index('id="logoutBtn"', start)]
        assert "theme-card" not in block
        assert 'id="mascotScale"' not in block
        assert 'id="accountMenuName"' in block

    def test_js_wires_new_controls(self):
        for fn in ("function toggleDisplayMenu(", "function setDisplayMenuOpen(",
                   "function setReduceMotion(", "function toggleMascotVisibility(",
                   "function syncMascotRailButton("):
            assert fn in self.js, fn
        assert "data-reduce-motion" in self.js

    def test_css_defines_overlay_and_motion_off(self):
        assert ".display-menu {" in self.css
        assert 'html[data-reduce-motion="1"] *' in self.css
        assert ".rail-quick {" in self.css
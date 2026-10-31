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

    def test_four_quick_buttons_exist(self):
        # 本轮契约：左栏 4 项固定顺序 指南→记忆→宠物→主题；上下文入口改由圆形按钮承载
        ids = re.findall(r'id="(\w+RailBtn)"', self.html)
        expect = ["guideRailBtn", "memoryRailBtn", "petRailBtn", "themeRailBtn"]
        order = [i for i in ids if i in expect]
        assert order == expect, ids
        assert "ctxRailBtn" not in ids
        assert 'id="ctxCircleBtn"' in self.html
        assert self.html.count("<svg") >= 4

    def test_theme_menu_holds_theme_and_motion_only(self):
        start = self.html.index('id="themeMenu"')
        block = self.html[start: self.html.index('<div class="sidebar-account"', start)]
        assert 'id="themePicker"' in block
        assert block.count('class="theme-card"') == 3
        assert 'id="reduceMotionToggle"' in block
        # 吉祥物控件已迁到宠物菜单
        assert 'id="mascotScale"' not in block
        assert 'id="petScaleRange"' not in block

    def test_pet_menu_holds_mascot_controls(self):
        start = self.html.index('id="petMenu"')
        block = self.html[start: self.html.index('id="themeMenu"', start)]
        for need in ('id="petToggleBtn"', 'id="petScaleRange"', 'id="petMotionToggle"',
                     'id="petToggleText"', 'id="petScaleValue"'):
            assert need in block, need
        assert 'data-theme-pick' not in block

    def test_account_menu_is_slimmed(self):
        start = self.html.index('<div class="account-menu" id="accountMenu"')
        block = self.html[start: self.html.index('id="logoutBtn"', start)]
        assert "theme-card" not in block
        assert 'id="mascotScale"' not in block
        assert 'id="accountMenuName"' in block

    def test_js_wires_new_controls(self):
        for fn in ("function toggleThemeMenu(", "function togglePetMenu(",
                   "function setRailMenuOpen(", "function setReduceMotion(",
                   "function toggleMascotVisibility(", "function syncMascotRailButton(",
                   "function onPetScaleInput("):
            assert fn in self.js, fn
        assert "data-reduce-motion" in self.js

    def test_css_defines_overlay_and_motion_off(self):
        assert ".display-menu {" in self.css
        assert 'html[data-reduce-motion="1"] *' in self.css
        assert ".rail-quick {" in self.css


class TestSidebarResizeAndMotion:
    @classmethod
    def setup_class(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "assets" / "app.js").read_text(encoding="utf-8")
        cls.css = (WEB / "assets" / "app.css").read_text(encoding="utf-8")

    def test_resizer_element_and_aria(self):
        assert 'id="sidebarResizer"' in self.html
        assert 'role="separator"' in self.html
        assert 'tabindex="0"' in self.html

    def test_width_is_driven_by_css_variable(self):
        assert "--sidebar-w" in self.css
        assert "applySidebarWidth" in self.js
        assert "citrus_sidebar_width" in self.js
        assert "SIDEBAR_MIN = 240" in self.js and "SIDEBAR_MAX = 480" in self.js

    def test_collapse_transition_on_wide_screens(self):
        assert "@media (min-width: 1025px)" in self.css
        assert ".main-container.sidebar-hidden .sidebar {" in self.css

    def test_caret_is_css_only_and_rotates_when_expanded(self):
        assert ".sidebar-header::before {" in self.css
        assert ".sidebar-section.expanded > .sidebar-header::before { transform: rotate(90deg)" in self.css
        # 不允许再回退到 JS 注入的 chevron（会在动态渲染的分组上漏掉）
        assert ".sidebar-caret" not in self.css

    def test_motion_tokens_and_focus_ring(self):
        for token in ("--motion-fast: .15s", "--motion-base: .2s", "--motion-slow: .28s"):
            assert token in self.css
        assert ".rail-button:focus-visible" in self.css

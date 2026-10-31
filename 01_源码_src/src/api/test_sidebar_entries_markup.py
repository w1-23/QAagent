"""左栏入口重组 + 宠物/主题弹层 + 请求快照表格的静态接线测试。

只做静态断言，防止改别处时把这些接线悄悄改掉；真实交互由浏览器探针覆盖
（`_scratch/p5*.js`，记录见 MASTER_AUDIT §22）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def test_rail_has_four_entries_in_fixed_order() -> None:
    html = _read("index.html")
    block = re.search(r'<div class="rail-quick"[^>]*>(.*?)</div>', html, re.S)
    assert block, "找不到 .rail-quick 容器"
    ids = re.findall(r'id="(\w*RailBtn)"', block.group(1))
    assert ids == ["guideRailBtn", "memoryRailBtn", "petRailBtn", "themeRailBtn"], ids


def test_context_rail_button_removed_but_drawer_kept() -> None:
    html = _read("index.html")
    assert 'id="ctxRailBtn"' not in html, "侧栏上下文概览入口应已删除"
    assert 'id="ctxCircleBtn"' in html, "圆形上下文概览入口必须保留"
    assert 'id="ctxDrawer"' in html or 'ctx-drawer' in html, "上下文抽屉必须保留"


def test_pet_and_theme_are_separate_menus() -> None:
    html = _read("index.html")
    assert 'id="petMenu"' in html and 'id="themeMenu"' in html
    assert 'id="petToggleBtn"' in html and 'id="petScaleRange"' in html
    assert 'id="petMotionToggle"' in html and 'id="petToggleText"' in html
    # 主题菜单里不再有吉祥物控件（彻底分离）
    theme = html.split('id="themeMenu"', 1)[1]
    theme = theme.split('</div>\n    </div>', 1)[0]
    assert 'mascotScale' not in theme, "主题菜单不应再包含吉祥物尺寸控件"
    assert 'data-theme-pick' in theme
    assert 'id="reduceMotionToggle"' in theme


def test_pet_menu_reuses_rail_menu_styles() -> None:
    html = _read("index.html")
    assert 'class="display-menu pet-menu"' in html
    assert 'class="display-menu theme-menu"' in html


def test_menu_helpers_exist_in_app_js() -> None:
    js = _read("assets/app.js")
    for name in (
        "function togglePetMenu(",
        "function toggleThemeMenu(",
        "function setRailMenuOpen(",
        "function syncPetPanel(",
        "function onPetScaleInput(",
        "function resetPetScale(",
        "function resetPetPosition(",
        "function setPetMotion(",
    ):
        assert name in js, f"缺少 {name}"


def test_loading_sprout_wired() -> None:
    html = _read("index.html")
    assert "loading-sprout.css" in html
    assert 'id="loadSproutTpl"' in html
    js = _read("assets/app.js")
    assert "function _createLoadSprout(" in js
    assert "function _hideLoadSprout(" in js
    assert "小橘正在回答您的问题..." in js
    assert "loadSproutRow" in js


def test_sent_context_table_contract() -> None:
    js = _read("assets/app.js")
    for token in (
        "function _sentTok(",
        "function _sentPreview(",
        "function focusSentRow(",
        "function copySentSnapshot(",
        "function labelSentSnapshot(",
        "ctx-sent-c-prev",
        "ctx-sent-c-tok",
        "ctx-sent-c-act",
    ):
        assert token in js, f"请求快照缺少 {token}"
    css = _read("assets/app.css")
    assert ".ctx-sent-row" in css and "grid-template-columns" in css


def test_sent_context_hidden_for_non_admin() -> None:
    js = _read("assets/app.js")
    assert "function _isAdminAccount(" in js
    assert "ctxSentBlock" in js
    html = _read("index.html")
    assert 'id="ctxSentBlock"' in html


def test_preview_truncated_to_55_chars() -> None:
    js = _read("assets/app.js")
    m = re.search(r"function _sentPreview\(text\) \{(.*?)\n\}", js, re.S)
    assert m, "找不到 _sentPreview"
    assert "55" in m.group(1), "预览必须截断到 55 字符"

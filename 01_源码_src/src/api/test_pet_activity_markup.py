"""宠物「活跃程度」三档的标记与脚本契约（Phase P1-C 补充）。

用户要求：宠物设置里加「活跃程度：安静 15–25s / 自然 6–12s / 活泼 4–8s」三档，
默认「自然」，且与 mascot.js 的 setActivityLevel 打通、可持久化。
"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


def _pet_menu_html() -> str:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    start = html.index('id="petMenu"')
    end = html.index('id="themeMenu"', start)
    return html[start:end]


def test_pet_menu_has_three_activity_options():
    block = _pet_menu_html()
    levels = re.findall(r'data-pet-activity="(\w+)"', block)
    assert levels == ["quiet", "natural", "lively"]
    for label in ("安静", "自然", "活泼"):
        assert label in block


def _button_for(block: str, level: str) -> str:
    """截取某个档位按钮的完整起始标签（属性跨行）。"""
    at = block.index('data-pet-activity="%s"' % level)
    start = block.rindex("<button", 0, at)
    end = block.index("</button>", at)
    return block[start:end]


def test_natural_is_default_and_marked_checked():
    block = _pet_menu_html()
    natural = _button_for(block, "natural")
    assert "is-on" in natural
    assert 'aria-checked="true"' in natural
    for other in ("quiet", "lively"):
        assert 'aria-checked="false"' in _button_for(block, other)


def test_activity_group_is_radiogroup_with_titles():
    block = _pet_menu_html()
    assert 'role="radiogroup"' in block
    for hint in ("15-25", "6-12", "4-8"):
        assert hint in block


def test_app_js_wires_activity_level():
    js = (WEB / "assets" / "app.js").read_text(encoding="utf-8")
    assert "function setPetActivity(level)" in js
    assert "function syncPetActivity()" in js
    assert "CitrusMascot.setActivityLevel(level)" in js
    assert "citrus_mascot_activity" in js
    # 面板同步时必须刷新活跃程度选中态
    panel = js[js.index("function syncPetPanel()"):]
    panel = panel[:panel.index("function onPetScaleInput")]
    assert "syncPetActivity();" in panel


def test_asset_versions_bumped():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "app.js?v=28" in html
    assert "app.css?v=22" in html

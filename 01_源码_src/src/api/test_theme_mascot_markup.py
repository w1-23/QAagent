"""C/D 前端契约测试：三套主题 + 吉祥物状态机。

守的是“设计稿要求”本身，不是实现细节：
- 主题只换背景与色彩变量，三套主题都在 CSS 里有变量与背景图；
- 吉祥物 8 态在 CSS 与 JS 里都有对应，且素材文件真实存在；
- 页面把 mascot.css / mascot.js 接进去了，DOM 挂在输入框内。
"""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
APP_CSS = WEB / "assets" / "app.css"
MASCOT_CSS = WEB / "assets" / "mascot.css"
MASCOT_JS = WEB / "assets" / "mascot.js"
APP_JS = WEB / "assets" / "app.js"
INDEX = WEB / "index.html"
MASCOT_DIR = WEB / "assets" / "mascot"
THEME_DIR = WEB / "assets" / "themes"

STATES = ["idle", "planning", "retrieving", "generating", "completed", "failed", "dragging", "dropping"]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_three_themes_have_variables_and_backgrounds() -> None:
    css = _read(APP_CSS)
    for theme in ("light", "dark", "cartoon"):
        assert f'[data-theme="{theme}"]' in css, f"缺少主题 {theme} 的变量块"
    # 背景图三层：浅色清新 / 深色 / 卡通
    assert "themes/fresh.png" in css
    assert "themes/dark.png" in css
    assert "themes/cartoon.png" in css
    # 背景用「遮罩 + 图片」两层叠加，保证正文对比度
    assert "linear-gradient(var(--theme-veil), var(--theme-veil))" in css


def test_theme_assets_exist_and_are_nonempty() -> None:
    for name in ("fresh.png", "dark.png", "cartoon.png"):
        p = THEME_DIR / name
        assert p.is_file(), f"主题背景缺失：{p}"
        assert p.stat().st_size > 50_000, f"主题背景疑似损坏：{p}"


def test_theme_cycle_covers_all_three() -> None:
    js = _read(APP_JS)
    assert "THEME_CYCLE = ['light', 'dark', 'cartoon']" in js
    assert "localStorage.setItem('citrus_theme'" in js


def test_mascot_states_declared_in_css_and_js() -> None:
    css = _read(MASCOT_CSS)
    js = _read(MASCOT_JS)
    for state in STATES:
        assert f'[data-state="{state}"]' in css, f"CSS 缺少吉祥物状态 {state}"
        assert re.search(rf"\b{state}:\s*\d", js), f"JS 缺少吉祥物状态 {state}"
    # 帧动画必须用 jump-none，否则 steps(10) 只走 0..90% 会串帧
    assert "steps(10, jump-none)" in css


def test_mascot_frame_strips_exist() -> None:
    for state in STATES:
        p = MASCOT_DIR / f"{state}.png"
        assert p.is_file(), f"缺少状态帧带：{p}"
        assert p.stat().st_size > 20_000, f"状态帧带疑似损坏：{p}"


def test_index_wires_mascot_and_theme_assets() -> None:
    html = _read(INDEX)
    assert 'href="assets/mascot.css"' in html
    assert '/assets/mascot.js' in html
    assert 'id="citrusMascot"' in html
    # 吉祥物挂在输入框内（贴在输入框上沿）
    mascot_at = html.index('id="citrusMascot"')
    box_at = html.index('class="input-box"')
    assert box_at < mascot_at, "吉祥物应位于 .input-box 内"


def test_mascot_driven_by_real_events_only() -> None:
    js = _read(APP_JS)
    # 只允许被真实事件驱动：发送、思考、工具、首字、完成、错误
    assert "mascotSay('planning')" in js
    assert "mascotSay('retrieving')" in js
    assert "mascotSay('generating')" in js
    assert "mascotSay(data.turn_status === 'failed' ? 'failed' : 'completed')" in js
    assert "mascotSay('failed')" in js
    # 不得有“假进度”式定时器推进状态
    assert "setInterval" not in _read(MASCOT_JS)

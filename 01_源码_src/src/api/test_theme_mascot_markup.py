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
    # 顺序可调（选择器按 春日花笺 / 午后书房 / 夜林花语 排列），但三套必须都在
    m = re.search(r"THEME_CYCLE = \[([^\]]+)\]", js)
    assert m, "缺少 THEME_CYCLE"
    names = re.findall(r"'([a-z]+)'", m.group(1))
    assert set(names) == {"light", "dark", "cartoon"}, names
    assert "localStorage.setItem('citrus_theme'" in js
    # 三套主题的中文名必须与设计稿一致
    for label in ("春日花笺", "午后书房", "夜林花语"):
        assert label in js, f"缺少主题名 {label}"


def test_mascot_states_declared_in_css_and_js() -> None:
    css = _read(MASCOT_CSS)
    js = _read(MASCOT_JS)
    for state in STATES:
        assert f'[data-state="{state}"]' in css, f"CSS 缺少吉祥物状态 {state}"
        # V3 的事实来源是 ACTIONS 动作清单：每态必须有帧带（strip），并由 CSS 的
        # [data-strip="…"] 决定显示哪一列，JS 逐帧写 --mascot-col。
        assert re.search(rf"strip:\s*'{state}'", js), f"JS 动作清单缺少帧带 {state}"
        assert f'[data-strip="{state}"]' in css, f"CSS 缺少帧带选择器 {state}"
    assert "--mascot-col" in js
    assert "@keyframes" not in css, "V2 不得再用 CSS keyframes 轮播"


def test_mascot_idle_is_quiet_not_looping() -> None:
    js = _read(MASCOT_JS)
    # 待机默认是「安静驻足」的定格帧，且不进入自主动作池
    rest = re.search(r"\{ id: 'idle\.rest'[^}]*\}", js)
    assert rest, "缺少 idle.rest 默认待机动作"
    assert "loop: 'hold'" in rest.group(0)
    assert "auto: false" in rest.group(0)
    # 活跃程度三档：自然档 6–12 秒才可能换一次微动作
    assert re.search(r"ACTIVITY = \{[^}]*natural:\s*\[6000,\s*12000\]", js), "缺少自然档 6–12s 间隔"
    assert re.search(r"quiet:\s*\[15000,\s*25000\]", js), "缺少安静档 15–25s 间隔"
    # 统一用 setTimeout 推进，不得用 setInterval 轮播
    assert not re.search(r"\bsetInterval\s*\(", js), "不得用 setInterval 轮播"


def test_mascot_preview_and_manifest_exist() -> None:
    preview = MASCOT_DIR / "preview.html"
    manifest = MASCOT_DIR / "manifest.json"
    assert preview.is_file(), "缺少动作预览页"
    assert manifest.is_file(), "缺少素材清单"
    import json
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for state in STATES:
        assert state in data["states"], f"清单缺少动作 {state}"
        assert data["states"][state]["strip"] == f"{state}.png"
    assert set(data["themes"]) == {"light", "cartoon", "dark"}
    assert data["preview"] == "preview.html"


def test_mascot_hide_preference_is_persisted() -> None:
    js = _read(MASCOT_JS)
    html = _read(INDEX)
    app = _read(APP_JS)
    assert "citrus_mascot_hidden" in js
    assert 'id="mascotClose"' in html
    # 显隐入口本轮并入宠物菜单（#petToggleBtn），文案由 syncVisibilityLabels 同步
    assert 'id="petToggleBtn"' in html
    assert 'id="petToggleText"' in html
    # 取消后必须立刻回待机，不能继续演翻书/敲字
    assert "CitrusMascot.setState('idle', { force: true })" in app


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


def test_mascot_real_events_and_bounded_autonomy() -> None:
    js = _read(APP_JS)
    mascot = _read(MASCOT_JS)
    # 真实事件仍必须驱动状态：发送、思考、工具、首字、完成、错误
    assert "mascotSay('planning')" in js
    assert "mascotSay('retrieving')" in js
    assert "mascotSay('generating')" in js
    assert "mascotSay(data.turn_status === 'failed' ? 'failed' : 'completed')" in js
    assert "mascotSay('failed')" in js
    # V3 允许受限的自主微动作，但必须：①不用 setInterval 轮播；②真实任务态优先；
    # ③页面隐藏暂停；④尊重减少动画；⑤有自主动作闸门。
    assert not re.search(r"\bsetInterval\s*\(", mascot), "不得用 setInterval 轮播"
    assert "canAuto(" in mascot, "缺少自主动作闸门 canAuto"
    assert "document.hidden" in mascot, "页面隐藏时必须暂停自主动作"
    assert "prefers-reduced-motion" in mascot, "必须尊重系统减少动画偏好"
    assert "interruptible" in mascot, "自主动作不得抢占真实任务动作（需要可打断标记）"

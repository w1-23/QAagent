# -*- coding: utf-8 -*-
"""工作台视觉/交互的静态接线测试（只查契约，不替代真实浏览器验收）。

这些断言对应的是「代码里有没有这条规则/这个入口」。真正的视觉效果与交互
必须在浏览器里量，见 experiments/trace/probes/ 下的探针。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CSS = (ROOT / "web" / "assets" / "app.css").read_text(encoding="utf-8")
SF_CSS = (ROOT / "web" / "assets" / "session_files.css").read_text(encoding="utf-8")
SF_JS = (ROOT / "web" / "assets" / "session_files.js").read_text(encoding="utf-8")
MASCOT_JS = (ROOT / "web" / "assets" / "mascot.js").read_text(encoding="utf-8")
INDEX = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


def test_rail_uses_brand_orange_not_gray():
    # 两套调色板（浅色/图片主题共用一套，深色一套）都以 --rail-track-bg 开头
    starts = [m.start() for m in re.finditer(r"--rail-track-bg:", CSS)]
    assert len(starts) >= 2, starts
    for start in starts:
        block = CSS[start:start + 700]
        assert "--rail-tick:" in block, block
        rgba = re.findall(r"--rail-tick(?:-active|-near)?:\s*rgba\((\d+), (\d+), (\d+)",
                          block)
        assert rgba, block
        for r, g, b in rgba:
            r, g, b = int(r), int(g), int(b)
            # 橙：红通道明显高于蓝通道（灰的三通道接近）
            assert r > b + 30, (r, g, b)


def test_preview_has_visible_close_button_and_escape():
    assert ".session-file-preview-actions .overlay-close" in SF_CSS
    assert "width: 34px" in SF_CSS
    assert "Escape" in SF_JS and "closeSessionFilePreview()" in SF_JS
    assert "previewReturnFocus" in SF_JS
    assert 'id="sessionFilePreviewOverlay"' in INDEX
    assert 'aria-label="关闭预览"' in INDEX


def test_mascot_has_scale_range_and_click_reaction():
    assert "citrus_mascot_scale" in MASCOT_JS
    assert "SCALE_MIN = 0.8" in MASCOT_JS and "SCALE_MAX = 1.8" in MASCOT_JS
    assert "setScale" in MASCOT_JS
    assert "suppressClick" in MASCOT_JS
    # 尺寸滑杆本轮迁入宠物菜单（#petScaleRange），数值由 syncScaleInputs 双向同步
    assert 'id="petScaleRange"' in INDEX
    assert "syncScaleInputs" in MASCOT_JS

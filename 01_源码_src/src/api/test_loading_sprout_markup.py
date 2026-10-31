# -*- coding: utf-8 -*-
"""CitrusScholar 加载态「小芽」静态契约测试（纯静态：不联网、不起服务、不读后端）。

覆盖四块：
1. assets/citrus-sprout-loading.svg —— 可被 ElementTree 解析、结构安全
   （无 script / image / foreignObject、除 SVG 命名空间外无 http(s) 外链）、
   aria-label 为「绿色小芽」，且原有双叶 path 与摇动 keyframes 未被重绘丢失。
2. assets/loading-sprout.css —— 类名齐全、三套主题的 --loading-accent 就位、
   有 prefers-reduced-motion 分支、hidden 兜底存在。
3. 动效安全 —— 不用 position:fixed、不用 translateY 类大位移。
4. index.html 接入 —— 主 Agent 尚未接入时 skip，避免抢跑误报。
"""
from __future__ import annotations

import pathlib
import re
import xml.etree.ElementTree as ET

import pytest

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
ASSETS = WEB / "assets"
SVG_PATH = ASSETS / "citrus-sprout-loading.svg"
CSS_PATH = ASSETS / "loading-sprout.css"
INDEX_PATH = WEB / "index.html"

SVG_NS = "http://www.w3.org/2000/svg"
SVG_TAG = "{%s}svg" % SVG_NS


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _index_ready() -> bool:
    """index.html 是否已接入 loading-sprout.css（由主 Agent 插入 <link>）。"""
    if not INDEX_PATH.is_file():
        return False
    return "loading-sprout.css" in _read(INDEX_PATH)


# --- 1. SVG ---------------------------------------------------------------

def test_svg_is_safe_and_parsable():
    assert SVG_PATH.is_file(), "缺少图标资源：%s" % SVG_PATH
    text = _read(SVG_PATH)

    root = ET.fromstring(text)                      # 解析失败即失败
    assert root.tag == SVG_TAG
    assert root.get("viewBox") == "0 0 64 64"
    assert root.get("aria-label") == "轻轻摇动的绿色双叶小芽", (
        "图标必须逐字节等于用户提供的 citrus_sprout_outline.svg 的 aria-label"
    )

    # 危险节点：命名空间查找 + 原始小写文本双保险
    for bad in ("script", "image", "foreignObject"):
        assert root.find(".//{%s}%s" % (SVG_NS, bad)) is None, bad
    lowered = text.lower()
    for bad in ("<script", "<image", "<foreignobject", "<iframe", "onload="):
        assert bad not in lowered, "SVG 含禁止内容：%s" % bad

    # 外链：只允许 SVG 自己的命名空间声明
    residual = text.replace('xmlns="%s"' % SVG_NS, "")
    urls = re.findall(r"https?://[^\s\"'>]+", residual)
    assert urls == [], "SVG 含外部引用：%r" % (urls,)
    assert "href" not in lowered, "SVG 不应含 href（无外链/无 use 引用）"


def test_svg_keeps_original_artwork_and_motion():
    text = _read(SVG_PATH)
    # 两片空心叶（stroke 无 fill）+ 茎 + 土壤弧线：原 path 未被重绘
    assert 'class="leaf-left"' in text
    assert 'class="leaf-right"' in text
    # 深绿 #398A58：茎 + 左右叶共 3 处；浅绿 #6AA77B：土壤弧线 1 处
    assert text.count('#398A58') >= 3
    assert text.count('#6AA77B') >= 1
    assert 'stroke="#' in text and 'fill="none"' in text  # 空心描边，非实心重绘
    # 自带摇动动画与 reduced-motion 兜底仍在
    assert "@keyframes left-sway" in text
    assert "@keyframes right-sway" in text
    assert "prefers-reduced-motion" in text
    assert text.count("<path") == 4


# --- 2. CSS ---------------------------------------------------------------

def test_css_defines_sprout_classes_and_theme_vars():
    assert CSS_PATH.is_file(), "缺少样式：%s" % CSS_PATH
    css = _read(CSS_PATH)

    for cls in (".load-sprout", ".load-sprout-icon", ".load-sprout-text",
                ".load-sprout-dots"):
        assert cls in css, "缺少类：%s" % cls

    assert "--loading-accent" in css
    for theme in ("light", "cartoon", "dark"):
        assert '[data-theme="%s"]' % theme in css, "缺少主题：%s" % theme

    # 三套主题必须是约定的三个色值
    for colour in ("#C9761F", "#B9722A", "#E8AE5A"):
        assert colour in css, "缺少主题色：%s" % colour

    # 图标与文案的尺寸/颜色契约
    assert "width: 30px" in css and "height: 30px" in css
    assert "14.5px" in css
    assert "filter: none" in css

    # display:flex 会压过 UA 的 [hidden]，必须有显式兜底
    assert ".load-sprout[hidden]" in css

    # 动效窗口与 reduced-motion 分支
    assert "prefers-reduced-motion" in css
    assert "1.4s" in css
    assert "300ms" in css


# --- 3. 动效安全 ----------------------------------------------------------

def test_css_avoids_fixed_overlay_and_vertical_jumps():
    css = _read(CSS_PATH)
    compact = re.sub(r"\s+", "", css).lower()

    assert "position:fixed" not in compact
    assert "position:absolute" not in compact
    assert "translatey" not in compact, "禁止上下跳动式位移"
    assert "translate3d" not in compact
    # 呼吸只能靠透明度
    assert "opacity" in compact
    # 不得出现超大 z-index 抢占层级
    assert "z-index:9999" not in compact


# --- 4. index.html 接入（可能早于主 Agent 完成，故 skip 而非失败） --------

@pytest.mark.skipif(not _index_ready(), reason="等待主 Agent 接入 index.html")
def test_index_wires_loading_sprout():
    html = _read(INDEX_PATH)
    assert "/assets/loading-sprout.css" in html
    if 'id="loadSprout"' not in html:
        pytest.skip("样式已接入，但 #loadSprout 节点尚未插入（等待主 Agent 完成 DOM 接入）")
    assert 'class="load-sprout"' in html
    assert "/assets/citrus-sprout-loading.svg" in html
    assert 'aria-live="polite"' in html
    assert 'role="status"' in html

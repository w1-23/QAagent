# -*- coding: utf-8 -*-
"""R15 静态契约：本地来源标识可点击 + 原文阅读器（纯静态，不联网/不起服务）。

覆盖：
1. index.html 接入 corpus_reader.css / corpus_reader.js，app.js 版本已 bump。
2. app.js 的键识别（paper_ / doc_）、只有在「整段就是键」时才转链接、
   行内代码形态单独样式、单一代理点击监听。
3. renderMarkdownSafe 是唯一出口 —— 历史重渲染与流式回答都会经过它。
4. corpus_reader.js 只传键、不传路径；401/403/404 有明确文案；
   原文用 renderMarkdownSafe 渲染（不直接 innerHTML 拼原文）；支持复制与 Esc 关闭。
5. corpus_reader.css 有入口样式、只读抽屉、深色主题与窄屏。
"""
from __future__ import annotations

import pathlib
import re

import pytest

PROJECT = pathlib.Path(__file__).resolve().parents[2]
WEB = PROJECT / "web"
ASSETS = WEB / "assets"

INDEX = WEB / "index.html"
APP_JS = ASSETS / "app.js"
READER_JS = ASSETS / "corpus_reader.js"
READER_CSS = ASSETS / "corpus_reader.css"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def index_html() -> str:
    return _read(INDEX)


@pytest.fixture(scope="module")
def app_js() -> str:
    return _read(APP_JS)


@pytest.fixture(scope="module")
def reader_js() -> str:
    return _read(READER_JS)


@pytest.fixture(scope="module")
def reader_css() -> str:
    return _read(READER_CSS)


# ── 1. 接入 ────────────────────────────────────────────────────────────────

def test_index_wires_reader_assets_and_bumps_app_version(index_html: str):
    assert re.search(r'href="/assets/corpus_reader\.css\?v=\d+"', index_html)
    assert re.search(r'src="/assets/corpus_reader\.js\?v=\d+"', index_html)
    # app.js 有改动，必须带版本号，避免浏览器吃旧缓存
    m = re.search(r'src="/assets/app\.js\?v=(\d+)"', index_html)
    assert m and int(m.group(1)) >= 29


def test_reader_js_is_loaded_after_app_js(index_html: str):
    app_at = index_html.find('/assets/app.js?v=')
    reader_at = index_html.find('/assets/corpus_reader.js?v=')
    assert 0 < app_at < reader_at


# ── 2. app.js 的键识别 ─────────────────────────────────────────────────────

def test_key_regex_covers_paper_and_doc_with_segments(app_js: str):
    m = re.search(r"var _CORPUS_KEY_RE = /(.+?)/g;", app_js)
    assert m, "缺少 _CORPUS_KEY_RE"
    assert "paper" in m.group(1) and "doc" in m.group(1)
    # 至少要 3 段下划线，否则普通单词（paper_id）会被误判
    assert "{2,}" in m.group(1)


def test_only_whole_segment_keys_become_links(app_js: str):
    # _corpusKeyOnly 必须要求「替换掉键后为空」，即整段就是键
    assert "function _corpusKeyOnly(" in app_js
    body = app_js[app_js.index("function _corpusKeyOnly("):]
    body = body[: body.index("function _corpusAnchor(")]
    assert "replace(" in body and "=== ''" in body


def test_inline_code_keys_get_their_own_style(app_js: str):
    assert "corpus-key-code" in app_js
    # 行内代码分支：/<code>([^<]{8,200})<\/code>/g
    assert "<code>(" in app_js, "行内代码分支缺失"
    assert "{8,200}" in app_js


def test_single_delegated_click_listener(app_js: str):
    assert app_js.count("closest('a.corpus-key')") == 1
    assert "window.openCorpusSource(" in app_js
    # 不得在每次渲染时绑定 onclick（历史重建会重复绑定）
    assert "a.corpus-key').onclick" not in app_js


def test_render_markdown_safe_linkifies_before_return(app_js: str):
    start = app_js.index("function renderMarkdownSafe(")
    end = app_js.index("function _corpusKeyOnly(")
    body = app_js[start:end]
    assert "linkifyCorpusKeys(rendered)" in body
    assert body.index("linkifyCorpusKeys(rendered)") < body.index("return rendered")


def test_all_answer_render_paths_use_render_markdown_safe(app_js: str):
    # 历史重渲染 + 流式 + 最终回答都必须走同一出口
    assert app_js.count("renderMarkdownSafe(") >= 4


# ── 4. corpus_reader.js ───────────────────────────────────────────────────

def test_reader_requests_by_key_only(reader_js: str):
    assert "/api/v2/corpus/source?record=" in reader_js
    assert "encodeURIComponent(key)" in reader_js
    # 前端绝不能拼磁盘路径或目录穿越（注释里提到语料文件名是允许的）
    for bad in ("/home/", "agent/data", "..%2f", "C:\\\\"):
        assert bad not in reader_js
    # 两处数据请求：原文（/corpus/source?record=）与本轮来源列表
    # （/turn-sources?request_id=），都只传不透明标识，不拼磁盘路径
    assert reader_js.count("fetch(") == 2
    assert "/turn-sources?request_id=" in reader_js


def test_reader_has_explicit_failure_copy(reader_js: str):
    assert "本地原文不可用" in reader_js
    assert "401" in reader_js and "403" in reader_js and "404" in reader_js
    assert "该来源需要更高权限" in reader_js


def test_reader_renders_markdown_through_safe_renderer(reader_js: str):
    assert "renderMarkdownSafe(d.markdown" in reader_js
    # 兜底才允许 <pre class="cr-raw"> 转义输出，不能直接 innerHTML 原文
    assert "cr-raw" in reader_js


def test_reader_supports_copy_toggle_and_escape(reader_js: str):
    assert "navigator.clipboard" in reader_js
    assert "execCommand('copy')" in reader_js
    assert "crViewBtn" in reader_js and "原始 Markdown" in reader_js
    assert "Escape" in reader_js


def test_reader_linkifies_doi_only_when_present(reader_js: str):
    assert "https://doi.org/" in reader_js
    assert "未匹配到 DOI" in reader_js
    # DOI may come from a registry lookup, so the wording must not claim
    # the markdown itself lacks one.
    assert "原文未含" not in reader_js


# ── 5. corpus_reader.css ──────────────────────────────────────────────────

def test_css_styles_entry_drawer_and_themes(reader_css: str):
    assert ".corpus-reader-mask[hidden]" in reader_css
    assert "a.corpus-key" in reader_css
    assert "corpus-key-code" in reader_css
    assert '[data-theme="dark"] .corpus-reader' in reader_css
    assert "@media (max-width: 640px)" in reader_css
    # 只读：不应该出现任何编辑态控件样式
    assert "contenteditable" not in reader_css

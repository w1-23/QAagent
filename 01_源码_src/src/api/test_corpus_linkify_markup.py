"""回答正文里的本地来源标识（paper_*/doc_*）必须可点击——契约级测试。

真实故障（2026-10-31 真机探针发现）：
1. 旧正则 `(?:paper|doc)_[A-Za-z0-9]+(?:_[A-Za-z0-9]+){2,}` 会丢掉键尾的 `_`
   （`paper_45bbcac3_000004_B_2017_Nature_Genetics_` → `…_Genetics`），
   `describe()` 因此查不到记录，阅读器只显示「本地原文不可用」。
2. 行内代码分支先生成 <a>，文本分支又把 <a> 里的文字包了一层 <a>，
   嵌套锚点点击事件目标错乱。
3. 任何 `paper_xxx` 形状的普通词都会被误识别成文献键。
"""
from __future__ import annotations

import pathlib
import re

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
APP_JS = (WEB / "assets" / "app.js").read_text(encoding="utf-8", errors="replace")


def test_paper_key_pattern_keeps_hash_prefix_and_trailing_underscore() -> None:
    m = re.search(r"var _CORPUS_KEY_RE = /(.+?)/g;", APP_JS, re.S)
    assert m, "未找到 _CORPUS_KEY_RE"
    pattern = m.group(1)
    assert "paper_[0-9a-f]{8}" in pattern, pattern
    assert r"\d{6}" in pattern, pattern
    assert "[A-Z]" in pattern, pattern
    # 期刊 slug 允许连续下划线，并允许以 _ 结尾（截断键）
    assert "[A-Za-z0-9_]{2,}" in pattern, pattern
    assert "doc_" in pattern, pattern


def test_plain_words_are_not_treated_as_keys() -> None:
    # 校验函数要求「整段就是一个键」，因此 paper_not_a_key 这类词不应被接受
    assert "function _corpusKeyOnly(text)" in APP_JS
    assert "t.replace(new RegExp(_CORPUS_KEY_RE.source, 'g'), '') === '' ? t : ''" in APP_JS


def test_linkify_uses_placeholder_to_avoid_nested_anchors() -> None:
    assert "var stash = [];" in APP_JS
    assert "CR' + (stash.length - 1)" in APP_JS or "CR' + (stash.length - 1) + " in APP_JS
    assert re.search(r"src\.replace\(/\\u0000CR\(\\d\+\)\\u0000/g", APP_JS), "缺少占位符还原"
    # 还原发生在文本分支之后，保证不会二次包裹
    body = APP_JS[APP_JS.index("function linkifyCorpusKeys"):]
    body = body[: body.index("\n}\n") + 3]
    assert body.index("普通文本节点里的裸键") < body.index("还原行内代码链接")


def test_click_delegate_opens_reader_with_full_key() -> None:
    idx = APP_JS.index("closest('a.corpus-key')")
    block = APP_JS[idx: idx + 700]
    assert "openCorpusSource" in block
    assert "data-corpus-key" in block
    assert "preventDefault" in block


def test_render_markdown_safe_is_the_single_entry_for_all_three_paths() -> None:
    # 流式、最终、历史三处都走 renderMarkdownSafe，linkify 挂在那里才覆盖历史回答
    assert APP_JS.count("renderMarkdownSafe(") >= 4
    assert "rendered = linkifyCorpusKeys(rendered);" in APP_JS


def test_index_loads_app_js_with_cache_busting_version() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8", errors="replace")
    assert re.search(r"/assets/app\.js\?v=\d+", html)
    assert re.search(r"/assets/corpus_reader\.js\?v=\d+", html)

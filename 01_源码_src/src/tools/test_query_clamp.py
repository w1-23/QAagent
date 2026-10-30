"""Web 检索词的边界处理：过长不应直接判失败。"""
from __future__ import annotations

import src.tools.deepseek_search_fetch as web


def test_short_query_is_untouched():
    assert web._clamp_query("柑橘 黄龙病 防控") == ("柑橘 黄龙病 防控", False)


def test_long_query_is_clamped_at_word_boundary():
    query = ("从本地资料库里找 2 篇关于柑橘黄龙病防控的文献，给出标题和来源标识，"
             "并说明它们是否提到媒介昆虫防治与苗木脱毒、抗病品种选育、园区清园与统一放梢，"
             "以及引用 DOI 10.16377/j.cnki.issn1007-7731.2026.10.021 的期刊卷期页码信息")
    assert len(query) > web._MAX_QUERY_CHARS
    clamped, changed = web._clamp_query(query)
    assert changed is True
    assert len(clamped) <= web._MAX_QUERY_CHARS
    assert clamped.startswith("从本地资料库里找 2 篇")
    assert not clamped.endswith(("，", ",", " ", "；"))


def test_whitespace_is_collapsed_before_measuring():
    query = "柑橘" + " " * 200 + "黄龙病"
    clamped, changed = web._clamp_query(query)
    assert clamped == "柑橘 黄龙病"
    assert changed is False


def test_clamped_query_still_searches(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"results": [{"title": "t", "link": "https://example.org/a",
                                 "snippet": "s", "content": "c"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(json or {})
        return _Resp()

    monkeypatch.setattr(web, "_enabled", lambda: True)
    monkeypatch.setattr(web.requests, "post", fake_post)
    monkeypatch.setattr(web, "_search_timeout", lambda: 5)
    monkeypatch.setattr(web, "_headers", lambda: {})
    long_query = "柑橘黄龙病防控技术" * 20
    content, artifact = web.deepseek_search.func(long_query)
    assert len(seen.get("query", "")) <= web._MAX_QUERY_CHARS
    assert "WEB_TOOL_ERROR" not in content
    assert "已按词边界截断" in content
    assert artifact["web_results"][0]["url"] == "https://example.org/a"

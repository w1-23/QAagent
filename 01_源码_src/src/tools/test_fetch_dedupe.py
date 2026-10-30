# -*- coding: utf-8 -*-
"""并行全文抓取的去重 / 相关性筛选 / 分层返回，以及同轮工具结果总预算。"""
from __future__ import annotations

import pytest

from src.core import tool_results as tr
from src.tools import deepseek_search_fetch as web


def _page(url: str, content: str, *, doi: str = "", fmt: str = "pmc_html", title: str = "T"):
    return {"url": url, "ok": True, "title": title, "content": content, "format": fmt, "doi": doi}


# ---------------------------------------------------------------- URL / key
def test_canonical_url_drops_tracking_and_fragment():
    a = web._canonical_url("HTTPS://Example.ORG/Path/?utm_source=x&id=7#frag")
    b = web._canonical_url("https://example.org/Path?id=7")
    assert a == b == "https://example.org/Path?id=7"


def test_canonical_url_normalises_doi_hosts():
    assert web._canonical_url("http://dx.doi.org/10.1234/ABC") == "https://doi.org/10.1234/abc"
    assert web._canonical_url("https://doi.org/10.1234/ABC") == "https://doi.org/10.1234/abc"


def test_paper_key_prefers_doi_then_url_then_title():
    assert web._paper_key("10.1234/abc", "https://x/y") == "doi:10.1234/abc"
    assert web._paper_key("", "https://x/y?a=1") == "url:https://x/y?a=1"
    key = web._paper_key("", "", "Same Title")
    assert key.startswith("title:") and key == web._paper_key("", "", " same   title ")


def test_paper_key_extracts_doi_from_text_when_not_supplied():
    assert web._paper_key("", "", "", "cite: doi:10.5555/XYZ.1.") == "doi:10.5555/xyz.1"


def test_content_fingerprint_ignores_markup_and_whitespace():
    a = web._content_fingerprint("<p>" + "黄龙病 " * 200 + "</p>")
    b = web._content_fingerprint("黄龙病\n" * 200)
    assert a and a == b


def test_content_fingerprint_returns_empty_for_short_text():
    assert web._content_fingerprint("short") == ""


# ---------------------------------------------------------------- dedupe
def test_dedupe_pages_merges_same_doi_and_keeps_better_version():
    long_text = "Abstract\n" + "柑橘黄龙病 防控 " * 120
    short_text = "短摘要"
    pages = [
        _page("https://doi.org/10.1/a", short_text, doi="10.1/a"),
        _page("https://pmc.example/a", long_text, doi="10.1/A"),
    ]
    kept, merged = web._dedupe_pages(pages)
    assert len(kept) == 1
    assert kept[0]["url"] == "https://pmc.example/a"
    assert merged and merged[0]["error"] == "superseded_by_better_version"


def test_dedupe_pages_merges_identical_body_from_different_urls():
    body = "Introduction\n" + "木虱 传播 媒介 " * 150
    pages = [_page("https://a.example/1", body), _page("https://b.example/2", body)]
    kept, merged = web._dedupe_pages(pages)
    assert len(kept) == 1
    assert merged and merged[0]["error"] == "duplicate_content_fingerprint"


def test_dedupe_pages_keeps_distinct_papers():
    a = _page("https://a.example/1", "Abstract\n" + "甲 " * 300, doi="10.1/a")
    b = _page("https://b.example/2", "Abstract\n" + "乙 " * 300, doi="10.1/b")
    kept, merged = web._dedupe_pages([a, b])
    assert len(kept) == 2 and merged == []


def test_body_similarity_matches_same_article_with_different_banner():
    core = "柑橘黄龙病由亚洲韧皮杆菌引起，木虱是主要传播媒介。" * 60
    a = "<div class=\"nav\">MDPI banner 欢迎访问旧版</div>" + core
    b = "<header>PMC 网站正在改版，查看常见问题</header>" + core
    assert web._body_similarity(a, b) >= 0.5


def test_body_similarity_rejects_different_articles():
    a = "柑橘黄龙病 传播 媒介 木虱 防控 " * 80
    b = "苹果腐烂病 病原 真菌 防治 果园 " * 80
    assert web._body_similarity(a, b) < 0.2


def test_dedupe_pages_merges_same_article_from_two_routes():
    core = "Abstract Huanglongbing is caused by Candidatus Liberibacter." * 80
    a = _page("https://doi.org/10.3390/plants12010160", "MDPI 导航块 " + core)
    b = _page("https://pmc.ncbi.nlm.nih.gov/articles/PMC9824665/", "PMC 导航块 " + core)
    kept, merged = web._dedupe_pages([a, b])
    assert len(kept) == 1
    assert merged and merged[0]["error"] == "duplicate_body_similarity"


# ---------------------------------------------------------------- filtering
def test_select_relevant_passages_keeps_matching_paragraph_and_budget():
    text = "\n\n".join([
        "Abstract: 柑橘黄龙病由亚洲韧皮杆菌引起。",
        "Methods: 我们采集了 120 份样品并做 PCR 检测。",
        "Results: 木虱传播效率与温度正相关，Table 1 给出回归系数。",
        "Discussion: 与既往研究一致。",
        "References: 1. 某文献",
    ])
    out, stats = web._select_relevant_passages(text, "木虱 传播 温度", 120)
    assert stats["passages"] >= 1
    assert len(out) <= 120
    assert "木虱" in out


def test_select_relevant_passages_zero_budget_returns_empty():
    out, stats = web._select_relevant_passages("任意正文", "query", 0)
    assert out == "" and stats["chars"] == 0


def test_select_relevant_passages_degrades_when_nothing_fits():
    text = "一整段很长的正文" * 200
    out, stats = web._select_relevant_passages(text, "不相关词", 50)
    assert stats["degraded"] is True and len(out) <= 50


# ---------------------------------------------------------------- switch / stats
def test_dedupe_disabled_by_default(monkeypatch):
    monkeypatch.delenv(web._FETCH_DEDUPE_ENV, raising=False)
    assert web._dedupe_enabled() is False
    monkeypatch.setenv(web._FETCH_DEDUPE_ENV, "1")
    assert web._dedupe_enabled() is True


def test_fetch_stats_counts(monkeypatch):
    monkeypatch.setenv(web._FETCH_DEDUPE_ENV, "1")
    stats = web._fetch_stats(5, [{"ok": True}, {"ok": False}], [{"url": "u"}], 1000, 600, 3)
    assert stats == {"candidates": 5, "dispatched": 2, "succeeded": 1, "failed": 1,
                     "merged": 1, "passages_in_model": 3, "chars_raw": 1000,
                     "chars_in_model": 600, "dedupe": True}


# ---------------------------------------------------------------- end to end
def test_fetch_paper_dedupes_and_reports_stats(monkeypatch):
    body = "Abstract\n" + "黄龙病 防控 木虱 " * 200
    calls: list[str] = []

    def fake_fetch(url, timeout=None):
        calls.append(url)
        return {"url": url, "ok": True, "title": "柑橘黄龙病研究", "content": body}

    monkeypatch.setenv(web._FETCH_DEDUPE_ENV, "1")
    monkeypatch.setattr(web, "_fetch_url", fake_fetch)
    text, meta = web.deepseek_fetch_paper.func(doi="10.1234/abc") \
        if hasattr(web.deepseek_fetch_paper, "func") else web.deepseek_fetch_paper(doi="10.1234/abc")
    assert meta["fetch_stats"]["dedupe"] is True
    assert meta["fetch_stats"]["succeeded"] >= 1
    assert meta["fetch_stats"]["chars_in_model"] <= web._FETCH_TOTAL_CHARS
    assert "untrusted_web_data" in text
    assert len(meta["web_results"]) == 1


# ---------------------------------------------------------------- turn budget
def test_turn_budget_caps_second_result(monkeypatch):
    tr.begin_turn_budget(1_000)
    first, cut1 = tr.clamp_for_model("甲" * 900)
    assert cut1 is False
    assert tr.turn_budget_remaining() == 1000 - len(first)
    second, cut2 = tr.clamp_for_model("乙" * 900)
    assert cut2 is True and len(second) <= 100
    assert tr.turn_budget_remaining() == 0
    third, cut3 = tr.clamp_for_model("丙")
    assert cut3 is True and "TOOL_RESULT_OMITTED" in third


def test_turn_budget_absent_keeps_old_behaviour():
    tr._TURN_BUDGET.set(None)
    out, cut = tr.clamp_for_model("丙" * 50)
    assert cut is False and out == "丙" * 50
    assert tr.turn_budget_remaining() is None

"""同一篇论文多来源并行竞争：首个合格结果胜出（不拼接多份全文）。

这些测试全部使用 mock 抓取函数，不调用真实网络或模型。
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.tools import deepseek_search_fetch as web

DOI = "10.3390/plants12010160"
BODY_A = "Introduction " + ("柑橘黄龙病传播与防控。" * 200) + " Methods Results Discussion"
BODY_B = "Introduction " + ("木虱媒介防治与苗木脱毒。" * 200) + " Methods Results Discussion"


def _page(url: str, body: str, *, ok: bool = True, title: str = "论文") -> dict:
    return {"url": url, "ok": ok, "title": title,
            "content": body if ok else "", "error": "" if ok else "boom"}


@pytest.fixture()
def patched(monkeypatch):
    monkeypatch.setattr(web, "_public_http_url_error", lambda url: None)
    monkeypatch.setattr(web, "_enabled", lambda: True)
    monkeypatch.setattr(web, "record_fetch_failures", lambda *a, **k: None, raising=False)
    pool = ThreadPoolExecutor(max_workers=4)
    monkeypatch.setattr(web, "WEB_FETCH_EXECUTOR", pool, raising=False)
    import src.core.executors as executors
    monkeypatch.setattr(executors, "WEB_FETCH_EXECUTOR", pool, raising=False)
    yield pool
    pool.shutdown(wait=False, cancel_futures=True)


def _run(monkeypatch, fake, **kwargs):
    monkeypatch.setattr(web, "_fetch_url", fake)
    fn = getattr(web.deepseek_fetch_paper, "func", web.deepseek_fetch_paper)
    return fn(**kwargs)


def test_first_valid_wins_even_when_it_is_not_the_fastest(patched, monkeypatch):
    """先返回但只有摘要的来源不能胜出；慢一点但有正文的来源胜出。"""
    def fake(url, timeout=None):
        if "fast" in url:
            time.sleep(0.02)
            return _page(url, "Abstract only. " * 40)
        time.sleep(0.15)
        return _page(url, BODY_A)

    text, meta = _run(monkeypatch, fake, doi=DOI,
                      urls=[f"https://example.org/fast-{DOI}", f"https://example.org/slow-{DOI}"])
    assert meta["fetch_status"] == "full_text"
    assert "柑橘黄龙病传播与防控" in text
    assert text.count("<untrusted_web_data>") == 1


def test_two_valid_sources_are_never_concatenated(patched, monkeypatch):
    def fake(url, timeout=None):
        first = "/a-" in url
        time.sleep(0.02 if first else 0.10)
        return _page(url, BODY_A if first else BODY_B)

    text, meta = _run(monkeypatch, fake, doi=DOI,
                      urls=[f"https://example.org/a-{DOI}", f"https://example.org/b-{DOI}"])
    bodies = text.count("<untrusted_web_data>")
    assert bodies == 1, "只允许一份正文进入工具消息"
    assert len(meta["web_results"]) == 1
    assert meta["fetch_stats"]["chars_returned"] <= len(BODY_A) + 10


def test_winner_stops_queue_and_does_not_wait_for_inflight(patched, monkeypatch):
    calls: list[str] = []

    def fake(url, timeout=None):
        calls.append(url)
        if "win" in url:
            time.sleep(0.02)
            return _page(url, BODY_A)
        time.sleep(1.2)          # 落败来源很慢
        return _page(url, BODY_B)

    started = time.monotonic()
    text, meta = _run(monkeypatch, fake, doi=DOI,
                      urls=[f"https://example.org/win-{DOI}",
                            f"https://example.org/slow1-{DOI}",
                            f"https://example.org/slow2-{DOI}",
                            f"https://example.org/slow3-{DOI}"])
    elapsed = time.monotonic() - started
    assert elapsed < 0.9, f"胜出后不应继续等待在途请求，实测 {elapsed:.2f}s"
    errors = [a.get("error", "") for a in meta["fetch_attempts"]]
    assert any(e.startswith(("cancelled_after_winner", "in_flight_discarded_after_winner"))
               for e in errors)
    assert any(e == "not_started_after_winner" for e in errors)
    assert len(calls) <= 2, f"胜出后不得启动新来源，实测启动 {len(calls)} 个"


def test_late_result_never_reaches_the_message(patched, monkeypatch):
    def fake(url, timeout=None):
        if "win" in url:
            time.sleep(0.02)
            return _page(url, BODY_A)
        time.sleep(0.30)
        return _page(url, BODY_B)

    text, meta = _run(monkeypatch, fake, doi=DOI,
                      urls=[f"https://example.org/win-{DOI}", f"https://example.org/late-{DOI}"])
    time.sleep(0.5)              # 让迟到结果真的返回
    assert "木虱媒介防治与苗木脱毒" not in text
    assert len(meta["web_results"]) == 1


def test_abstract_only_is_reported_honestly(patched, monkeypatch):
    def fake(url, timeout=None):
        return _page(url, f"{DOI} Abstract only. " * 30)

    text, meta = _run(monkeypatch, fake, doi=DOI, urls=[f"https://example.org/abs-{DOI}"])
    assert meta["fetch_status"] == "abstract_only"
    assert "WEB_FETCH_ABSTRACT_ONLY" in text
    assert "full_text" != meta["fetch_status"]


def test_all_sources_fail_reports_reasons(patched, monkeypatch):
    def fake(url, timeout=None):
        return _page(url, "", ok=False)

    text, meta = _run(monkeypatch, fake, doi=DOI, urls=[f"https://example.org/x-{DOI}"])
    assert meta["fetch_status"] == "failed"
    assert "WEB_FETCH_FAILED" in text
    assert meta["fetch_stats"]["chars_returned"] == 0
    assert meta["web_results"] == []


def test_identity_mismatch_is_rejected(patched, monkeypatch):
    def fake(url, timeout=None):
        if "doi.org" in url:
            return _page(url, "", ok=False)
        return _page(url, "Introduction " + ("别的论文。" * 300) + " Methods Results", title="另一篇")

    text, meta = _run(monkeypatch, fake, doi=DOI, urls=["https://example.org/other"])
    assert meta["fetch_status"] == "failed"
    assert any(a.get("validity_reason") == "identity_mismatch"
               for a in meta["fetch_attempts"])


def test_login_page_is_not_a_full_text(patched, monkeypatch):
    def fake(url, timeout=None):
        return _page(url, f"{DOI} Sign in to access this article. " + ("navigation " * 400))

    text, meta = _run(monkeypatch, fake, doi=DOI, urls=[f"https://example.org/login-{DOI}"])
    assert meta["fetch_status"] == "failed"
    assert any(a.get("validity_reason") == "login_or_error_page"
               for a in meta["fetch_attempts"])


def test_cancelled_future_exception_is_consumed(patched, monkeypatch):
    """取消/丢弃的在途任务不能留下未处理异常。"""
    errors: list[BaseException] = []
    old_hook = threading.excepthook

    def fake(url, timeout=None):
        if "win" in url:
            time.sleep(0.02)
            return _page(url, BODY_A)
        time.sleep(0.05)
        raise RuntimeError("provider exploded after the winner")

    def hook(args):
        errors.append(args.exc_value)

    threading.excepthook = hook
    try:
        text, meta = _run(monkeypatch, fake, doi=DOI,
                          urls=[f"https://example.org/win-{DOI}", f"https://example.org/boom-{DOI}"])
        time.sleep(0.4)
    finally:
        threading.excepthook = old_hook
    assert errors == [], f"出现未处理异常: {errors}"
    assert "柑橘黄龙病传播与防控" in text


def test_three_strategies_compared(patched, monkeypatch):
    """顺序抓取 / 旧并行合并 / 新并行竞争：耗时与返回字符数对比。"""
    latencies = {"a": 0.20, "b": 0.05}

    def fake(url, timeout=None):
        key = "a" if "/a-" in url else "b"
        time.sleep(latencies[key])
        return _page(url, BODY_A if key == "a" else BODY_B)

    candidates = [f"https://example.org/a-{DOI}", f"https://example.org/b-{DOI}"]

    # 1) 顺序：第一个来源就成功 → 只抓一次，耗时 0.20s，返回 1 份
    seq_start = time.monotonic()
    seq_chars = len(str(fake(candidates[0]).get("content") or ""))
    seq_elapsed = time.monotonic() - seq_start

    # 2) 旧并行合并：等全部完成并拼接两份正文
    merge_start = time.monotonic()
    merged_chars = sum(len(str(fake(url).get("content") or "")) for url in candidates)
    merge_elapsed = time.monotonic() - merge_start

    # 3) 新并行竞争：最快合格者胜出
    race_start = time.monotonic()
    text, meta = _run(monkeypatch, fake, doi=DOI, urls=candidates)
    race_elapsed = time.monotonic() - race_start

    assert meta["fetch_status"] == "full_text"
    assert meta["fetch_stats"]["chars_returned"] <= seq_chars * 1.2
    assert meta["fetch_stats"]["chars_returned"] < merged_chars
    assert race_elapsed < merge_elapsed + 0.15
    assert race_elapsed < seq_elapsed + 0.15

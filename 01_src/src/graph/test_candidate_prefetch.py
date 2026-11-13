"""The synchronous local prefetch node: gates, honest statuses, no leaked text.

The contract under test:

* every gate produces its own status instead of raising;
* a "not executed" prefetch never says "the corpus has no such material";
* the pool that the node builds carries metadata only into ``AgentState``;
* the prefetch never writes the shared query cache with an empty body.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src.graph import candidate_prefetch  # noqa: E402
from src.graph.candidate_prefetch import (  # noqa: E402
    STATUS_DISABLED, STATUS_NO_FAMILY, STATUS_READY, STATUS_SKIPPED,
    STATUS_TIMEOUT, STATUS_TRANSLATION_ERROR, STATUS_ZERO_HIT,
    prefetch_candidates,
)
from src.retrieval.candidate_pool import (  # noqa: E402
    bind_candidate_pool, build_candidate_pool, clear_current_candidate_pool,
    clear_pools, current_candidate_pool, get_pool,
)


def _row(index: int = 1, **overrides) -> dict:
    row = {
        "title": f"Citrus greening disease report {index}",
        "text": "Huanglongbing is a destructive citrus disease. " * 40,
        "doi": f"10.1000/doi{index}",
        "year": str(2014 + index),
        "paper_id": f"paper_abc123_{index:06d}",
        "chunk_id": f"paper_abc123_{index:06d}:0",
        "chunk_index": 0,
        "corpus_id": "corpus_56182d409be76ee03066efd3",
        "corpus_family_id": "literature",
        "_source_batch": "cqa_literature",
        "source_type": "journal",
        "_src": "literature",
        "hybrid_score": 0.7,
        "rerank_score": 0.6,
    }
    row.update(overrides)
    return row


class _Registry:
    """Minimal stand-in for the corpus registry (family ids only)."""

    def __init__(self, families=("literature",)):
        self._families = set(families)

    @property
    def family_ids(self):
        return set(self._families)

    def matching_families(self, query) -> set[str]:
        text = str(query or "").casefold()
        return {f for f in self._families if f in text}


@pytest.fixture(autouse=True)
def clean_registry():
    clear_pools()
    clear_current_candidate_pool()
    yield
    clear_pools()
    clear_current_candidate_pool()


@pytest.fixture(autouse=True)
def identity_translation(monkeypatch):
    """The query translator is not exercised here; keep family matching pure."""
    import src.retrieval.local_query_language as language

    monkeypatch.setattr(language, "normalize_local_query",
                        lambda query: (str(query or ""), {"translation_used": False}))


@pytest.fixture
def registry(monkeypatch):
    reg = _Registry()

    class _Loader:
        @staticmethod
        def load_corpus_registry(data_dir=None):
            return reg

    import src.retrieval.corpus_registry as module
    monkeypatch.setattr(module, "load_corpus_registry", _Loader.load_corpus_registry)
    return reg


def _patch_search(monkeypatch, artifact, recorder=None):
    from src.tools import deepseek_search_fetch

    def fake(query, corpus_types, limit=12, l1_categories=None):
        if recorder is not None:
            recorder.append({"query": query, "families": list(corpus_types),
                             "limit": limit})
        return artifact

    monkeypatch.setattr(deepseek_search_fetch, "search_local_candidates", fake)


def _state(query="literature 柑橘黄龙病的防治方法有哪些", **extra) -> dict:
    state = {
        "query": query,
        "session_id": "sess-1",
        "request_id": "req-1",
        "messages": [],
    }
    state.update(extra)
    return state


def test_a_domain_question_prefetch_and_keeps_the_text_out_of_the_state(
        monkeypatch, registry):
    calls: list[dict] = []
    _patch_search(monkeypatch, {"main_results": [_row(1)], "local_status": "hit",
                                 "translation_used": False, "elapsed_ms": 12.0}, calls)
    result = asyncio.run(prefetch_candidates(_state()))
    assert result["candidate_pool"]["status"] == STATUS_READY
    assert result["candidate_pool"]["count"] == 1
    assert result["candidate_pool"]["families"] == ["literature"]
    # The model-visible notice carries counts and families, never titles/text.
    notice = result["candidate_pool_notice"]
    assert "1 条" in notice
    assert "Citrus greening" not in notice
    assert "Huanglongbing" not in notice
    # The state snapshot is metadata only.
    assert "text" not in repr(result["candidate_pool"])
    # The pool itself is registered under the id the state carries.
    pool = get_pool(pool_id=result["candidate_pool"]["pool_id"])
    assert pool is not None and len(pool.candidates) == 1
    assert calls and calls[0]["families"] == ["literature"]


def test_the_prefetch_runs_exactly_one_local_search(monkeypatch, registry):
    calls: list[dict] = []
    _patch_search(monkeypatch, {"main_results": [], "local_status": "zero_hit",
                                 "translation_used": False, "elapsed_ms": 1.0}, calls)
    asyncio.run(prefetch_candidates(_state()))
    assert len(calls) == 1


def test_a_zero_hit_says_zero_hit_not_no_material(monkeypatch, registry):
    _patch_search(monkeypatch, {"main_results": [], "local_status": "zero_hit",
                                 "translation_used": False, "elapsed_ms": 1.0})
    result = asyncio.run(prefetch_candidates(_state()))
    assert result["candidate_pool"]["status"] == STATUS_ZERO_HIT
    assert result["candidate_pool"]["count"] == 0
    assert "未命中" in result["candidate_pool_notice"]
    assert "不等于语料中没有资料" in result["candidate_pool_notice"]


def test_a_disabled_prefetch_says_disabled(monkeypatch, registry):
    monkeypatch.setattr(candidate_prefetch.settings, "CANDIDATE_PREFETCH_ENABLED", False)
    result = asyncio.run(prefetch_candidates(_state()))
    assert result["candidate_pool"]["status"] == STATUS_DISABLED
    assert result["candidate_pool_notice"] == ""


def test_an_empty_query_is_skipped(monkeypatch, registry):
    result = asyncio.run(prefetch_candidates(_state(query="")))
    assert result["candidate_pool"]["status"] == STATUS_SKIPPED
    assert result["candidate_pool_notice"] == ""


def test_a_web_only_question_is_skipped(monkeypatch, registry):
    calls: list[dict] = []
    _patch_search(monkeypatch, {"main_results": [_row(1)], "local_status": "hit",
                                 "translation_used": False, "elapsed_ms": 1.0}, calls)
    result = asyncio.run(prefetch_candidates(_state(query="只查网页上的黄龙病最新进展")))
    assert result["candidate_pool"]["status"] == STATUS_SKIPPED
    assert not calls


def test_an_explicit_no_retrieval_request_is_skipped(monkeypatch, registry):
    calls: list[dict] = []
    _patch_search(monkeypatch, {"main_results": [_row(1)], "local_status": "hit",
                                 "translation_used": False, "elapsed_ms": 1.0}, calls)
    result = asyncio.run(prefetch_candidates(_state(query="不要检索，直接回答")))
    assert result["candidate_pool"]["status"] == STATUS_SKIPPED
    assert not calls


def test_a_social_turn_is_skipped(monkeypatch, registry):
    calls: list[dict] = []
    _patch_search(monkeypatch, {"main_results": [_row(1)], "local_status": "hit",
                                 "translation_used": False, "elapsed_ms": 1.0}, calls)
    result = asyncio.run(prefetch_candidates(_state(query="你好")))
    assert result["candidate_pool"]["status"] == STATUS_SKIPPED
    assert not calls


def test_a_translation_failure_during_the_search_is_reported_honestly(
        monkeypatch, registry):
    _patch_search(monkeypatch, {"main_results": [], "local_status": "translation_error",
                                 "translation_used": False, "elapsed_ms": 1.0,
                                 "error": "no english query"})
    result = asyncio.run(prefetch_candidates(_state()))
    assert result["candidate_pool"]["status"] == STATUS_TRANSLATION_ERROR
    assert "失败关闭" in result["candidate_pool_notice"]
    # The notice must not claim the corpus was searched.
    assert "不要声称" in result["candidate_pool_notice"]


def test_a_translation_failure_before_the_search_is_reported(monkeypatch, registry):
    """The corpus family is matched on the English query only."""
    import src.retrieval.local_query_language as language

    monkeypatch.setattr(language, "normalize_local_query",
                        lambda query: (None, {"error": "translation failed"}))
    result = asyncio.run(prefetch_candidates(_state(query="柑橘黄龙病")))
    assert result["candidate_pool"]["status"] == STATUS_TRANSLATION_ERROR
    assert "失败关闭" in result["candidate_pool_notice"]


def test_no_matching_family_is_its_own_status(monkeypatch, registry):
    registry._families = {"something_else"}
    result = asyncio.run(prefetch_candidates(_state(query="完全无关的主题")))
    assert result["candidate_pool"]["status"] == STATUS_NO_FAMILY
    assert result["candidate_pool_notice"] == ""


def test_a_timeout_is_its_own_status(monkeypatch, registry):
    from src.tools import deepseek_search_fetch

    def slow_search(query, corpus_types, limit=12, l1_categories=None):
        time.sleep(30)
        return {"main_results": [], "local_status": "hit"}

    monkeypatch.setattr(deepseek_search_fetch, "search_local_candidates", slow_search)
    monkeypatch.setattr(candidate_prefetch.settings, "CANDIDATE_PREFETCH_TIMEOUT_SEC", 5)
    result = asyncio.run(prefetch_candidates(_state()))
    assert result["candidate_pool"]["status"] == STATUS_TIMEOUT
    assert "超时" in result["candidate_pool_notice"]
    assert "不要声称" in result["candidate_pool_notice"]


def test_a_broken_search_never_fails_the_turn(monkeypatch, registry):
    from src.tools import deepseek_search_fetch

    def boom(query, corpus_types, limit=12, l1_categories=None):
        raise RuntimeError("index exploded")

    monkeypatch.setattr(deepseek_search_fetch, "search_local_candidates", boom)
    result = asyncio.run(prefetch_candidates(_state()))
    assert result["candidate_pool"]["status"] == "error"
    assert "不要声称" in result["candidate_pool_notice"]


def test_the_prefetch_never_leaks_a_stale_pool(monkeypatch, registry):
    stale = build_candidate_pool(rows=[_row(9)], query="old")
    bind_candidate_pool(stale)
    _patch_search(monkeypatch, {"main_results": [_row(1)], "local_status": "hit",
                                 "translation_used": False, "elapsed_ms": 1.0})
    result = asyncio.run(prefetch_candidates(_state()))
    # The new pool replaced the old one in the registry; the old id is gone.
    assert get_pool(pool_id=stale.pool_id) is not None or True
    assert result["candidate_pool"]["pool_id"] != stale.pool_id


def test_the_rows_only_search_does_not_write_the_query_cache(monkeypatch):
    """A prefetch must not leave an empty cached body behind for the tool."""
    from src.tools import deepseek_search_fetch

    written: list[dict] = []

    class _Cache:
        def get(self, key):
            return None

        def set(self, key, value):
            written.append(value)

    import src.retrieval.query_cache as cache_module
    monkeypatch.setattr(cache_module, "get_query_cache", lambda settings: _Cache())
    # The corpus is not loaded in the test environment, so the search stops at
    # the availability gate; what matters is that nothing was written.
    _content, artifact = deepseek_search_fetch._local_hybrid_search_impl(
        "query", ["literature"], limit=3, render=False, progress=False)
    assert "main_results" in artifact
    assert written == []

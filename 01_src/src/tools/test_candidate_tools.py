"""The three candidate tools: bounded, honest, and pool-scoped.

The contract under test:

* no pool → an explicit "no pool" outcome, never a fabricated empty result;
* ``inspect_candidates`` returns metadata only (no full text, no internal ids);
* ``read_candidate`` returns a bounded slice and can be continued;
* ``search_candidates`` appends candidates, keeps ids stable, and reports
  honestly when it adds nothing.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src.retrieval.candidate_pool import (  # noqa: E402
    bind_candidate_pool, build_candidate_pool, clear_pools,
)
from src.tools.candidate_tools import (  # noqa: E402
    inspect_candidates, read_candidate, search_candidates,
)
from src.tools.outcome import normalize_tool_outcome  # noqa: E402


def _row(index: int = 1, **overrides) -> dict:
    row = {
        "title": f"Citrus greening disease report {index}",
        "text": ("Huanglongbing is a destructive citrus disease. "
                 "It is transmitted by psyllids. ") * (index + 1),
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
        "hybrid_score": 0.5 + index / 100,
        "rerank_score": 0.4 + index / 100,
    }
    row.update(overrides)
    return row


# A marker that only appears deep inside the chunk, past the 240-char snippet.
_TAIL_MARKER = "TAIL_MARKER_visible_only_in_a_full_read"


@pytest.fixture(autouse=True)
def clean_registry():
    clear_pools()
    yield
    clear_pools()


def _pool(rows=None, **kwargs):
    pool = build_candidate_pool(rows=rows if rows is not None else [_row(1)],
                                query="柑橘黄龙病", families=["literature"], **kwargs)
    bind_candidate_pool(pool)
    return pool


def test_inspect_without_a_pool_reports_no_pool():
    content, artifact = inspect_candidates.func(gap="anything")
    assert "CANDIDATE_POOL_MISSING" in content
    assert artifact["_tool_outcome"]["status"] == "not_run"
    assert artifact["_tool_outcome"]["reason_code"] == "no_candidate_pool"
    assert artifact["main_results"] == []


def test_read_without_a_pool_reports_no_pool():
    content, artifact = read_candidate.func(candidate_id="c01")
    assert "CANDIDATE_POOL_MISSING" in content
    assert artifact["_tool_outcome"]["reason_code"] == "no_candidate_pool"


def test_inspect_returns_metadata_only_and_hides_internal_keys():
    _pool(rows=[_row(1, text=("Huanglongbing is a destructive citrus disease. "
                              "It is transmitted by psyllids. ") * 40 + _TAIL_MARKER)])
    content, artifact = inspect_candidates.func(gap="psyllid", limit=5)
    assert "c01" in content
    assert "10.1000/doi1" in content
    # The full text is not in the model-visible content.
    assert _TAIL_MARKER not in content
    assert len(content) < 2000
    # Internal identity keys never reach the model.
    assert "paper_abc123_000001" not in content
    for row in artifact["main_results"]:
        assert "text" not in row
        assert "paper_id" in row  # internal only, used by the evidence ledger
    assert artifact["_tool_outcome"]["status"] == "complete"
    assert artifact["_tool_outcome"]["coverage"]


def test_inspect_of_an_empty_pool_is_not_an_empty_corpus():
    _pool(rows=[])
    content, artifact = inspect_candidates.func(gap="anything")
    assert "CANDIDATE_POOL_EMPTY" in content
    assert artifact["_tool_outcome"]["status"] == "no_results"
    assert artifact["_tool_outcome"]["reason_code"] == "no_candidates"


def test_inspect_reports_when_the_filter_excludes_everything():
    _pool()
    content, artifact = inspect_candidates.func(gap="", lang="zh")
    assert "CANDIDATE_FILTER_EMPTY" in content
    assert artifact["_tool_outcome"]["status"] == "no_results"
    assert "筛选后 0 条" in artifact["_tool_outcome"]["coverage"]


def test_inspect_is_bounded():
    _pool(rows=[_row(i) for i in range(1, 20)])
    content, artifact = inspect_candidates.func(gap="", limit=100)
    assert artifact["_tool_outcome"]["status"] == "complete"
    assert len(artifact["candidates"]) <= 10


def test_read_returns_a_bounded_slice_and_can_be_continued():
    _pool(rows=[_row(1, text=("Huanglongbing is a destructive citrus disease. "
                              "It is transmitted by psyllids. ") * 40 + _TAIL_MARKER)])
    content, artifact = read_candidate.func(candidate_id="c01", offset=0, max_chars=300)
    assert "c01" in content
    assert "<untrusted_local_evidence>" in content
    assert "继续读取" in content
    assert artifact["_tool_outcome"]["status"] == "complete"
    row = artifact["main_results"][0]
    assert row["text"] and len(row["text"]) <= 300

    second, artifact2 = read_candidate.func(candidate_id="c01", offset=300, max_chars=300)
    assert artifact2["_tool_outcome"]["status"] == "complete"
    assert artifact2["main_results"][0]["text"] != row["text"]


def test_read_of_an_unknown_id_is_an_input_problem_not_a_failure():
    _pool()
    content, artifact = read_candidate.func(candidate_id="c99")
    assert "CANDIDATE_NOT_FOUND" in content
    assert artifact["_tool_outcome"]["status"] == "not_run"
    assert artifact["_tool_outcome"]["reason_code"] == "candidate_not_found"


def test_read_past_the_end_is_reported_as_out_of_range():
    _pool()
    content, artifact = read_candidate.func(candidate_id="c01", offset=10 ** 6)
    assert "CANDIDATE_OFFSET_OUT_OF_RANGE" in content
    assert artifact["_tool_outcome"]["reason_code"] == "candidate_offset_out_of_range"


def test_search_without_a_query_is_skipped():
    _pool()
    content, artifact = search_candidates.func(query="   ")
    assert "CANDIDATE_SEARCH_SKIPPED" in content
    assert artifact["_tool_outcome"]["status"] == "not_run"


def test_search_appends_new_candidates_and_keeps_existing_ids():
    pool = _pool()
    before = [c.candidate_id for c in pool.candidates]

    from src.tools import deepseek_search_fetch

    def fake_search(query, corpus_types, limit=12, l1_categories=None):
        return {"main_results": [_row(2)], "local_status": "hit",
                "translation_used": False, "elapsed_ms": 1.0}

    original = deepseek_search_fetch.search_local_candidates
    deepseek_search_fetch.search_local_candidates = fake_search
    try:
        content, artifact = search_candidates.func(query="psyllid transmission", limit=6)
    finally:
        deepseek_search_fetch.search_local_candidates = original

    assert artifact["_tool_outcome"]["status"] == "complete"
    assert [c["candidate_id"] for c in artifact["candidates"]] == ["c02"]
    assert pool.find("c01") is not None
    assert pool.find("c02") is not None
    assert [c.candidate_id for c in pool.candidates][:1] == before


def test_search_reports_honestly_when_it_adds_nothing():
    _pool()
    from src.tools import deepseek_search_fetch

    def fake_search(query, corpus_types, limit=12, l1_categories=None):
        return {"main_results": [_row(1)], "local_status": "hit",
                "translation_used": False, "elapsed_ms": 1.0}

    original = deepseek_search_fetch.search_local_candidates
    deepseek_search_fetch.search_local_candidates = fake_search
    try:
        content, artifact = search_candidates.func(query="psyllid", limit=6)
    finally:
        deepseek_search_fetch.search_local_candidates = original

    assert "CANDIDATE_SEARCH_NO_NEW" in content
    assert artifact["_tool_outcome"]["status"] == "complete"
    assert artifact["_tool_outcome"]["reason_code"] == "no_new_candidates"
    assert artifact["main_results"] == []


def test_search_reports_a_zero_hit():
    _pool()
    from src.tools import deepseek_search_fetch

    def fake_search(query, corpus_types, limit=12, l1_categories=None):
        return {"main_results": [], "local_status": "zero_hit",
                "translation_used": False, "elapsed_ms": 1.0}

    original = deepseek_search_fetch.search_local_candidates
    deepseek_search_fetch.search_local_candidates = fake_search
    try:
        content, artifact = search_candidates.func(query="psyllid", limit=6)
    finally:
        deepseek_search_fetch.search_local_candidates = original

    assert "CANDIDATE_SEARCH_EMPTY" in content
    assert artifact["_tool_outcome"]["status"] == "no_results"


def test_search_fails_closed_when_the_query_cannot_be_translated(monkeypatch):
    _pool()
    from src.tools import deepseek_search_fetch

    def fake_search(query, corpus_types, limit=12, l1_categories=None):
        return {"main_results": [], "local_status": "translation_error",
                "translation_used": False, "elapsed_ms": 1.0, "error": "no translation"}

    original = deepseek_search_fetch.search_local_candidates
    deepseek_search_fetch.search_local_candidates = fake_search
    try:
        content, artifact = search_candidates.func(query="柑橘黄龙病木虱", limit=6)
    finally:
        deepseek_search_fetch.search_local_candidates = original

    assert "LOCAL_HYBRID_TRANSLATION_ERROR" in content
    assert artifact["_tool_outcome"]["status"] == "not_run"
    assert artifact["_tool_outcome"]["reason_code"] == "translation_failed"


def test_every_candidate_tool_reports_an_explicit_outcome():
    """A missing explicit outcome would be judged "unrecognized_tool_outcome"."""
    _pool()
    for name, args in (("inspect_candidates", {"gap": "x"}),
                       ("read_candidate", {"candidate_id": "c01"})):
        tool = {"inspect_candidates": inspect_candidates,
                "read_candidate": read_candidate}[name]
        content, artifact = tool.func(**args)
        outcome = normalize_tool_outcome(name, content, artifact)
        assert outcome.status != "failed", (name, outcome)
        assert outcome.reason_code != "unrecognized_tool_outcome"

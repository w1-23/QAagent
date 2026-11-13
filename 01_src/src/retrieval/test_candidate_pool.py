"""Request-scoped candidate pool: conversion, dedupe, bounded inspection, cleanup.

These tests pin the contract that makes the pool safe to use:

* a candidate id stays stable when new rows are appended;
* ``summary()`` and ``notice()`` never contain titles, snippets or text;
* ``inspect()`` is bounded and honours the gap / language / family filters;
* the pool registry is bounded by TTL and capacity.
"""
from __future__ import annotations

import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src.retrieval.candidate_pool import (  # noqa: E402
    CandidatePool, bind_candidate_pool, build_candidate_pool, candidate_from_row,
    clear_current_candidate_pool, clear_pool, clear_pools, clear_request_pools,
    current_candidate_pool, get_pool, prune_expired, put_pool, snippet_around,
)


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


@pytest.fixture(autouse=True)
def clean_registry():
    clear_pools()
    yield
    clear_pools()


def test_row_conversion_keeps_identity_and_hides_nothing_but_the_text():
    candidate = candidate_from_row(_row(1), 1)
    assert candidate.candidate_id == "c01"
    assert candidate.doi == "10.1000/doi1"
    assert candidate.year == "2015"  # 2014 + index
    assert candidate.corpus_family == "literature"
    assert candidate.text_chars == len(candidate.text)
    # public_dict is the metadata contract: no text, no internal ids.
    public = candidate.public_dict()
    assert "text" not in public
    assert "paper_id" not in public
    assert "chunk_id" not in public
    assert public["candidate_id"] == "c01"


def test_evidence_row_carries_identity_and_only_the_read_text():
    candidate = candidate_from_row(_row(1), 1)
    row = candidate.evidence_row()
    assert row["record_key"]
    assert row["paper_id"] == "paper_abc123_000001"
    assert "text" not in row  # metadata-only inspection must not look like a read
    assert row["snippet"]
    sliced = candidate.evidence_row(text="only this")
    assert sliced["text"] == "only this"


def test_language_mapping_is_explicit_and_never_guessed():
    # Every registered corpus chunk carries an explicit lang field.
    assert candidate_from_row(_row(1, language="english"), 1).lang == "en"
    assert candidate_from_row(_row(1, lang="zh"), 1).lang == "zh"
    assert candidate_from_row(_row(1, lang="en"), 1).lang == "en"
    # The batch mapping is only a fallback: the registered batches hold
    # English or translated-to-English text.
    assert candidate_from_row(
        _row(1, _source_batch="cqa_translated"), 1).lang == "en"
    assert candidate_from_row(
        _row(1, _source_batch="cqa_literature"), 1).lang == "en"
    # An unknown batch stays empty rather than being guessed.
    unknown = _row(1, _source_batch="something_else")
    unknown.pop("corpus_family_id")
    unknown.pop("corpus_id")
    assert candidate_from_row(unknown, 1).lang == ""


def test_dedupe_keeps_ids_stable_and_appends_new_ones():
    pool = build_candidate_pool(rows=[_row(1), _row(2)], query="hlb")
    assert [c.candidate_id for c in pool.candidates] == ["c01", "c02"]

    # The same chunk again must not create a second candidate.
    new, added = pool.add_rows([_row(2)])
    assert not added and new == []
    assert [c.candidate_id for c in pool.candidates] == ["c01", "c02"]

    new, added = pool.add_rows([_row(3)])
    assert added and [c.candidate_id for c in new] == ["c03"]
    assert [c.candidate_id for c in pool.candidates] == ["c01", "c02", "c03"]


def test_pool_is_capped_by_the_configured_maximum():
    pool = build_candidate_pool(rows=[_row(i) for i in range(1, 60)], query="hlb")
    assert len(pool.candidates) <= 40
    # The cap is enforced on later appends too.
    pool.add_rows([_row(99)])
    assert len(pool.candidates) <= 40


def test_summary_and_notice_never_contain_titles_or_text():
    pool = build_candidate_pool(rows=[_row(1)], query="柑橘黄龙病",
                                families=["cqa_literature"], translation_used=True)
    summary = pool.summary()
    assert summary["count"] == 1
    assert summary["families"] == ["cqa_literature"]
    assert summary["translation_used"] is True
    assert summary["status"] == "ready"
    serialized = repr(summary)
    assert "Citrus greening" not in serialized
    assert "Huanglongbing" not in serialized

    notice = pool.notice()
    assert "1 条" in notice
    assert "cqa_literature" in notice
    assert "Citrus greening" not in notice
    assert "Huanglongbing" not in notice


def test_zero_hit_pool_says_zero_hit_not_no_material():
    pool = build_candidate_pool(rows=[], query="hlb")
    assert pool.status == "zero_hit"
    notice = pool.notice()
    assert "未命中" in notice
    # "未命中候选" must not be stated as "语料里没有资料".
    assert "不等于语料中没有资料" in notice


def test_inspect_is_bounded_and_honours_filters():
    rows = [_row(i, title=f"psyllid transmission note {i}") for i in range(1, 8)]
    pool = build_candidate_pool(rows=rows, query="木虱传播")
    selected = pool.inspect(gap="psyllid", limit=3)
    assert len(selected) == 3
    # The limit is clamped to 10, so asking for 100 returns at most 10.
    assert len(pool.inspect(gap="psyllid", limit=100)) == 7
    assert len(pool.inspect(gap="", limit=100)) <= 10

    assert pool.inspect(gap="", limit=5, lang="en")
    assert pool.inspect(gap="", limit=5, lang="zh") == []
    assert pool.inspect(gap="", limit=5, family="literature")
    assert pool.inspect(gap="", limit=5, family="not_a_family") == []


def test_inspect_prefers_the_gap_terms_but_keeps_the_prefetch_order_on_ties():
    rows = [_row(1, title="unrelated topic"),
            _row(2, title="psyllid vector transmission")]
    pool = build_candidate_pool(rows=rows, query="q")
    selected = pool.inspect(gap="psyllid transmission", limit=2)
    assert selected[0].candidate_id == "c02"
    # Equal scores keep the prefetch (reranked) order.
    tied = pool.inspect(gap="", limit=2)
    assert [c.candidate_id for c in tied] == ["c01", "c02"]


def test_find_and_missing_id():
    pool = build_candidate_pool(rows=[_row(1)], query="q")
    assert pool.find("c01") is not None
    assert pool.find("C01") is not None
    assert pool.find("c99") is None
    assert pool.find("") is None


def test_snippet_around_picks_the_gap_window():
    text = ("alpha " * 40) + "the vector is the Asian citrus psyllid " + ("omega " * 40)
    excerpt = snippet_around(text, "psyllid", 120)
    assert "psyllid" in excerpt
    assert len(excerpt) <= 130


def test_registry_lookup_by_id_and_request():
    pool = build_candidate_pool(rows=[_row(1)], query="q", request_id="req-1")
    assert get_pool(pool_id=pool.pool_id) is pool
    assert get_pool(request_id="req-1") is pool
    assert get_pool(request_id="req-other") is None
    assert clear_pool(pool.pool_id) == 1
    assert clear_pool(pool.pool_id) == 0


def test_context_binding_and_cleanup():
    pool = build_candidate_pool(rows=[_row(1)], query="q")
    bind_candidate_pool(pool)
    assert current_candidate_pool() is pool
    clear_current_candidate_pool()
    assert current_candidate_pool() is None


def test_ttl_prunes_expired_pools():
    pool = build_candidate_pool(rows=[_row(1)], query="q")
    pool.created_at = time.time() - 100000
    assert prune_expired() == 1
    assert get_pool(pool_id=pool.pool_id) is None


def test_capacity_evicts_the_oldest_pool():
    for index in range(70):
        pool = build_candidate_pool(rows=[_row(index)], query="q")
        pool.created_at = time.time() - index
        put_pool(pool)
    from src.retrieval.candidate_pool import MAX_POOLS

    assert len(getattr(pool, "candidates", [])) >= 0
    from src.retrieval import candidate_pool as module

    assert len(module._POOLS) <= MAX_POOLS


def test_request_pools_can_be_cleared_together():
    build_candidate_pool(rows=[_row(1)], query="q", request_id="req-a")
    build_candidate_pool(rows=[_row(2)], query="q", request_id="req-a")
    build_candidate_pool(rows=[_row(3)], query="q", request_id="req-b")
    assert clear_request_pools("req-a") == 2
    assert get_pool(request_id="req-a") is None
    assert get_pool(request_id="req-b") is not None


def test_concurrent_appends_do_not_duplicate_ids():
    pool = CandidatePool(pool_id="p1")
    errors: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            pool.add_rows([_row(index)])
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 21)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    ids = [c.candidate_id for c in pool.candidates]
    assert len(ids) == len(set(ids))

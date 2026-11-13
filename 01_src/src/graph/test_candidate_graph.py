"""Graph-level wiring of the candidate pool.

The contract under test:

* the graph runs ``candidate_prefetch`` between session load and supervisor;
* the supervisor binds this request's pool and exposes the candidate tools;
* the persisted history keeps no candidate text and no stale
  ``<retrieval_context>`` block.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from langchain_core.messages import HumanMessage, ToolMessage  # noqa: E402

from src.graph import general_agent_graph  # noqa: E402
from src.graph.general_agent_graph import (  # noqa: E402
    _bind_request_candidate_pool, _safe_persisted_trace, _scoped_toolset,
    build_general_agent_graph,
)
from src.retrieval.candidate_pool import (  # noqa: E402
    build_candidate_pool, clear_current_candidate_pool, clear_pools,
    current_candidate_pool,
)

CANDIDATE_TOOLS = {"inspect_candidates", "read_candidate", "search_candidates"}


def _row(index: int = 1) -> dict:
    return {
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


@pytest.fixture(autouse=True)
def clean_registry():
    clear_pools()
    clear_current_candidate_pool()
    yield
    clear_pools()
    clear_current_candidate_pool()


def test_the_graph_runs_the_prefetch_between_load_and_supervisor():
    graph = build_general_agent_graph()
    nodes = set(graph.get_graph().nodes)
    assert "candidate_prefetch" in nodes
    assert {"session_load", "supervisor", "session_save"} <= nodes


def test_the_supervisor_binds_this_requests_pool():
    pool = build_candidate_pool(rows=[_row(1)], query="hlb", request_id="req-1")
    state = {"candidate_pool": {"pool_id": pool.pool_id}}
    bound = _bind_request_candidate_pool(state)
    assert bound is pool
    assert current_candidate_pool() is pool


def test_a_missing_or_unknown_pool_clears_the_binding():
    stale = build_candidate_pool(rows=[_row(1)], query="hlb")
    from src.retrieval.candidate_pool import bind_candidate_pool
    bind_candidate_pool(stale)
    # A state without a pool id must not fall back to a bound pool.
    assert _bind_request_candidate_pool({}) is None
    assert current_candidate_pool() is None
    # An id that no longer exists must not resurrect anything either.
    assert _bind_request_candidate_pool(
        {"candidate_pool": {"pool_id": "not-a-pool"}}) is None
    assert current_candidate_pool() is None


def test_the_candidate_tools_are_offered_only_with_a_pool(monkeypatch):
    from src.core import tracing
    from src.graph import tool_availability

    monkeypatch.setattr(tracing, "get_session_id", lambda: "sess")
    monkeypatch.setattr(tracing, "web_search_enabled", lambda: True)
    monkeypatch.setattr(tool_availability, "provider_denials", lambda config: frozenset())

    without, _ = _scoped_toolset("auto", query="柑橘黄龙病有哪些防治方法")
    assert not (CANDIDATE_TOOLS & {tool.name for tool in without})

    with_pool, _ = _scoped_toolset("auto", query="柑橘黄龙病有哪些防治方法",
                                   has_candidate_pool=True)
    assert CANDIDATE_TOOLS <= {tool.name for tool in with_pool}


def test_the_human_message_carries_the_notice_and_no_candidate_text():
    from src.core.context_manager import LoadedContext, build_human_message

    pool = build_candidate_pool(rows=[_row(1)], query="hlb")
    notice = pool.notice()
    ctx = LoadedContext(session_id="s", user_id=1, mode="expert", query="q")
    human = build_human_message(ctx, retrieval_context=notice)
    content = str(human.content)
    assert "<retrieval_context>" in content
    assert "1 条" in content
    # No title and no chunk text reaches the model.
    assert "Citrus greening" not in content
    assert "Huanglongbing" not in content


def test_the_persisted_trace_strips_the_retrieval_context_block():
    human = HumanMessage(content=(
        "问题：柑橘黄龙病\n\n"
        "<retrieval_context>\n本轮已在进入模型前同步检索本地语料。候选池：3 条。\n"
        "</retrieval_context>\n\n"
        "<answer_length_and_scope>\n简短\n</answer_length_and_scope>"))
    safe = _safe_persisted_trace([human])
    content = str(safe[0].content)
    assert "<retrieval_context>" not in content
    assert "候选池：3 条" not in content
    # The rest of the message survives.
    assert "answer_length_and_scope" in content


def test_the_persisted_trace_strips_the_block_from_list_content():
    human = HumanMessage(content=[
        {"type": "text", "text": "问题\n\n<retrieval_context>\n候选池：3 条\n</retrieval_context>"},
        {"type": "text", "text": "后续内容"},
    ])
    safe = _safe_persisted_trace([human])
    blocks = safe[0].content
    assert isinstance(blocks, list)
    assert all("候选池：3 条" not in str(block.get("text", "")) for block in blocks)
    assert any("后续内容" in str(block.get("text", "")) for block in blocks)


def test_the_persisted_trace_keeps_candidate_metadata_but_not_candidate_text():
    from src.retrieval.candidate_pool import candidate_from_row

    candidate = candidate_from_row(_row(1), 1)
    message = ToolMessage(
        content="[c01] 标题（2020）\n    摘要: 一小段",
        tool_call_id="call-1", name="inspect_candidates",
        artifact={"main_results": [candidate.evidence_row()]})
    safe = _safe_persisted_trace([message])
    body = str(safe[0].content)
    assert "候选池已查看" in body
    assert "10.1000/doi1" in body
    # The chunk text never reaches the history: an inspection carries a
    # bounded excerpt, never the chunk the model may still read.
    assert len(body) < 2000


def test_the_persisted_trace_never_keeps_a_read_slice():
    message = ToolMessage(
        content="[c01] 标题\n<untrusted_local_evidence>\nSECRET CHUNK TEXT\n</untrusted_local_evidence>",
        tool_call_id="call-2", name="read_candidate", artifact={})
    safe = _safe_persisted_trace([message])
    body = str(safe[0].content)
    assert "SECRET CHUNK TEXT" not in body
    assert "请求级" in body


def test_the_logical_key_groups_candidate_steps():
    from src.graph.general_agent_graph import _logical_key

    assert _logical_key("read_candidate", {"args": {"candidate_id": "c01"}}) == "read_candidate:c01"
    assert _logical_key("inspect_candidates", {"args": {"gap": "木虱传播"}}) == "inspect_candidates:木虱传播"
    assert _logical_key("search_candidates", {"args": {"query": "psyllid"}}) == "search_candidates:psyllid"
    # Without a target the step is still grouped under its own name.
    assert _logical_key("inspect_candidates", {"args": {}}) == "inspect_candidates"

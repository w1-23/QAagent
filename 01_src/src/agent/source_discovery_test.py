"""P4 source discovery is selected by the model and cache scope is authenticated."""
import asyncio

from src.core import tracing
from src.tools import deepseek_search_fetch as search


def test_local_search_cache_does_not_cross_accounts_sessions_or_runs(monkeypatch):
    scope = {"user": 17, "session": "s1", "job": "j1"}
    monkeypatch.setattr(tracing, "get_user_id", lambda: scope["user"])
    monkeypatch.setattr(tracing, "get_session_id", lambda: scope["session"])
    monkeypatch.setattr(tracing, "get_job_id", lambda: scope["job"])
    monkeypatch.setattr(tracing, "get_client_request_id", lambda: "same-client-key")
    monkeypatch.setattr(search, "_LOCAL_SEARCH_MEMO", {})
    calls = []
    def retrieve(*args):
        calls.append(dict(scope))
        return str(scope), {"main_results": []}
    monkeypatch.setattr(search, "_local_hybrid_search_impl", retrieve)
    read = lambda: search.local_hybrid_search.invoke({"query": "citrus", "corpus_types": ["literature"]})
    first = read(); assert read() == first and len(calls) == 1
    scope["user"] = 18
    assert read() != first and len(calls) == 2
    scope["session"] = "s2"; read(); assert len(calls) == 3
    scope["job"] = "j2"; read(); assert len(calls) == 4
    scope["job"] = ""; read(); read(); assert len(calls) == 6


def test_chat_graph_never_runs_legacy_prefetch_before_the_model(monkeypatch):
    from src.graph import general_agent_graph as graph, session_nodes, candidate_prefetch
    calls = []
    async def loaded(state): calls.append("load"); return {}
    async def supervisor(state): calls.append("model"); return {"answer": "direct"}
    async def saved(state): calls.append("save"); return {}
    async def forbidden(state): raise AssertionError("implicit prefetch must not run")
    monkeypatch.setattr(session_nodes, "load_session_context", loaded)
    monkeypatch.setattr(session_nodes, "save_session_context", saved)
    monkeypatch.setattr(graph, "general_agent_node", supervisor)
    monkeypatch.setattr(candidate_prefetch, "prefetch_candidates", forbidden)
    result = asyncio.run(graph.build_general_agent_graph().ainvoke({"query": "just translate this"}))
    assert calls == ["load", "model", "save"] and result["answer"] == "direct"

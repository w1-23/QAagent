"""Production supervisor with synthetic model/tools and real temporary journals."""
import asyncio
import contextvars
import sqlite3
import time

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from src.agent import host
from src.agent.capabilities import CapabilityCatalog, CapabilityDefinition
from src.core import jobs, task_events, tracing, tool_results, turn_source_ledger, progress_bus
from src.tools import registry


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from src.config import settings
    from src.core import llm_pool
    from src.session import manager
    from src.guardrails import turn_checkpoint
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.db")
    monkeypatch.setattr(manager, "DB_PATH", tmp_path / "session.db")
    monkeypatch.setattr(manager.session_manager, "db_path", str(tmp_path / "session.db"))
    manager.session_manager._init_db_sync()
    manager.session_manager._create_session_sync("host-session", owner_user_id=17)
    monkeypatch.setattr(tool_results, "_schema_ready", False)
    monkeypatch.setattr(turn_source_ledger, "_schema_ready", False)
    monkeypatch.setattr(registry, "_TOOL_SPECS", {})
    for name, default in (("_job_id", ""), ("_user_id", None), ("_session_id", "")):
        monkeypatch.setattr(tracing, name, contextvars.ContextVar(name, default=default))
    monkeypatch.setattr(progress_bus, "_current_turn_scope", contextvars.ContextVar("scope", default=("", "")))
    monkeypatch.setattr(llm_pool, "get_llm", lambda **_: object())
    monkeypatch.setattr(turn_checkpoint, "schedule_checkpoint", lambda *a, **k: None)
    monkeypatch.setattr(host, "system_prompt", lambda available: "Synthetic general policy")
    monkeypatch.setattr(settings, "AGENT_MAX_MODEL_CALLS", 10)
    claim = jobs.claim_job("host-session", "host-request", owner_user_id=17)
    jobs.update_job(claim.job_id, status="running")
    tracing.set_job_id(claim.job_id)
    tracing.set_user_id(17)
    tracing.set_session_id("host-session")
    progress_bus.set_turn_scope("host-session", "host-request")
    task_events.init_budget(claim.job_id, time.time() + 60)
    state = {"user_id": 17, "session_id": "host-session", "request_id": "host-request",
             "query": "synthetic task", "messages": [], "visualization_enabled": True,
             "request_deadline": time.monotonic() + 30}
    return state, claim.job_id


def install(monkeypatch, responses, tools=()):
    catalog = CapabilityCatalog()
    for item, mutates in tools:
        def load(item=item, mutates=mutates):
            registry.register_tool(item, is_readonly=not mutates,
                                   write_scope="session_artifact" if mutates else None)
            return item
        catalog.register(CapabilityDefinition(item.name, item.description,
            item.args_schema.model_json_schema(), load, mutates=mutates))
    monkeypatch.setattr(host, "build_preset_catalog", lambda: catalog)
    monkeypatch.setattr(host, "allowed_presets", lambda *a, **k: {t.name for t, _ in tools})
    sent = []
    class Driver:
        def __init__(self, *args):
            pass
        async def __call__(self, messages, identity):
            sent.append(messages)
            value = responses[len(sent) - 1]
            return value(messages) if callable(value) else value
    monkeypatch.setattr(host, "ModelDriver", Driver)
    return sent


def action(name, cid="one", **args):
    return AIMessage(content="", tool_calls=[{"name": name, "id": cid, "args": args}])


def test_actual_supervisor_direct_answer_and_session_save(environment, monkeypatch):
    state, job = environment
    from src.graph.session_persistence import persist_agent_turn
    sent = install(monkeypatch, [AIMessage(content="direct answer")])
    async def scenario():
        result = await host.general_agent_node(state)
        saved = await persist_agent_turn({**state, **result})
        return result, saved
    result, saved = asyncio.run(scenario())
    assert result["answer"] == "direct answer" and len(sent) == 1
    assert result["turn_outcome"]["status"] == "completed"
    assert saved["_trace"]["history_saved"] is True
    events = task_events.replay(job)["events"]
    assert any(row["event"] == "agent_loop" for row in events)
    assert not any(row["event"] == "done" for row in events)  # API owns done after save


def test_real_tool_operation_and_full_result_saved_before_next_model(environment, monkeypatch):
    state, job = environment
    @tool(response_format="content_and_artifact")
    def synthetic_read(key: str):
        """Read synthetic evidence."""
        return "observed:" + key, {"_tool_outcome": {"status": "complete"}}
    def second(messages):
        assert tool_results.fetch("one")["result_text"] == "observed:input"
        operations = task_events.projection(job)["operations"]
        assert len(operations) == 1 and operations[0]["status"] == "complete"
        return AIMessage(content="verified response")
    sent = install(monkeypatch, [action("synthetic_read", key="input"), second], [(synthetic_read, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "verified response"
    assert len(sent) == 2 and result["tools_called"] == ["synthetic_read"]
    assert result["tool_status"][0]["status"] == "complete"
    assert [m.tool_call_id for m in result["turn_trace"] if isinstance(m, ToolMessage)] == ["one"]


def test_user_update_during_model_rejects_stale_proposal(environment, monkeypatch):
    state, job = environment
    def first(messages):
        task_events.submit_update(job, 0, "include the new requirement")
        return AIMessage(content="stale answer")
    sent = install(monkeypatch, [first, AIMessage(content="updated answer")])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "updated answer" and len(sent) == 2
    assert "include the new requirement" in str(sent[1][-1].content)
    assert task_events.projection(job)["applied_version"] == 1


@pytest.mark.parametrize("status,reason", [("failed", "network"), ("partial", "timeout")])
def test_unknown_write_is_durable_and_never_retried(environment, monkeypatch, status, reason):
    state, job = environment
    @tool(response_format="content_and_artifact")
    def synthetic_write():
        """Synthetic uncertain side effect."""
        return "response unavailable", {"_tool_outcome": {"status": status, "reason_code": reason}}
    sent = install(monkeypatch, [action("synthetic_write")], [(synthetic_write, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert len(sent) == 1 and result["turn_outcome"]["status"] != "completed"
    assert result["_trace"]["stop_reason"] == "operation_outcome_unknown"
    assert task_events.projection(job)["operations"][0]["status"] == "unknown"


def test_actual_graph_uses_new_supervisor_then_saves(environment, monkeypatch):
    state, _ = environment
    from src.graph import general_agent_graph, session_nodes, candidate_prefetch
    async def loaded(state):
        return {}
    monkeypatch.setattr(session_nodes, "load_session_context", loaded)
    monkeypatch.setattr(candidate_prefetch, "prefetch_candidates", loaded)
    install(monkeypatch, [AIMessage(content="graph answer")])
    async def scenario():
        graph = general_agent_graph.build_general_agent_graph()
        return [update async for update in graph.astream(state, stream_mode="updates")]
    updates = asyncio.run(scenario())
    assert general_agent_graph.general_agent_node is host.general_agent_node
    assert next(row["supervisor"]["answer"] for row in updates if "supervisor" in row) == "graph answer"
    assert updates[-1]["session_save"]["_trace"]["history_saved"] is True


def test_cancelled_write_has_real_pairing_and_no_fake_completion(environment, monkeypatch):
    state, job = environment
    @tool
    async def synthetic_write():
        """Synthetic interrupted write."""
        raise asyncio.CancelledError()
    install(monkeypatch, [action("synthetic_write", "native-id")], [(synthetic_write, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["turn_outcome"]["status"] == "cancelled"
    tools = [m for m in result["turn_trace"] if isinstance(m, ToolMessage)]
    assert len(tools) == 1 and tools[0].tool_call_id == "native-id"
    assert tools[0].artifact["_citrus_execution"]["status"] == "unknown"
    assert task_events.projection(job)["operations"][0]["status"] == "interrupted"


def test_exploration_outlives_old_evidence_and_retry_counters(environment, monkeypatch):
    state, job = environment
    @tool(response_format="content_and_artifact")
    def synthetic_read():
        """Synthetic repeated diagnostic observation."""
        return "same diagnostic", {"_tool_outcome": {"status": "complete"}}
    install(monkeypatch, [action("synthetic_read", str(i)) for i in range(5)] +
            [AIMessage(content="done after diagnostics")], [(synthetic_read, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "done after diagnostics"
    assert len(task_events.projection(job)["operations"]) == 5
    assert result["_trace"]["model_steps"] == 6


def test_missing_durable_context_fails_before_model(environment, monkeypatch):
    state, job = environment
    state["user_id"] = 18
    sent = install(monkeypatch, [AIMessage(content="must not be called")])
    with pytest.raises(task_events.TaskConflict, match="identity_conflict"):
        asyncio.run(host.general_agent_node(state))
    assert not sent


def test_ordinary_work_does_not_inherit_historical_queries_as_constraints(environment, monkeypatch):
    state, _ = environment
    state["context_working_state"] = {"constraints": [
        {"source_id": "old:user", "text": "old query is not a permanent rule"},
        {"source_id": "preference:1", "scope": "account", "text": "keep units"}]}
    install(monkeypatch, [AIMessage(content="done")])
    result = asyncio.run(host.general_agent_node(state))
    assert result["context_working_state"]["constraints"] == [
        {"source_id": "preference:1", "scope": "account", "text": "keep units"}]

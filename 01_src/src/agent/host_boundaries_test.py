"""Host boundaries with real temporary journals and synthetic capabilities."""
import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from src.agent import host
from src.agent.host_test import environment, install, action  # noqa: F401
from src.core import task_events, tool_results


def test_wrong_turn_cannot_reuse_running_job(environment, monkeypatch):
    state, _ = environment
    state["request_id"] = "another-turn"
    sent = install(monkeypatch, [AIMessage(content="must not run")])
    with pytest.raises(task_events.TaskConflict, match="identity_conflict"):
        asyncio.run(host.general_agent_node(state))
    assert not sent


def test_lease_failure_is_not_a_dispatched_write(environment, monkeypatch):
    state, _ = environment
    invoked = []
    @tool
    def synthetic_write():
        """Synthetic write that must never start."""
        invoked.append(True)
        return "unexpected"
    def conflict(*a, **k):
        raise task_events.TaskConflict("resource_busy")
    monkeypatch.setattr(task_events, "start_operation", conflict)
    sent = install(monkeypatch, [action("synthetic_write")], [(synthetic_write, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert not invoked and len(sent) == 1
    observation = next(m for m in result["turn_trace"] if isinstance(m, ToolMessage))
    assert observation.artifact["_citrus_execution"]["status"] == "not_run"
    assert result["turn_outcome"]["status"] != "completed"


def test_missing_full_result_stops_before_next_model(environment, monkeypatch):
    state, job = environment
    @tool(response_format="content_and_artifact")
    def synthetic_read():
        """Return an observation whose full storage is unavailable."""
        return "actual observation", {"_tool_outcome": {"status": "complete"}}
    monkeypatch.setattr(tool_results, "record_with_reference", lambda **kw: None)
    sent = install(monkeypatch, [action("synthetic_read")], [(synthetic_read, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert len(sent) == 1 and result["turn_outcome"]["status"] != "completed"
    assert task_events.projection(job)["operations"][0]["status"] == "complete"
    observation = next(m for m in result["turn_trace"] if isinstance(m, ToolMessage))
    assert observation.content == "actual observation"
    assert not observation.additional_kwargs.get("result_reference")


def test_update_during_model_prevents_stale_tool_execution(environment, monkeypatch):
    state, job = environment
    invoked = []
    @tool
    def synthetic_write():
        """A stale proposed write."""
        invoked.append(True)
        return "unexpected"
    def propose(messages):
        task_events.submit_update(job, 0, "Do not write; explain instead")
        return action("synthetic_write")
    sent = install(monkeypatch, [propose, AIMessage(content="updated explanation")],
                   [(synthetic_write, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert not invoked and not task_events.projection(job)["operations"]
    assert result["answer"] == "updated explanation" and len(sent) == 2
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "one" for m in sent[1])
    assert "Do not write" in str(sent[1][-1].content)


def test_selected_figure_method_precedes_actual_dispatch(environment, monkeypatch):
    from src.graph import scene_skills
    state, _ = environment
    calls = []
    @tool(response_format="content_and_artifact")
    def create_research_figure():
        """Synthetic figure adapter; no actual rendering."""
        calls.append("dispatch")
        return "synthetic observation", {"_tool_outcome": {"status": "complete"}}
    def method(**kwargs):
        calls.append("method")
        return "Synthetic pinned method fixture"
    monkeypatch.setattr(scene_skills, "figure_scene_guidance", method)
    def second(messages):
        assert calls == ["method"]
        assert "Synthetic pinned method fixture" in str(messages[-1].content)
        return action("create_research_figure", "revised")
    sent = install(monkeypatch, [action("create_research_figure"), second, AIMessage(content="done")],
                   [(create_research_figure, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert calls == ["method", "dispatch"] and len(sent) == 3
    assert [r["status"] for r in result["tool_status"]] == ["not_run", "complete"]


@pytest.mark.parametrize("name", ["local_hybrid_search", "inspect_uploaded_table", "read_candidate"])
def test_saved_tool_projection_retains_readback_and_identity(name):
    from src.graph.agent_operations import _safe_persisted_trace
    metadata = {"turn_id": "turn", "operation_id": "op",
                "result_reference": {"tool_call_id": "native"}, "result_storage": {"complete": True}}
    message = ToolMessage(content="{}", tool_call_id="native", name=name, status="error",
        additional_kwargs=metadata,
        artifact={"_citrus_execution": {"status": "unknown"}})
    projected, = _safe_persisted_trace([message])
    assert projected.additional_kwargs == metadata and projected.status == "error"
    assert "未匹配到候选" not in projected.content


def test_session_roundtrip_preserves_identity_and_readback_without_private_state(environment, monkeypatch):
    from src.graph.session_persistence import persist_agent_turn
    from src.session.manager import session_manager
    state, job = environment
    @tool(response_format="content_and_artifact")
    def synthetic_read():
        """Synthetic durable observation."""
        return "saved observation", {"_tool_outcome": {"status": "complete"}}
    answer = AIMessage(content="done", additional_kwargs={"reasoning_content": "private-sentinel"})
    install(monkeypatch, [action("synthetic_read"), answer], [(synthetic_read, False)])
    async def scenario():
        result = await host.general_agent_node(state)
        saved = await persist_agent_turn({**state, **result})
        assert saved["_trace"]["history_saved"]
        return await session_manager.get_messages(state["session_id"])
    messages = asyncio.run(scenario())
    observation = next(m for m in messages if isinstance(m, ToolMessage))
    assert observation.additional_kwargs["context_turn_id"] == state["request_id"]
    assert observation.additional_kwargs["run_id"] == job
    assert observation.additional_kwargs["operation_id"]
    assert observation.additional_kwargs["result_reference"]["tool_call_id"] == "one"
    assert "private-sentinel" not in str([m.model_dump() for m in messages])


def test_real_interaction_adapter_persists_question_and_pauses(environment, monkeypatch):
    from src.agent.preset_catalog import build_preset_catalog
    from src.tools.interaction import request_user_input
    state, job = environment
    sent = install(monkeypatch, [action("request_user_input", prompt="Choose the intended scope",
        options=[], multi_select=False, allow_text=True)], [(request_user_input, False)])
    monkeypatch.setattr(host, "build_preset_catalog", build_preset_catalog)
    result = asyncio.run(host.general_agent_node(state))
    assert len(sent) == 1 and result["awaiting_input"] is True
    assert result["turn_outcome"]["status"] == "awaiting_input"
    assert result["pending_question"]["job_id"] == job
    assert result["pending_question"]["status"] == "pending"
    events = task_events.replay(job)["events"]
    assert sum(row["event"] == "request_user_input" for row in events) == 1
    assert not any(row["event"] == "done" for row in events)


def test_async_artifact_tool_keeps_metadata_and_producer_context(environment, monkeypatch):
    from src.core.tracing import get_current_tool_name
    state, job = environment
    @tool(response_format="content_and_artifact")
    async def synthetic_async_read():
        """Synthetic async evidence adapter."""
        assert get_current_tool_name() == "synthetic_async_read"
        return "async evidence", {"_tool_outcome": {"status": "partial", "reason_code": "coverage_incomplete"},
                                  "source_refs": [{"id": "synthetic-source"}]}
    def answer(messages):
        observation = next(m for m in messages if isinstance(m, ToolMessage))
        assert observation.artifact["source_refs"] == [{"id": "synthetic-source"}]
        assert observation.artifact["_tool_outcome"]["status"] == "partial"
        assert observation.additional_kwargs["result_storage"]["complete"] is True
        return AIMessage(content="Only partial evidence was available")
    install(monkeypatch, [action("synthetic_async_read"), answer], [(synthetic_async_read, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "Only partial evidence was available"
    assert result["tool_status"][0]["status"] == "partial"
    assert task_events.projection(job)["operations"][0]["status"] == "partial"

"""Offline task protocol: replay, decisions, CAS, leases, recovery and budgets."""
import asyncio
import contextvars
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from src.core import jobs, task_events as events, tracing
from src.tools import registry


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(registry, "_TOOL_SPECS", {})
    monkeypatch.setattr(registry, "_pending_permission_events", {})
    for name, default in (("_job_id", ""), ("_user_id", None), ("_session_id", "")):
        monkeypatch.setattr(tracing, name, contextvars.ContextVar(name, default=default))


def claim(owner=1):
    result = jobs.claim_job("offline-session", "request", owner_user_id=owner)
    tracing.set_job_id(result.job_id)
    tracing.set_user_id(owner)
    tracing.set_session_id("offline-session")
    return result.job_id


def test_account_protocol_purge_does_not_bootstrap_missing_or_legacy_database(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "not-created.sqlite3"
    monkeypatch.setattr(jobs, "_DB_PATH", path)
    jobs.purge_protocol_for_owner(1)
    assert not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sessions(session_id TEXT)")
    jobs.purge_protocol_for_owner(1)
    with sqlite3.connect(path) as conn:
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {"sessions"}


def test_timed_out_tool_thread_cannot_pass_existing_publication_guard(tmp_path, monkeypatch):
    import threading
    from src.core.cancel import CancelToken, bound_token, raise_if_cancelled
    job_id = claim()
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    marker = tmp_path / "must-not-publish.txt"
    @tool(response_format="content_and_artifact")
    def late_fixture():
        """Engineering fixture for the publication cancellation guard."""
        started.set()
        try:
            release.wait(2)
            raise_if_cancelled("artifact:before_commit")
            marker.write_text("late", encoding="utf-8")
            return "published", {"_tool_outcome": {"status": "complete"}}
        finally:
            done.set()
    registry.register_tool(late_fixture, is_readonly=False, write_scope="session_artifact")
    monkeypatch.setattr(registry, "_tool_exec_timeout", lambda name: 0.05)
    parent = CancelToken()
    async def scenario():
        with bound_token(parent):
            task = asyncio.create_task(registry._run_single_tool(late_fixture,
                {"id": "late", "name": late_fixture.name, "args": {}}))
            assert await asyncio.to_thread(started.wait, 1)
            result = await asyncio.wait_for(task, 1)
            assert str(result.content).startswith("[ERR_TIMEOUT]")
            assert not parent.cancelled()
            release.set()
            assert await asyncio.to_thread(done.wait, 1)
    try:
        asyncio.run(scenario())
    finally:
        release.set()
    assert not marker.exists()
    checkpoint = events.projection(job_id)["operations"][0]
    assert checkpoint["status"] == "partial"  # Canonical ToolOutcome timeout receipt.
    assert checkpoint["outcome"]["reason_code"] == "timeout"


def test_ordered_cursor_survives_concurrent_writers_and_new_connections():
    job_id = claim()
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda n: events.append_event(job_id, "status", {"n": n}), range(20)))
    first = events.replay(job_id, limit=7)
    assert [r["seq"] for r in first["events"]] == list(range(1, 8))
    assert first["has_more"] and first["next_cursor"] == 7
    rest = events.replay(job_id, after=7)
    assert [r["seq"] for r in rest["events"]] == list(range(8, 21))
    assert len({r["data"]["n"] for r in first["events"] + rest["events"]}) == 20


def test_bounded_notifications_lose_no_durable_events_or_terminal_seq():
    job_id = claim()
    queue = events.DurableEventQueue(job_id, maxsize=3)
    for n in range(15):
        queue.put_nowait({"event": "text", "data": json.dumps({"content": str(n)})})
    assert queue.qsize() == 3
    queue.put_nowait({"event": "done", "data": json.dumps({"turn_envelope": {"display_status": "completed"}})})
    page = events.replay(job_id)
    assert len(page["events"]) == 16
    assert page["events"][-1]["data"]["turn_envelope"]["terminal_seq"] == 16
    assert jobs.decode_terminal_snapshot(jobs.get_job(job_id))["terminal_seq"] == 16
    # Bridging a persisted notification never appends it a second time.
    other = events.DurableEventQueue(job_id, maxsize=2)
    other.put_nowait(queue.get_nowait())
    assert events.projection(job_id)["event_cursor"] == 16


def test_versioned_updates_merge_once_and_refuse_terminal_or_finalizing_jobs():
    from src.graph.request_plan import merge_task_instructions
    job_id = claim()
    assert events.submit_update(job_id, 0, "use the existing file")["version"] == 1
    with pytest.raises(events.TaskConflict, match="version_conflict"):
        events.submit_update(job_id, 0, "stale")
    assert events.projection(job_id)["applied_version"] == 0
    updates = events.apply_updates(job_id)
    assert "任务版本 1" in merge_task_instructions("original", updates)
    assert not events.apply_updates(job_id)
    assert events.projection(job_id)["applied_version"] == 1
    jobs.update_job(job_id, current_step="finalizing")
    with pytest.raises(events.TaskConflict):
        events.submit_update(job_id, 1, "too late")
    jobs.mark_completed_if_open(job_id)
    with pytest.raises(events.TaskConflict):
        events.submit_update(job_id, 1, "terminal")


@pytest.mark.parametrize("decision", ["granted", "rejected"])
def test_explicit_approval_wakes_exact_waiter_and_decision_is_immutable(monkeypatch, decision):
    from src.config import settings
    from src.session.manager import session_manager
    monkeypatch.setattr(settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(settings, "PERMISSION_MODE", "ask")
    monkeypatch.setattr(settings, "PERMISSION_WAIT_SEC", 2)
    monkeypatch.setattr(session_manager, "consume_grant", lambda *a: False)
    job_id = claim()
    registry._TOOL_SPECS["bounded_test_write"] = registry.ToolSpec(func=lambda: None, is_readonly=False)

    async def scenario():
        waiter = asyncio.create_task(registry._check_tool_sandbox("bounded_test_write", {"value": "fixture"}))
        async def pending():
            while not registry._pending_permission_events:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(pending(), 2)
        approval_id = next(iter(registry._pending_permission_events))
        events.decide_approval(job_id, approval_id, decision)
        registry.signal_permission_decision(approval_id)
        result = await asyncio.wait_for(waiter, 1)
        assert (result is None) == (decision == "granted")
        assert events.approval_status(approval_id) == decision
        assert events.decide_approval(job_id, approval_id, decision)["status"] == decision
        with pytest.raises(events.TaskConflict, match="already_decided"):
            events.decide_approval(job_id, approval_id, "rejected" if decision == "granted" else "granted")
        assert not registry._pending_permission_events
    asyncio.run(scenario())


def test_restart_has_one_interrupted_terminal_and_never_replays_unsafe_write():
    job_id = claim()
    operation_id, lease = events.start_operation(job_id, 1, "external_write", {"recipient": "fixture"})
    approval = events.create_approval(job_id, "external_write", {})
    assert jobs.mark_interrupted_jobs() == 1
    assert events.approval_status(approval) == "expired"
    assert events.projection(job_id)["operations"][0]["resumable"] is False
    assert jobs.mark_interrupted_jobs() == 0
    assert len([r for r in events.replay(job_id)["events"] if r["event"] == "done"]) == 1
    with pytest.raises(events.TaskConflict, match="unsafe"):
        events.claim_resume(job_id, operation_id, 1)
    with pytest.raises(events.TaskConflict, match="lease_lost"):
        events.finish_operation(operation_id, lease, ToolMessage(content="late", tool_call_id="id", name="external_write"))


def test_safe_local_read_resumes_real_verified_file_but_keeps_interrupted_turn(tmp_path, monkeypatch):
    from src.test_account_deletion import _configure, _add_user
    from src.session import files
    from src.tools.account_files import read_account_file
    _configure(tmp_path, monkeypatch)
    owner = _add_user(username="offline_resume")
    job_id = claim(owner)
    item = files.register_file(session_id="offline-session", owner_user_id=owner,
                               filename="input.txt", content=b"verified fixture", mime="text/plain")
    registry.register_tool(read_account_file, is_readonly=True)
    args = {"file_id": item["file_id"]}
    mode, refs = registry._recovery_inputs("read_account_file", args, owner)
    operation_id, old_lease = events.start_operation(job_id, owner, "read_account_file", args,
                                                    recovery_mode=mode, input_refs=refs)
    jobs.mark_interrupted_jobs()
    snapshot = jobs.decode_terminal_snapshot(jobs.get_job(job_id))
    assert events.projection(job_id)["operations"][0]["resumable"]
    result = asyncio.run(registry.resume_safe_operation(job_id, operation_id, owner))
    assert result["recovery"] == "safe_step" and result["operation_status"] == "complete"
    assert "verified fixture" in result["result"]
    assert jobs.get_job(job_id)["status"] == "failed"
    assert jobs.decode_terminal_snapshot(jobs.get_job(job_id)) == snapshot
    with pytest.raises(events.TaskConflict):
        asyncio.run(registry.resume_safe_operation(job_id, operation_id, owner))
    with pytest.raises(events.TaskConflict):
        events.finish_operation(operation_id, old_lease, ToolMessage(content="late", tool_call_id="old", name="read_account_file"))


def test_safe_resume_refuses_changed_file_owner_and_input_hash(tmp_path, monkeypatch):
    from src.test_account_deletion import _configure, _add_user
    from src.session import files
    from src.tools.account_files import read_account_file
    _configure(tmp_path, monkeypatch)
    owner = _add_user(username="offline_changed")
    job_id = claim(owner)
    item = files.register_file(session_id="offline-session", owner_user_id=owner,
                               filename="input.txt", content=b"original", mime="text/plain")
    registry.register_tool(read_account_file, is_readonly=True)
    args = {"file_id": item["file_id"]}
    mode, refs = registry._recovery_inputs("read_account_file", args, owner)
    operation_id, _ = events.start_operation(job_id, owner, "read_account_file", args,
                                             recovery_mode=mode, input_refs=refs)
    jobs.mark_interrupted_jobs()
    with pytest.raises(events.TaskConflict):
        events.claim_resume(job_id, operation_id, owner + 1)
    files.get_account_file(item["file_id"], owner)["path"].write_bytes(b"changed")
    with pytest.raises((events.TaskConflict, ValueError)):
        asyncio.run(registry.resume_safe_operation(job_id, operation_id, owner))
    assert events.projection(job_id)["operations"][0]["status"] == "interrupted"


def test_declared_pure_computation_resumes_without_replaying_default_readonly_tool():
    job_id = claim()
    @tool(response_format="content_and_artifact")
    def add_fixture(a: int, b: int):
        """Deterministic engineering fixture with no I/O."""
        return str(a + b), {"_tool_outcome": {"status": "complete"}}
    registry.register_tool(add_fixture, is_readonly=True, recovery_mode="pure_computation")
    operation_id, _ = events.start_operation(job_id, 1, add_fixture.name, {"a": 2, "b": 5},
        recovery_mode="pure_computation")
    jobs.mark_interrupted_jobs()
    resumed = asyncio.run(registry.resume_safe_operation(job_id, operation_id, 1))
    assert resumed["operation_status"] == "complete" and resumed["result"] == "7"
    registry.register_tool(add_fixture, is_readonly=True)
    assert registry._recovery_inputs(add_fixture.name, {"a": 2, "b": 5}, 1) == ("never", [])


def test_context_view_preserves_constraints_recent_steps_and_source_ids():
    from langchain_core.messages import HumanMessage, SystemMessage
    from src.graph.request_plan import bounded_tool_context
    raw = "old evidence " * 2000 + "\ndoi: 10.1234/fixture"
    messages = [SystemMessage(content="required source policy"), HumanMessage(content="original request"),
        AIMessage(content="step"), ToolMessage(content=raw, tool_call_id="old"),
        AIMessage(content="recent"), ToolMessage(content="current fact", tool_call_id="recent"),
        HumanMessage(content="version 2 pending deliverable"), AIMessage(content="plan")]
    view, status = bounded_tool_context(messages, max_tokens=2000, output_tokens=100)
    assert status["fits"] and status["tool_results_shortened"]
    assert view[0].content == messages[0].content and view[-2].content == messages[-2].content
    assert "10.1234/fixture" in view[3].content and "TRUNCATED" in view[3].content
    assert messages[3].content == raw
    protected = [HumanMessage(content="x" * 20000)]
    view, status = bounded_tool_context(protected, max_tokens=100)
    assert not status["fits"] and view[0].content == protected[0].content


def test_every_retry_and_thinking_fallback_charges_shared_budget(monkeypatch):
    from src.graph import general_agent_graph as graph
    from src.core.llm_pool import _ThinkingOffWrapper
    job_id = claim()
    events.init_budget(job_id, time.time() + 30, max_calls=3, max_tokens=10000)
    calls = []
    class Primary:
        max_tokens = 50
        async def ainvoke(self, messages):
            calls.append("primary")
            raise ValueError("thinking not supported")
    class Fallback:
        max_tokens = 50
        async def ainvoke(self, messages):
            calls.append("fallback")
            return AIMessage(content="ok", usage_metadata={"input_tokens": 4, "output_tokens": 3, "total_tokens": 7})
    wrapper = _ThinkingOffWrapper(Primary(), Fallback())
    result = asyncio.run(graph._budgeted_model_attempt(wrapper, [], lambda: wrapper.ainvoke([])))
    assert result.content == "ok" and calls == ["primary", "fallback"]
    budget = events.projection(job_id)["budget"]
    assert budget["calls"] == 2 and budget["tokens"] == 57
    # Unknown failed usage is retained, never silently charged as zero.
    attempt = events.reserve_attempt(job_id, "retry", 20)
    events.finish_attempt(attempt, error="TimeoutError")
    with pytest.raises(events.TaskConflict, match="budget_exhausted"):
        events.reserve_attempt(job_id, "one-too-many", 1)
    assert events.projection(job_id)["budget"]["tokens"] == 77


def test_graph_applies_update_before_executing_stale_model_proposal(tmp_path, monkeypatch):
    from src.graph import general_agent_graph as graph
    from src.core import llm_pool
    from src.session.manager import session_manager
    job_id = claim()
    monkeypatch.setattr(session_manager, "db_path", str(tmp_path / "jobs.sqlite3"))
    session_manager._init_db_sync()
    seen = []
    class Model:
        def bind_tools(self, tools):
            return self
    async def model_call(model, messages, **kwargs):
        seen.append([str(getattr(m, "content", "")) for m in messages])
        if len(seen) == 1:
            events.submit_update(job_id, 0, "补充要求：回答中注明已使用更新版本。")
            return AIMessage(content="stale proposal")
        return AIMessage(content="已使用更新版本。")
    monkeypatch.setattr(llm_pool, "get_llm", lambda **kwargs: Model())
    monkeypatch.setattr(graph, "_call_llm", model_call)
    monkeypatch.setattr(graph, "_scoped_toolset", lambda *a, **k: ([], None))
    monkeypatch.setattr(graph, "_local_corpus_matches", lambda query: False)
    result = asyncio.run(graph.general_agent_node({
        "query": "你好，请简短回答", "session_id": "offline-session", "messages": []}))
    assert len(seen) == 2
    assert any("补充要求" in m for m in seen[1])
    assert "stale proposal" not in result["answer"]
    assert events.projection(job_id)["applied_version"] == 1


def test_compaction_and_memory_models_share_the_task_budget(monkeypatch):
    import openai
    from langchain_core.messages import HumanMessage
    from src.guardrails.history_compactor import compact_messages
    from src.guardrails.memory import memory_store
    job_id = claim()
    events.init_budget(job_id, time.time() + 30, max_calls=2, max_tokens=10000)
    calls = []
    class Model:
        async def ainvoke(self, messages, **kwargs):
            calls.append("summary")
            return AIMessage(content="source-preserving fixture summary", usage_metadata={
                "input_tokens": 4, "output_tokens": 3, "total_tokens": 7})
    assert asyncio.run(compact_messages([HumanMessage(content="fixture")], fast_llm=Model()))
    def create(**kwargs):
        calls.append("memory")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))],
                               usage={"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7})
    def client(**kwargs):
        assert kwargs["max_retries"] == 0
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(openai, "OpenAI", client)
    assert memory_store.extract_key_facts("fixture", "fixture answer " * 10) == []
    assert events.projection(job_id)["budget"]["calls"] == 2
    # A third auxiliary call fails soft without reaching a provider.
    assert memory_store.extract_key_facts("fixture", "fixture answer " * 10) == []
    assert calls == ["summary", "memory"]


def test_transport_retries_each_charge_even_when_only_last_response_succeeds(monkeypatch):
    from src.graph import general_agent_graph as graph
    job_id = claim()
    events.init_budget(job_id, time.time() + 30, max_calls=3, max_tokens=10000)
    calls = []
    async def stream(*args, **kwargs):
        calls.append("stream")
        raise ConnectionError("fixture transport failure")
    class Model:
        max_tokens = 50
        async def ainvoke(self, messages):
            calls.append("nonstream")
            return AIMessage(content="ok", usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5})
    async def no_backoff(*args):
        pass
    monkeypatch.setattr(graph, "_TRANSIENT_STREAM_ERRORS", (ConnectionError,))
    monkeypatch.setattr(graph, "_LLM_STREAM_ATTEMPTS", 2)
    monkeypatch.setattr(graph, "stream_llm_response", stream)
    monkeypatch.setattr(graph.asyncio, "sleep", no_backoff)
    result = asyncio.run(graph._call_llm(Model(), [], timeout=10))
    assert result.content == "ok" and calls == ["stream", "stream", "nonstream"]
    assert events.projection(job_id)["budget"]["calls"] == 3
    assert events.projection(job_id)["budget"]["tokens"] == 105


def test_expired_budget_does_not_launch_provider_and_facts_are_structured(monkeypatch):
    from src.graph import general_agent_graph as graph
    job_id = claim()
    events.init_budget(job_id, time.time() - 1)
    calls = []
    async def forbidden():
        calls.append(True)
    with pytest.raises(events.TaskConflict):
        asyncio.run(graph._budgeted_model_attempt(object(), [], forbidden))
    assert not calls
    assert events.result_details("mean is 100", {}) == {}
    assert events.result_details(json.dumps({"facts": {"n": 3}, "result_refs": [{"kind": "analysis_result", "id": "fixture"}]}), {})["facts"] == {"n": 3}

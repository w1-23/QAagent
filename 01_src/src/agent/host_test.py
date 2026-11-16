"""Production supervisor with synthetic model/tools and real temporary journals."""
import asyncio
import contextvars
import json
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
    monkeypatch.setattr(settings, "MAIN_BASE_URL", "https://synthetic.invalid/v1")
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


def completion_message(answer, *, status="completed", refs=(), incidental=(), repairs=(), goals=None, remaining=None):
    return AIMessage(content=json.dumps({"schema_version": 1, "answer": answer, "status": status,
        "goals": goals if goals is not None else [{"goal": "synthetic task", "kind": "answer",
            "evidence_call_ids": list(refs), "artifact_links": []}],
        "remaining": remaining if remaining is not None else ([] if status == "completed" else ["work remains"]),
        "incidental_partials": list(incidental), "proposal_repairs": list(repairs),
        "review": {key: "Checked against the available evidence; no correctness certification."
                   for key in ("facts", "sources", "numbers", "conditions", "delivery")}}, ensure_ascii=False))


def scripted_response(responses, index, messages):
    if index < len(responses):
        value = responses[index]
        return value(messages) if callable(value) else value
    # Existing tool/identity tests still exercise the production review call.
    # Only the extra final response is synthesized, and it reports hard failures.
    if index == len(responses) and any("[TASK_FINAL_REVIEW]" in str(m.content)
            for m in messages if m.type == "system"):
        from src.core.task_result import effective_tool_records
        rows = effective_tool_records([m.additional_kwargs["host_publication"] for m in messages
            if isinstance(m, ToolMessage) and "host_publication" in m.additional_kwargs])
        refs = [r["tool_call_id"] for r in rows if r["status"] == "complete"
                and r["name"] not in {"read_skill", "load_skill"}]
        observations = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
        repairs = []
        for index, row in enumerate(rows):
            original = observations.get(row["tool_call_id"])
            corrected = next((r for r in rows[index + 1:] if r["name"] == row["name"]
                              and r["status"] == "complete"), None)
            if (row["status"] == "not_run" and row.get("reason") in {
                    "invalid_tool_arguments", "required_skill_not_loaded", "preparation_required",
                    "script_execution_rejected"}
                    and original is not None and (not original.additional_kwargs.get("execution_dispatched")
                        or original.additional_kwargs.get("host_preflight_reconciled"))
                    and corrected):
                repairs.append({"rejected_call_id": row["tool_call_id"],
                    "corrected_call_id": corrected["tool_call_id"], "reason": "Corrected the rejected proposal."})
        repaired = {r["rejected_call_id"] for r in repairs}
        partial = any(r["status"] not in {"complete", "no_results"}
                      and r["tool_call_id"] not in repaired for r in rows)
        if rows and not refs:
            partial = True
        answer = next(str(m.content) for m in reversed(messages)
                      if isinstance(m, AIMessage) and not m.tool_calls)
        return completion_message(answer, status="partial" if partial else "completed", refs=refs,
                                  repairs=repairs, goals=[] if rows and not refs else None)
    raise AssertionError("Synthetic response script exhausted")


def install(monkeypatch, responses, tools=()):
    def build_catalog():
        catalog = CapabilityCatalog()
        for item, mutates in tools:
            def load(item=item, mutates=mutates):
                registry.register_tool(item, is_readonly=not mutates,
                                       write_scope="session_artifact" if mutates else None)
                return item
            catalog.register(CapabilityDefinition(item.name, item.description,
                item.args_schema.model_json_schema(), load, mutates=mutates))
        return catalog
    monkeypatch.setattr(host, "build_preset_catalog", build_catalog)
    monkeypatch.setattr(host, "allowed_presets", lambda *a, **k: {t.name for t, _ in tools})
    sent = []
    class Driver:
        def __init__(self, *args, **kwargs):
            self.output_tokens = None
        async def __call__(self, messages, identity):
            sent.append(messages)
            return scripted_response(responses, len(sent) - 1, messages)
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
    assert result["answer"] == "direct answer" and len(sent) == 2
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
    assert len(sent) == 3 and result["tools_called"] == ["synthetic_read"]
    assert result["tool_status"][0]["status"] == "complete"
    assert [m.tool_call_id for m in result["turn_trace"] if isinstance(m, ToolMessage)] == ["one"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_resume_saved_batch_without_reexecution_or_evidence_overwrite(environment, monkeypatch, corrupt):
    from src.agent.batch_journal import BatchJournal
    state, job = environment
    effects = []
    @tool(response_format="content_and_artifact")
    def synthetic_read():
        """Read a complete synthetic result with a bounded delivery view."""
        effects.append(1)
        return "original-evidence-" * 4000, {"_tool_outcome": {"status": "complete"}}
    install(monkeypatch, [action("synthetic_read")], [(synthetic_read, False)])
    original = BatchJournal.execute
    async def stop_after_receipt(self, response, execute):
        await original(self, response, execute)
        raise RuntimeError("synthetic_crash_after_committed_batch")
    monkeypatch.setattr(BatchJournal, "execute", stop_after_receipt)
    first = asyncio.run(host.general_agent_node(state))
    assert first["turn_outcome"]["status"] != "completed" and effects == [1]
    stored = tool_results.fetch("one")
    assert stored["result_text"] == "original-evidence-" * 4000
    if corrupt:
        with tool_results._connect() as conn:
            conn.execute("UPDATE tool_results SET result_text='corrupted' WHERE tool_call_id='one'")
            conn.commit()
    monkeypatch.setattr(BatchJournal, "execute", original)
    sent = install(monkeypatch, [AIMessage(content="recovered answer")], [(synthetic_read, False)])
    recovered = asyncio.run(host.general_agent_node({**state, "agent_resume": True}))
    assert effects == [1]
    if corrupt:
        assert not sent and recovered["turn_outcome"]["status"] != "completed"
    else:
        assert recovered["answer"] == "recovered answer" and len(sent) == 2
        assert tool_results.fetch("one")["result_text"] == stored["result_text"]
        assert recovered["tools_called"] == ["synthetic_read"]
        assert len(task_events.projection(job)["operations"]) == 1


def test_user_update_during_model_rejects_stale_proposal(environment, monkeypatch):
    state, job = environment
    def first(messages):
        task_events.submit_update(job, 0, "include the new requirement")
        return AIMessage(content="stale answer")
    sent = install(monkeypatch, [first, AIMessage(content="updated answer")])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "updated answer" and len(sent) == 3
    assert "include the new requirement" in str(sent[1][-1].content)
    assert task_events.projection(job)["applied_version"] == 1


def test_update_ack_waits_for_durable_watermark_and_survives_save_failure(environment, monkeypatch):
    from src.agent.checkpoint_store import _ProtectedSqliteSaver
    state, job = environment
    original = _ProtectedSqliteSaver.aput
    rejected = []
    async def fail_updated_state(self, config, checkpoint, metadata, versions):
        saved_host = checkpoint.get("channel_values", {}).get("host_state") or {}
        if saved_host.get("update_version") == 1 and not rejected:
            rejected.append(True)
            raise OSError("synthetic_watermark_store_failure")
        return await original(self, config, checkpoint, metadata, versions)
    monkeypatch.setattr(_ProtectedSqliteSaver, "aput", fail_updated_state)
    def first(_):
        task_events.submit_update(job, 0, "preserve the new requirement")
        return AIMessage(content="stale answer")
    sent = install(monkeypatch, [first])
    failed = asyncio.run(host.general_agent_node(state))
    assert rejected and failed["turn_outcome"]["status"] != "completed"
    assert len(sent) == 1 and task_events.projection(job)["applied_version"] == 0
    monkeypatch.setattr(_ProtectedSqliteSaver, "aput", original)
    sent = install(monkeypatch, [AIMessage(content="updated durable answer")])
    result = asyncio.run(host.general_agent_node({**state, "agent_resume": True}))
    assert result["answer"] == "updated durable answer" and len(sent) == 2
    assert "preserve the new requirement" in str(sent[0])
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
    assert result["_trace"]["model_steps"] == 7


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


def test_checkpoint_restores_accumulated_assets_and_pending_update(environment, monkeypatch):
    from src.agent.recovery import load_recovery_state
    from src.agent.identity import ExecutionIdentity
    from src.config import PROJECT_ROOT, settings, get_deepseek_model
    state, job = environment
    state.update(client_request_id="asset-client", idempotency_key="asset-idem")
    effects = []
    source = {"file_id": "synthetic-source", "sha256": "source-hash", "sheet": "Sheet1"}
    dataset = {"kind": "dataset_snapshot", "id": "synthetic-dataset", "sha256": "data-hash"}
    script = {"kind": "research_script", "id": "synthetic-script", "version": 1}
    intent = {"goal": "retain the actual plotted comparison"}

    @tool(response_format="content_and_artifact")
    def inspect_uploaded_table():
        """Synthetic table source receipt."""
        effects.append("source")
        return "{}", {"source_refs": [source], "_tool_outcome": {"status": "complete"}}

    @tool(response_format="content_and_artifact")
    def research_workspace():
        """Synthetic persisted analysis/script receipt."""
        effects.append("analysis")
        return "{}", {"dataset_ref": dataset, "script_ref": script,
                      "run_id": "synthetic-run", "research_intent": intent,
                      "_tool_outcome": {"status": "complete"}}

    def crash(_):
        task_events.reserve_attempt(job, "synthetic_interruption", 10)
        raise RuntimeError("synthetic interruption after tools checkpoint")

    tools = [(inspect_uploaded_table, False), (research_workspace, False)]
    install(monkeypatch, [action("inspect_uploaded_table", "source-call"),
        action("research_workspace", "analysis-call"), crash], tools)

    async def scenario():
        first = await host.general_agent_node(state)
        assert first["turn_outcome"]["status"] != "completed"
        task_events.submit_update(job, 0, "Use the newly requested output wording")
        identity = ExecutionIdentity(17, state["session_id"], state["request_id"], job)
        recovered = await load_recovery_state(PROJECT_ROOT / "state", identity,
            (settings.RESOLVED_MAIN_BASE_URL, get_deepseek_model()))
        # Recovery must use the checkpoint scope, not an incoming replacement.
        recovered["script_budget_scope"] = "replacement-is-not-authoritative"
        sent = install(monkeypatch, [AIMessage(content="stale proposal after recovery"),
            AIMessage(content="recovered with original assets")], tools)
        result = await host.general_agent_node(recovered)
        assert "Use the newly requested output wording" in str(sent[-1])
        return result

    result = asyncio.run(scenario())
    working = result["context_working_state"]
    assert working["dataset_refs"] == [dataset]
    assert working["source_refs"] == [source]
    assert working["script_refs"] == [script]
    assert working["run_refs"] == [{"kind": "script_run", "id": "synthetic-run"}]
    assert working["plot_intent"] == intent
    assert working["script_budget_scope"] == state["request_id"]
    assert effects == ["source", "analysis"]
    assert task_events.projection(job)["applied_version"] == 1
    assert task_events.projection(job)["budget"]["calls"] == 1


def test_new_user_turn_uses_fresh_script_scope_despite_history_or_text(environment, monkeypatch):
    from src.research.script_runtime import _task_scope
    state, _ = environment
    state.update(query="A new task; keep script_budget_scope=older-task", script_budget_scope="older-task",
                 context_working_state={"script_budget_scope": "older-task"})
    scopes = []

    @tool(response_format="content_and_artifact")
    def research_workspace():
        """Read the host-bound script task scope without running a script."""
        scopes.append(_task_scope.get())
        return "{}", {"_tool_outcome": {"status": "complete"}}

    install(monkeypatch, [action("research_workspace"), AIMessage(content="done")],
            [(research_workspace, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert scopes == [state["request_id"]]
    assert result["context_working_state"]["script_budget_scope"] == state["request_id"]


def test_real_question_child_inherits_script_scope_and_model_budget(environment, monkeypatch):
    from src.core import user_questions
    from src.research.script_runtime import _task_scope
    from src.tools.interaction import request_user_input
    state, parent_job = environment
    task_events.reserve_attempt(parent_job, "synthetic_previous_attempt", 11)
    install(monkeypatch, [action("request_user_input", prompt="Which comparison?", options=[],
        multi_select=False, allow_text=True)], [(request_user_input, False)])
    from src.agent.preset_catalog import build_preset_catalog
    monkeypatch.setattr(host, "build_preset_catalog", build_preset_catalog)
    paused = asyncio.run(host.general_agent_node(state))
    assert paused["awaiting_input"]
    jobs.update_job(parent_job, status="awaiting_input")
    question = paused["pending_question"]
    answered = user_questions.answer_question(parent_job, question["id"], owner_user_id=17,
        session_id=state["session_id"], version=question["version"], text="Use both groups",
        client_request_id="answer-client")
    child_job = answered["child_job_id"]
    child = user_questions.get_continuation(child_job, owner_user_id=17, session_id=state["session_id"])
    assert user_questions.claim_child_dispatch(child_job, owner_user_id=17,
        session_id=state["session_id"], deadline=time.time() + 60)
    child.update(messages=[], request_deadline=time.monotonic() + 30)
    # Simulate stale session navigation after the authoritative answer was saved.
    child["context_working_state"] = {"script_budget_scope": "unrelated-session-task"}
    tracing.set_job_id(child_job)
    progress_bus.set_turn_scope(state["session_id"], child["request_id"])
    scopes = []

    @tool(response_format="content_and_artifact")
    def research_workspace():
        """Observe the authenticated question continuation's script scope."""
        scopes.append(_task_scope.get())
        return "{}", {"_tool_outcome": {"status": "complete"}}

    install(monkeypatch, [action("research_workspace", "child-call"), AIMessage(content="continued")],
            [(research_workspace, False)])
    result = asyncio.run(host.general_agent_node(child))
    assert result["answer"] == "continued"
    assert child["request_id"] != state["request_id"]
    assert scopes == [state["request_id"]]
    assert result["context_working_state"]["script_budget_scope"] == state["request_id"]
    assert task_events.projection(child_job)["budget"]["calls"] == 1
    assert task_events.projection(child_job)["budget"]["tokens"] == 11


@pytest.mark.parametrize("foreign", ["identity", "original_request_state"])
def test_host_checkpoint_rejects_foreign_identity_before_model(environment, monkeypatch, foreign):
    from src.agent.harness import HarnessRuntime
    state, _ = environment
    sent = install(monkeypatch, [AIMessage(content="must not run")])
    original_run = HarnessRuntime.run

    async def foreign_checkpoint(runtime, *args, **kwargs):
        saved = runtime.export_host()
        saved[foreign]["owner_user_id" if foreign == "identity" else "user_id"] = 18
        runtime.restore_host(saved)
        return await original_run(runtime, *args, **kwargs)

    monkeypatch.setattr(HarnessRuntime, "run", foreign_checkpoint)
    result = asyncio.run(host.general_agent_node(state))
    assert not sent
    assert result["_trace"]["stop_reason"] == "identity_conflict"


def test_review_uses_same_tools_and_budget_and_keeps_raw_envelope_private(environment, monkeypatch):
    from src.graph.session_persistence import persist_agent_turn
    from src.session.manager import session_manager
    state, job = environment
    @tool(response_format="content_and_artifact")
    def synthetic_read():
        """Read actual evidence during the final review."""
        return "checked numerical conditions", {"_tool_outcome": {"status": "complete"}}
    attempts = []
    def metered(value):
        def response(messages):
            attempts.append(len(attempts))
            task_events.reserve_attempt(job, "review-attempt-" + str(len(attempts)), 7)
            return value
        return response
    sent = install(monkeypatch, [metered(AIMessage(content="unreviewed numerical candidate")),
        metered(action("synthetic_read", "review-evidence")),
        metered(completion_message("revised answer with conditions", refs=["review-evidence"]))],
        [(synthetic_read, False)])
    async def scenario():
        result = await host.general_agent_node(state)
        saved = await persist_agent_turn({**state, **result})
        history = await session_manager.get_messages(state["session_id"])
        # Replaying an accepted checkpoint must use the saved extracted answer,
        # never the raw JSON or another review/model call.
        replay = await host.general_agent_node({**state, "agent_resume": True})
        return result, saved, history, replay
    result, saved, history, replay = asyncio.run(scenario())
    assert len(sent) == 3 and task_events.projection(job)["budget"]["calls"] == 3
    assert "[TASK_FINAL_REVIEW]" in str(sent[1][-1].content)
    assert result["answer"] == replay["answer"] == "revised answer with conditions"
    assert result["turn_outcome"]["reason_code"] == "reviewed_completion"
    assert saved["_trace"]["history_saved"]
    public = [str(m.content) for m in history if isinstance(m, AIMessage) and not m.tool_calls]
    assert public == [result["answer"]]
    assert all("unreviewed numerical candidate" not in str(m.content) for m in result["turn_trace"])


@pytest.mark.parametrize("declare_incidental", [True, False])
def test_reviewed_auxiliary_partial_accepts_goal_without_rewriting_execution(environment, monkeypatch, declare_incidental):
    from src.core.turn_envelope import build_turn_envelope
    state, _ = environment
    @tool(response_format="content_and_artifact")
    def synthetic_fetch(url: str):
        """Fetch an optional source or its replacement."""
        return ("404", {"_tool_outcome": {"status": "partial", "reason_code": "source_unavailable",
                 "reason_class": "source_unavailable", "artifacts": []}}) if url == "missing" else (
                 "verified replacement source", {"_tool_outcome": {"status": "complete"}})
    batch = AIMessage(content="", tool_calls=[
        {"name": "synthetic_fetch", "id": "missing", "args": {"url": "missing"}},
        {"name": "synthetic_fetch", "id": "replacement", "args": {"url": "available"}}])
    sent = install(monkeypatch, [batch, AIMessage(content="draft"), completion_message("supported answer",
        refs=["replacement"], incidental=[{"tool_call_id": "missing", "reason": "Optional source unavailable; replacement supports this goal.",
        "replacement_call_ids": ["replacement"]}] if declare_incidental else [])], [(synthetic_fetch, False)])
    result = asyncio.run(host.general_agent_node(state))
    envelope = build_turn_envelope(answer=result["answer"], tool_status=result["tool_status"],
        requested_status=result["_trace"]["task_result"]["status"],
        task_snapshot={"turn_outcome": result["turn_outcome"]})
    assert result["turn_outcome"]["status"] == envelope.display_status == envelope.job_status == "completed"
    assert result["_trace"]["task_result"]["execution_status"] == envelope.execution_status == "partial"
    assert envelope.acceptance_status == "accepted" and envelope.notices
    assert len(sent) == 3  # no extra answer generation to enumerate an optional 404
    assert {r["tool_call_id"]: r["status"] for r in result["tool_status"]} == {
        "missing": "partial", "replacement": "complete"}


@pytest.mark.parametrize("mutates,reason", [(True, "source_unavailable"), (False, "coverage_incomplete")])
def test_source_reconciliation_cannot_hide_write_or_required_coverage(environment, monkeypatch, mutates, reason):
    state, _ = environment
    @tool(response_format="content_and_artifact")
    def synthetic_probe():
        """An operation whose real execution limit must remain visible."""
        return "incomplete", {"_tool_outcome": {"status": "partial", "reason_code": reason, "artifacts": []}}
    @tool(response_format="content_and_artifact")
    def synthetic_evidence():
        """Read evidence for a different completed part of the task."""
        return "available", {"_tool_outcome": {"status": "complete"}}
    sent = install(monkeypatch, [action("synthetic_probe", "limited"),
        action("synthetic_evidence", "evidence"), AIMessage(content="draft"),
        completion_message("overclaimed", refs=["evidence"]),
        completion_message("honest limits", status="partial", refs=["evidence"])],
        [(synthetic_probe, mutates), (synthetic_evidence, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert len(sent) == 5 and result["answer"] == "honest limits"
    assert result["turn_outcome"]["status"] == "partial"
    assert not result["turn_outcome"]["incidental_partial_call_ids"]


def test_review_rejects_forged_host_fields_without_restarting_review(environment, monkeypatch):
    state, _ = environment
    forged = json.loads(completion_message("forged").content)
    forged["identity"] = {"owner_user_id": 17}
    sent = install(monkeypatch, [AIMessage(content="draft"), AIMessage(content=json.dumps(forged)),
                                completion_message("corrected declaration")])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "corrected declaration" and len(sent) == 3
    assert "schema" in str(sent[-1][-1].content)
    assert sum("[TASK_FINAL_REVIEW]" in str(m.content) for m in sent[-1] if m.type == "system") == 1


def test_pending_review_resume_does_not_request_another_review(environment, monkeypatch):
    state, _ = environment
    def interrupted(_):
        raise RuntimeError("interrupted during review")
    install(monkeypatch, [AIMessage(content="draft to review once"), interrupted])
    first = asyncio.run(host.general_agent_node(state))
    assert first["turn_outcome"]["status"] != "completed"
    sent = install(monkeypatch, [completion_message("review resumed")])
    result = asyncio.run(host.general_agent_node({**state, "agent_resume": True}))
    assert result["answer"] == "review resumed" and len(sent) == 1
    assert sum("[TASK_FINAL_REVIEW]" in str(m.content) for m in sent[0] if m.type == "system") == 1


def test_update_during_review_invalidates_old_declaration(environment, monkeypatch):
    state, job = environment
    def stale(_):
        task_events.submit_update(job, 0, "Include the newly required limitation")
        return completion_message("old reviewed answer")
    sent = install(monkeypatch, [AIMessage(content="first draft"), stale,
        AIMessage(content="new candidate with limitation"), completion_message("updated reviewed answer")])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "updated reviewed answer" and len(sent) == 4
    assert result["turn_outcome"]["completion"]["request_version"] == 1
    assert sum("[TASK_FINAL_REVIEW]" in str(m.content) for m in sent[-1] if m.type == "system") == 2


def test_update_after_accepted_graph_checkpoint_reopens_same_loop(environment, monkeypatch):
    state, job = environment
    install(monkeypatch, [AIMessage(content="old candidate"), completion_message("old answer")])
    assert asyncio.run(host.general_agent_node(state))["answer"] == "old answer"
    task_events.submit_update(job, 0, "New requirement before API finalization")
    sent = install(monkeypatch, [AIMessage(content="new candidate"), completion_message("new answer")])
    result = asyncio.run(host.general_agent_node({**state, "agent_resume": True}))
    assert result["answer"] == "new answer" and len(sent) == 2
    assert result["turn_outcome"]["completion"]["request_version"] == 1


def test_review_cannot_refresh_exhausted_main_loop_budget(environment, monkeypatch):
    from src.config import settings
    state, _ = environment
    monkeypatch.setattr(settings, "AGENT_MAX_MODEL_CALLS", 1)
    sent = install(monkeypatch, [AIMessage(content="unreviewed draft must not be delivered")])
    result = asyncio.run(host.general_agent_node(state))
    assert len(sent) == 1 and result["turn_outcome"]["status"] != "completed"
    assert result["_trace"]["stop_reason"] == "model_step_limit"
    assert "unreviewed draft must not be delivered" not in result["answer"]


def test_pure_argument_rejection_can_be_explicitly_repaired(environment, monkeypatch):
    state, _ = environment
    @tool(response_format="content_and_artifact")
    def synthetic_read(key: str):
        """Read the same task's evidence after correcting the argument name."""
        return key, {"_tool_outcome": {"status": "complete"}}
    install(monkeypatch, [action("synthetic_read", "rejected", wrong="input"),
        action("synthetic_read", "corrected", key="input"), AIMessage(content="corrected and checked")],
        [(synthetic_read, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "corrected and checked"
    assert result["turn_outcome"]["status"] == "completed"
    assert result["_trace"]["task_result"]["execution_status"] == "partial"
    assert [r["status"] for r in result["tool_status"]] == ["not_run", "complete"]
    assert result["turn_outcome"]["completion"]["verified_proposal_repairs"] == ["rejected"]


def test_zero_dispatch_wrong_tool_can_be_replaced_by_suitable_tool(environment, monkeypatch):
    state, _ = environment
    executed = []
    @tool(response_format="content_and_artifact")
    def synthetic_wrong(image_ids: list[str]):
        """A different capability whose invalid proposal must never execute."""
        executed.append("wrong")
        return "unused", {"_tool_outcome": {"status": "complete"}}
    @tool(response_format="content_and_artifact")
    def synthetic_table(file_id: str):
        """Process the task with a suitable capability."""
        executed.append("table")
        return "actual result", {"_tool_outcome": {"status": "complete"}}
    sent = install(monkeypatch, [action("synthetic_wrong", "wrong-proposal", missing="argument"),
        action("synthetic_table", "right-tool", file_id="synthetic"), AIMessage(content="draft"),
        completion_message("delivered", refs=["right-tool"])],
        [(synthetic_wrong, True), (synthetic_table, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert executed == ["table"] and len(sent) == 4
    assert result["answer"] == "delivered" and result["turn_outcome"]["status"] == "completed"
    assert result["turn_outcome"]["completion"]["verified_proposal_repairs"] == ["wrong-proposal"]
    assert [r["status"] for r in result["tool_status"]] == ["not_run", "complete"]

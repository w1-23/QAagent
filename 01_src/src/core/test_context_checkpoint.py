"""Offline checkpoint fidelity tests. All databases are isolated tmp_path files."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.core.context_budget import ContextBudget, ContextBudgetConfig, ContextBudgetLevel
from src.core.context_manager import ContextManager, LoadedContext, build_human_message
from src.guardrails.history_compactor import CompactionResult, compact_messages


def run(coro):
    return asyncio.run(coro)


def user(text, turn="t1"):
    return HumanMessage(content=text, additional_kwargs={
        "context_origin": "generated_user", "context_trusted": True,
        "user_text": text, "turn_id": turn, "request_id": turn,
    })


@pytest.fixture
def store(tmp_path, monkeypatch):
    # Load a private copy after redirecting its root. Never import the production
    # singleton at its real path, even transiently.
    import src.config as config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    path = Path(__file__).resolve().parents[1] / "session" / "manager.py"
    spec = importlib.util.spec_from_file_location("checkpoint_test_session", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert Path(module.session_manager.db_path).is_relative_to(tmp_path)
    module.session_manager._create_session_sync("s")
    return module


def test_metadata_and_multimodal_round_trip_without_rewriting(store):
    blocks = [{"type": "text", "text": "原文 <user_query>literal</user_query>"},
              {"type": "image_url", "image_url": {"url": "data:image/png;base64,TEST"}}]
    message = user("raw")
    message.content = blocks
    message.additional_kwargs["attachment_refs"] = [{"file_id": "f1", "source_id": "upload:1"}]
    assert run(store.session_manager.save_messages("s", [message], "crid:r1"))
    restored, ids = run(store.session_manager.get_messages_with_ids("s"))
    assert restored[0].content == blocks
    assert restored[0].additional_kwargs["user_text"] == "raw"
    assert restored[0].additional_kwargs["context_origin"] == "generated_user"
    assert restored[0].additional_kwargs["context_trusted"] is True
    assert restored[0].additional_kwargs["attachment_refs"] == message.additional_kwargs["attachment_refs"]
    assert restored[0].additional_kwargs["source_id"] == str(ids[0])
    with store._connect_db(store.session_manager.db_path) as conn:
        content = conn.execute("SELECT content FROM messages").fetchone()[0]
    assert json.loads(content) == blocks


def test_legacy_text_stays_unknown_and_literal_json_stays_text(store):
    literal = '[{"type":"text","text":"not a multimodal message"}]'
    assert run(store.session_manager.save_messages("s", [HumanMessage(content=literal)]))
    messages = run(store.session_manager.get_messages("s"))
    assert messages[0].content == literal
    assert "context_origin" not in messages[0].additional_kwargs
    assert "user_text" not in messages[0].additional_kwargs


def test_checkpoint_revision_watermark_and_actual_write_result(store):
    manager = store.session_manager
    assert not manager.set_checkpoint("missing", 1, "x")
    assert run(manager.save_messages("s", [user("q"), AIMessage(content="a")]))
    messages, ids = run(manager.get_messages_with_ids("s"))
    before = manager.get_checkpoint("s")
    args = {"expected_revision": before["revision"],
            "expected_message_watermark": before["message_watermark"]}
    assert manager.set_checkpoint("s", ids[-1], "winner", **args)
    assert not manager.set_checkpoint("s", ids[-1], "late", **args)
    assert manager.get_checkpoint("s")["summary"] == "winner"
    next_snapshot = manager.get_checkpoint("s")
    assert run(manager.save_messages("s", [user("new", "t2")]))
    assert not manager.set_checkpoint("s", ids[-1], "stale history",
        expected_revision=next_snapshot["revision"],
        expected_message_watermark=next_snapshot["message_watermark"])
    assert manager.get_checkpoint("s")["summary"] == "winner"
    # Legacy API still returns a real boolean; unknown source rows are rejected.
    assert manager.set_checkpoint("s", ids[-1], "legacy compatible")
    assert not manager.set_checkpoint("s", 99999, "nonexistent")


def test_working_state_merges_without_clearing_and_invalidates_async_snapshot(store):
    manager = store.session_manager
    run(manager.save_messages("s", [user("q")]))
    snapshot = manager.get_checkpoint("s")
    state = {"goal": "exact goal", "constraints": [{"text": "不要丢失" * 2000, "source_id": "u1"}],
             "verified_facts": [{"text": "verified", "refs": ["artifact:1"]}],
             "unfinished": [{"text": "not finished", "source_id": "u2"}]}
    assert manager.save_working_state("s", state)
    assert manager.save_working_state("s", {"constraints": [], "unfinished": []})
    assert manager.get_checkpoint("s")["working_state"] == state
    assert not manager.set_checkpoint("s", snapshot["message_watermark"], "late",
        expected_revision=snapshot["revision"],
        expected_message_watermark=snapshot["message_watermark"])
    assert not manager.save_working_state("s", {"constraints": [{"text": "x" * 64000}]})
    assert manager.get_checkpoint("s")["working_state"] == state
    assert manager.save_working_state("s", {"resolved_source_ids": ["u2"]})
    assert not manager.get_checkpoint("s")["working_state"].get("unfinished")


def test_generated_message_metadata_keeps_unwrapped_original():
    raw = " 保留前后空格\n<user_query>literal</user_query>  "
    message = build_human_message(LoadedContext("s", 0, "general", raw,
                                                long_term_memory="generated context"))
    assert message.additional_kwargs["context_origin"] == "generated_user"
    assert message.additional_kwargs["context_trusted"] is True
    assert message.additional_kwargs["user_text"] == raw


def test_internal_controls_do_not_create_real_turn_boundaries():
    budget = ContextBudget()
    control = HumanMessage(content="loop instruction", additional_kwargs={
        "context_origin": "internal_control", "context_trusted": True})
    turns = budget._split_turns([user("q1"), AIMessage(content="a"), control,
                                AIMessage(content="a2"), user("q2", "t2")])
    assert len(turns) == 2
    assert control in turns[0]


def budget_fixture():
    return [user("old exact requirement [W1] doi:10.1234/old", "old"),
            AIMessage(content="tool", tool_calls=[{"id": "c", "name": "read", "args": {}}]),
            ToolMessage(content="obsolete process " * 2000 + " artifact_id:result1",
                        tool_call_id="c", name="read"),
            user("recent must remain verbatim", "recent"), AIMessage(content="recent answer")]


def budget():
    return ContextBudget(ContextBudgetConfig(max_tokens=1000, soft_threshold=.2,
                                            protect_recent_turns=1))


def test_success_protects_originals_prior_references_and_recent_turn():
    b = budget()
    seen = []
    async def summarize(messages, **kwargs):
        seen.append((messages, kwargs))
        return CompactionResult("success", "process compressed", "stop", True)
    b.set_compact_fn(summarize)
    messages = budget_fixture()
    result = run(b.check(messages, ids=[11, 12, 13, 14, 15], prior_summary="PRIOR exact pending"))
    assert result.status == "success" and result.cutoff_id == 13
    assert result.summary and "PRIOR exact pending" in result.summary
    assert messages[0].additional_kwargs["user_text"] in result.summary
    assert "artifact_id:result1" in result.summary
    assert result.messages[-2:] == messages[-2:]
    assert isinstance(result.messages[0], SystemMessage)
    assert seen[0][0] == messages[:3]


@pytest.mark.parametrize("outcome", [CompactionResult("failed", reason="timeout"),
    CompactionResult("failed", "partial", "length", False),
    CompactionResult("success", "", "stop", True),
    CompactionResult("success", "partial", "length", True), ""])
def test_failures_never_advance_cutoff_or_replace_view(outcome):
    b = budget()
    async def summarize(*args, **kwargs):
        return outcome
    b.set_compact_fn(summarize)
    messages = budget_fixture()
    result = run(b.check(messages, prior_summary="old"))
    assert result.messages == messages
    assert result.cutoff_id is None and result.summary is None
    assert result.level == ContextBudgetLevel.NORMAL


def test_empty_and_partial_responses_trip_breaker(monkeypatch):
    import src.core.context_budget as module
    monkeypatch.setattr(module, "_compaction_failures", {})
    monkeypatch.setattr(module, "_compaction_breaker_at", {})
    b = budget()
    calls = []
    async def summarize(*args, **kwargs):
        calls.append(1)
        return CompactionResult("failed", reason="empty")
    b.set_compact_fn(summarize)
    for _ in range(4):
        run(b.check(budget_fixture(), session_id="breaker"))
    assert len(calls) == 3 and module._breaker_tripped("breaker")
    monkeypatch.setitem(module._compaction_breaker_at, "breaker", 0)
    run(b.check(budget_fixture(), session_id="breaker"))
    assert len(calls) == 4


def test_hard_trim_cannot_delete_protected_users():
    b = budget()
    messages = [user("very large original " * 10000), user("new exact")]
    assert b._hard_trim(messages, b.config) == messages
    result = run(b.check(messages))
    assert result.messages == messages


def test_projection_before_budget_and_incremental_checkpoint_reuse(store, monkeypatch):
    from src.core import model_context
    manager = store.session_manager
    messages = budget_fixture()
    run(manager.save_messages("s", messages))
    raw, ids = run(manager.get_messages_with_ids("s"))
    b = budget()
    calls = []
    cm = ContextManager(manager, budget=b)
    async def summarize(messages, **kwargs):
        calls.append([m.content for m in messages])
        return CompactionResult("success", "compressed", "stop", True)
    b.set_compact_fn(summarize)
    original = model_context.historical_send_view
    observed = []
    def project(messages):
        result = original(messages)
        observed.append(result)
        return result
    monkeypatch.setattr(model_context, "historical_send_view", project)
    view, compacted = run(cm._build_send_view("s", raw, ids, "query"))
    assert observed and compacted
    first_cp = manager.get_checkpoint("s")
    assert first_cp["msg_id"] == ids[2]
    assert calls[0] == [m.content for m in observed[0][:3]]
    assert len(first_cp["summary"]) > 0
    # No new compressible turns: the previous synthetic summary is not fed back
    # as a human turn or newly covered source message.
    view2, compacted2 = run(cm._build_send_view("s", raw, ids, "query"))
    assert not compacted2 and len(calls) == 1
    assert view2[0].additional_kwargs["context_origin"] == "summary"


def test_legacy_checkpoint_keeps_prior_and_recovers_raw_coverage(store):
    manager = store.session_manager
    run(manager.save_messages("s", [user("old requirement"), AIMessage(content="old fact"), user("recent")]))
    raw, ids = run(manager.get_messages_with_ids("s"))
    with store._connect_db(manager.db_path) as conn:
        conn.execute("UPDATE sessions SET checkpoint_msg_id=?, checkpoint_summary=? WHERE session_id=?",
                     (ids[1], "legacy possibly incomplete", "s"))
        conn.commit()
    cm = ContextManager(manager, budget=ContextBudget(ContextBudgetConfig(enabled=False)))
    view, changed = run(cm._build_send_view("s", raw, ids, "q"))
    assert not changed and len(view) == len(raw) + 1
    assert "legacy possibly incomplete" in view[0].content
    assert any(m.content == "old requirement" for m in view)
    assert any(m.content == "old fact" for m in view)


def test_cas_rejection_keeps_previous_view(store):
    manager = store.session_manager
    run(manager.save_messages("s", budget_fixture()))
    raw, ids = run(manager.get_messages_with_ids("s"))
    b = budget()
    cm = ContextManager(manager, budget=b)
    async def summarize(*args, **kwargs):
        manager.save_working_state("s", {"unfinished": [{"text": "new work", "source_id": "n"}]})
        return CompactionResult("success", "late", "stop", True)
    b.set_compact_fn(summarize)
    view, changed = run(cm._build_send_view("s", raw, ids, "q"))
    assert not changed and manager.get_checkpoint("s")["msg_id"] == 0
    assert len(view) == len(raw)


def test_compactor_no_head_clipping_and_finish_validation(monkeypatch):
    from src.core import task_events
    prompts = []
    async def invoke(_model, messages, **kwargs):
        prompts.append(messages[-1].content)
        return AIMessage(content="complete summary", response_metadata={"finish_reason": "stop"})
    monkeypatch.setattr(task_events, "counted_ainvoke", invoke)
    text = "完整用户约束" * 700 + " LAST_USER_REQUIREMENT"
    prior = "prior " * 600 + " LAST_PRIOR_REQUIREMENT"
    result = run(compact_messages([user(text)], object(), query="full query", prior_summary=prior,
                                  return_result=True))
    assert result.complete
    assert text in prompts[0] and prior in prompts[0]
    async def partial(*args, **kwargs):
        return AIMessage(content="partial", response_metadata={"finish_reason": "length"})
    monkeypatch.setattr(task_events, "counted_ainvoke", partial)
    result = run(compact_messages([user("q")], object(), return_result=True))
    assert result.status == "failed" and not result.complete and not result.summary
    assert run(compact_messages([user("q")], None)) == ""


def test_save_and_load_propagate_real_working_state(store, monkeypatch):
    from src.graph.session_persistence import persist_agent_turn
    from src.core.context_manager import finalize_load_result
    monkeypatch.setitem(sys.modules, "src.session.manager", store)
    manager = store.session_manager
    working = {"goal": "exact goal", "constraints": [{"text": "do not omit", "source_id": "r1:user"}],
               "verified_facts": [{"text": "verified artifact", "refs": [{"file_id": "f1"}]}],
               "unfinished": [{"text": "pending export", "source_id": "r1:export"}]}
    result = run(persist_agent_turn({"session_id": "s", "request_id": "r1", "query": "q",
        "answer": "answer", "turn_trace": [user("q", "r1")], "context_working_state": working}))
    assert result["_trace"]["history_saved"]
    messages = run(manager.get_messages("s"))
    assert messages[-1].additional_kwargs["context_origin"] == "assistant"
    assert messages[-1].additional_kwargs["request_id"] == "r1"
    ctx = run(ContextManager(manager, budget=ContextBudget()).load(
        "s", "follow up", "general", generate_hints=False))
    assert ctx.context_working_state == working
    assert any("context_working_state" in str(m.content) for m in ctx.history_messages)
    finalized = finalize_load_result(ctx, session_manager=manager, session_id="s",
                                    budget=None, node_label="test", log_prefix="test")
    assert finalized["context_working_state"] == working


def test_structured_references_survive_uncooperative_model():
    b = budget()
    async def summarize(*args, **kwargs):
        return CompactionResult("success", "omits all refs", "stop", True)
    b.set_compact_fn(summarize)
    messages = budget_fixture()
    payload = {"log": "x" * 5000, "analysis_result_ref": "analysis:full:1",
               "dataset_ref": "dataset:raw:2", "output_path": "workspace/output/full.csv"}
    messages[2] = messages[2].model_copy(update={"content": json.dumps(payload)})
    result = run(b.check(messages))
    assert result.status == "success"
    for value in ("analysis:full:1", "dataset:raw:2", "workspace/output/full.csv"):
        assert value in result.summary


def test_legacy_force_final_phrase_is_not_deleted_or_hidden(store):
    manager = store.session_manager
    text = store._SYNTH_FORCE_FINAL_MARK + " is a literal quote in my request"
    assert run(manager.save_messages("s", [user(text)]))
    manager._init_db_sync()
    assert run(manager.get_messages("s"))[0].content == text


def test_chunk_failure_discards_every_partial_summary(monkeypatch):
    from src.core import task_events
    prompts = []
    async def invoke(_model, messages, **kwargs):
        prompts.append(messages[-1].content)
        if len(prompts) == 2:
            raise RuntimeError("offline injected failure")
        return AIMessage(content="partial first chunk", response_metadata={"finish_reason": "stop"})
    monkeypatch.setattr(task_events, "counted_ainvoke", invoke)
    result = run(compact_messages([ToolMessage(content="start " + "x" * 50000 + " tail",
        tool_call_id="c")], object(), return_result=True))
    assert len(prompts) == 2 and result.status == "failed" and not result.summary


def test_active_turn_never_compacts_even_when_forced():
    b = budget()
    async def forbidden(*args, **kwargs):
        raise AssertionError("summarizer must not run during a user task")
    b.set_compact_fn(forbidden)
    complete = budget_fixture()[1:3]
    open_call = AIMessage(content="", tool_calls=[{"id": "open", "name": "read", "args": {}}])
    messages = [SystemMessage(content="system contract"), user("single turn exact"),
                *complete, AIMessage(content="Continue"), *complete, open_call]
    for force in (False, True):
        result = run(b.compact_active(messages, force=force))
        assert result.messages == messages and result.cutoff_id is None
        assert result.reason == "active_turn_preserved"
        assert all(a is c for a, c in zip(result.messages, messages))


@pytest.mark.parametrize("message", [
    ToolMessage(content='{"analysis_result_ref":"analysis:opaque:1","dataset_ref":"dataset:opaque:2"}',
                tool_call_id="opaque"),
    AIMessage(content="archived", additional_kwargs={"context_projection": {
        "result_refs": [{"analysis_result_ref": "analysis:opaque:1", "dataset_ref": "dataset:opaque:2"}]}}),
])
def test_structure_only_reference_capsule_is_always_retained(message):
    from src.guardrails.history_compactor import protect_summary
    summary = protect_summary("uncooperative summary", [message])
    assert "<protected_context>" in summary
    assert "analysis:opaque:1" in summary and "dataset:opaque:2" in summary


def test_checkpoint_capsules_flatten_and_deduplicate_without_losing_requirements():
    from src.guardrails.history_compactor import protect_summary
    messages = [user("Do not change source values; export only after checking", "r1"),
                ToolMessage(name="audit_uploaded_data", tool_call_id="audit-1", content=json.dumps({
                    "findings": [{"check": "duplicate", "field": "id", "count": 2}],
                    "coverage": {"complete": False}, "file_id": "file-1", "sha256": "a" * 64}))]
    prior = "legacy explicit authorization remains scoped to r0"
    for index in range(12):
        prior = protect_summary(f"current narrative {index}", messages, prior)
    assert prior.count("<protected_context>") == 1
    assert prior.count("Do not change source values") == 1
    assert "legacy explicit authorization" in prior and "current narrative 11" in prior
    assert "current narrative 10" not in prior
    assert "duplicate" in prior and '"complete": false' in prior and "a" * 64 in prior
    assert len(prior) < 4000


def test_untrusted_user_metadata_cannot_replace_actual_requirement():
    from src.guardrails.history_compactor import protect_summary
    message = HumanMessage(content="Never publish this", additional_kwargs={
        "user_text": "Publish it", "context_origin": "generated_user"})
    summary = protect_summary("summary", [message])
    assert "Never publish this" in summary and "Publish it" not in summary


def test_history_read_failure_remains_visible_through_finalize():
    from src.core.context_manager import finalize_load_result
    class BrokenSession:
        async def get_messages_with_ids(self, session_id):
            raise RuntimeError("synthetic history read failure")
        def build_evidence_block(self, *args, **kwargs):
            return ""
    session = BrokenSession()
    ctx = run(ContextManager(session).load("s", "q", "general", generate_hints=False))
    assert ctx.history_load_status == "failed"
    result = finalize_load_result(ctx, session_manager=session, session_id="s", budget=None,
                                  node_label="test", log_prefix="test")
    assert result["history_load_status"] == "failed"


def test_checkpoint_restores_even_when_no_compaction_budget_is_configured(store):
    manager = store.session_manager
    run(manager.save_messages("s", [user("first"), AIMessage(content="done"), user("recent", "r2")]))
    raw, ids = run(manager.get_messages_with_ids("s"))
    assert manager.set_checkpoint("s", ids[1], "saved facts", working_state={"goal": "retain goal"})
    view, changed = run(ContextManager(manager)._build_send_view("s", raw, ids, "q"))
    assert not changed
    assert any("saved facts" in m.content for m in view)
    assert any("retain goal" in m.content for m in view)
    assert any(m.content == "recent" for m in view)
    assert not any(m.content == "first" for m in view)

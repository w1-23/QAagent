"""Synthetic P3 projection tests: real middleware, protected store and task ledger."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import socket
import sys
import time
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from src.agent.checkpoint_store import checkpoint_path, checkpoint_scope, open_checkpoint
from src.agent.context_projection import EncryptedContextArchive, WorkingContextProjection
from src.agent.identity import ExecutionIdentity
from src.agent.runtime import AdmissionStopped
from src.core.provider_protocol import _DeepSeekMessage, _ProtocolState

SCOPE = ("https://api.deepseek.com/v1", "deepseek-reasoner")
PRIVATE = "synthetic-private-summary-test"
RAW = "synthetic-raw-evidence-never-plaintext " * 400


class SyntheticModel(BaseChatModel):
    max_retries: int = 0
    max_tokens: int = 128
    seen: list = Field(default_factory=list)
    seen_limits: list = Field(default_factory=list)
    responses: list = Field(default_factory=list)
    on_call: Any = Field(default=None, exclude=True)
    failure: str = ""
    content: str = "Synthetic concise summary."
    finish_reason: str = "stop"

    @property
    def _llm_type(self):
        return "synthetic-context-projection"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        raise AssertionError("Async tests only")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        self.seen_limits.append(self.max_tokens)
        if self.on_call:
            await self.on_call()
        if self.failure == "error":
            raise RuntimeError("synthetic provider error " + PRIVATE)
        if self.failure == "wait":
            await asyncio.Event().wait()
        content, finish = self.responses.pop(0) if self.responses else (self.content, self.finish_reason)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=content, response_metadata={"finish_reason": finish},
            usage_metadata={"input_tokens": 17, "output_tokens": 3, "total_tokens": 20}))])


class ObservedArchive(EncryptedContextArchive):
    reference = None

    async def archive(self, messages):
        self.reference = await super().archive(messages)
        return self.reference


@pytest.fixture
def environment(tmp_path, monkeypatch):
    # No production config/.env import and no network. Windows loop initialization
    # precedes the socket guard because its wakeup socket uses loopback.
    loop = asyncio.new_event_loop()
    config = ModuleType("src.config")
    config.PROJECT_ROOT = tmp_path
    config.settings = SimpleNamespace(CONTEXT_ACTIVE_MAX_INPUT_TOKENS=100000,
        RESOLVED_CONTEXT_BUDGET_MAX_TOKENS=120000, CONTEXT_SAFETY_MARGIN_TOKENS=1000,
        AGENT_MAX_OUTPUT_TOKENS=128)
    monkeypatch.setitem(sys.modules, "src.config", config)
    from src.core import jobs, task_events, tracing, context_snapshot, cache_metrics
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "ledger.sqlite")
    snapshots = []
    monkeypatch.setattr(context_snapshot, "emit_context_snapshot", snapshots.append)
    monkeypatch.setattr(cache_metrics, "emit_usage_from_response", lambda *a, **k: None)
    job = jobs.claim_job("synthetic-session", "synthetic-turn", owner_user_id=17)
    identity = ExecutionIdentity(17, "synthetic-session", "synthetic-turn", job.job_id)
    task_events.init_budget(job.job_id, time.time() + 60, max_calls=4, max_tokens=100000)
    jt = tracing._job_id.set(job.job_id)
    st = tracing._session_id.set(identity.session_id)

    def forbidden(*args, **kwargs):
        raise AssertionError("Synthetic context test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"):
        monkeypatch.setenv(name, "false")
    env = SimpleNamespace(loop=loop, identity=identity, events=task_events, tracing=tracing,
        root=tmp_path, snapshots=snapshots,
        archive=ObservedArchive(tmp_path, identity, SCOPE), model=SyntheticModel())
    yield env
    tracing._job_id.reset(jt)
    tracing._session_id.reset(st)
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.run_until_complete(loop.shutdown_default_executor())
    loop.close()


def history():
    return [SystemMessage(content="Required system instructions", id="system"),
        HumanMessage(content=RAW, id="old-user"), AIMessage(content=RAW, id="old-answer"),
        HumanMessage(content="Current real request", id="current-user"),
        _DeepSeekMessage(content="", id="call-message", provider_protocol=_ProtocolState(SCOPE, PRIVATE),
            tool_calls=[{"name": "lookup", "args": {}, "id": "call-1", "type": "tool_call"}]),
        ToolMessage(content="Exact evidence doi:synthetic/example", tool_call_id="call-1", id="result")]


def projection(env, **kwargs):
    return WorkingContextProjection(archive=env.archive, summary_model=env.model,
        enabled=kwargs.pop("enabled", True), deadline=kwargs.pop("deadline", time.monotonic() + 30),
        trigger_tokens=kwargs.pop("trigger_tokens", 1000), keep_messages=1,
        readback_available=kwargs.pop("readback_available", True), **kwargs)


def budget(env):
    return env.events.projection(env.identity.run_id)["budget"]


def test_archive_before_summary_raw_checkpoint_and_evidence_readback(environment):
    env = environment
    raw = history()
    before = [m.model_copy(deep=True) for m in raw]

    async def observe():
        assert env.archive.reference is not None
        restored = await env.archive.read(env.archive.reference)
        assert restored == tuple(before)
        assert restored[4].provider_protocol.reasoning == PRIVATE

    env.model.on_call = observe

    async def scenario():
        sent = await projection(env).project(raw)
        assert raw == before
        assert sent[0] is raw[0]
        assert sent[1] is raw[1]  # Real user requirements remain verbatim.
        assert isinstance(sent[2], SystemMessage)
        assert sent[2].additional_kwargs["context_origin"] == "internal_control"
        assert "Raw evidence readback:" in sent[2].content
        assert '"tool": "read_context_evidence"' in sent[2].content
        assert sent[3:] == tuple(raw[3:])
        assert all(a is b for a, b in zip(sent[3:], raw[3:]))
        assert sum(isinstance(m, HumanMessage) for m in sent) == 2
        assert PRIVATE not in str(env.model.seen)
        assert RAW in env.model.seen[0][0].content  # Native summary input not trimmed.
        evidence = await env.archive.read_evidence(env.archive.reference, start=1, stop=5)
        assert evidence[0]["data"]["content"] == RAW
        assert PRIVATE not in json.dumps(evidence)
        reopened = EncryptedContextArchive(env.root, env.identity, SCOPE)
        assert await reopened.read(env.archive.reference) == tuple(raw)
        other = EncryptedContextArchive(env.root, replace(env.identity, run_id="other-run"), SCOPE)
        with pytest.raises(AdmissionStopped, match="scope_mismatch"):
            await other.read(env.archive.reference)

    env.loop.run_until_complete(scenario())
    assert budget(env)["calls"] == 1
    assert budget(env)["reported_total_tokens"] == 20
    assert budget(env)["in_flight_attempts"] == 0
    assert env.snapshots[-1]["source"] == "working_context_summary"
    blob = checkpoint_path(env.root, env.identity, SCOPE).read_bytes()
    assert RAW.encode() not in blob and PRIVATE.encode() not in blob


def test_real_host_tool_metadata_does_not_pin_every_tool_group(environment):
    env = environment
    env.archive = ObservedArchive(env.root, env.identity, ("https://synthetic.invalid", "synthetic"))
    raw = [HumanMessage(content="Preserve this actual request", id="request")]
    for index in range(24):
        raw.extend([AIMessage(content="", id=f"a-{index}", tool_calls=[{
            "name": "lookup", "args": {}, "id": f"call-{index}", "type": "tool_call"}]),
            ToolMessage(content="Synthetic archived evidence. " * 120, id=f"t-{index}",
                tool_call_id=f"call-{index}", additional_kwargs={"execution_dispatched": True,
                    "host_preflight_reconciled": {"schema_version": 1, "operation_id": f"op-{index}",
                                                  "result_sha256": "a" * 64},
                    "first_delivery_reference": {"tool_call_id": f"call-{index}"}})])
    async def scenario():
        sent = await projection(env).project(raw)
        assert len(sent) < len(raw)
        assert sent[0] is raw[0] and sent[-2:] == tuple(raw[-2:])
        assert await env.archive.read(env.archive.reference) == tuple(raw)
        assert len(env.model.seen) == 1
    env.loop.run_until_complete(scenario())


def test_public_model_hook_leaves_authoritative_graph_state_raw(environment):
    env = environment
    raw = history()
    main = SyntheticModel(content="Final synthetic response")

    async def scenario():
        async with open_checkpoint(env.root, env.identity, SCOPE) as saver:
            graph = create_agent(main, middleware=[projection(env)], checkpointer=saver)
            config = {"configurable": checkpoint_scope(env.identity, SCOPE)}
            result = await graph.ainvoke({"messages": raw}, config, durability="sync")
            assert result["messages"][:-1] == raw
            saved = await graph.aget_state(config)
            assert saved.values["messages"][:-1] == raw
            assert saved.values["messages"][4].provider_protocol.reasoning == PRIVATE
        assert len(main.seen) == 1
        assert sum(len(m.content) for m in main.seen[0]) < sum(len(m.content) for m in raw)
        assert sum(isinstance(m, HumanMessage) for m in main.seen[0]) == 2
        assert all(m.additional_kwargs.get("context_projection") != "working_summary_v1"
                   for m in result["messages"])

    env.loop.run_until_complete(scenario())


@pytest.mark.parametrize("enabled,threshold", [(False, 1), (True, 1000000)])
def test_disabled_or_under_threshold_has_no_summary_or_archive(environment, enabled, threshold):
    env = environment
    raw = history()
    sent = env.loop.run_until_complete(projection(env, enabled=enabled, trigger_tokens=threshold).project(raw))
    assert all(a is b for a, b in zip(sent, raw))
    assert env.model.seen == [] and env.archive.reference is None
    assert budget(env)["calls"] == 0


@pytest.mark.parametrize("kind", ["system", "required-id", "installed-skill"])
@pytest.mark.parametrize("recover", [False, True])
def test_required_methods_remain_verbatim(environment, kind, recover):
    env = environment
    if recover:
        sys.modules["src.config"].settings.AGENT_MAX_OUTPUT_TOKENS = 256
        env.model.responses.extend([("truncated", "length"), ("Complete summary.", "stop")])
    raw = history()
    method = SystemMessage(content="Exact required method: synthetic precision rule", id="method")
    kwargs = {}
    if kind != "system":
        # Generic-provider fixture: required-ID/skill protection must work even
        # when no provider-private message independently pins the same group.
        env.archive = ObservedArchive(env.root, env.identity, ("https://synthetic.invalid", "synthetic"))
        raw[4] = AIMessage(**raw[4].model_dump())
        raw[2:2] = [AIMessage(content="", tool_calls=[{
            "name": "read_skill", "args": {}, "id": "method-call", "type": "tool_call"}])]
        method = ToolMessage(content=json.dumps({"content_origin": "installed_skill_guidance" if
            kind == "installed-skill" else "other", "text": "Exact required method"}),
            name="read_skill", tool_call_id="method-call", id="method")
        if kind == "required-id":
            kwargs["required_message_ids"] = lambda: {"method"}
        raw.insert(3, method)
    else:
        raw.insert(3, method)
    sent = env.loop.run_until_complete(projection(env, **kwargs).project(raw))
    assert any(m is method for m in sent)
    assert method.content in [m.content for m in sent]
    assert len(env.model.seen) == (2 if recover else 1)


def test_missing_required_method_and_readback_fail_closed(environment):
    env = environment
    for kwargs, reason in [({"required_message_ids": lambda: {"missing"}}, "required_message_missing"),
                           ({"readback_available": False}, "readback_required")]:
        with pytest.raises(AdmissionStopped, match=reason):
            env.loop.run_until_complete(projection(env, **kwargs).project(history()))
    assert env.model.seen == [] and budget(env)["calls"] == 0


@pytest.mark.parametrize("defect", ["missing", "scope", "plain", "subclass"])
def test_private_protocol_state_is_never_silently_projected_away(environment, defect):
    env = environment
    raw = history()
    if defect == "missing":
        raw[4].provider_protocol = None
    elif defect == "scope":
        raw[4].provider_protocol = _ProtocolState(("synthetic-other", "other"), PRIVATE)
    elif defect == "plain":
        raw[4] = AIMessage(**raw[4].model_dump())
    else:
        class UnknownProvider(AIMessage):
            secret_state: str = Field(default=PRIVATE, exclude=True)
        raw[2] = UnknownProvider(content=RAW)
    before = [m.model_copy(deep=True) for m in raw]
    with pytest.raises(AdmissionStopped):
        env.loop.run_until_complete(projection(env).project(raw))
    assert raw == before and env.model.seen == []


def test_archive_failure_prevents_summary_and_main_dispatch(environment, monkeypatch):
    env = environment
    async def fail(raw):
        raise OSError("Synthetic archive failure " + PRIVATE)
    monkeypatch.setattr(env.archive, "archive", fail)
    main = SyntheticModel()
    graph = create_agent(main, middleware=[projection(env)])
    with pytest.raises(AdmissionStopped) as error:
        env.loop.run_until_complete(graph.ainvoke({"messages": history()}))
    assert error.value.reason == "context_projection_failed"
    assert PRIVATE not in str(error.value)
    assert env.model.seen == main.seen == []
    assert budget(env)["calls"] == 0


@pytest.mark.parametrize("defect", ["error", "length", "empty", "expanded"])
def test_failed_summary_pauses_without_dispatch_or_budget_reset(environment, defect):
    env = environment
    if defect == "error":
        env.model.failure = "error"
    elif defect == "length":
        env.model.finish_reason = "length"
    elif defect == "empty":
        env.model.content = "  "
    else:
        env.model.content = RAW * 3
    main = SyntheticModel()
    graph = create_agent(main, middleware=[projection(env)])
    with pytest.raises(AdmissionStopped) as error:
        env.loop.run_until_complete(graph.ainvoke({"messages": history()}))
    assert PRIVATE not in str(error.value)
    assert len(env.model.seen) == 1 and main.seen == []
    assert budget(env)["calls"] == 1
    assert budget(env)["in_flight_attempts"] == 0
    assert env.loop.run_until_complete(env.archive.read(env.archive.reference)) == tuple(history())
    if defect == "error":
        assert budget(env)["unknown_reserved_tokens"] > 0


@pytest.mark.parametrize("finish,partial", [
    ("length", "synthetic truncated summary"), ("max_tokens", ""),
    ("max_output_tokens", "synthetic truncated summary")])
def test_length_recovery_after_success_preserves_raw_scope_and_protocol(environment, finish, partial):
    env = environment
    sys.modules["src.config"].settings.AGENT_MAX_OUTPUT_TOKENS = 192
    raw = history()
    p = projection(env)

    async def scenario():
        await p.project(raw)
        first_ref = env.archive.reference
        # A changed raw prefix requires another summary, as in QA_BATCH_V2.
        updated = list(raw)
        updated[2] = AIMessage(content=RAW + "New verified evidence", id="updated-answer")
        before = [m.model_copy(deep=True) for m in updated]
        env.model.responses.extend([(partial, finish), ("Complete recovered summary.", "stop")])
        sent = await p.project(updated)
        assert updated == before and raw == history()
        assert all(a is b for a, b in zip(sent[-3:], updated[-3:]))
        assert sent[0] is updated[0] and sent[1] is updated[1]
        assert "Complete recovered summary." in sent[2].content
        assert "synthetic truncated summary" not in sent[2].content
        assert PRIVATE not in str(env.model.seen)
        assert env.model.seen[1] == env.model.seen[2]  # Complete original prompt, no retry trimming.
        assert RAW in env.model.seen[2][0].content
        assert await env.archive.read(first_ref) == tuple(raw)
        assert await env.archive.read(env.archive.reference) == tuple(before)
        other = EncryptedContextArchive(env.root, replace(env.identity, run_id="other-run"), SCOPE)
        with pytest.raises(AdmissionStopped, match="scope_mismatch"):
            await other.read(env.archive.reference)
        assert len(env.model.seen) == 3

    env.loop.run_until_complete(scenario())
    assert env.model.max_tokens == 128
    assert env.model.seen_limits == [128, 128, 192]
    assert budget(env)["calls"] == 3
    assert budget(env)["reported_total_tokens"] == 60
    assert budget(env)["in_flight_attempts"] == 0
    assert [s["output_tokens"] for s in env.snapshots if s["source"] == "working_context_summary"][-1] == 192


@pytest.mark.parametrize("cap,expected", [(64, [64]), (384, [128, 256, 384]),
                                         (4096, [128, 256, 512])])
def test_length_recovery_stops_at_cap_or_attempt_limit_without_main_dispatch(environment, cap, expected):
    env = environment
    sys.modules["src.config"].settings.AGENT_MAX_OUTPUT_TOKENS = cap
    env.model.finish_reason = "length"
    main = SyntheticModel()
    graph = create_agent(main, middleware=[projection(env)])
    with pytest.raises(AdmissionStopped, match="context_summary_incomplete"):
        env.loop.run_until_complete(graph.ainvoke({"messages": history()}))
    assert env.model.seen_limits == expected and env.model.max_tokens == 128
    assert main.seen == []
    assert budget(env)["calls"] == len(expected)
    assert budget(env)["reported_total_tokens"] == 20 * len(expected)
    assert budget(env)["in_flight_attempts"] == 0
    assert env.loop.run_until_complete(env.archive.read(env.archive.reference)) == tuple(history())


@pytest.mark.parametrize("exhausted", ["calls", "tokens", "deadline"])
def test_length_recovery_cannot_reset_original_budget_or_deadline(environment, monkeypatch, exhausted):
    env = environment
    sys.modules["src.config"].settings.AGENT_MAX_OUTPUT_TOKENS = 256
    env.model.responses.extend([("truncated", "length"), ("Must never be used", "stop")])
    p = projection(env)
    original = budget(env)

    async def exhaust():
        if exhausted == "deadline":
            # Expire the adapter's clock without changing asyncio's clock.
            monkey_time["now"] += 60
        else:
            with env.events.transaction() as conn:
                field = "max_calls" if exhausted == "calls" else "max_tokens"
                value = 1 if exhausted == "calls" else 20
                conn.execute(f"UPDATE task_budgets SET {field}=? WHERE job_id=?",
                             (value, env.identity.run_id))

    env.model.on_call = exhaust
    monkey_time = {"now": time.monotonic()}
    if exhausted == "deadline":
        monkeypatch.setattr("src.agent.context_projection.time",
                            SimpleNamespace(monotonic=lambda: monkey_time["now"]))
    with pytest.raises(AdmissionStopped):
        env.loop.run_until_complete(p.project(history()))
    assert env.model.seen_limits == [128]
    assert budget(env)["calls"] == 1 and budget(env)["reported_total_tokens"] == 20
    assert budget(env)["deadline"] == original["deadline"]
    assert budget(env)["in_flight_attempts"] == 0
    assert env.loop.run_until_complete(env.archive.read(env.archive.reference)) == tuple(history())


def test_run_deadline_cancels_summary_and_preserves_unknown_usage(environment):
    env = environment
    env.model.failure = "wait"
    with pytest.raises(AdmissionStopped):
        env.loop.run_until_complete(projection(env, deadline=time.monotonic() + 0.3).project(history()))
    assert len(env.model.seen) == 1
    assert budget(env)["calls"] == 1 and budget(env)["unknown_reserved_tokens"] > 0
    assert budget(env)["in_flight_attempts"] == 0


def test_cancellation_propagates_and_settles_attempt(environment):
    env = environment
    env.model.failure = "wait"
    async def scenario():
        entered = asyncio.Event()
        async def started():
            entered.set()
        env.model.on_call = started
        task = asyncio.create_task(projection(env).project(history()))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    env.loop.run_until_complete(scenario())
    assert budget(env)["unknown_reserved_tokens"] > 0
    assert budget(env)["in_flight_attempts"] == 0


@pytest.mark.parametrize("defect", ["wrong-run", "no-budget", "maintenance", "calls", "tokens", "deadline", "retry"])
def test_summary_cannot_escape_existing_execution_budget(environment, defect):
    env = environment
    scope_token = None
    if defect == "wrong-run":
        env.tracing.set_job_id("other-run")
    elif defect == "maintenance":
        scope_token = env.events._checkpoint_scope.set({"key": "synthetic", "deadline": time.monotonic() + 60})
    elif defect == "retry":
        env.model.max_retries = 1
    else:
        with env.events.transaction() as conn:
            if defect == "no-budget":
                conn.execute("DELETE FROM task_budgets WHERE job_id=?", (env.identity.run_id,))
            else:
                field, value = {"calls": ("max_calls", 0), "tokens": ("max_tokens", 1),
                                "deadline": ("deadline", time.time() - 1)}[defect]
                conn.execute(f"UPDATE task_budgets SET {field}=? WHERE job_id=?", (value, env.identity.run_id))
    try:
        with pytest.raises(AdmissionStopped):
            env.loop.run_until_complete(projection(env).project(history()))
    finally:
        if scope_token is not None:
            env.events._checkpoint_scope.reset(scope_token)
    assert env.model.seen == []


def test_reconstructed_projection_uses_same_run_attempt_limit(environment):
    env = environment
    with env.events.transaction() as conn:
        conn.execute("UPDATE task_budgets SET max_calls=1 WHERE job_id=?", (env.identity.run_id,))
    env.loop.run_until_complete(projection(env).project(history()))
    env.archive = ObservedArchive(env.root, env.identity, SCOPE)
    with pytest.raises(AdmissionStopped):
        env.loop.run_until_complete(projection(env).project(history()))
    assert len(env.model.seen) == 1 and budget(env)["calls"] == 1


def test_source_ids_are_not_mutated_by_upstream_middleware(environment):
    env = environment
    raw = history()
    for message in raw:
        message.id = None
    env.loop.run_until_complete(projection(env).project(raw))
    assert all(m.id is None for m in raw)


@pytest.mark.parametrize("field", ["additional_kwargs", "response_metadata", "extra", "content"])
def test_unknown_provider_fields_are_preserved_in_sent_view(environment, field):
    env = environment
    raw = history()
    opaque = raw[2]
    if field == "extra":
        opaque = raw[2] = AIMessage(content=RAW, provider_signature="synthetic-signature")
    elif field == "content":
        opaque.content = [{"type": "provider_block", "signature": "synthetic-signature"}]
    else:
        setattr(opaque, field, {"provider_signature": "synthetic-signature"})
    raw.insert(2, AIMessage(content=RAW, id="older-public"))
    sent = env.loop.run_until_complete(projection(env).project(raw))
    assert any(m is opaque for m in sent)
    assert "synthetic-signature" not in str(env.model.seen)


@pytest.mark.parametrize("defect", ["orphan", "pending", "duplicate"])
def test_malformed_tool_groups_pause_without_summary(environment, defect):
    env = environment
    raw = history()
    if defect == "orphan":
        raw[-1].tool_call_id = "unknown"
    elif defect == "pending":
        raw.pop()
    else:
        raw[-2].tool_calls.append(dict(raw[-2].tool_calls[0]))
    with pytest.raises(AdmissionStopped, match="tool_group"):
        env.loop.run_until_complete(projection(env).project(raw))
    assert env.model.seen == [] and budget(env)["calls"] == 0


def test_multiple_large_archives_roundtrip_independently(environment):
    env = environment
    raw = [HumanMessage(content="Synthetic input")]
    raw.extend(AIMessage(content=RAW) for _ in range(8))
    async def scenario():
        first = await env.archive.archive(raw)
        raw.append(AIMessage(content="New observation"))
        second = await env.archive.archive(raw)
        assert await env.archive.read(first) == tuple(raw[:-1])
        assert await env.archive.read(second) == tuple(raw)
    env.loop.run_until_complete(scenario())


def test_long_single_turn_with_host_metadata_compacts_and_reuses_bounded_cache(environment):
    env = environment
    env.archive = ObservedArchive(env.root, env.identity, ("https://synthetic.invalid", "synthetic"))
    user = HumanMessage(content="Analyze synthetic measurements and retain exact method.", id="only-user",
                        additional_kwargs=env.identity.message_metadata("user"))
    method = SystemMessage(content="Required method: report uncertainty exactly.", id="method")
    raw = [user, method]
    for index in range(8):
        cid = f"call-{index}"
        raw.extend([AIMessage(content="", id=f"assistant-{index}", tool_calls=[{
            "name": "synthetic_analysis", "args": {"index": index}, "id": cid, "type": "tool_call"}],
            additional_kwargs=env.identity.message_metadata("assistant")),
            ToolMessage(content="Synthetic measurements: " + "measurement 12345. " * 500,
                id=f"result-{index}", tool_call_id=cid, name="synthetic_analysis",
                artifact={"op_receipt": {"coverage": {"_tool_outcome": {
                    "artifacts": ({"id": "synthetic-asset"},), "status": "success"}}}},
                additional_kwargs={
                    **env.identity.message_metadata("tool"), "operation_id": f"op-{index}",
                    "result_storage": {"complete": True}, "result_reference": {"tool_call_id": cid},
                    "host_publication": {"status": "completed"},
                    "citrus_execution": {"status": "success"}})])
    required, events = set(), []
    async def emitted(event):
        events.append(event)
    p = projection(env, trigger_tokens=8000, required_message_ids=lambda: required,
                   on_projection=emitted)

    async def scenario():
        first = await p.project(raw)
        assert first[0] is user and first[1] is method
        assert sum(isinstance(m, HumanMessage) for m in first) == 1
        assert len(first) < len(raw)
        assert all(a is b for a, b in zip(first[-2:], raw[-2:]))
        assert len(env.model.seen) == 1
        event = events[0]
        assert event["before_tokens"] > event["after_tokens"]
        assert event["archive"]["message_count"] == len(raw)
        assert event["message_indices"] == list(range(2, len(raw) - 2))
        assert "measurement 12345" not in json.dumps(events)
        # Append-only progress reuses the existing view without another attempt.
        raw.append(AIMessage(content="A small new observation", id="new-observation"))
        second = await p.project(raw)
        assert len(env.model.seen) == 1 and len(events) == 1
        assert second[-1] is raw[-1]
        # A newly required earlier method invalidates the cached removal set.
        required.add("result-0")
        third = await p.project(raw)
        assert raw[2] in third and raw[3] in third
        assert len(env.model.seen) == 2
        # Resume reconstructs from raw state and pays the same run's ledger.
        fresh = projection(env, trigger_tokens=8000, required_message_ids=lambda: required)
        await fresh.project(raw)
        assert len(env.model.seen) == 3
    env.loop.run_until_complete(scenario())
    assert budget(env)["calls"] == 3


def test_protected_chain_without_safe_cutoff_pauses_without_summarization(environment):
    env = environment
    raw = history()[3:]
    raw[-1].content = RAW
    with pytest.raises(AdmissionStopped, match="no_safe_reduction"):
        env.loop.run_until_complete(projection(env).project(raw))
    assert env.model.seen == [] and budget(env)["calls"] == 0

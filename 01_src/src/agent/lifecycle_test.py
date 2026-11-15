"""Boundary order, cancellation and recovery, without services or user data."""
import asyncio
from dataclasses import asdict
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent.events import LoopCheckpoint
from src.agent.identity import ExecutionIdentity
from src.agent.runtime import AgentRuntime, BatchObservation, RunLimits
from src.agent.runtime_test import IDENTITY, call, observe, never_execute


USER = HumanMessage(content="Synthetic task", additional_kwargs=IDENTITY.message_metadata("user"))


def test_events_are_ordered_and_do_not_expose_message_or_tool_payloads():
    events, checkpoints, order = [], [], []

    async def checkpoint(value):
        checkpoints.append(value)
        order.append("saved:" + value.phase)

    async def emit(value):
        events.append(value)
        order.append(value.kind)

    async def model(messages, identity):
        assert order[-1] == "model_started"
        return call(0, args={"private_input": "synthetic-private"}) if len(messages) == 1 else AIMessage(content="done")

    async def execute(response, identity):
        assert order[-1] == "batch_started"
        return observe(response, content="synthetic-private-result")

    result = asyncio.run(AgentRuntime(model, execute, lambda *_: None,
        on_event=emit, checkpoint=checkpoint).run(IDENTITY, [USER]))
    assert result.status == "complete"
    assert [e.kind for e in events] == ["run_started", "model_started", "model_finished",
        "batch_started", "batch_finished", "model_started", "model_finished", "run_finished"]
    assert all(order[i - 1].startswith("saved:") for i in range(1, len(order), 2))
    assert [e.model_steps for e in events] == [0, 1, 1, 1, 1, 2, 2, 2]
    assert "synthetic-private" not in str([asdict(e) for e in events])
    assert checkpoints[-1].phase == "stopped"
    assert checkpoints[3].pending_tool_calls == ("call-0",)
    assert checkpoints[4].pending_tool_calls == ()


def test_checkpoint_failure_prevents_tool_dispatch():
    async def checkpoint(value):
        if value.phase == "tools":
            raise OSError("synthetic storage unavailable")

    async def model(*_):
        return call(0, "write")

    with pytest.raises(OSError, match="storage unavailable"):
        asyncio.run(AgentRuntime(model, never_execute, lambda *_: None,
            checkpoint=checkpoint).run(IDENTITY, [USER]))


@pytest.mark.parametrize("component", ["model", "tools", "final"])
def test_deadline_interrupts_inflight_work_and_keeps_unresolved_call_identity(component):
    settled = []
    events = []

    async def block():
        try:
            await asyncio.Event().wait()
        finally:
            settled.append(component)

    async def model(*_):
        if component == "model":
            await block()
        return call(0, "write") if component == "tools" else AIMessage(content="provisional")

    async def execute(*_):
        await block()

    async def final(*_):
        await block()

    async def emit(event):
        events.append(event)

    async def scenario():
        runtime = AgentRuntime(model, execute, lambda *_: None, check_final=final,
            limits=RunLimits(deadline=time.monotonic() + .05), on_event=emit)
        return await runtime.run(IDENTITY, [USER])

    result = asyncio.run(scenario())
    assert settled == [component]
    assert result.status == "incomplete" and result.reason == "deadline"
    assert result.pending_tool_calls == (("call-0",) if component == "tools" else ())
    assert not any(isinstance(m, ToolMessage) for m in result.messages)
    assert result.answer is None
    assert events[-1].kind == "run_finished" and events[-1].reason == "deadline"


def test_component_timeout_is_not_misreported_as_turn_deadline():
    async def model(*_):
        raise TimeoutError("synthetic component timeout")

    with pytest.raises(TimeoutError, match="component timeout"):
        asyncio.run(AgentRuntime(model, never_execute, lambda *_: None).run(IDENTITY, [USER]))


def test_cancelled_write_keeps_actual_call_id_for_reconciliation():
    async def model(*_):
        return call("existing", "write")

    async def execute(*_):
        raise asyncio.CancelledError()

    result = asyncio.run(AgentRuntime(model, execute, lambda *_: None).run(IDENTITY, [USER]))
    assert result.pending_tool_calls == ("call-existing",)
    assert result.unknown_operations == ()  # no operation IDs fabricated by the loop
    assert result.status == "cancelled"


def test_unsettled_history_never_calls_model_or_reexecutes_write():
    async def model(*_):
        pytest.fail("unsettled transcript sent to model")

    result = asyncio.run(AgentRuntime(model, never_execute, lambda *_: None).run(
        IDENTITY, [USER, call("pending", "write")]))
    assert result.reason == "unresolved_tool_calls"
    assert result.model_steps == 0
    assert result.pending_tool_calls == ("call-pending",)


def test_resume_carries_step_budget_and_does_not_add_a_user_turn():
    request = call(0).model_copy(update={"additional_kwargs": IDENTITY.message_metadata("assistant")})
    observation = ToolMessage(content="stored output", tool_call_id="call-0", name="read",
                              additional_kwargs=IDENTITY.message_metadata("tool"))
    checkpoint = LoopCheckpoint(IDENTITY, (USER, request, observation), 3, "ready")
    recovered = ExecutionIdentity(7, IDENTITY.session_id, IDENTITY.user_turn_id, "run-two")

    async def model(messages, identity):
        assert identity == recovered
        assert messages == checkpoint.messages
        return AIMessage(content="resumed from stored observation")

    result = asyncio.run(AgentRuntime(model, never_execute, lambda *_: None,
        limits=RunLimits(max_model_steps=4)).resume(recovered, checkpoint))
    assert result.model_steps == 4 and result.status == "complete"
    assert len([m for m in result.messages if isinstance(m, HumanMessage)]) == 1
    assert result.messages[-1].additional_kwargs["run_id"] == "run-two"
    exhausted = asyncio.run(AgentRuntime(model, never_execute, lambda *_: None,
        limits=RunLimits(max_model_steps=3)).resume(recovered, checkpoint))
    assert exhausted.reason == "model_step_limit" and exhausted.model_steps == 3


def test_resume_cannot_reuse_an_earlier_runs_call_id():
    request = call(0).model_copy(update={"additional_kwargs": IDENTITY.message_metadata("assistant")})
    checkpoint = LoopCheckpoint(IDENTITY, (USER, request, *observe(request).messages), 1, "ready")

    async def model(*_):
        return call(0, "write")

    result = asyncio.run(AgentRuntime(model, never_execute, lambda *_: None).resume(IDENTITY, checkpoint))
    assert result.reason == "invalid_or_reused_tool_call_id"
    assert result.model_steps == 2


@pytest.mark.parametrize("phase", ["model", "tools", "final", "stopped"])
def test_resume_requires_reconciliation_of_inflight_or_terminal_checkpoint(phase):
    async def model(*_):
        pytest.fail("unreconciled resume called provider")

    checkpoint = LoopCheckpoint(IDENTITY, (USER,), 2, phase, ("original-call",))
    result = asyncio.run(AgentRuntime(model, never_execute, lambda *_: None).resume(IDENTITY, checkpoint))
    assert result.reason == "recovery_requires_reconciliation"
    assert result.pending_tool_calls == ("original-call",)


def test_resume_rejects_other_owner_or_turn():
    checkpoint = LoopCheckpoint(IDENTITY, (USER,), 1, "ready")
    runtime = AgentRuntime(never_execute, never_execute, lambda *_: None)
    for changed in [ExecutionIdentity(8, IDENTITY.session_id, IDENTITY.user_turn_id, "r"),
                    ExecutionIdentity(7, IDENTITY.session_id, "another-turn", "r")]:
        with pytest.raises(ValueError, match="identity_mismatch"):
            asyncio.run(runtime.resume(changed, checkpoint))


def test_new_run_cannot_reset_budget_of_an_existing_turn():
    prior = AIMessage(content="previous attempt", additional_kwargs=IDENTITY.message_metadata("assistant"))
    with pytest.raises(ValueError, match="existing_turn_requires_resume"):
        asyncio.run(AgentRuntime(never_execute, never_execute, lambda *_: None).run(IDENTITY, [USER, prior]))


def test_deadline_is_not_hidden_by_a_component_suppressing_cancellation():
    async def model(*_):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return AIMessage(content="late response")

    async def scenario():
        return await AgentRuntime(model, never_execute, lambda *_: None,
            limits=RunLimits(deadline=time.monotonic() + .02)).run(IDENTITY, [USER])

    result = asyncio.run(scenario())
    assert result.reason == "deadline" and result.answer is None
    assert result.messages[-1].content == "late response"


@pytest.mark.parametrize("reason", ["length", "max_tokens", "max_output_tokens", "unknown", "aborted"])
def test_truncated_or_interrupted_tool_arguments_never_execute(reason):
    async def model(*_):
        return call(0, "write").model_copy(update={"response_metadata": {"finish_reason": reason}})

    result = asyncio.run(AgentRuntime(model, never_execute, lambda *_: None).run(IDENTITY, [USER]))
    normalized = "length" if reason in {"max_tokens", "max_output_tokens"} else reason
    assert result.reason == "model_" + normalized
    assert result.pending_tool_calls == ("call-0",)

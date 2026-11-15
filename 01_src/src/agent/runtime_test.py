"""Behavior of the domain-independent loop; no model services or account files."""
import asyncio
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.agent.identity import ExecutionIdentity
from src.agent.runtime import (AdmissionStopped, AgentRuntime, BatchObservation,
                               FinalCheck, RunLimits)


IDENTITY = ExecutionIdentity(7, "synthetic-session", "synthetic-user-turn", "run-one")


def call(step, name="read", args=None):
    return AIMessage(content="", tool_calls=[{
        "id": "call-" + str(step), "name": name, "args": args or {}}])


def observe(response, *, content="observed", status="complete", **kwargs):
    return BatchObservation(tuple(ToolMessage(
        content=content, name=c["name"], tool_call_id=c["id"],
        artifact={"_tool_outcome": {"status": status}})
        for c in response.tool_calls), **kwargs)


def admit(messages, identity):
    assert identity == IDENTITY


async def never_execute(*args):
    pytest.fail("unexpected tool execution")


def run(model, executor=never_execute, **kwargs):
    return asyncio.run(AgentRuntime(model, executor, admit, **kwargs).run(
        IDENTITY, [HumanMessage(content="Synthetic task")]))


def test_direct_answer_requires_no_plugin_plan_or_tool():
    async def model(messages, identity):
        return AIMessage(content="A direct explanation.")
    result = run(model)
    assert result.status == "complete"
    assert result.answer.content == "A direct explanation."
    assert result.model_steps == 1


def test_exploration_and_recoverable_failures_outlive_old_two_batch_limits():
    snapshots = []
    async def model(messages, identity):
        snapshots.append(messages)
        n = len([m for m in messages if isinstance(m, ToolMessage)])
        return call(n, "inspect" if n < 4 else "edit") if n < 8 else AIMessage(content="Ready")
    async def execute(response, identity):
        n = int(response.tool_calls[0]["id"].split("-")[1])
        return observe(response, content=f"Exact observation {n}",
                       status="failed" if n in (1, 3, 5) else "complete")
    result = run(model, execute)
    assert result.status == "complete" and result.model_steps == 9
    assert [m.content for m in result.messages if isinstance(m, ToolMessage)] == [
        f"Exact observation {i}" for i in range(8)]
    for previous, following in zip(snapshots, snapshots[1:]):
        assert following[:len(previous)] == previous


def test_final_check_returns_observations_to_same_tool_capable_loop():
    calls = []
    async def model(messages, identity):
        calls.append(messages)
        if len(calls) == 1:
            return AIMessage(content="The requested file is ready.")
        if len(calls) == 2:
            assert isinstance(messages[-1], SystemMessage)
            assert messages[-1].additional_kwargs["context_turn_id"] == identity.user_turn_id
            return call("publish", "publish")
        return AIMessage(content="Here is the registered file.")
    async def check(answer, messages, identity):
        if not any(isinstance(m, ToolMessage) for m in messages):
            return FinalCheck(False, ("No registered bytes exist for the requested file.",))
        return FinalCheck()
    async def execute(response, identity):
        return observe(response)
    result = run(model, execute, check_final=check)
    assert result.status == "complete" and result.model_steps == 3
    assert len([m for m in result.messages if isinstance(m, HumanMessage)]) == 1


def test_unknown_write_pauses_without_another_model_call_or_replay():
    seen = []
    async def model(messages, identity):
        seen.append("model")
        return call(0, "publish")
    async def execute(response, identity):
        seen.append("execute")
        return observe(response, content="Result requires reconciliation.",
                       status="unknown", unknown_operations=("op-one",))
    result = run(model, execute)
    assert seen == ["model", "execute"]
    assert result.status == "incomplete"
    assert result.reason == "operation_outcome_unknown"
    assert result.unknown_operations == ("op-one",)


def test_pending_question_keeps_same_turn_and_stops_dispatch():
    async def model(messages, identity):
        return call(0, "ask")
    async def execute(response, identity):
        return observe(response, pending_question=True)
    result = run(model, execute)
    assert result.status == "waiting" and result.model_steps == 1
    assert result.messages[-1].additional_kwargs["turn_id"] == IDENTITY.user_turn_id


def test_concurrent_observations_are_paired_in_provider_call_order():
    async def model(messages, identity):
        if len(messages) == 1:
            return AIMessage(content="", tool_calls=[
                {"id": "a", "name": "read", "args": {}},
                {"id": "b", "name": "read", "args": {}}])
        assert [m.tool_call_id for m in messages if isinstance(m, ToolMessage)] == ["a", "b"]
        return AIMessage(content="done")
    async def execute(response, identity):
        return BatchObservation(tuple(reversed(observe(response).messages)))
    assert run(model, execute).status == "complete"


def test_missing_observation_is_an_integration_error_not_success():
    async def model(messages, identity):
        return call(0)
    async def execute(response, identity):
        return BatchObservation(())
    with pytest.raises(ValueError, match="pairing_mismatch"):
        run(model, execute)


def test_reused_tool_call_id_never_reexecutes():
    executions = []
    async def model(messages, identity):
        return call(0, "write")
    async def execute(response, identity):
        executions.append(response)
        return observe(response)
    result = run(model, execute)
    assert len(executions) == 1
    assert result.reason == "invalid_or_reused_tool_call_id"


@pytest.mark.parametrize("reason", ["length", "content_filter", "error"])
def test_provider_termination_cannot_become_completed_answer(reason):
    async def model(messages, identity):
        return AIMessage(content="An unfinished response", response_metadata={"finish_reason": reason})
    result = run(model)
    assert result.status == "incomplete" and result.reason == "model_" + reason
    assert result.answer is None


def test_bad_tool_json_does_not_dispatch_or_resend_malformed_protocol():
    async def model(messages, identity):
        return AIMessage(content="", invalid_tool_calls=[
            {"name": "write", "args": "{", "id": "broken", "error": "invalid JSON"}])
    result = run(model)
    assert result.reason == "invalid_tool_call" and result.model_steps == 1


def test_context_admission_never_trims_prior_observations():
    inputs = []
    async def model(messages, identity):
        return call(len(messages))
    async def execute(response, identity):
        return observe(response, content="x" * 10000)
    def boundary(messages, identity):
        inputs.append(messages)
        if len(inputs) > 1:
            raise AdmissionStopped("input_window")
    result = asyncio.run(AgentRuntime(model, execute, boundary).run(
        IDENTITY, [HumanMessage(content="original request")]))
    assert result.reason == "input_window"
    assert result.messages[-1].content == "x" * 10000
    assert result.messages[0].content == "original request"
    assert result.model_steps == 1


def test_cancellation_does_not_assert_dispatched_write_was_undone():
    async def model(messages, identity):
        return call(0, "write")
    async def execute(response, identity):
        raise asyncio.CancelledError()
    result = run(model, execute)
    assert result.status == "cancelled"
    assert not any(isinstance(m, ToolMessage) for m in result.messages)


def test_resource_limits_stop_without_success_or_extra_dispatch():
    async def model(messages, identity):
        return call(len(messages))
    async def execute(response, identity):
        return observe(response)
    result = run(model, execute, limits=RunLimits(max_model_steps=4))
    assert result.status == "incomplete" and result.model_steps == 4
    assert result.reason == "model_step_limit"
    result = run(model, limits=RunLimits(deadline=time.monotonic() - 1))
    assert result.reason == "deadline" and result.model_steps == 0


def test_recovery_run_does_not_change_turn_identity():
    recovered = ExecutionIdentity(7, "synthetic-session", "synthetic-user-turn", "run-two")
    assert recovered.turn_key == IDENTITY.turn_key
    assert recovered.run_id != IDENTITY.run_id


@pytest.mark.parametrize("owner", [0, -1, True, "7"])
def test_untrusted_or_missing_owner_is_rejected(owner):
    with pytest.raises(ValueError, match="owner"):
        ExecutionIdentity(owner, "session", "turn", "run")

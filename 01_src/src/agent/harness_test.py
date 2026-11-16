"""Production HarnessRuntime contracts using real graph steps and SQLite."""
import asyncio
import time

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.agent.harness import HarnessRuntime
from src.agent.identity import ExecutionIdentity
from src.agent.runtime import BatchObservation, FinalCheck, RunLimits

IDENTITY = ExecutionIdentity(1, "session", "turn", "run")
SCHEMAS = [{"type": "function", "function": {"name": "fixture", "description": "Read fixture",
           "parameters": {"type": "object", "properties": {}}}}]


def action(cid="one", reason="tool_calls"):
    return AIMessage(content="", tool_calls=[{"name": "fixture", "id": cid, "args": {}}],
                     response_metadata={"finish_reason": reason})


@pytest.mark.parametrize("async_admission", [False, True])
def test_admission_is_awaited_before_model_and_tool(tmp_path, async_admission):
    admissions, sent = [], []

    def record(history, identity):
        assert identity == IDENTITY
        admissions.append(len(history))

    async def async_admit(history, identity):
        await asyncio.sleep(0)
        record(history, identity)

    async def model(*_):
        sent.append(1)
        assert len(admissions) == (1 if len(sent) == 1 else 3)
        return action() if len(sent) == 1 else AIMessage(content="done")

    async def execute(*_):
        assert len(admissions) == 2
        return BatchObservation((ToolMessage(content="observed", name="fixture", tool_call_id="one"),))

    async def final(*_):
        return FinalCheck()

    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "state.sqlite")) as saver:
            result = await HarnessRuntime(model, execute, async_admit if async_admission else record,
                final, RunLimits(), SCHEMAS).run(IDENTITY, [HumanMessage(content="task")], saver=saver)
            assert result.status == "complete"

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["complete", "waiting", "unknown"])
def test_real_graph_dispatch_and_stopping(tmp_path, status):
    calls, sent = [], []
    async def model(messages, identity):
        sent.append(messages)
        return action() if len(sent) == 1 else AIMessage(content="done")
    async def execute(response, identity):
        calls.append(response.tool_calls[0]["id"])
        return BatchObservation((ToolMessage(content="observed", name="fixture", tool_call_id="one"),),
                                pending_question=status == "waiting", unknown_operations=("op",) if status == "unknown" else ())
    async def final(*args):
        return FinalCheck()
    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "state.sqlite")) as saver:
            runtime = HarnessRuntime(model, execute, lambda *args: None, final, RunLimits(), SCHEMAS)
            result = await runtime.run(IDENTITY, [HumanMessage(content="task")], saver=saver)
            assert result.status == ("incomplete" if status == "unknown" else status)
            assert result.model_steps == (2 if status == "complete" else 1)
            assert calls == ["one"]
            assert not result.pending_tool_calls
            if status == "complete":
                assert result.answer.content == "done"
            with pytest.raises(ValueError, match="existing_turn_requires_explicit_resume"):
                await runtime.run(IDENTITY, [HumanMessage(content="task")], saver=saver)
            replay = await runtime.run(IDENTITY, [], saver=saver, resume=True)
            assert replay.status == result.status and calls == ["one"]
    asyncio.run(scenario())


def test_final_rejection_returns_to_same_metered_model(tmp_path):
    sent = []
    async def model(messages, identity):
        sent.append(messages)
        return AIMessage(content="invalid" if len(sent) == 1 else "valid")
    async def final(answer, *args):
        return FinalCheck(answer.content == "valid", () if answer.content == "valid" else ("fix evidence",))
    async def execute(*args):
        pytest.fail("no tool should execute")
    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "state.sqlite")) as saver:
            result = await HarnessRuntime(model, execute, lambda *args: None, final, RunLimits(), SCHEMAS).run(
                IDENTITY, [HumanMessage(content="task")], saver=saver)
            assert result.answer.content == "valid" and result.model_steps == 2
            assert sum(isinstance(m, HumanMessage) for m in result.messages) == 1
            assert "fix evidence" in str(sent[-1][-1].content)
    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["length", "unknown", "content_filter"])
def test_invalid_finish_never_dispatches(tmp_path, reason):
    async def model(*args):
        return action(reason=reason)
    async def execute(*args):
        pytest.fail("invalid call dispatched")
    async def final(*args):
        return FinalCheck()
    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "state.sqlite")) as saver:
            result = await HarnessRuntime(model, execute, lambda *args: None, final, RunLimits(), SCHEMAS).run(
                IDENTITY, [HumanMessage(content="task")], saver=saver)
            assert result.reason == "model_"+reason and result.pending_tool_calls == ("one",)
    asyncio.run(scenario())


def test_cancelled_node_is_durable_and_keeps_host_observation(tmp_path):
    host_state = {"observations": []}
    events = []
    async def model(*_):
        return action()
    async def execute(*_):
        host_state["observations"].append("effect outcome unknown")
        raise asyncio.CancelledError()
    async def final(*_):
        return FinalCheck()
    async def event(value):
        events.append(value)
    def restore(saved):
        host_state["observations"] = list(saved["observations"])
    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "state.sqlite")) as saver:
            runtime = HarnessRuntime(model, execute, lambda *_: None, final, RunLimits(), SCHEMAS,
                on_event=event, export_host=lambda: {"observations": list(host_state["observations"])}, restore_host=restore)
            result = await runtime.run(IDENTITY, [HumanMessage(content="task")], saver=saver)
            assert result.status == "cancelled"
            assert host_state["observations"] == ["effect outcome unknown"]
            replay = await runtime.run(IDENTITY, [], saver=saver, resume=True)
            assert replay.status == "cancelled"
            assert host_state["observations"] == ["effect outcome unknown"]
            assert events[-1].kind == "run_finished" and events[-1].status == "cancelled"
    asyncio.run(scenario())


def test_production_driver_preserves_private_protocol_through_checkpoint(tmp_path):
    from src.agent.checkpoint_store import open_checkpoint, checkpoint_scope
    from src.core.provider_protocol import _DeepSeekMessage, _ProtocolState
    scope = ("https://api.deepseek.com/v1", "deepseek-reasoner")
    steps = []
    async def model(messages, _):
        steps.append(1)
        if len(steps) == 1:
            base = action()
            return _DeepSeekMessage(**base.model_dump(), provider_protocol=_ProtocolState(scope, "synthetic-private"))
        original = next(m for m in messages if isinstance(m, _DeepSeekMessage))
        assert original.provider_protocol.reasoning == "synthetic-private"
        return _DeepSeekMessage(content="done", provider_protocol=_ProtocolState(scope, ""))
    async def execute(*_):
        return BatchObservation((ToolMessage(content="observation", name="fixture", tool_call_id="one"),))
    async def final(*_):
        return FinalCheck()
    async def scenario():
        runtime = HarnessRuntime(model, execute, lambda *_: None, final, RunLimits(), SCHEMAS)
        async with open_checkpoint(tmp_path, IDENTITY, scope) as saver:
            result = await runtime.run(IDENTITY, [HumanMessage(content="task")], saver=saver,
                checkpoint_config=checkpoint_scope(IDENTITY, scope))
            assert result.status == "complete"
        async with open_checkpoint(tmp_path, IDENTITY, scope) as saver:
            replay = await runtime.run(IDENTITY, [], saver=saver, resume=True,
                checkpoint_config=checkpoint_scope(IDENTITY, scope))
            assert replay.status == "complete" and len(steps) == 2
            original = next(m for m in replay.messages if isinstance(m, _DeepSeekMessage))
            assert original.provider_protocol.reasoning == "synthetic-private"
            assert "synthetic-private" not in repr(original) and "provider_protocol" not in original.model_dump()
    asyncio.run(scenario())


def test_tool_batch_runs_once_for_parallel_tool_node_callbacks(tmp_path):
    calls, steps = [], []
    async def model(*args):
        steps.append(1)
        if len(steps) == 1:
            return AIMessage(content="", tool_calls=[{"name": "fixture", "id": str(i), "args": {}} for i in range(3)])
        return AIMessage(content="done")
    async def execute(response, identity):
        calls.append(1)
        await asyncio.sleep(0)
        return BatchObservation(tuple(ToolMessage(content="ok", name=c["name"], tool_call_id=c["id"]) for c in response.tool_calls))
    async def final(*args):
        return FinalCheck()
    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "state.sqlite")) as saver:
            result = await HarnessRuntime(model, execute, lambda *args: None, final, RunLimits(), SCHEMAS).run(
                IDENTITY, [HumanMessage(content="task")], saver=saver)
            assert result.status == "complete" and calls == [1]
            assert len([m for m in result.messages if isinstance(m, ToolMessage)]) == 3
    asyncio.run(scenario())

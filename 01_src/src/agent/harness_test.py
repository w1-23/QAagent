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


@pytest.mark.parametrize("sink", ["default", "sync", "async"])
def test_public_working_note_precedes_dispatch_and_keeps_final_candidates_private(tmp_path, monkeypatch, sink):
    import json
    from src.core import public_progress, progress_bus

    monkeypatch.setattr(public_progress, "_persist_last_note", lambda *a, **k: None)
    observed, published = [], []
    queue = asyncio.Queue()
    progress_bus.set_request_queue(queue)
    public_progress.reset_emitted()

    def publish(text, **metadata):
        observed.append("public_note")
        published.append({"text": text, **metadata})

    async def async_publish(text, **metadata):
        await asyncio.sleep(0)
        publish(text, **metadata)

    steps = []
    async def model(*_):
        steps.append(1)
        if len(steps) == 1:
            return AIMessage(content=[
                {"type": "reasoning", "reasoning": "synthetic-private"},
                {"type": "text", "text": "先核对公开来源[W1]。"}],
                additional_kwargs={"reasoning_content": "synthetic-private"},
                tool_calls=[{"name": "fixture", "id": cid, "args": {}} for cid in ("one", "two")],
                response_metadata={"finish_reason": "tool_calls"})
        return AIMessage(content="synthetic-final-candidate" if len(steps) == 2 else
                         '{"schema_version":1,"answer":"synthetic-final-answer"}')

    async def execute(response, _):
        if sink == "default":
            while not queue.empty():
                frame = queue.get_nowait()
                if frame["event"] == "assistant_progress":
                    published.append(json.loads(frame["data"]))
        assert len(published) == 1  # Published before any execution.
        assert published[0]["tool_call_ids"] == ["one", "two"]
        observed.append("execute")
        return BatchObservation(tuple(ToolMessage(content="ok", name=c["name"], tool_call_id=c["id"])
                                      for c in response.tool_calls))

    async def final(response, *_):
        return FinalCheck(response.content != "synthetic-final-candidate",
                          ("return reviewed final",) if response.content == "synthetic-final-candidate" else ())

    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "progress.sqlite")) as saver:
            callback = None if sink == "default" else publish if sink == "sync" else async_publish
            result = await HarnessRuntime(model, execute, lambda *_: None, final, RunLimits(), SCHEMAS,
                on_public_progress=callback).run(IDENTITY, [HumanMessage(content="task")], saver=saver)
            assert result.status == "complete" and result.model_steps == 3
            assert len(published) == 1
            note = published[0]
            assert note["text"] == "先核对公开来源。"
            assert note["job_id"] == IDENTITY.run_id
            assert note["note_id"] == f"{IDENTITY.run_id}:model:1"
            assert note["model_step"] == 1 and note["step_id"] == "model_1"
            assert note["phase"] == "working"
            assert "synthetic-private" not in str(note)
            assert "synthetic-final" not in str(note)
            if sink != "default":
                assert observed == ["public_note", "execute"]
    try:
        asyncio.run(scenario())
    finally:
        progress_bus.clear_request_queue()
        public_progress.reset_emitted()


@pytest.mark.parametrize("recover_output", [False, True])
@pytest.mark.parametrize("invalid", ["length", "invalid_arguments", "missing_id", "duplicate_id"])
def test_rejected_native_rounds_never_publish_working_notes(tmp_path, invalid, recover_output):
    events = []
    async def record_event(value):
        events.append(value)

    calls = [{"name": "fixture", "id": "one", "args": {}}]
    reason, bad_calls = "tool_calls", []
    if invalid == "length":
        reason = "length"
    elif invalid == "invalid_arguments":
        bad_calls = [{"name": "fixture", "id": "bad", "args": "{", "error": "invalid"}]
    elif invalid == "missing_id":
        calls[0]["id"] = ""
    else:
        calls.append(dict(calls[0]))

    async def model(*_):
        return AIMessage(content="这段不能公开。", tool_calls=calls, invalid_tool_calls=bad_calls,
                         response_metadata={"finish_reason": reason})

    def forbidden(*_, **__):
        pytest.fail("rejected round was published or dispatched")

    async def final(*_):
        return FinalCheck()

    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "rejected.sqlite")) as saver:
            result = await HarnessRuntime(model, forbidden, lambda *_: None, final,
                RunLimits(max_model_steps=1), SCHEMAS, recover_output=recover_output,
                on_event=record_event, on_public_progress=forbidden).run(
                    IDENTITY, [HumanMessage(content="task")], saver=saver)
            assert result.status == "incomplete"
            rejected = [event for event in events if event.kind == "model_output_rejected"]
            assert len(rejected) == 1
            expected = {"length": "model_length", "invalid_arguments": "invalid_tool_call",
                        "missing_id": "invalid_or_reused_tool_call_id",
                        "duplicate_id": "invalid_or_reused_tool_call_id"}[invalid]
            assert rejected[0].reason == expected
            assert "这段不能公开" not in repr(rejected[0])
            # Recovery stops at the model limit; direct rejection keeps its
            # original terminal reason. Event diagnostics never mutate either.
            recovering = recover_output and invalid in {"length", "invalid_arguments"}
            assert result.reason == ("model_step_limit" if recovering else expected)
    asyncio.run(scenario())


def test_public_progress_callback_failure_does_not_block_execution(tmp_path):
    steps = []
    async def model(*_):
        steps.append(1)
        message = action() if len(steps) == 1 else AIMessage(content="done")
        if len(steps) == 1:
            message.content = "我先核对来源。"
        return message
    async def execute(*_):
        return BatchObservation((ToolMessage(content="ok", name="fixture", tool_call_id="one"),))
    async def final(*_):
        return FinalCheck()
    def unavailable(*_, **__):
        raise RuntimeError("synthetic timeline outage")
    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "outage.sqlite")) as saver:
            result = await HarnessRuntime(model, execute, lambda *_: None, final, RunLimits(), SCHEMAS,
                on_public_progress=unavailable).run(IDENTITY, [HumanMessage(content="task")], saver=saver)
            assert result.status == "complete" and result.model_steps == 2
    asyncio.run(scenario())

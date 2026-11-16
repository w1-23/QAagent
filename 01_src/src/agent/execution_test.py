"""Scheduler contracts; all tools and operation receipts here are synthetic."""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent.capabilities import CapabilityCatalog, CapabilityDefinition
from src.agent.execution import CapabilityExecutor, ToolExecution
from src.agent.runtime import AgentRuntime
from src.agent.runtime_test import IDENTITY


def catalog(**tools):
    result = CapabilityCatalog()
    for name, options in tools.items():
        result.register(CapabilityDefinition(name, "Synthetic " + name,
            {"type": "object", "properties": {}}, lambda name=name: name, **options))
    return result


def response(*names):
    return AIMessage(content="", tool_calls=[{"id": f"call-{i}", "name": name, "args": {}}
        for i, name in enumerate(names)])


def receipt(call, **kwargs):
    return ToolExecution(ToolMessage(content="synthetic observation", tool_call_id=call["id"],
        name=call["name"]), **kwargs)


async def publish(call, result, identity):
    assert identity == IDENTITY
    return result.message


def test_reads_overlap_but_writes_are_ordered_and_results_stay_in_call_order():
    log, active = [], set()
    released = asyncio.Event()
    async def invoke(tool, call, identity):
        if tool == "write":
            assert not active
            log.append("write")
        else:
            active.add(call["id"])
            log.append("start:" + call["id"])
            if len(active) == 2:
                released.set()
            await released.wait()
            log.append("end:" + call["id"])
            active.remove(call["id"])
        return receipt(call)

    c = catalog(read={}, write={"mutates": True})
    result = asyncio.run(CapabilityExecutor(c, lambda _: {"read", "write"}, invoke, publish)(
        response("read", "read", "write", "read"), IDENTITY))
    assert log.index("write") > log.index("end:call-0")
    assert log.index("write") > log.index("end:call-1")
    assert log.index("write") < log.index("start:call-3")
    assert [m.tool_call_id for m in result.messages] == [f"call-{i}" for i in range(4)]


def test_authorization_rechecked_for_each_dispatch_not_cached_for_batch():
    permitted = {"read"}
    calls = []
    async def invoke(tool, call, identity):
        calls.append(call["id"])
        permitted.clear()
        return receipt(call)

    result = asyncio.run(CapabilityExecutor(catalog(read={}), lambda _: permitted, invoke, publish,
        max_parallel=1)(response("read", "read"), IDENTITY))
    assert calls == ["call-0"]
    assert result.messages[1].artifact["_tool_outcome"]["status"] == "not_run"
    assert "not_authorized_or_ready" in result.messages[1].content


def test_one_failed_plugin_does_not_prevent_core_tool_execution():
    c = catalog(read={})
    def missing():
        raise ImportError("synthetic secret-bearing provider error")
    c.register(CapabilityDefinition("plot", "Optional plugin", {"type": "object"}, missing,
                                    provider="figure", mutates=True))
    called = []
    async def invoke(tool, call, identity):
        called.append(tool)
        return receipt(call)
    result = asyncio.run(CapabilityExecutor(c, lambda _: {"plot", "read"}, invoke, publish)(
        response("plot", "read"), IDENTITY))
    assert called == ["read"]
    assert "load_failed:ImportError" in result.messages[0].content
    assert "secret-bearing" not in str(result)
    assert c.describe()[0]["state"] == "loaded"


def test_question_is_a_barrier_even_when_write_appears_first():
    executed = []
    async def invoke(tool, call, identity):
        executed.append(tool)
        return receipt(call, pending_question=True)
    c = catalog(write={"mutates": True}, ask={"execution_mode": "interaction"})
    result = asyncio.run(CapabilityExecutor(c, lambda _: {"write", "ask"}, invoke, publish)(
        response("write", "ask", "write", "ask"), IDENTITY))
    assert executed == ["ask"] and result.pending_question
    assert [m.artifact["_tool_outcome"]["reason_code"] for i, m in enumerate(result.messages)
            if i != 1] == ["interaction_barrier"] * 3


def test_unknown_write_stops_dispatching_remaining_batch_and_model():
    executed = []
    async def invoke(tool, call, identity):
        executed.append(tool)
        return receipt(call, operation_id="durable-op-1", uncertain=True)
    c = catalog(write={"mutates": True}, read={})
    executor = CapabilityExecutor(c, lambda _: {"write", "read"}, invoke, publish)
    async def model(messages, identity):
        assert len(messages) == 1
        return response("write", "read", "write")
    result = asyncio.run(AgentRuntime(model, executor, lambda *_: None).run(IDENTITY, [HumanMessage(content="task")]))
    assert executed == ["write"]
    assert result.unknown_operations == ("durable-op-1",)
    assert result.reason == "operation_outcome_unknown"
    assert len([m for m in result.messages if isinstance(m, ToolMessage)]) == 3


def test_persistence_precedes_following_write():
    log = []
    async def invoke(tool, call, identity):
        log.append("invoke:" + call["id"])
        return receipt(call)
    async def persist(call, result, identity):
        log.append("persist:" + call["id"])
        return result.message
    c = catalog(write={"mutates": True})
    asyncio.run(CapabilityExecutor(c, lambda _: {"write"}, invoke, persist)(
        response("write", "write"), IDENTITY))
    assert log == ["invoke:call-0", "persist:call-0", "invoke:call-1", "persist:call-1"]


def test_unexpected_read_failure_joins_cancelled_siblings_and_never_starts_write():
    settled = []
    both_started = asyncio.Event()
    async def invoke(tool, call, identity):
        assert tool == "read"
        if call["id"] == "call-0":
            await both_started.wait()
            raise OSError("synthetic execution store unavailable")
        try:
            both_started.set()
            await asyncio.Event().wait()
        finally:
            settled.append(call["id"])
    c = catalog(read={}, write={"mutates": True})
    with pytest.raises(ExceptionGroup):
        asyncio.run(CapabilityExecutor(c, lambda _: {"read", "write"}, invoke, publish)(
            response("read", "read", "write"), IDENTITY))
    assert settled == ["call-1"]


def test_publisher_cannot_change_call_identity():
    async def invoke(tool, call, identity):
        return receipt(call)
    async def broken(call, result, identity):
        return ToolMessage(content="wrong pairing", tool_call_id="another-call")
    c = catalog(write={"mutates": True})
    with pytest.raises(ValueError, match="pairing_mismatch"):
        asyncio.run(CapabilityExecutor(c, lambda _: {"write"}, invoke, broken)(response("write"), IDENTITY))


def test_no_generic_tool_can_suspend_the_loop_as_a_plugin_side_effect():
    async def invoke(tool, call, identity):
        return receipt(call, pending_question=True)
    c = catalog(write={"mutates": True})
    with pytest.raises(ValueError, match="non_interaction"):
        asyncio.run(CapabilityExecutor(c, lambda _: {"write"}, invoke, publish)(response("write"), IDENTITY))


def test_uncertain_result_requires_real_operation_identity():
    with pytest.raises(ValueError, match="durable_id"):
        receipt({"id": "call-1", "name": "write"}, uncertain=True)

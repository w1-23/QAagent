"""General loop -> existing metered SDK transport -> tool observations."""
import asyncio
import json

import httpx
from langchain_core.messages import HumanMessage, ToolMessage

from src.agent.capabilities import CapabilityCatalog, CapabilityDefinition
from src.agent.identity import ExecutionIdentity
from src.agent.execution import CapabilityExecutor, ToolExecution
from src.agent.model import ModelDriver
from src.agent.runtime import AgentRuntime, BatchObservation
from src.core.provider_protocol_test import pool, client, stream_response, PRIVATE  # noqa: F401


def test_general_driver_sends_discovered_schema_and_preserves_tool_protocol(pool, monkeypatch):
    from src.core import tracing
    monkeypatch.setattr(tracing, "get_job_id", lambda: "")
    identity = ExecutionIdentity(7, "synthetic-session", "turn-one", "run-one")
    sent, visible = [], []
    catalog = CapabilityCatalog()
    # The SDK can see the schema without loading the optional implementation.
    catalog.register(CapabilityDefinition("lookup", "Synthetic lookup",
        {"type": "object", "properties": {"key": {"type": "string"}}},
        lambda: (_ for _ in ()).throw(AssertionError("model transport loaded tool"))))

    def receive(request):
        body = json.loads(request.content)
        sent.append(body)
        n = len(sent) - 1
        assert [m["reasoning_content"] for m in body["messages"]
                if m["role"] == "assistant"] == [PRIVATE + str(i) for i in range(n)]
        assert [t["function"]["name"] for t in body["tools"]] == ["lookup"]
        return stream_response(n)

    async def execute(response, scope):
        assert scope == identity
        return BatchObservation(tuple(ToolMessage(
            content="Synthetic observed result", name=c["name"], tool_call_id=c["id"])
            for c in response.tool_calls))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            driver = ModelDriver(client(pool._ObservedDeepSeekChatOpenAI, http), catalog,
                                 lambda scope: {"lookup"}, visible.append)
            return await AgentRuntime(driver, execute, lambda *_: None).run(identity, [
                HumanMessage(content="Synthetic task",
                             additional_kwargs=identity.message_metadata("user"))])
    result = asyncio.run(scenario())
    assert result.status == "complete" and result.answer.content == "done"
    assert result.model_steps == len(sent) == 3
    assert "".join(visible) == "done"
    assert catalog.describe()[0]["state"] == "discoverable"


def test_sdk_loop_dispatches_lazy_capabilities_and_reads_published_observations(pool, monkeypatch):
    from src.core import tracing
    monkeypatch.setattr(tracing, "get_job_id", lambda: "")
    identity = ExecutionIdentity(7, "synthetic-session", "turn-one", "run-one")
    catalog = CapabilityCatalog()
    loaded, published, requests = [], [], []
    def load():
        loaded.append("lookup")
        return {"a": "observed-a", "b": "observed-b", "c": "observed-c"}
    catalog.register(CapabilityDefinition("lookup", "Synthetic lookup",
        {"type": "object", "properties": {"key": {"type": "string"}}}, load))
    def receive(request):
        body = json.loads(request.content)
        n = len(requests)
        requests.append(body)
        if n == 0:
            assert loaded == []
        else:
            expected = ["one", "two"] if n == 1 else ["one", "two", "three"]
            assert published == expected
            assert [m["tool_call_id"] for m in body["messages"] if m["role"] == "tool"] == expected
        assert [m["reasoning_content"] for m in body["messages"]
                if m["role"] == "assistant"] == [PRIVATE + str(i) for i in range(n)]
        return stream_response(n)
    async def invoke(tool, call, scope):
        assert scope == identity
        return ToolExecution(ToolMessage(content=tool[call["args"]["key"]],
            name=call["name"], tool_call_id=call["id"]), operation_id="synthetic-" + call["id"])
    async def publish(call, result, scope):
        published.append(call["id"])
        return result.message
    async def scenario():
        allowed = lambda _: {"lookup"}
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            driver = ModelDriver(client(pool._ObservedDeepSeekChatOpenAI, http), catalog, allowed)
            executor = CapabilityExecutor(catalog, allowed, invoke, publish)
            return await AgentRuntime(driver, executor, lambda *_: None).run(identity, [
                HumanMessage(content="Synthetic task", additional_kwargs=identity.message_metadata("user"))])
    result = asyncio.run(scenario())
    assert result.status == "complete" and result.answer.content == "done"
    assert loaded == ["lookup"] and result.model_steps == 3
    assert [m.content for m in result.messages if isinstance(m, ToolMessage)] == [
        "observed-a", "observed-b", "observed-c"]


def test_sdk_interaction_schema_is_strict_without_loading_tool(pool, monkeypatch):
    from src.core import tracing
    from src.agent.preset_catalog import build_preset_catalog
    monkeypatch.setattr(tracing, "get_job_id", lambda: "")
    catalog = build_preset_catalog()
    requests = []
    def receive(request):
        body = json.loads(request.content)
        requests.append(body)
        schema, = body["tools"]
        assert schema["function"]["name"] == "request_user_input"
        assert schema["function"]["strict"] is True
        params = schema["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert set(params["required"]) == set(params["properties"])
        return stream_response(2)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            driver = ModelDriver(client(pool._ObservedDeepSeekChatOpenAI, http), catalog,
                                 lambda _: {"request_user_input"})
            identity = ExecutionIdentity(7, "synthetic-session", "turn-one", "run-one")
            return await driver((HumanMessage(content="synthetic question"),), identity)
    assert asyncio.run(scenario()).content == "done" and len(requests) == 1
    assert next(row for row in catalog.describe() if row["name"] == "request_user_input")["state"] == "discoverable"

"""P2 public-hook proof, not a production Skill loading implementation.

Trusted immutable fixture resources stand in for skill_registry.verified_sources.
Receipts are reconstructed from paired observations, never model assertions.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

import pytest
pytest.importorskip("deepagents")
from deepagents.backends import StateBackend
from deepagents.backends.utils import create_file_data
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.skills import SkillsMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from src.agent.harness_compatibility_test import _SkillModel

METHOD = """---
name: fixture
description: Retrieve the fixture value using the required method and resource.
metadata:
  include_tools: read_fixture
---
Read this whole method and /skills/fixture/rules.md before using read_fixture.
Return the value for fixture-1 without inventing results.
"""
RULES = "Use fixture-1 only. Preserve the exact returned value.\n"
RESOURCES = {"/skills/fixture/SKILL.md": METHOD, "/skills/fixture/rules.md": RULES}


@dataclass(frozen=True)
class ProbeIdentity:
    owner: str
    turn: str


class CompleteMethodGate(AgentMiddleware):
    """Proof that public hooks can narrow disclosure AND reject direct dispatch.

    Parsing the pinned release's read header is intentionally limited to this
    compatibility probe. Production should use structured authoritative read
    receipts from the existing registry, not a display-text parser.
    """
    def __init__(self, resources, *, authorized=True, identity=ProbeIdentity("fixture-owner", "turn-1")):
        self.resources = dict(resources)
        self.authorized = authorized
        self.identity = identity

    def receipt(self, messages):
        calls = {}
        covered = {path: set() for path in self.resources}
        for message in messages:
            if isinstance(message, AIMessage):
                calls.update({call["id"]: call for call in message.tool_calls})
            if not isinstance(message, ToolMessage) or message.status == "error":
                continue
            call = calls.get(message.tool_call_id, {})
            args = call.get("args", {})
            path = args.get("file_path")
            if call.get("name") != "read_file" or path not in self.resources:
                continue
            offset = args.get("offset", 0)
            if type(offset) is not int or offset < 0 or not isinstance(message.content, str):
                continue
            lines = message.content.splitlines()
            header = next((i for i, line in enumerate(lines) if line.startswith("@@ ") and line.endswith(" @@")), None)
            if header is None:
                continue
            body = lines[header+1:]
            expected = self.resources[path].splitlines()
            if body and body == expected[offset:offset+len(body)]:
                covered[path].update(range(offset, offset+len(body)))
        loaded = all(covered[p] == set(range(len(text.splitlines()))) for p, text in self.resources.items())
        return {"loaded": loaded, "resources": {
            p: {"sha256": hashlib.sha256(text.encode()).hexdigest(), "lines": sorted(covered[p])}
            for p, text in self.resources.items()}}

    def permits(self, state, runtime):
        return (self.authorized and runtime.context == self.identity
                and self.receipt(state.get("messages", []))["loaded"])

    async def awrap_model_call(self, request, handler):
        if self.receipt(request.state.get("messages", []))["loaded"] and not self.receipt(request.messages)["loaded"]:
            raise RuntimeError("required_method_missing_from_active_projection")
        if not self.permits(request.state, request.runtime):
            request = request.override(tools=[t for t in request.tools if getattr(t, "name", None) != "read_fixture"])
        return await handler(request)

    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] == "read_fixture" and not self.permits(request.state, request.runtime):
            return ToolMessage(content="not_run:method_or_authorization_incomplete", name="read_fixture",
                               tool_call_id=request.tool_call["id"], status="error")
        return await handler(request)


def make_graph(model, gate, calls, saver=None, interrupt_after=None, projection=None):
    @tool
    def read_fixture(asset_id: str) -> str:
        """Read an authorized fixture after all required methods are loaded."""
        assert asset_id == "fixture-1"
        calls.append(asset_id)
        return "value=17"
    backend = StateBackend()
    return create_agent(model, middleware=[
        SkillsMiddleware(backend=backend, sources=["/skills/"], tools=[read_fixture]),
        *([projection] if projection else []),
        gate,
        FilesystemMiddleware(backend=backend, tools=["read_file", "ls"],
            tool_token_limit_before_evict=None, human_message_token_limit_before_evict=None),
    ], checkpointer=saver, interrupt_after=interrupt_after, context_schema=ProbeIdentity)


@pytest.mark.parametrize("read_limit", [1, 1000])
def test_partial_method_or_missing_required_resource_blocks_disclosure_and_execution(read_limit):
    model, calls = _SkillModel(read_limit=read_limit), []
    gate = CompleteMethodGate(RESOURCES)
    graph = make_graph(model, gate, calls)
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="Read fixture-1")],
        "files": {p: create_file_data(t) for p, t in RESOURCES.items()}}, context=gate.identity))
    assert not calls
    assert all("read_fixture" not in names for names in model.bound_names)
    assert all("read_file" in names for names in model.bound_names)
    assert result["messages"][-1].content == "Observed: not_run:method_or_authorization_incomplete"
    assert not gate.receipt(result["messages"])["loaded"]


class _ReadAllModel(_SkillModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult
        reads = [m for m in messages if isinstance(m, ToolMessage) and m.name == "read_file"]
        if len(reads) == 1:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[{
                "type": "tool_call", "id": "rules-read", "name": "read_file",
                "args": {"file_path": "/skills/fixture/rules.md", "offset": 0, "limit": 1000}}]))])
        return super()._generate(messages, **kwargs)


@pytest.mark.parametrize("change", ["none", "version", "permission", "owner", "projection"])
def test_required_resource_gate_rechecks_after_checkpoint_reconstruction(tmp_path, change):
    async def scenario():
        config = {"configurable": {"thread_id": "server-selected-owner/session/turn"}}
        db = str(tmp_path / "gate.sqlite")
        calls = []
        gate = CompleteMethodGate(RESOURCES)
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            graph = make_graph(_ReadAllModel(read_limit=1000), gate, calls, saver, ["tools"])
            state = await graph.ainvoke({"messages": [HumanMessage(content="Read fixture-1")],
                "files": {p: create_file_data(t) for p, t in RESOURCES.items()}}, config, context=gate.identity)
            state = await graph.ainvoke(None, config, context=gate.identity)
            assert gate.receipt(state["messages"])["loaded"]
        resources = dict(RESOURCES)
        if change == "version":
            resources["/skills/fixture/rules.md"] += "New required constraint.\n"
        gate = CompleteMethodGate(resources, authorized=change != "permission")
        context = ProbeIdentity("other-owner", "turn-1") if change == "owner" else gate.identity
        model = _ReadAllModel(read_limit=1000)
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            class DropMethodView(AgentMiddleware):
                async def awrap_model_call(self, request, handler):
                    return await handler(request.override(messages=[HumanMessage(content="summary says loaded")]))
            graph = make_graph(model, gate, calls, saver, ["tools"],
                               DropMethodView() if change == "projection" else None)
            if change == "projection":
                with pytest.raises(RuntimeError, match="required_method_missing_from_active_projection"):
                    await graph.ainvoke(None, config, context=context)
                assert not calls and not model.bound_names
                return
            state = await graph.ainvoke(None, config, context=context)
            assert calls == (["fixture-1"] if change == "none" else [])
            assert ("read_fixture" in model.bound_names[0]) == (change == "none")
            assert sum(isinstance(m, HumanMessage) for m in state["messages"]) == 1
    asyncio.run(scenario())

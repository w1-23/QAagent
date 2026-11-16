"""Public Harness interface contracts; synthetic model, no account/config reads.

Run in the optional CitrusSchalor Harness environment. These tests do not certify
real provider compatibility or the production operation/checkpoint adapters.
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import Field

pytest.importorskip("langchain")
pytest.importorskip("langgraph.checkpoint.sqlite.aio")

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class _FixtureModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "synthetic-harness-contract"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        results = [m for m in messages if isinstance(m, ToolMessage)]
        if results:
            assert results[-1].tool_call_id == "fixture-call"
            message = AIMessage(content="Observed: " + results[-1].content)
        else:
            message = AIMessage(content="", tool_calls=[{
                "type": "tool_call", "id": "fixture-call", "name": "read_fixture",
                "args": {"asset_id": "fixture-1"},
            }])
        return ChatResult(generations=[ChatGeneration(message=message)])


def _fixture_tools(calls):
    @tool
    def read_fixture(asset_id: str) -> str:
        """Read a named synthetic in-memory fixture."""
        assert asset_id == "fixture-1"
        calls.append(asset_id)
        return "value=17"

    return [read_fixture]


def test_public_tool_guard_can_prevent_dispatch():
    calls, guarded = [], []

    class Guard(AgentMiddleware):
        async def awrap_tool_call(self, request, handler):
            guarded.append(request.tool_call["id"])
            return ToolMessage(content="not_run:permission_denied",
                               name=request.tool_call["name"],
                               tool_call_id=request.tool_call["id"], status="error")

    async def scenario():
        graph = create_agent(_FixtureModel(), tools=_fixture_tools(calls), middleware=[Guard()])
        result = await graph.ainvoke({"messages": [HumanMessage(content="Read fixture-1")]})
        assert result["messages"][-1].content == "Observed: not_run:permission_denied"
        assert guarded == ["fixture-call"]
        assert not calls

    asyncio.run(scenario())


def test_new_graph_resumes_sqlite_checkpoint_without_repeating_tool(tmp_path):
    calls, guarded = [], []

    class Guard(AgentMiddleware):
        async def awrap_tool_call(self, request, handler):
            guarded.append(request.tool_call["id"])
            return await handler(request)

    async def scenario():
        config = {"configurable": {"thread_id": "fixture-owner/session/turn"}}
        database = str(tmp_path / "checkpoints.sqlite")
        async with AsyncSqliteSaver.from_conn_string(database) as saver:
            graph = create_agent(_FixtureModel(), tools=_fixture_tools(calls),
                                 middleware=[Guard()], checkpointer=saver,
                                 interrupt_after=["tools"])
            result = await graph.ainvoke({"messages": [HumanMessage(content="Read fixture-1")]}, config)
            assert isinstance(result["messages"][-1], ToolMessage)
            assert (await graph.aget_state(config)).next
        async with AsyncSqliteSaver.from_conn_string(database) as saver:
            graph = create_agent(_FixtureModel(), tools=_fixture_tools(calls),
                                 middleware=[Guard()], checkpointer=saver,
                                 interrupt_after=["tools"])
            result = await graph.ainvoke(None, config)
            assert result["messages"][-1].content == "Observed: value=17"
            assert calls == ["fixture-1"]
            assert guarded == ["fixture-call"]
            assert sum(isinstance(m, HumanMessage) for m in result["messages"]) == 1
            assert not (await graph.aget_state(config)).next
            # A different server-selected scope must not inherit this state.
            other = await graph.aget_state({"configurable": {"thread_id": "other-owner/session/turn"}})
            assert not other.values

    asyncio.run(scenario())


class _SkillModel(BaseChatModel):
    read_limit: int
    bound_names: list[list[str]] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "synthetic-skill-contract"

    def bind_tools(self, tools, **kwargs):
        self.bound_names.append([t.name for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        observations = [m for m in messages if isinstance(m, ToolMessage)]
        if not observations:
            message = AIMessage(content="", tool_calls=[{
                "type": "tool_call", "id": "method-read", "name": "read_file",
                "args": {"file_path": "/skills/fixture/SKILL.md", "offset": 0, "limit": self.read_limit},
            }])
        elif observations[-1].name == "read_file":
            message = AIMessage(content="", tool_calls=[{
                "type": "tool_call", "id": "fixture-call", "name": "read_fixture",
                "args": {"asset_id": "fixture-1"},
            }])
        else:
            message = AIMessage(content="Observed: " + observations[-1].content)
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.mark.parametrize("read_limit,authorized", [(1, True), (1000, True), (1000, False)])
def test_upstream_skill_disclosure_is_not_a_complete_loading_receipt(read_limit, authorized):
    """Pin the upstream gap: a one-line read also discloses specialist tools.

    This is dependency behavior, not permission to mark partial reads loaded in
    production. The product adapter must enforce required resources separately.
    """
    pytest.importorskip("deepagents")
    from deepagents.backends import StateBackend
    from deepagents.backends.utils import create_file_data
    from deepagents.middleware.filesystem import FilesystemMiddleware
    from deepagents.middleware.skills import SkillsMiddleware

    calls = []
    model = _SkillModel(read_limit=read_limit)
    backend = StateBackend()
    method = """---
name: fixture
description: Read a synthetic fixture using its method.
metadata:
  include_tools: read_fixture
---
Read fixture-1 once with read_fixture; report its value.
REQUIRED_METHOD_END
"""
    class PermissionGuard(AgentMiddleware):
        async def awrap_tool_call(self, request, handler):
            if request.tool_call["name"] == "read_fixture" and not authorized:
                return ToolMessage(content="not_run:permission_denied", name="read_fixture",
                                   tool_call_id=request.tool_call["id"], status="error")
            return await handler(request)

    graph = create_agent(model, middleware=[
        PermissionGuard(),
        FilesystemMiddleware(backend=backend, tools=["read_file"],
                             tool_token_limit_before_evict=None,
                             human_message_token_limit_before_evict=None),
        SkillsMiddleware(backend=backend, sources=["/skills/"], tools=_fixture_tools(calls)),
    ])

    async def scenario():
        result = await graph.ainvoke({
            "messages": [HumanMessage(content="Read fixture-1")],
            "files": {"/skills/fixture/SKILL.md": create_file_data(method)},
        })
        assert "read_fixture" not in model.bound_names[0]
        assert "read_fixture" in model.bound_names[1]
        assert calls == (["fixture-1"] if authorized else [])
        read = next(m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "read_file")
        assert ("REQUIRED_METHOD_END" in read.content) == (read_limit == 1000)
        expected = "value=17" if authorized else "not_run:permission_denied"
        assert result["messages"][-1].content == "Observed: " + expected

    asyncio.run(scenario())

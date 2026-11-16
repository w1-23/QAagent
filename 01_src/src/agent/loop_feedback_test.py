"""Failure observations return to one durable loop without dispatching bad args."""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import Field

from src.agent.harness import HarnessRuntime
from src.agent.harness_test import IDENTITY, SCHEMAS
from src.agent.runtime import BatchObservation, FinalCheck, RunLimits
from src.tools.argument_check import check_tool_arguments


@pytest.mark.parametrize("invalid_args,finish_reason", [(False, "length"), (True, "length"), (True, "tool_calls")])
def test_truncated_proposal_is_saved_then_regenerated_in_same_graph(tmp_path, invalid_args, finish_reason):
    sent, dispatched = [], []
    rejected = AIMessage(content="unfinished", response_metadata={"finish_reason": finish_reason},
        **({"invalid_tool_calls": [{"name": "fixture", "args": '{"broken":', "id": "bad", "error": "incomplete"}]}
           if invalid_args else {"tool_calls": [{"name": "fixture", "id": "bad", "args": {}}]}))
    async def model(messages, identity):
        assert identity == IDENTITY
        sent.append(messages)
        if len(sent) == 1:
            return rejected
        assert sum(isinstance(m, HumanMessage) for m in messages) == 1
        assert not any(m.invalid_tool_calls for m in messages if isinstance(m, AIMessage))
        if not invalid_args:
            assert any(isinstance(m, ToolMessage) and m.tool_call_id == "bad"
                       and m.artifact["_tool_outcome"]["status"] == "not_run" for m in messages)
        if len(sent) == 2:
            return AIMessage(content="", tool_calls=[{"name": "fixture", "id": "corrected", "args": {}}])
        return AIMessage(content="complete")
    async def execute(response, identity):
        dispatched.extend(c["id"] for c in response.tool_calls)
        return BatchObservation((ToolMessage(content="actual", tool_call_id="corrected", name="fixture"),))
    async def final(*args):
        return FinalCheck()
    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "state.sqlite")) as saver:
            runtime = HarnessRuntime(model, execute, lambda *_: None, final, RunLimits(), SCHEMAS, recover_output=True)
            result = await runtime.run(IDENTITY, [HumanMessage(content="actual request")], saver=saver)
            assert result.status == "complete" and result.model_steps == 3
            saved = await saver.aget({"configurable": {"thread_id": IDENTITY.run_id}})
            assert saved["channel_values"]["rejected_responses"][0].content == "unfinished"
            assert await runtime.run(IDENTITY, [], saver=saver, resume=True) == result
    asyncio.run(scenario())
    assert dispatched == ["corrected"]


def test_argument_feedback_validates_types_and_bounds_without_execution():
    invoked = []
    @tool(response_format="content_and_artifact")
    def sample(width: float = Field(ge=0.1, le=4), mode: str = "safe"):
        """Validate actual inputs before a raw function can run."""
        invoked.append(width)
        return "done", {}
    for args in ({"width": 0.05}, {"width": "0.5"}, {"linewidth": 0.5}, {}):
        feedback = check_tool_arguments(sample, sample.name, args)
        assert "未执行" in feedback
    assert check_tool_arguments(sample, sample.name, {"width": 0.5}) == ""
    assert not invoked

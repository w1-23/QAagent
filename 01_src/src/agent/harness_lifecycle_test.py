"""P2 cancellation, durable questions and actual child-process interruption.

All tools operate on synthetic fixtures inside pytest temporary directories.
"""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
pytest.importorskip("langchain")
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command, interrupt
from src.agent.harness_compatibility_test import _FixtureModel


@pytest.mark.parametrize("boundary", ["model", "tool"])
def test_cancel_propagates_through_awaited_public_hooks(boundary):
    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()
        calls = []
        async def wait():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        class Gate(AgentMiddleware):
            async def awrap_model_call(self, request, handler):
                if boundary == "model":
                    await wait()
                return await handler(request)
        @tool
        async def read_fixture(asset_id: str) -> str:
            """Await a synthetic fixture operation."""
            calls.append(asset_id)
            await wait()
            return "unreachable"
        graph = create_agent(_FixtureModel(), tools=[read_fixture], middleware=[Gate()])
        task = asyncio.create_task(graph.ainvoke({"messages": [HumanMessage(content="Read fixture-1")]}))
        try:
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert cancelled.is_set()
            assert calls == ([] if boundary == "model" else ["fixture-1"])
        finally:
            task.cancel()
    asyncio.run(scenario())


def test_question_resume_uses_saved_call_without_new_human_turn(tmp_path):
    calls = []
    @tool
    def read_fixture(asset_id: str) -> str:
        """Ask the fixture owner for its measurement before returning it."""
        answer = interrupt({"kind": "fixture_question", "record": asset_id, "question": "What is the value?"})
        if not isinstance(answer, dict) or answer.get("value") != 17:
            raise ValueError("invalid_fixture_answer")
        calls.append(asset_id)
        return "value=17"
    async def scenario():
        config = {"configurable": {"thread_id": "owner/session/turn"}}
        db = str(tmp_path / "question.sqlite")
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            graph = create_agent(_FixtureModel(), tools=[read_fixture], checkpointer=saver)
            state = await graph.ainvoke({"messages": [HumanMessage(content="Read fixture-1")]}, config)
            assert state["__interrupt__"][0].value["kind"] == "fixture_question"
            assert not calls
        async with AsyncSqliteSaver.from_conn_string(db) as saver:
            graph = create_agent(_FixtureModel(), tools=[read_fixture], checkpointer=saver)
            state = await graph.ainvoke(Command(resume={"value": 17}), config)
            assert state["messages"][-1].content == "Observed: value=17"
            assert calls == ["fixture-1"]
            assert sum(isinstance(m, HumanMessage) for m in state["messages"]) == 1
            assert [m.tool_call_id for m in state["messages"] if isinstance(m, ToolMessage)] == ["fixture-call"]
    asyncio.run(scenario())


_CHILD = r'''
import asyncio,sys,threading
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from src.agent.harness_compatibility_test import _FixtureModel
root,phase=Path(sys.argv[2]),sys.argv[3]
@tool
async def read_fixture(asset_id: str) -> str:
    """Write a single local synthetic effect marker."""
    with (root/'effect.txt').open('a') as stream:
        stream.write(asset_id+'\n')
    if phase=='dispatched':
        print('DISPATCHED',flush=True)
        await asyncio.Event().wait()
    return 'value=17'
async def main():
    async with AsyncSqliteSaver.from_conn_string(str(root/'checkpoint.sqlite')) as saver:
        graph=create_agent(_FixtureModel(),tools=[read_fixture],checkpointer=saver,interrupt_after=['tools'])
        await graph.ainvoke({'messages':[HumanMessage(content='Read fixture-1')]},{'configurable':{'thread_id':'owner/session/turn'}})
    print('CHECKPOINTED',flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
'''


@pytest.mark.parametrize("phase", ["checkpointed", "dispatched"])
def test_process_kill_completed_step_or_unknown_effect(tmp_path, phase):
    script = tmp_path / "fixed_child_fixture.py"
    script.write_text(_CHILD, encoding="utf-8")
    source = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ, LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    child = subprocess.Popen([sys.executable, "-I", str(script), source, str(tmp_path), phase],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(child.stdout.readline)
            try:
                line = future.result(timeout=15).strip()
                assert line == ("CHECKPOINTED" if phase == "checkpointed" else "DISPATCHED")
            finally:
                child.kill()
                child.wait(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
    assert child.returncode != 0
    calls = []
    @tool
    def read_fixture(asset_id: str) -> str:
        """Record any unexpected repeat of the fixture operation."""
        calls.append(asset_id)
        return "value=17"
    class ReconciliationGuard(AgentMiddleware):
        async def awrap_tool_call(self, request, handler):
            # The marker is a synthetic unknown-operation receipt, not a new
            # production operation ledger. HostTools must supply the real one.
            if phase == "dispatched" and (tmp_path / "effect.txt").exists():
                raise RuntimeError("operation_unknown_reconciliation_required")
            return await handler(request)
    async def scenario():
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoint.sqlite")) as saver:
            graph = create_agent(_FixtureModel(), tools=[read_fixture], middleware=[ReconciliationGuard()], checkpointer=saver)
            config = {"configurable": {"thread_id": "owner/session/turn"}}
            if phase == "dispatched":
                with pytest.raises(RuntimeError, match="operation_unknown_reconciliation_required"):
                    await graph.ainvoke(None, config)
            else:
                state = await graph.ainvoke(None, config)
                assert state["messages"][-1].content == "Observed: value=17"
                assert sum(isinstance(m, HumanMessage) for m in state["messages"]) == 1
    asyncio.run(scenario())
    assert not calls
    assert (tmp_path / "effect.txt").read_text().splitlines() == ["fixture-1"]


def test_sync_checkpoint_failure_stops_before_next_model(tmp_path):
    import aiosqlite
    calls, model_calls = [], []
    class CountModel(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            model_calls.append(1)
            return await handler(request)
    class FailedStore(AsyncSqliteSaver):
        async def aput(self, config, checkpoint, metadata, new_versions):
            messages = checkpoint.get("channel_values", {}).get("messages", [])
            if messages and isinstance(messages[-1], ToolMessage):
                raise OSError("synthetic_checkpoint_storage_failure")
            return await super().aput(config, checkpoint, metadata, new_versions)
    @tool
    def read_fixture(asset_id: str) -> str:
        """Read synthetic data with simulated subsequent storage failure."""
        calls.append(asset_id)
        return "value=17"
    async def scenario():
        async with aiosqlite.connect(str(tmp_path / "failed.sqlite")) as connection:
            graph = create_agent(_FixtureModel(), tools=[read_fixture], middleware=[CountModel()],
                                 checkpointer=FailedStore(connection))
            with pytest.raises(OSError, match="synthetic_checkpoint_storage_failure"):
                await graph.ainvoke({"messages": [HumanMessage(content="Read fixture-1")]},
                    {"configurable": {"thread_id": "owner/session/turn"}}, durability="sync")
    asyncio.run(scenario())
    assert calls == ["fixture-1"]
    assert model_calls == [1]


def test_question_batch_barrier_blocks_sibling_side_effect(tmp_path):
    from langchain_core.outputs import ChatGeneration, ChatResult
    effects = []
    class BatchModel(_FixtureModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            observations = [m for m in messages if isinstance(m, ToolMessage)]
            message = AIMessage(content="question-complete") if observations else AIMessage(content="", tool_calls=[
                {"type": "tool_call", "id": "question", "name": "ask_fixture", "args": {}},
                {"type": "tool_call", "id": "write", "name": "write_fixture", "args": {}},
            ])
            return ChatResult(generations=[ChatGeneration(message=message)])
    @tool
    def ask_fixture() -> str:
        """Request a synthetic fixture answer."""
        answer = interrupt({"kind": "fixture_question"})
        return str(answer)
    @tool
    def write_fixture() -> str:
        """Record a synthetic effect which the batch barrier must prevent."""
        effects.append("must-not-execute")
        return "written"
    class QuestionBarrier(AgentMiddleware):
        async def awrap_tool_call(self, request, handler):
            proposal = next(m for m in reversed(request.state["messages"]) if isinstance(m, AIMessage))
            if request.tool_call["name"] != "ask_fixture" and any(c["name"] == "ask_fixture" for c in proposal.tool_calls):
                return ToolMessage(content="not_run:interaction_barrier", tool_call_id=request.tool_call["id"],
                                   name=request.tool_call["name"], status="error")
            return await handler(request)
    async def scenario():
        config = {"configurable": {"thread_id": "owner/session/turn"}}
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "batch.sqlite")) as saver:
            graph = create_agent(BatchModel(), tools=[ask_fixture, write_fixture], middleware=[QuestionBarrier()], checkpointer=saver)
            paused = await graph.ainvoke({"messages": [HumanMessage(content="fixture task")]}, config)
            assert paused["__interrupt__"] and not effects
            state = await graph.ainvoke(Command(resume="synthetic answer"), config)
            assert not effects
            results = {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}
            assert results["write"].content == "not_run:interaction_barrier"
            assert results["question"].content == "synthetic answer"
    asyncio.run(scenario())

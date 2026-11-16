"""P2 public model hooks use the existing durable attempt ledger.

No production configuration is loaded. HTTP failures are synthetic MockTransport.
"""
import asyncio
import sys
import time
from types import ModuleType

import httpx
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from openai import RateLimitError


class LedgerHook(AgentMiddleware):
    """Compatibility proof of reserve/finalize around one handler call.

    Production must additionally integrate run_model_attempt's context snapshots,
    exact bound request estimate and tracing scope; this fixed estimate is fixture-only.
    """
    def __init__(self, events, job_id):
        self.events, self.job_id = events, job_id

    async def awrap_model_call(self, request, handler):
        attempt = self.events.reserve_attempt(self.job_id, "harness_probe", 100)
        usage, error = {}, ""
        try:
            response = await handler(request)
            usage = response.result[-1].usage_metadata or {}
            return response
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            self.events.finish_attempt(attempt, usage, error=error)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    config = ModuleType("src.config")
    config.PROJECT_ROOT = tmp_path
    monkeypatch.setitem(sys.modules, "src.config", config)
    from src.core import jobs, task_events
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "ledger.sqlite")
    job = jobs.claim_job("fixture-session", "fixture-turn", owner_user_id=1)
    task_events.init_budget(job.job_id, time.time()+60, max_calls=2, max_tokens=1000)
    return task_events, job.job_id


def test_429_and_retry_after_reconstruction_share_original_budget(tmp_path, ledger):
    events, job_id = ledger
    requests = []
    def transport(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(429, json={"error": {"message": "synthetic rate limit", "type": "rate_limit", "code": "rate_limit"}})
        return httpx.Response(200, json={"id": "fixture-response", "object": "chat.completion", "created": 1,
            "model": "fixture-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": "fixture-complete"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            model = ChatOpenAI(model="fixture-model", api_key="synthetic-not-a-key", max_retries=0,
                               base_url="https://fixture.invalid/v1", http_async_client=http, use_responses_api=False)
            config = {"configurable": {"thread_id": "fixture-owner/session/turn"}}
            db = str(tmp_path / "checkpoints.sqlite")
            async with AsyncSqliteSaver.from_conn_string(db) as saver:
                graph = create_agent(model, middleware=[LedgerHook(events, job_id)], checkpointer=saver)
                with pytest.raises(RateLimitError):
                    await graph.ainvoke({"messages": [HumanMessage(content="Report completion")]}, config)
            assert len(requests) == 1  # No hidden SDK retry.
            budget = events.projection(job_id)["budget"]
            assert budget["calls"] == 1 and budget["unknown_reserved_tokens"] == 100
            # Re-initialization cannot silently refresh a persisted task budget.
            events.init_budget(job_id, time.time()+999, max_calls=99, max_tokens=9999)
            async with AsyncSqliteSaver.from_conn_string(db) as saver:
                graph = create_agent(model, middleware=[LedgerHook(events, job_id)], checkpointer=saver)
                state = await graph.ainvoke(None, config)
                assert state["messages"][-1].content == "fixture-complete"
                assert sum(isinstance(m, HumanMessage) for m in state["messages"]) == 1
                with pytest.raises(events.TaskConflict, match="model_budget_exhausted:calls"):
                    await graph.ainvoke({"messages": [HumanMessage(content="This must not reach transport")]}, config)
            assert len(requests) == 2
            budget = events.projection(job_id)["budget"]
            assert budget["calls"] == 2 and budget["max_calls"] == 2
            assert budget["tokens"] == 110
            assert budget["reported_total_tokens"] == 10
            assert budget["unknown_reserved_tokens"] == 100
    asyncio.run(scenario())


def test_cancelled_model_attempt_keeps_unknown_usage_reservation(ledger):
    events, job_id = ledger
    async def scenario():
        started = asyncio.Event()
        async def handler(request):
            started.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(LedgerHook(events, job_id).awrap_model_call(None, handler))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    budget = events.projection(job_id)["budget"]
    assert budget["calls"] == 1 and budget["unknown_reserved_tokens"] == 100
    assert budget["in_flight_attempts"] == 0

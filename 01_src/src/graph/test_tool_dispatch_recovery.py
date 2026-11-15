"""Real dispatch/receipt reduction with synthetic tools and no persistent stores."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from src.core.task_result import build_task_result
from src.graph import agent_operations as operations


@pytest.fixture
def dispatch(monkeypatch):
    from src.core import progress_bus, tool_results, turn_source_ledger
    from src.tools import registry

    # Exercise the production publisher and reducer without touching account data.
    monkeypatch.setattr(tool_results, "record", lambda **kwargs: False)
    monkeypatch.setattr(turn_source_ledger, "record_tool_sources", lambda *a, **k: None)
    monkeypatch.setattr(progress_bus, "emit_encoded", lambda *a, **k: None)
    monkeypatch.setattr(operations, "current_execution_ledger", lambda: None)
    starts, executed, records, seen = [], [], [], {}
    responses = []
    monkeypatch.setattr(operations, "emit_tool_call_start",
                        lambda name, args, call_id, **kwargs: starts.append(call_id))
    monkeypatch.setattr(operations, "emit_tool_executing", lambda *a, **k: None)

    async def run_tool(_tool, call):
        executed.append(call["id"])
        content, artifact = (responses.pop(0) if responses else
                             ("Synthetic result", {"_tool_outcome": {"status": "complete"}}))
        return ToolMessage(content=content, artifact=artifact,
                           name=call["name"], tool_call_id=call["id"])

    monkeypatch.setattr(registry, "_run_single_tool", run_tool)

    async def run(calls):
        return await operations._execute_calls(
            SimpleNamespace(tool_calls=calls),
            tools_by_name={call["name"]: object() for call in calls},
            deadline=float("inf"), seen_call_signatures=seen, tool_status_sink=records)

    return SimpleNamespace(run=run, starts=starts, executed=executed,
                           records=records, seen=seen, responses=responses)


def call(call_id, target="synthetic-file", name="read_account_file"):
    return {"id": call_id, "name": name, "args": {"file_id": target}}


def test_deferred_batch_call_is_satisfied_by_later_execution(dispatch):
    async def scenario():
        await dispatch.run([call(str(i), str(i)) for i in range(9)])
        assert "8" not in dispatch.starts
        deferred = next(r for r in dispatch.records if r["tool_call_id"] == "8")
        assert deferred["reason"] == "tool_batch_limit"
        assert build_task_result(dispatch.records).status == "partial"
        await dispatch.run([call("retry-8", "8")])
        assert deferred["logical_key"] == dispatch.records[-1]["logical_key"]
        assert deferred["status"] == "skipped"  # raw receipt remains honest
        result = build_task_result(dispatch.records)
        assert result.status == "completed" and result.failures == ()
        assert result.executed_steps == len(dispatch.executed) == 9
    asyncio.run(scenario())


def test_overflow_duplicate_keeps_prior_success(dispatch):
    async def scenario():
        await dispatch.run([call("first", "already-read")])
        await dispatch.run([call(str(i), str(i)) for i in range(8)] +
                           [call("overflow-duplicate", "already-read")])
        duplicate = next(r for r in dispatch.records
                         if r["tool_call_id"] == "overflow-duplicate")
        assert duplicate["reason"] == "duplicate_request"
        assert duplicate["reused_from_tool_call_id"] == "first"
        assert len(dispatch.executed) == 9
        assert build_task_result(dispatch.records).status == "completed"
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [
    ("[ERR_TIMEOUT] Retry the same read window", {}),
    ("Network unavailable", {"_tool_outcome": {"status": "failed", "reason_code": "network"}}),
])
def test_transient_read_retries_once_and_success_repairs_status(dispatch, failure):
    dispatch.responses.append(failure)
    async def scenario():
        await dispatch.run([call("first")])
        await dispatch.run([call("retry")])
        await dispatch.run([call("duplicate")])
        assert dispatch.executed == ["first", "retry"]
        assert [r["attempt"] for r in dispatch.records] == [1, 2, 2]
        assert dispatch.records[-1]["reason"] == "duplicate_request"
        assert build_task_result(dispatch.records).status == "completed"
    asyncio.run(scenario())


def test_repeated_read_timeout_stops_after_two_attempts(dispatch):
    dispatch.responses.extend([("[ERR_TIMEOUT] Still unavailable", {})] * 2)
    async def scenario():
        for call_id in ("first", "retry", "third"):
            await dispatch.run([call(call_id)])
        assert dispatch.executed == ["first", "retry"]
        assert dispatch.records[-1]["status"] == "skipped"
        assert build_task_result(dispatch.records).status == "partial"
    asyncio.run(scenario())


@pytest.mark.parametrize("name,status,reason", [
    ("save_research_note", "partial", "timeout"),
    ("unregistered_tool", "partial", "timeout"),
    ("read_account_file", "cancelled", "stop_requested"),
    ("read_account_file", "partial", "coverage_incomplete"),
    ("read_account_file", "failed", "artifact_validation_failed"),
])
def test_transient_retry_does_not_admit_unsafe_or_unrelated_outcomes(dispatch, name, status, reason):
    dispatch.responses.append(("Synthetic unsuccessful result", {
        "_tool_outcome": {"status": status, "reason_code": reason}}))
    async def scenario():
        await dispatch.run([call("first", name=name)])
        await dispatch.run([call("second", name=name)])
        assert dispatch.executed == ["first"]
        assert dispatch.records[-1]["reason"] == "duplicate_request"
        assert build_task_result(dispatch.records).status != "completed"
    asyncio.run(scenario())

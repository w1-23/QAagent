"""Offline owner/session checks for the same result used by agent and UI."""
import asyncio
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.agent.host_test import environment  # noqa: F401
from src.core import tool_results, tracing
from src.research.result_tools import read_tool_result


def request(user_id=17, role="user"):
    value = Request({"type": "http", "method": "GET", "path": "/"})
    value.state.user = {"user_id": user_id, "role": role}
    return value


def stored(sid="host-session"):
    assert tool_results.record(tool_call_id="prior-turn-call", session_id=sid,
        request_id="earlier-request", tool_name="synthetic_read", status="complete",
        args={"key": "actual-input"}, result_text="prior turn exact evidence")


def test_same_owner_reads_previous_turn_without_reexecuting(environment):
    from src.api.main import tool_result_detail_endpoint
    stored()
    detail = asyncio.run(tool_result_detail_endpoint("host-session", "prior-turn-call", request()))
    assert detail["result_text"] == "prior turn exact evidence"
    assert json.loads(detail["args_json"]) == {"key": "actual-input"}
    body = json.loads(read_tool_result.invoke({"tool_call_id": "prior-turn-call"}))
    assert body["status"] == "ok" and body["result_text"] == detail["result_text"]
    assert body["content_origin"] == "untrusted_historical_tool_result"


@pytest.mark.parametrize("role", ["user", "admin"])
def test_foreign_account_rejected_before_reading_result(environment, monkeypatch, role):
    from src.api.main import tool_result_detail_endpoint
    stored()
    def forbidden_lookup(*args):
        pytest.fail("foreign account reached result lookup")
    monkeypatch.setattr(tool_results, "fetch", forbidden_lookup)
    with pytest.raises(HTTPException) as denied:
        asyncio.run(tool_result_detail_endpoint("host-session", "prior-turn-call", request(18, role)))
    assert denied.value.status_code == 404
    tracing.set_user_id(18)
    assert json.loads(read_tool_result.invoke({"tool_call_id": "prior-turn-call"}))["status"] == "unavailable"


def test_old_terminal_replay_removes_private_completion_without_rewriting_storage(environment):
    from src.core import jobs, task_events
    _, job = environment
    original = {"display_status": "completed", "task_snapshot": {"turn_outcome": {
        "status": "completed", "completion": {"statement": "private-review-sentinel"}}}}
    # Simulate an existing historical event; its raw evidence stays unchanged.
    task_events.append_event(job, "done", {"turn_envelope": original})
    replay = task_events.replay(job)
    event = next(row for row in replay["events"] if row["event"] == "done")
    assert "completion" not in event["data"]["turn_envelope"]["task_snapshot"]["turn_outcome"]
    assert "completion" not in replay["terminal_snapshot"]["task_snapshot"]["turn_outcome"]
    assert "completion" in original["task_snapshot"]["turn_outcome"]
    assert "private-review-sentinel" in jobs.get_job(job)["terminal_snapshot"]


@pytest.mark.parametrize("result_session", ["other-session", ""])
def test_foreign_or_unscoped_result_rejected_even_for_owned_url(environment, result_session):
    from src.api.main import tool_result_detail_endpoint
    stored(result_session)
    with pytest.raises(HTTPException) as denied:
        asyncio.run(tool_result_detail_endpoint("host-session", "prior-turn-call", request()))
    assert denied.value.status_code == 404
    assert json.loads(read_tool_result.invoke({"tool_call_id": "prior-turn-call"}))["status"] == "unavailable"

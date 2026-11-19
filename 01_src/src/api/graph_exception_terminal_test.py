"""Graph-exception terminalization through the real API commit seam.

``process_graph`` no longer writes ``failed`` straight to the jobs table: its
exception path builds one honest failure envelope and commits it through
``main._commit_failed_terminal`` -> ``ExecutionSegments.finish_terminal``. These
regressions drive that seam against a temporary auth/jobs store; no Host, model,
worker or HTTP service is started. The authority under test is the durable claim.
"""
import asyncio
import time

import pytest

from src.agent.execution_segments import ExecutionSegments
from src.agent.identity import ExecutionIdentity
from src.api import main
from src.core import account_leases, jobs, task_events

SAFE_ERROR = "处理请求时发生内部错误，请稍后重试"


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    from src import auth, config
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", state / "accounts.sqlite3")
    monkeypatch.setattr(jobs, "_DB_PATH", state / "sessions.db")
    monkeypatch.setattr(account_leases, "_states", {})
    auth.init_store()
    with auth._connect() as conn:
        conn.execute(
            "INSERT INTO users(username,salt,password_hash,active,created_at) "
            "VALUES(?,?,?,1,?)",
            ("n5_api_owner", b"salt", b"hash", "2026-11-19"))
        owner = int(conn.execute("SELECT user_id FROM users").fetchone()[0])
    job = jobs.claim_job("n5_api_session", "n5_api_turn", owner_user_id=owner)
    identity = ExecutionIdentity(owner, "n5_api_session", "n5_api_turn", job.job_id)
    task_events.init_budget(job.job_id, time.time() + 60, max_calls=4,
                            max_tokens=10000)
    segments = ExecutionSegments(identity)
    return segments, segments.start()


def segment_commit(segments, claim):
    """The API's segment-aware commit route, without the SSE notification."""
    async def commit(done_payload, *, status, current_step, **kwargs):
        segments.finish_terminal(
            claim, done_payload=done_payload, status=status,
            current_step=current_step, progress_summary=kwargs.get("progress_summary", ""),
            error=kwargs.get("error", ""))
    return commit


def done_events(segments):
    return [event for event in task_events.replay(segments.identity.run_id)["events"]
            if event["event"] == "done"]


def add_unknown_operation(segments):
    with task_events.transaction() as conn:
        conn.execute(
            "INSERT INTO task_operations (operation_id,job_id,owner_user_id,tool_name,"
            "input_hash,inputs,input_refs,recovery_mode,status,lease,lease_until,"
            "attempts,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("op-unknown", segments.identity.run_id, segments.identity.owner_user_id,
             "code_execute", "h", "{}", "[]", "never", "unknown", "", 0, 1,
             time.time()))


def test_api_failure_commit_closes_segment_and_keeps_one_done(claimed):
    segments, claim = claimed

    result = asyncio.run(main._commit_failed_terminal(
        segment_commit(segments, claim), session_id="n5_api_session",
        job_id=segments.identity.run_id, safe_error=SAFE_ERROR,
        tool_status=[], elapsed_ms=1234, current_step="incomplete",
        error="graph_error"))

    assert result is None
    job = jobs.get_job(segments.identity.run_id)
    assert job["status"] == "failed"
    assert job["current_step"] == "incomplete"
    assert job["error"] == "graph_error"
    assert job["finished_at"] and job["terminal_snapshot"]
    with task_events.transaction() as conn:
        phase = conn.execute(
            "SELECT phase FROM agent_execution_segments WHERE job_id=?",
            (segments.identity.run_id,)).fetchone()["phase"]
    assert phase == "terminal"
    done = done_events(segments)
    assert len(done) == 1
    payload = done[0]["data"]
    assert payload["answer"] == "" and payload["history_saved"] is False
    assert payload["turn_status"] == "failed"
    assert payload["turn_envelope"]["display_status"] == "failed"
    assert SAFE_ERROR in payload["turn_envelope"]["notices"]


def test_api_failure_commit_defers_while_execution_outcome_is_unknown(claimed):
    segments, claim = claimed
    add_unknown_operation(segments)

    result = asyncio.run(main._commit_failed_terminal(
        segment_commit(segments, claim), session_id="n5_api_session",
        job_id=segments.identity.run_id, safe_error=SAFE_ERROR,
        tool_status=[], elapsed_ms=1))

    assert isinstance(result, task_events.TaskConflict)
    assert "requires_reconciliation" in str(result)
    job = jobs.get_job(segments.identity.run_id)
    assert job["status"] == "running" and job["finished_at"] == ""
    with task_events.transaction() as conn:
        row = conn.execute(
            "SELECT phase,token FROM agent_execution_segments WHERE job_id=?",
            (segments.identity.run_id,)).fetchone()
    assert row["phase"] == "active" and row["token"] == claim.token
    assert done_events(segments) == []

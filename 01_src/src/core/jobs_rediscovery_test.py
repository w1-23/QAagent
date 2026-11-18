"""Task navigation reads retain active work and enforce account boundaries."""
import sqlite3
from contextlib import closing

import pytest

from src.core import jobs


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.sqlite3")


def add_job(index, *, status="completed", owner=1, session="session"):
    claim = jobs.claim_job(session, f"request-{index}", owner_user_id=owner,
                           client_request_id=f"client-{index}", payload={})
    assert jobs.update_job(claim.job_id, status=status)
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn, conn:
        conn.execute("UPDATE task_jobs SET created_at=? WHERE job_id=?",
                     (f"2026-01-{index:02d}T00:00:00", claim.job_id))
    return claim.job_id


def test_default_recent_limit_and_opt_in_retains_all_active_waiting_jobs():
    active = {add_job(i, status=status) for i, status in enumerate(
        ["running", "queued", "awaiting_input", "detached", "cancel_requested"] * 3, 1)}
    terminal = [add_job(i) for i in range(16, 28)]
    assert [j["job_id"] for j in jobs.list_for_session("session")] == terminal[-10:][::-1]
    view = jobs.list_for_session("session", include_active=True)
    assert len(view) == 25
    assert {j["job_id"] for j in view} == active | set(terminal[-10:])
    assert [j["created_at"] for j in view] == sorted(
        [j["created_at"] for j in view], reverse=True)
    assert {j["job_id"] for j in jobs.list_for_session("session", limit=0, include_active=True)} == active


def test_owner_filter_applies_before_recent_limit_and_to_active_jobs():
    own = add_job(1)
    add_job(2, status="running", owner=2)
    add_job(3, owner=2)
    add_job(4, status="queued", session="elsewhere")
    view = jobs.list_for_session("session", limit=1, include_active=True, owner_user_id=1)
    assert [j["job_id"] for j in view] == [own]
    assert jobs.list_for_session("session", owner_user_id=0) == []


def test_task_presence_is_one_owner_scoped_batch_query(monkeypatch):
    add_job(1, session="owned-empty", status="queued")
    add_job(2, session="foreign-only", owner=2)
    add_job(3, session="outside-list")
    queries = []
    connect = jobs._connect

    def traced_connect():
        conn = connect()
        conn.set_trace_callback(queries.append)
        return conn

    monkeypatch.setattr(jobs, "_connect", traced_connect)
    assert jobs.sessions_with_jobs(["owned-empty", "foreign-only", "none", "owned-empty"], 1) == {"owned-empty"}
    selects = [q for q in queries if q.startswith("SELECT DISTINCT session_id")]
    assert len(selects) == 1
    assert "owner_user_id=1" in selects[0]
    assert jobs.sessions_with_jobs([], 1) == set()
    assert jobs.sessions_with_jobs(["owned-empty"], 0) == set()

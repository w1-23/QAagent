"""Durable HTTP claims: offline SQLite only, no API/service/model imports."""
from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from src.core import jobs


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.sqlite3")


def _payload(**changes):
    return {"session_id": None, "query": "offline request", "attachments": [],
            "file_refs": [], "visualization_enabled": True, **changes}


def _claim(*, owner=1, key="client-request", payload=None, sid="winner-session", rid="request-1"):
    return jobs.claim_job(sid, rid, owner_user_id=owner, client_request_id=key,
                          payload=_payload() if payload is None else payload)


def _count():
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn:
        return conn.execute("SELECT count(*) FROM task_jobs").fetchone()[0]


def test_two_threads_claim_same_key_once_without_process_lock(monkeypatch):
    barrier = threading.Barrier(2)

    class ForbiddenLock:
        def __enter__(self):
            raise AssertionError("claim must use SQLite, not a process-local lock")

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(jobs, "_write_lock", ForbiddenLock())

    def claim(index):
        barrier.wait(timeout=5)
        return _claim(sid=f"session-{index}", rid=f"request-{index}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (1, 2)))
    assert sum(result.created for result in results) == 1
    assert len({result.job_id for result in results}) == 1
    assert results[0].job == results[1].job
    assert _count() == 1


def test_same_key_is_scoped_to_owner():
    barrier = threading.Barrier(2)

    def claim(owner):
        barrier.wait(timeout=5)
        return _claim(owner=owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (1, 2)))
    assert all(result.created for result in results)
    assert results[0].job_id != results[1].job_id
    assert {result.job["owner_user_id"] for result in results} == {1, 2}
    assert _count() == 2


def test_concurrent_conflicting_payload_has_one_winner():
    barrier = threading.Barrier(2)

    def claim(query):
        barrier.wait(timeout=5)
        try:
            return _claim(payload=_payload(query=query))
        except jobs.IdempotencyConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("first payload", "different payload")))
    winners = [result for result in results if isinstance(result, jobs.JobClaim)]
    conflicts = [result for result in results if isinstance(result, jobs.IdempotencyConflict)]
    assert len(winners) == len(conflicts) == 1
    assert winners[0].created and conflicts[0].job_id == winners[0].job_id
    assert _count() == 1


@pytest.mark.parametrize("change", [
    {"query": "changed"},
    {"attachments": [{"name": "input.txt", "data": "changed bytes"}]},
    {"file_refs": ["other-file-id"]},
    {"visualization_enabled": False},
    {"session_id": "another-client-session"},
])
def test_changed_request_fields_are_conflicts_and_do_not_mutate_existing(change):
    first = _claim()
    with pytest.raises(jobs.IdempotencyConflict) as caught:
        _claim(payload=_payload(**change))
    assert caught.value.job_id == first.job_id
    assert jobs.get_job(first.job_id) == first.job
    assert _count() == 1


def test_canonical_hash_reuses_same_payload_and_original_server_identity():
    payload = _payload()
    first = _claim(payload=payload)
    replay = _claim(payload=dict(reversed(list(payload.items()))), sid="new-proposed-session", rid="new-rid")
    assert first.created and not replay.created
    assert replay.job_id == first.job_id
    assert replay.job["session_id"] == "winner-session"
    assert replay.job["request_id"] == "request-1"
    assert len(replay.job["payload_hash"]) == 64
    assert "offline request" not in replay.job["payload_hash"]


@pytest.mark.parametrize("status", sorted(jobs.VALID_STATUS))
def test_every_persisted_state_returns_facts_without_permitting_reexecution(status):
    first = _claim()
    assert jobs.update_job(first.job_id, status=status, progress_summary="persisted fact")
    if status in jobs.TERMINAL_STATUS:
        assert jobs.save_terminal_snapshot(first.job_id, {"display_status": status})
    stored = jobs.get_job(first.job_id)
    replay = _claim(rid="after-reconnect")  # Opens a new SQLite connection each time.
    assert not replay.created and replay.job == stored
    assert replay.job["status"] == status
    assert _count() == 1


def test_restart_reconciliation_never_turns_a_claim_into_a_new_execution():
    first = _claim()
    assert jobs.mark_interrupted_jobs() == 1
    replay = _claim(rid="after-restart")
    assert not replay.created and replay.job_id == first.job_id
    assert replay.job["status"] == "failed"
    assert replay.job["current_step"] == "interrupted"
    assert replay.job["error"] == "service_restart"
    assert _count() == 1


def test_mutable_job_type_does_not_change_original_request_fingerprint():
    first = _claim()
    assert jobs.update_job(first.job_id, job_type="write")
    replay = _claim()
    assert not replay.created and replay.job["job_type"] == "write"
    with pytest.raises(jobs.IdempotencyConflict):
        jobs.claim_job("winner-session", "new-rid", "other-request-type", owner_user_id=1,
                       client_request_id="client-request", payload=_payload())


def test_database_unique_index_enforces_owner_key_even_for_other_writers():
    first = _claim()
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn, conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO task_jobs(job_id, owner_user_id, client_request_id, payload_hash) "
                "VALUES(?,?,?,?)", ("bypass", 1, "client-request", first.job["payload_hash"]))
    assert _count() == 1


def test_legacy_schema_migrates_without_claiming_or_merging_old_rows():
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn, conn:
        conn.execute("""CREATE TABLE task_jobs (
            job_id TEXT PRIMARY KEY, session_id TEXT, request_id TEXT, job_type TEXT,
            status TEXT, current_step TEXT, progress_summary TEXT, result_path TEXT,
            error TEXT, created_at TEXT, updated_at TEXT, finished_at TEXT)""")
        conn.executemany(
            "INSERT INTO task_jobs(job_id,session_id,request_id,status) VALUES(?,?,?,?)",
            [("legacy-1", "old-session", "same-rid", "completed"),
             ("legacy-2", "old-session", "same-rid", "running")])
    first = _claim()
    legacy = jobs.get_job("legacy-1")
    assert legacy["status"] == "completed"
    assert legacy["owner_user_id"] is None and legacy["client_request_id"] == ""
    assert legacy["payload_hash"] == ""
    assert jobs.get_job("legacy-2")["status"] == "running"
    assert not _claim().created
    assert first.created and _count() == 3
    with closing(jobs._connect()) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(task_jobs)")]
        assert columns.count("owner_user_id") == columns.count("payload_hash") == 1


def test_legacy_no_key_requests_remain_independent():
    first = jobs.create_job("s", "r")
    second = jobs.create_job("s", "r")
    third = jobs.claim_job("s", "r", owner_user_id=1)
    fourth = jobs.claim_job("s", "r", owner_user_id=1, client_request_id="")
    assert third.created and fourth.created
    assert len({first, second, third.job_id, fourth.job_id}) == 4
    assert _count() == 4


@pytest.mark.parametrize("kwargs", [
    {"owner_user_id": None}, {"owner_user_id": 0}, {"owner_user_id": "1"},
    {"client_request_id": "x" * 65}, {"client_request_id": "  "},
    {"client_request_id": 3}, {"payload": None},
])
def test_invalid_keyed_claim_is_rejected_before_insertion(kwargs):
    values = {"owner_user_id": 1, "client_request_id": "key", "payload": _payload()}
    values.update(kwargs)
    with pytest.raises(ValueError):
        jobs.claim_job("s", "r", **values)
    assert not jobs._DB_PATH.exists()


def test_storage_failure_propagates_instead_of_granting_execution(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "missing-parent" / "jobs.sqlite3")
    with pytest.raises(sqlite3.OperationalError):
        _claim()

"""N5 terminal lifecycle: a completed job must not leave a finalizing segment.

These regressions use a temporary auth/jobs store only; no Host, model, worker
or live service is started. The authority under test is the durable claim.
"""
import json
import sqlite3
import time

import pytest

from src.agent.execution_segments import ExecutionSegments
from src.agent.identity import ExecutionIdentity
from src.core import account_leases, jobs, task_events
from src.core.turn_envelope import build_turn_envelope


@pytest.fixture
def segments(tmp_path, monkeypatch):
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
            ("n5_owner", b"salt", b"hash", "2026-11-19"))
        owner = int(conn.execute("SELECT user_id FROM users").fetchone()[0])
    claim = jobs.claim_job("n5_session", "n5_turn", owner_user_id=owner)
    identity = ExecutionIdentity(owner, "n5_session", "n5_turn", claim.job_id)
    task_events.init_budget(claim.job_id, time.time() + 60, max_calls=4,
                            max_tokens=10000)
    return ExecutionSegments(identity)


def terminal_envelope():
    return build_turn_envelope(
        answer="done", requested_status="completed", display_status="completed",
        job_status="completed", history_saved=True).as_dict()


def finish(segments, claim, *, status="completed", current_step="completed"):
    return segments.finish_terminal(
        claim,
        done_payload={"job_id": segments.identity.run_id, "answer": "done",
                      "turn_envelope": terminal_envelope()},
        status=status, current_step=current_step)


def stored(segments):
    with task_events.transaction() as conn:
        return dict(conn.execute(
            "SELECT * FROM agent_execution_segments WHERE job_id=?",
            (segments.identity.run_id,)).fetchone())


def phases(segments):
    with task_events.transaction() as conn:
        return [json.loads(row[0])["phase"] for row in conn.execute(
            "SELECT data FROM task_events WHERE job_id=? "
            "AND event='execution_segment' ORDER BY seq",
            (segments.identity.run_id,))]


def test_completed_terminal_commit_closes_segment_atomically(segments):
    claim = segments.start()
    segments.begin_finalize(claim)
    assert stored(segments)["phase"] == "finalizing"

    finish(segments, claim)

    row, job = stored(segments), jobs.get_job(segments.identity.run_id)
    assert row["phase"] == "terminal"
    assert row["generation"] == claim.generation
    assert row["token"] != claim.token
    assert job["status"] == "completed"
    assert job["current_step"] == "completed"
    assert job["finished_at"]
    assert job["terminal_snapshot"]
    assert phases(segments)[-1] == "terminal"
    with task_events.transaction() as conn:
        terminal = task_events.current_terminal_event(conn, segments.identity.run_id)
        assert terminal is not None and terminal[1] == "done"
        terminal_seq = terminal[0]
        done_rows = conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE job_id=? AND event='done'",
            (segments.identity.run_id,)).fetchone()[0]
    # The live notification in api.main re-appends the same done payload; it
    # must dedup to this committed terminal sequence instead of adding a frame.
    duplicate = task_events.append_event(
        segments.identity.run_id, "done", {"job_id": segments.identity.run_id})
    assert duplicate["seq"] == terminal_seq
    with task_events.transaction() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE job_id=? AND event='done'",
            (segments.identity.run_id,)).fetchone()[0] == done_rows
    assert account_leases.active_count(segments.identity.owner_user_id) == 0


def test_active_cancellation_commits_after_confirmed_stop(segments):
    claim = segments.start()
    run_id = segments.identity.run_id
    assert jobs.request_cancel(run_id)
    envelope = build_turn_envelope(
        requested_status="cancelled", display_status="cancelled", job_status="cancelled",
        stop_reason="user", history_saved=True).as_dict()
    payload = {"job_id": run_id, "turn_envelope": envelope}
    for _ in range(2):
        segments.finish_terminal(claim, done_payload=payload, status="cancelled", current_step="cancelled")
    assert stored(segments)["phase"] == "terminal"
    assert jobs.get_job(run_id)["status"] == "cancelled"
    assert phases(segments) == ["active", "terminal"]
    assert [frame["event"] for frame in task_events.replay(run_id)["events"]].count("done") == 1


def test_completion_with_late_stop_preserves_completion_note(segments):
    claim = segments.start()
    segments.begin_finalize(claim)
    run_id = segments.identity.run_id
    assert jobs.request_cancel(run_id)
    finish(segments, claim)
    job = jobs.get_job(run_id)
    assert job["status"] == "completed"
    assert job["stop_note"] == "停止请求晚于完成"


def test_stale_old_claim_cannot_close_the_newer_segment(segments):
    old = segments.start()
    segments.prepare_park(old, checkpoint_ref="checkpoint_one",
                          wait_ids=("a" * 32,))
    segments.release_park(old)
    fresh = segments.claim_resume()["claim"]
    segments.activate(fresh)
    segments.begin_finalize(fresh)
    before = stored(segments)

    with pytest.raises(task_events.TaskConflict, match="claim_lost"):
        finish(segments, old)

    assert stored(segments) == before
    assert jobs.get_job(segments.identity.run_id)["status"] == "running"
    with task_events.transaction() as conn:
        assert task_events.current_terminal_event(
            conn, segments.identity.run_id) is None

    finish(segments, fresh)
    with pytest.raises(task_events.TaskConflict, match="claim_lost"):
        finish(segments, old)
    assert stored(segments)["phase"] == "terminal"
    assert jobs.get_job(segments.identity.run_id)["status"] == "completed"


def test_crash_between_terminal_fact_and_segment_rolls_back(segments):
    claim = segments.start()
    segments.begin_finalize(claim)
    with task_events.transaction() as conn:
        conn.execute(
            "CREATE TRIGGER n5_block_segment_terminal "
            "BEFORE UPDATE ON agent_execution_segments "
            "BEGIN SELECT RAISE(ABORT,'simulated_crash'); END")

    with pytest.raises(sqlite3.IntegrityError):
        finish(segments, claim)

    # The atomic commit rolled back in full: no durable done fact, the job is
    # still open and the segment is still finalizing. There is no persisted
    # "terminal job with a finalizing segment" residue to reconcile.
    row, job = stored(segments), jobs.get_job(segments.identity.run_id)
    assert row["phase"] == "finalizing"
    assert job["status"] == "running"
    assert job["finished_at"] == ""
    assert job["terminal_snapshot"] == ""
    with task_events.transaction() as conn:
        assert task_events.current_terminal_event(
            conn, segments.identity.run_id) is None

    with task_events.transaction() as conn:
        conn.execute("DROP TRIGGER n5_block_segment_terminal")
    finish(segments, claim)
    assert stored(segments)["phase"] == "terminal"
    assert jobs.get_job(segments.identity.run_id)["status"] == "completed"


def failure_envelope():
    return build_turn_envelope(
        requested_status="failed", display_status="failed", job_status="failed",
        stop_reason="error", history_saved=False,
        notices=["处理请求时发生内部错误，请稍后重试"]).as_dict()


def fail(segments, claim, *, envelope=None):
    return segments.finish_terminal(
        claim,
        done_payload={"job_id": segments.identity.run_id, "answer": "",
                      "history_saved": False,
                      "turn_envelope": envelope or failure_envelope()},
        status="failed", current_step="incomplete", error="graph_error")


def done_count(segments):
    with task_events.transaction() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE job_id=? AND event='done'",
            (segments.identity.run_id,)).fetchone()[0]


def add_unknown_operation(segments):
    with task_events.transaction() as conn:
        conn.execute(
            "INSERT INTO task_operations (operation_id,job_id,owner_user_id,tool_name,"
            "input_hash,inputs,input_refs,recovery_mode,status,lease,lease_until,"
            "attempts,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("op-unknown", segments.identity.run_id, segments.identity.owner_user_id,
             "code_execute", "h", "{}", "[]", "never", "unknown", "", 0, 1,
             time.time()))


def test_graph_failure_closes_active_segment_with_one_durable_done(segments):
    """The API exception path (process_graph) must not leave an active segment."""
    claim = segments.start()
    assert stored(segments)["phase"] == "active"

    fail(segments, claim)
    fail(segments, claim)  # retrying the same live claim is idempotent

    row, job = stored(segments), jobs.get_job(segments.identity.run_id)
    assert row["phase"] == "terminal"
    assert row["generation"] == claim.generation
    assert row["token"] != claim.token
    assert job["status"] == "failed"
    assert job["current_step"] == "incomplete"
    assert job["error"] == "graph_error"
    assert job["finished_at"] and job["terminal_snapshot"]
    assert phases(segments) == ["active", "terminal"]
    assert done_count(segments) == 1


def test_failed_terminal_defers_while_execution_outcome_is_unknown(segments):
    """An unfinalized segment stays open when execution outcome is unknown."""
    claim = segments.start()
    add_unknown_operation(segments)

    with pytest.raises(task_events.TaskConflict, match="requires_reconciliation"):
        fail(segments, claim)

    row, job = stored(segments), jobs.get_job(segments.identity.run_id)
    assert row["phase"] == "active" and row["token"] == claim.token
    assert job["status"] == "running" and job["finished_at"] == ""
    assert done_count(segments) == 0


def test_finalized_turn_still_reports_unknown_operations_as_failed(segments):
    """Finalization already owns the outcome; its failed commit still lands."""
    claim = segments.start()
    segments.begin_finalize(claim)
    add_unknown_operation(segments)

    fail(segments, claim)

    assert stored(segments)["phase"] == "terminal"
    assert jobs.get_job(segments.identity.run_id)["status"] == "failed"
    assert done_count(segments) == 1


def test_stale_claim_cannot_fail_close_the_newer_segment(segments):
    old = segments.start()
    segments.prepare_park(old, checkpoint_ref="checkpoint_one",
                          wait_ids=("a" * 32,))
    segments.release_park(old)
    fresh = segments.claim_resume()["claim"]
    segments.activate(fresh)
    before = stored(segments)

    with pytest.raises(task_events.TaskConflict, match="claim_lost"):
        fail(segments, old)

    assert stored(segments) == before
    assert jobs.get_job(segments.identity.run_id)["status"] == "running"
    assert done_count(segments) == 0

"""Durable independent turn queue: repository and concurrency tests.

Self-contained: each test gets a private SQLite file. No live service, tunnel,
network, user database or runtime state is touched. Run with the isolated
test-runtime runner described in the task hand-off.
"""
import sqlite3
import threading
from contextlib import closing

import pytest

from src.core import chat_turn_queue as turn_queue
from src.core import jobs, user_questions


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "sessions.db")
    turn_queue.ensure_schema()


def _payload(text="hello"):
    return {"text": text, "settings": {"visualization": False}}


def _turn(owner=1, session="s1", client="c1", payload=None, **kwargs):
    return turn_queue.enqueue_turn(
        owner_user_id=owner,
        session_id=session,
        client_request_id=client,
        bounded_payload=_payload() if payload is None else payload,
        **kwargs,
    )


def _rows(owner=1, session="s1"):
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(
            "SELECT * FROM chat_turn_queue WHERE owner_user_id=? "
            "AND session_id=? ORDER BY queue_seq", (owner, session))]


def _seq(owner=1, session="s1"):
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn:
        row = conn.execute(
            "SELECT last_seq FROM chat_turn_queue_seq WHERE owner_user_id=? "
            "AND session_id=?", (owner, session)).fetchone()
        return None if row is None else row[0]


def _claim(owner=1, session="s1", expected_generation=None):
    return turn_queue.claim_head(
        owner_user_id=owner, session_id=session,
        expected_generation=expected_generation)


def _dispatch(turn, current_step="dispatched"):
    return turn_queue.mark_dispatch_intent(
        owner_user_id=turn["owner_user_id"],
        session_id=turn["session_id"],
        client_request_id=turn["client_request_id"],
        generation=turn["generation"],
        current_step=current_step,
    )


def _awaiting(turn, child=""):
    return turn_queue.mark_awaiting_input(
        owner_user_id=turn["owner_user_id"],
        session_id=turn["session_id"],
        client_request_id=turn["client_request_id"],
        generation=turn["generation"],
        active_child_job_id=child,
    )


def _settle(turn, *, history_saved=True, reason="completed"):
    started = turn_queue.mark_started(
        owner_user_id=turn["owner_user_id"],
        session_id=turn["session_id"],
        client_request_id=turn["client_request_id"],
        generation=turn["generation"],
    )
    return turn_queue.begin_settling(
        owner_user_id=started["owner_user_id"],
        session_id=started["session_id"],
        client_request_id=started["client_request_id"],
        generation=started["generation"],
        terminal_evidence={"status": "completed" if history_saved else "cancelled"},
        history_saved=history_saved,
        clear_reason="" if history_saved else reason,
    )


def _finish(turn, *, history_saved=True, reason="completed"):
    if turn["state"] == "claimed":
        turn = _dispatch(turn)
    if turn["state"] == "running":
        turn = _settle(turn, history_saved=history_saved, reason=reason)
    assert turn["state"] == "settling", turn["state"]
    return turn_queue.finalize_terminal(
        owner_user_id=turn["owner_user_id"],
        session_id=turn["session_id"],
        client_request_id=turn["client_request_id"],
        generation=turn["generation"],
        history_saved=history_saved,
        reason=reason,
    )


def _running(owner=1, session="s1", client="c1"):
    claimed = _claim(owner, session)
    assert claimed["client_request_id"] == client
    return _dispatch(claimed)


# ---------------------------------------------------------------------------
# Admission, identity and storage.
# ---------------------------------------------------------------------------

def test_enqueue_atomically_creates_queued_root_job_and_stable_fifo_order():
    first = _turn(client="c1")
    second = _turn(client="c2")
    third = _turn(client="c3")

    assert [x["created"] for x in (first, second, third)] == [True, True, True]
    assert [x["queue_seq"] for x in (first, second, third)] == [1, 2, 3]
    assert [x["queue_position"] for x in (first, second, third)] == [1, 2, 3]
    assert {x["state"] for x in (first, second, third)} == {"queued"}
    assert len({x["job_id"] for x in (first, second, third)}) == 3

    for turn in (first, second, third):
        job = jobs.get_job(turn["job_id"])
        assert job is not None
        assert job["status"] == "queued"
        assert job["current_step"] == "turn_queue"
        assert job["owner_user_id"] == 1
        assert job["session_id"] == "s1"
        assert job["client_request_id"] == turn["client_request_id"]
        assert job["payload_hash"] == turn["payload_digest"]

    listed = turn_queue.list_turns(owner_user_id=1, session_id="s1")
    assert [t["queue_seq"] for t in listed] == [1, 2, 3]
    assert [t["queue_position"] for t in listed] == [1, 2, 3]
    assert [t["queue_seq"] for t in turn_queue.list_turns(
        owner_user_id=1, session_id="s1", limit=0)] == [1, 2, 3]
    assert [t["queue_seq"] for t in turn_queue.list_turns(
        owner_user_id=1, session_id="s1", include_terminal=False)] == [1, 2, 3]


def test_idempotent_replay_happens_before_limits_and_consumes_no_sequence():
    first = _turn(client="k", attachment_bytes=7)
    zero = turn_queue.QueueLimits(
        max_pending_per_session=0, max_pending_per_owner=0,
        max_queued_bytes_per_session=0, max_queued_bytes_per_owner=0,
        max_payload_bytes=turn_queue.DEFAULT_MAX_PAYLOAD_BYTES,
    )
    replay = _turn(client="k", attachment_bytes=7, limits=zero)

    assert replay["created"] is False
    assert replay["job_id"] == first["job_id"]
    assert replay["queue_seq"] == first["queue_seq"] == 1
    assert len(_rows()) == 1
    assert _seq() == 1


def test_same_client_id_conflicts_on_session_payload_or_attachment_bytes():
    _turn(client="k", payload={"text": "a"}, attachment_bytes=3)

    with pytest.raises(turn_queue.TurnPayloadConflict) as conflict:
        _turn(client="k", session="s2", payload={"text": "a"},
              attachment_bytes=3)
    assert conflict.value.code == "turn_payload_conflict"

    with pytest.raises(turn_queue.TurnPayloadConflict):
        _turn(client="k", payload={"text": "changed"}, attachment_bytes=3)

    with pytest.raises(turn_queue.TurnPayloadConflict):
        _turn(client="k", payload={"text": "a"}, attachment_bytes=4)

    replay = _turn(client="k", payload={"text": "a"}, attachment_bytes=3)
    assert replay["created"] is False
    assert len(_rows()) == 1


def test_owner_level_client_id_is_scoped_and_reads_do_not_leak():
    own = _turn(owner=1, session="shared", client="same")
    foreign = _turn(owner=2, session="shared", client="same")

    assert own["job_id"] != foreign["job_id"]
    assert turn_queue.get_turn(
        owner_user_id=1, session_id="shared",
        client_request_id="same")["job_id"] == own["job_id"]
    assert turn_queue.get_turn(
        owner_user_id=2, session_id="shared",
        client_request_id="same")["job_id"] == foreign["job_id"]
    assert turn_queue.get_turn(
        owner_user_id=3, session_id="shared",
        client_request_id="same") is None

    assert [t["job_id"] for t in turn_queue.list_turns(
        owner_user_id=1, session_id="shared")] == [own["job_id"]]
    assert [t["job_id"] for t in turn_queue.list_turns(
        owner_user_id=2, session_id="shared")] == [foreign["job_id"]]

    first_owner = _claim(owner=1, session="shared")
    assert first_owner["client_request_id"] == "same"
    # The other owner's queue head is untouched and still claimable.
    second_owner = _claim(owner=2, session="shared")
    assert second_owner["client_request_id"] == "same"
    assert second_owner["job_id"] == foreign["job_id"]


def test_pending_and_byte_limits_are_checked_inside_admission():
    pending = turn_queue.QueueLimits(
        max_pending_per_session=2, max_pending_per_owner=10)
    _turn(client="a", limits=pending)
    _turn(client="b", limits=pending)
    with pytest.raises(turn_queue.TurnQueueFull):
        _turn(client="c", limits=pending)
    # Idempotent replay still works while the queue is full.
    assert _turn(client="a", limits=pending)["created"] is False

    owner_wide = turn_queue.QueueLimits(
        max_pending_per_session=10, max_pending_per_owner=2)
    _turn(owner=2, session="x", client="x1", limits=owner_wide)
    _turn(owner=2, session="y", client="y1", limits=owner_wide)
    with pytest.raises(turn_queue.TurnQueueFull):
        _turn(owner=2, session="z", client="z1", limits=owner_wide)

    byte_limits = turn_queue.QueueLimits(
        max_queued_bytes_per_session=10, max_queued_bytes_per_owner=10)
    _turn(owner=3, session="b", client="b1", attachment_bytes=6,
          limits=byte_limits)
    with pytest.raises(turn_queue.TurnQueueFull):
        _turn(owner=3, session="b", client="b2", attachment_bytes=6,
              limits=byte_limits)
    _turn(owner=3, session="b", client="b3", attachment_bytes=4,
          limits=byte_limits)
    with pytest.raises(turn_queue.TurnQueueFull):
        _turn(owner=3, session="b", client="b4", attachment_bytes=1,
              limits=byte_limits)


def test_sequence_is_not_reused_after_cancel_cleanup_or_restart():
    _turn(client="a")
    _turn(client="b")
    turn_queue.cancel_queued(owner_user_id=1, session_id="s1",
                             client_request_id="a")

    third = _turn(client="c")
    assert third["queue_seq"] == 3

    # Simulate cleanup deleting a terminal row: the allocator must not rewind.
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn:
        conn.execute("DELETE FROM chat_turn_queue WHERE client_request_id='a'")
        conn.commit()

    fourth = _turn(client="d")
    assert fourth["queue_seq"] == 4

    # A new process/connection keeps allocating monotonically.
    fifth = _turn(client="e")
    assert fifth["queue_seq"] == 5
    assert _seq() == 5


def test_payload_validation_rejects_non_json_or_oversized_without_rows():
    with pytest.raises(ValueError):
        _turn(payload={"x": float("nan")})
    with pytest.raises(ValueError):
        _turn(payload={"x": object()})
    with pytest.raises(ValueError):
        _turn(payload="not-an-object")
    with pytest.raises(ValueError):
        _turn(payload={"text": "too long"},
              limits=turn_queue.QueueLimits(max_payload_bytes=4))

    assert _rows() == []
    assert jobs.list_for_session("s1") == []


def test_root_job_and_queue_row_commit_or_roll_back_together():
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn:
        conn.execute(
            "CREATE TRIGGER fail_queue_insert BEFORE INSERT ON chat_turn_queue "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _turn(client="a")

    assert jobs.list_for_session("s1") == []
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_turn_queue").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_turn_queue_seq").fetchone()[0] == 0
        conn.execute("DROP TRIGGER fail_queue_insert")
        conn.commit()

    assert _turn(client="a")["queue_seq"] == 1


# ---------------------------------------------------------------------------
# FIFO gate, CAS and single-agent serialization.
# ---------------------------------------------------------------------------

def test_claim_head_is_fifo_single_agent_and_honors_expected_generation():
    _turn(client="a")
    _turn(client="b")
    with pytest.raises(turn_queue.TurnQueueNotReady):
        _claim(expected_generation=99)

    first = _claim(expected_generation=0)
    assert first["client_request_id"] == "a"
    assert first["state"] == "claimed"
    assert first["generation"] == 1

    # One Agent per session: the next queued turn stays queued.
    with pytest.raises(turn_queue.TurnQueueNotReady):
        _claim()
    assert turn_queue.get_turn(
        owner_user_id=1, session_id="s1",
        client_request_id="b")["state"] == "queued"

    _finish(first)
    second = _claim()
    assert second["client_request_id"] == "b"


def test_different_sessions_are_independent():
    _turn(session="s1", client="a")
    _turn(session="s2", client="b")

    first = _claim(session="s1")
    second = _claim(session="s2")
    assert first["queue_seq"] == second["queue_seq"] == 1
    assert first["client_request_id"] == "a"
    assert second["client_request_id"] == "b"


@pytest.mark.parametrize("count", [2, 5, 10])
def test_two_connections_racing_for_the_head_have_one_winner(count):
    for index in range(count):
        _turn(client=f"c{index}")

    barrier = threading.Barrier(2)
    results = []

    def worker():
        barrier.wait()
        try:
            results.append(("ok", _claim()))
        except BaseException as exc:  # noqa: BLE001 - asserted below
            results.append(("error", exc))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    wins = [value for kind, value in results if kind == "ok"]
    errors = [value for kind, value in results if kind == "error"]
    assert len(wins) == 1, errors
    assert len(errors) == 1
    assert isinstance(errors[0], turn_queue.TurnQueueNotReady)
    assert wins[0]["queue_seq"] == 1
    assert wins[0]["state"] == "claimed"


@pytest.mark.parametrize("count", [2, 5, 10])
def test_two_connections_enqueue_unique_monotonic_sequences(count):
    barrier = threading.Barrier(2)
    errors = []

    def worker(start):
        barrier.wait()
        try:
            for index in range(start, count, 2):
                _turn(client=f"c{index}")
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(0,)),
        threading.Thread(target=worker, args=(1,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    rows = _rows()
    assert len(rows) == count
    assert [row["queue_seq"] for row in rows] == list(range(1, count + 1))
    assert len({row["client_request_id"] for row in rows}) == count
    assert _seq() == count


def test_dispatch_intent_is_a_shared_durable_fence_and_release_is_pre_intent():
    _turn(client="a")
    claimed = _claim()
    released = turn_queue.release_claim(
        owner_user_id=1, session_id="s1", client_request_id="a",
        generation=claimed["generation"])
    assert released["state"] == "queued"
    assert released["generation"] == claimed["generation"] + 1

    reclaimed = _claim()
    assert reclaimed["state"] == "claimed"
    running = _dispatch(reclaimed)
    assert running["state"] == "running"
    assert running["dispatch_at"] > 0
    assert turn_queue.dispatch_phase(running) == "started_frozen"
    assert jobs.get_job(running["job_id"])["status"] == "running"

    with pytest.raises(turn_queue.TurnNotQueued):
        turn_queue.release_claim(
            owner_user_id=1, session_id="s1", client_request_id="a",
            generation=running["generation"])
    with pytest.raises(turn_queue.TurnNotQueued):
        _dispatch(running)  # no longer claimed; state check wins

    started = turn_queue.mark_started(
        owner_user_id=1, session_id="s1", client_request_id="a",
        generation=running["generation"])
    assert started["started_at"] > 0
    assert started["generation"] == running["generation"]
    same = turn_queue.mark_started(
        owner_user_id=1, session_id="s1", client_request_id="a",
        generation=running["generation"])
    assert same["started_at"] == started["started_at"]


def test_dispatch_intent_refuses_when_task_job_left_queued_by_another_path():
    _turn(client="a")
    claimed = _claim()
    jobs.update_job(claimed["job_id"], status="running",
                    current_step="legacy_path")

    with pytest.raises(turn_queue.TurnQueueNotReady):
        _dispatch(claimed)
    # The failed transition rolled back; the queue record is still claimed.
    assert turn_queue.get_turn(
        owner_user_id=1, session_id="s1",
        client_request_id="a")["state"] == "claimed"


def test_claim_refuses_when_root_job_is_no_longer_queued():
    _turn(client="a")
    turn_queue.get_turn(owner_user_id=1, session_id="s1",
                        client_request_id="a")
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn:
        conn.execute("UPDATE task_jobs SET status='running' "
                     "WHERE client_request_id='a'")
        conn.commit()
    with pytest.raises(turn_queue.TurnQueueNotReady):
        _claim()


# ---------------------------------------------------------------------------
# Awaiting, settling, terminal and reconcile.
# ---------------------------------------------------------------------------

def test_awaiting_input_survives_restart_and_child_resolves_to_parent_turn():
    _turn(client="a")
    running = _running(client="a")
    awaiting = _awaiting(running, child="child-a")

    assert awaiting["state"] == "awaiting_input"
    assert awaiting["active_child_job_id"] == "child-a"
    assert turn_queue.dispatch_phase(awaiting) == "started_frozen"
    with pytest.raises(turn_queue.TurnQueueNotReady):
        _claim()

    # New connections read the same durable fact.
    loaded = turn_queue.get_turn(
        owner_user_id=1, session_id="s1", client_request_id="a")
    assert loaded["state"] == "awaiting_input"
    assert loaded["active_child_job_id"] == "child-a"

    by_root = turn_queue.find_turn_by_job(
        owner_user_id=1, session_id="s1", job_id=loaded["job_id"])
    by_child = turn_queue.find_turn_by_job(
        owner_user_id=1, session_id="s1", job_id="child-a")
    assert by_root["client_request_id"] == "a"
    assert by_child["client_request_id"] == "a"
    assert by_child["queue_seq"] == 1
    assert by_child["queue_position"] == 0

    # Repeating the same pause is idempotent; a different child conflicts.
    same = _awaiting(loaded, child="child-a")
    assert same["generation"] == loaded["generation"]
    with pytest.raises(turn_queue.TurnStateConflict):
        _awaiting(loaded, child="child-b")

    resumed = turn_queue.resume_continuation(
        owner_user_id=1, session_id="s1", client_request_id="a",
        generation=loaded["generation"], active_child_job_id="child-a")
    assert resumed["state"] == "running"
    assert resumed["generation"] == loaded["generation"] + 1
    assert resumed["active_child_job_id"] == "child-a"
    # The continuation fence still blocks a new turn.
    with pytest.raises(turn_queue.TurnQueueNotReady):
        _claim()


def test_settling_and_terminal_require_generation_history_and_cleanup():
    _turn(client="a")
    running = _running(client="a")
    settling = _settle(running, history_saved=True)

    assert settling["state"] == "settling"
    with pytest.raises(turn_queue.TurnQueueNotReady):
        turn_queue.finalize_terminal(
            owner_user_id=1, session_id="s1", client_request_id="a",
            generation=running["generation"], history_saved=True)

    with pytest.raises(turn_queue.TurnReconcileRequired):
        turn_queue.finalize_terminal(
            owner_user_id=1, session_id="s1", client_request_id="a",
            generation=settling["generation"], history_saved=True,
            cleanup_confirmed=False)

    terminal = turn_queue.finalize_terminal(
        owner_user_id=1, session_id="s1", client_request_id="a",
        generation=settling["generation"], history_saved=True,
        reason="completed")
    assert terminal["state"] == "terminal"
    assert terminal["history_saved"] is True
    assert terminal["finished_at"] > 0

    retry = turn_queue.finalize_terminal(
        owner_user_id=1, session_id="s1", client_request_id="a",
        generation=settling["generation"], history_saved=True,
        reason="completed")
    assert retry["state"] == "terminal"
    assert retry["generation"] == terminal["generation"]
    assert retry["finished_at"] == terminal["finished_at"]


def test_false_history_saved_requires_an_explicit_reason():
    _turn(client="a")
    running = _running(client="a")
    with pytest.raises(ValueError):
        _settle(running, history_saved=False, reason="")

    settling = _settle(running, history_saved=False, reason="cancelled")
    assert settling["state"] == "settling"
    assert settling["history_saved"] is False
    terminal = turn_queue.finalize_terminal(
        owner_user_id=1, session_id="s1", client_request_id="a",
        generation=settling["generation"], history_saved=False,
        reason="cancelled")
    assert terminal["state"] == "terminal"
    assert terminal["history_saved"] is False
    assert terminal["clear_reason"] == "cancelled"


def test_reconcile_required_blocks_session_successors_not_admission():
    _turn(client="a")
    _turn(client="b")
    running = _running(client="a")
    reconcile = turn_queue.mark_reconcile_required(
        owner_user_id=1, session_id="s1", client_request_id="a",
        generation=running["generation"], reason="dispatch_unknown")

    assert reconcile["state"] == "reconcile_required"
    assert reconcile["clear_reason"] == "dispatch_unknown"
    assert turn_queue.dispatch_phase(reconcile) == "reconcile_required"
    assert turn_queue.session_blocker(
        owner_user_id=1, session_id="s1")["kind"] == "reconcile_required"
    with pytest.raises(turn_queue.TurnReconcileRequired):
        _claim()

    # Admission is durable; only execution waits for explicit reconciliation.
    third = _turn(client="c")
    assert third["queue_seq"] == 3
    with pytest.raises(turn_queue.TurnReconcileRequired):
        _claim()

    # A terminal fact cannot be silently reopened as reconcile.
    _turn(session="s2", client="done")
    done = _finish(_running(session="s2", client="done"))
    with pytest.raises(turn_queue.TurnStateConflict):
        turn_queue.mark_reconcile_required(
            owner_user_id=1, session_id="s2", client_request_id="done",
            reason="late")


def test_unknown_session_does_not_consume_another_sessions_capacity():
    # Reconcile in s1 must not block s2's head.
    _turn(session="s1", client="a")
    running = _running(session="s1", client="a")
    turn_queue.mark_reconcile_required(
        owner_user_id=1, session_id="s1", client_request_id="a",
        generation=running["generation"], reason="unknown")

    _turn(session="s2", client="b")
    claimed = _claim(session="s2")
    assert claimed["client_request_id"] == "b"
    assert claimed["state"] == "claimed"


# ---------------------------------------------------------------------------
# Cancellation.
# ---------------------------------------------------------------------------

def test_cancel_middle_queued_turn_is_atomic_and_keeps_fifo():
    _turn(client="a")
    _turn(client="b")
    _turn(client="c")

    cancelled = turn_queue.cancel_queued(
        owner_user_id=1, session_id="s1", client_request_id="b")
    assert cancelled["state"] == "terminal"
    assert cancelled["clear_reason"] == "cancelled"
    assert cancelled["history_saved"] is False
    assert cancelled["finished_at"] > 0

    job = jobs.get_job(cancelled["job_id"])
    assert job["status"] == "cancelled"
    assert job["current_step"] == "cancelled"
    assert job["stop_reason"] == "turn_queue_cancelled"

    repeated = turn_queue.cancel_queued(
        owner_user_id=1, session_id="s1", client_request_id="b")
    assert repeated["finished_at"] == cancelled["finished_at"]
    assert repeated["generation"] == cancelled["generation"]

    first = _claim()
    assert first["client_request_id"] == "a"
    _finish(first)

    # Cancelled b is skipped without shifting c.
    third = _claim()
    assert third["client_request_id"] == "c"
    assert third["queue_seq"] == 3


def test_cancel_claimed_or_running_is_rejected_without_stopping_the_task():
    _turn(client="a")
    claimed = _claim()
    with pytest.raises(turn_queue.TurnNotQueued):
        turn_queue.cancel_queued(
            owner_user_id=1, session_id="s1", client_request_id="a")

    running = _dispatch(claimed)
    with pytest.raises(turn_queue.TurnNotQueued):
        turn_queue.cancel_queued(
            owner_user_id=1, session_id="s1", client_request_id="a")

    job = jobs.get_job(running["job_id"])
    assert job["status"] == "running"
    assert job["stop_reason"] == ""


def test_cancel_is_owner_and_session_scoped():
    _turn(owner=1, session="s1", client="a")

    with pytest.raises(turn_queue.TurnNotFound):
        turn_queue.cancel_queued(
            owner_user_id=2, session_id="s1", client_request_id="a")
    with pytest.raises(turn_queue.TurnNotFound):
        turn_queue.cancel_queued(
            owner_user_id=1, session_id="other", client_request_id="a")

    stored = turn_queue.get_turn(
        owner_user_id=1, session_id="s1", client_request_id="a")
    assert stored["state"] == "queued"
    assert jobs.get_job(stored["job_id"])["status"] == "queued"


def test_vanished_root_job_is_not_resurrected_but_queue_row_can_be_cancelled():
    turn = _turn(client="a")
    with closing(sqlite3.connect(jobs._DB_PATH)) as conn:
        conn.execute("DELETE FROM task_jobs WHERE job_id=?", (turn["job_id"],))
        conn.commit()

    with pytest.raises(turn_queue.TurnNotFound):
        _turn(client="a")  # replay cannot hand out a deleted root job
    with pytest.raises(turn_queue.TurnNotFound):
        _claim()

    cancelled = turn_queue.cancel_queued(
        owner_user_id=1, session_id="s1", client_request_id="a")
    assert cancelled["state"] == "terminal"
    assert cancelled["clear_reason"] == "cancelled"


# ---------------------------------------------------------------------------
# Legacy awaiting/open shields.
# ---------------------------------------------------------------------------

def test_legacy_open_job_shields_the_session_until_it_is_terminal():
    legacy = jobs.claim_job(
        "s1", "request-legacy", owner_user_id=1,
        client_request_id="legacy", payload={})
    assert legacy.created is True
    _turn(client="new")

    with pytest.raises(turn_queue.TurnQueueNotReady) as blocked:
        _claim()
    assert blocked.value.detail == "session_blocked"

    blocker = turn_queue.session_blocker(owner_user_id=1, session_id="s1")
    assert blocker["kind"] == "legacy_job"
    assert blocker["job_id"] == legacy.job_id

    jobs.update_job(legacy.job_id, status="failed")
    claimed = _claim()
    assert claimed["client_request_id"] == "new"


def test_pending_question_remains_a_shield_even_after_task_terminalizes():
    legacy = jobs.claim_job(
        "s2", "request-question", owner_user_id=1,
        client_request_id="legacy-question", payload={})
    question = user_questions.create_question(
        legacy.job_id, owner_user_id=1, session_id="s2",
        prompt="Choose one", options=[], allow_text=True, context={})
    assert question["status"] == "pending"
    jobs.update_job(legacy.job_id, status="failed")

    _turn(session="s2", client="new-question")
    blocker = turn_queue.session_blocker(owner_user_id=1, session_id="s2")
    assert blocker["kind"] == "pending_question"
    assert blocker["question_id"] == question["id"]

    with pytest.raises(turn_queue.TurnQueueNotReady) as blocked:
        _claim(session="s2")
    assert blocked.value.detail == "session_blocked"


# ---------------------------------------------------------------------------
# Migration and HTTP-facing view.
# ---------------------------------------------------------------------------

def test_migration_is_idempotent_and_preserves_rows_and_sequence():
    _turn(client="a")
    for _ in range(3):
        turn_queue.ensure_schema()

    rows = _rows()
    assert len(rows) == 1
    assert rows[0]["queue_seq"] == 1
    assert _seq() == 1
    assert _turn(client="b")["queue_seq"] == 2


def test_concurrent_migration_on_a_fresh_database_is_idempotent(
        monkeypatch, tmp_path):
    fresh = tmp_path / "fresh-sessions.db"
    monkeypatch.setattr(jobs, "_DB_PATH", fresh)
    barrier = threading.Barrier(2)
    errors = []

    def migrate():
        barrier.wait()
        try:
            turn_queue.ensure_schema()
        except BaseException as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=migrate) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    with closing(sqlite3.connect(fresh)) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"chat_turn_queue", "chat_turn_queue_seq"} <= tables


def test_public_view_omits_owner_and_stored_payload():
    turn = _turn(client="a")
    view = turn_queue.public_view(turn)
    assert "owner_user_id" not in view
    assert "bounded_payload" not in view
    assert view["job_id"] == turn["job_id"]
    assert view["session_id"] == "s1"
    assert view["queue_position"] == 1
    assert view["state"] == "queued"


def test_find_turn_by_job_is_owner_and_session_scoped():
    turn = _turn(client="a")
    assert turn_queue.find_turn_by_job(
        owner_user_id=1, session_id="s1",
        job_id=turn["job_id"])["client_request_id"] == "a"
    assert turn_queue.find_turn_by_job(
        owner_user_id=2, session_id="s1", job_id=turn["job_id"]) is None
    assert turn_queue.find_turn_by_job(
        owner_user_id=1, session_id="s2", job_id=turn["job_id"]) is None
    assert turn_queue.find_turn_by_job(
        owner_user_id=1, session_id="s1", job_id="missing") is None


def test_list_turns_always_returns_active_turns_and_supports_a_cursor():
    # Five terminal turns, then two active turns. A 2-row terminal window must
    # not hide either the active turns or the newer terminal history.
    for index in range(5):
        _turn(client=f"done{index}")
    for _ in range(5):
        _finish(_claim())
    _turn(client="active1")
    _turn(client="active2")

    window = turn_queue.list_turns(owner_user_id=1, session_id="s1", limit=2)
    active = {t["client_request_id"] for t in window
              if t["state"] != "terminal"}
    assert active == {"active1", "active2"}
    terminal_window = [t for t in window if t["state"] == "terminal"]
    assert [t["client_request_id"] for t in terminal_window] == [
        "done3", "done4"]

    # Forward cursor over terminal history, independent of the active set.
    seen = []
    cursor = 0
    for _ in range(10):
        page = turn_queue.list_turns(
            owner_user_id=1, session_id="s1", limit=2,
            include_active=False, after_seq=cursor)
        if not page:
            break
        seen.extend(t["client_request_id"] for t in page)
        cursor = page[-1]["queue_seq"]
    assert seen == [f"done{index}" for index in range(5)]

    with pytest.raises(ValueError):
        turn_queue.list_turns(
            owner_user_id=1, session_id="s1", after_seq=-1)


def test_scan_open_turns_is_bounded_cross_session_with_durable_cursor():
    _turn(session="a", client="a1")
    _turn(session="b", client="b1")
    _turn(session="c", client="c1")
    _running(session="c", client="c1")
    _turn(session="a", client="a2")
    _turn(session="d", client="d-done")
    _finish(_claim(session="d"))

    with closing(sqlite3.connect(jobs._DB_PATH)) as conn:
        expected = {row[0] for row in conn.execute(
            "SELECT root_job_id FROM chat_turn_queue "
            "WHERE state<>'terminal'")}
    assert len(expected) == 4

    collected = []
    cursor = None
    pages = 0
    while True:
        page = turn_queue.scan_open_turns(limit=2, after=cursor)
        pages += 1
        collected.extend(t["root_job_id"] for t in page["turns"])
        assert all(t["state"] != "terminal" for t in page["turns"])
        assert all("bounded_payload" not in t for t in page["turns"])
        assert all(t["dispatch_phase"] for t in page["turns"])
        cursor = page["next_cursor"]
        if not page["has_more"]:
            break
        assert cursor is not None
        assert pages < 10

    assert pages == 2
    assert len(collected) == len(set(collected))
    assert set(collected) == expected

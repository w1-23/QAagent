"""Same-host SQLite queue with fenced attempts and durable completion events.

This is an internal repository, not an authorization boundary or remote API.
The service must hold account admission and validate the task/input snapshot at
submission, claim and result acceptance. Only a trusted worker controller may
claim/heartbeat/confirm physical stop. A lost lease never proves a process died.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

from .protocol import ExecutionRequest, OCCUPYING, TERMINAL, bounded_integer, identifier, json_object, token


class ExecutionConflict(ValueError):
    pass


class ExecutionLeaseLost(RuntimeError):
    pass


class QueueFull(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS code_executions (
 id TEXT PRIMARY KEY, owner INTEGER NOT NULL, session TEXT NOT NULL,
 task TEXT NOT NULL, workspace TEXT NOT NULL, operation TEXT NOT NULL,
 request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, request TEXT NOT NULL,
 status TEXT NOT NULL, fence INTEGER NOT NULL DEFAULT 0,
 worker TEXT NOT NULL DEFAULT '', lease_until REAL,
 created REAL NOT NULL, updated REAL NOT NULL, deadline REAL NOT NULL,
 receipt TEXT, reason TEXT NOT NULL DEFAULT '',
 UNIQUE(owner, session, workspace, operation, request_key));
CREATE INDEX IF NOT EXISTS code_queue_status ON code_executions(status, created);
CREATE INDEX IF NOT EXISTS code_queue_owner ON code_executions(owner, workspace, status);
CREATE TABLE IF NOT EXISTS code_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 execution_id TEXT NOT NULL REFERENCES code_executions(id),
 event TEXT NOT NULL, fence INTEGER NOT NULL, created REAL NOT NULL,
 payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
"""


class ExecutionRepository:
    def __init__(self, path: Path, *, slots: int = 1, queue_limit: int = 100,
                 owner_queue_limit: int = 10, clock=time.time):
        self.path = Path(path)
        if not self.path.is_absolute() or str(self.path).startswith(("\\\\", "//")):
            raise ValueError("execution_database_must_be_local_absolute_path")
        for part in (self.path, *self.path.parents):
            if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
                raise PermissionError("execution_database_link_denied")
        self.slots = bounded_integer(slots, 1, 8, "slots")
        self.queue_limit = bounded_integer(queue_limit, 1, 10000, "queue_limit")
        self.owner_queue_limit = bounded_integer(owner_queue_limit, 1, 1000, "owner_queue_limit")
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connection() as conn:
            conn.executescript(_SCHEMA)
        # Keep host run deadlines outside the versioned execution fingerprint.
        # Serialize migration across independently constructed worker facades.
        with self._connection(write=True) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(code_executions)")}
            if "run_deadline" not in columns:
                conn.execute("ALTER TABLE code_executions ADD COLUMN run_deadline REAL NOT NULL DEFAULT 0")

    @contextmanager
    def _connection(self, *, write=False):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=10000")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _now(self):
        value = self.clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("invalid_execution_clock")
        return float(value)

    @staticmethod
    def _event(conn, row, event, now, payload=None):
        conn.execute("INSERT INTO code_events(execution_id,event,fence,created,payload) VALUES(?,?,?,?,?)",
                     (row["id"], event, row["fence"], now, json_object(payload or {})))

    @staticmethod
    def _public(row):
        if row is None:
            return None
        value = dict(row)
        value["execution_id"] = value.pop("id")
        value["receipt"] = json.loads(value["receipt"]) if value["receipt"] else None
        return value

    @staticmethod
    def _scope(conn, execution_id, owner, session):
        identifier(execution_id)
        if type(owner) is not int or owner <= 0 or not isinstance(session, str):
            raise PermissionError("execution_scope_required")
        row = conn.execute("SELECT * FROM code_executions WHERE id=? AND owner=? AND session=?",
                           (execution_id, owner, session)).fetchone()
        if row is None:
            raise LookupError("execution_not_found")
        return row

    @staticmethod
    def _owned(conn, execution_id, worker, fence, now):
        identifier(execution_id)
        token(worker)
        bounded_integer(fence, 1, 2**63 - 1, "fence")
        row = conn.execute("SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone()
        if (row is None or row["worker"] != worker or row["fence"] != fence
                or row["status"] not in OCCUPYING or row["status"] == "unknown"
                or row["lease_until"] is None or row["lease_until"] <= now):
            raise ExecutionLeaseLost("execution_lease_lost_reconcile_before_retry")
        return row

    def submit(self, request: ExecutionRequest, *, queue_timeout_sec: int = 600,
               absolute_deadline=None):
        if type(request) is not ExecutionRequest:
            raise ValueError("execution_request_required")
        bounded_integer(queue_timeout_sec, 1, 86400, "queue_timeout")
        # None/+infinity preserve the legacy finite execution timeout. A finite
        # run deadline is host authority, immutable across idempotent retries.
        if absolute_deadline is None or (type(absolute_deadline) is float and absolute_deadline == math.inf):
            run_deadline = 0.0
        elif (type(absolute_deadline) not in (int, float)
                or not math.isfinite(absolute_deadline) or absolute_deadline <= 0):
            raise ValueError("invalid_execution_run_deadline")
        else:
            run_deadline = float(absolute_deadline)
        now = self._now()
        with self._connection(write=True) as conn:
            same = conn.execute("SELECT * FROM code_executions WHERE owner=? AND session=? "
                "AND workspace=? AND operation=? AND request_key=?", (request.identity.owner_user_id,
                 request.identity.session_id, request.workspace_id, request.operation_id,
                 request.idempotency_key)).fetchone()
            if same is not None:
                if same["fingerprint"] != request.fingerprint or same["run_deadline"] != run_deadline:
                    raise ExecutionConflict("execution_idempotency_conflict")
                return self._public(same)
            if run_deadline and run_deadline <= now:
                raise ExecutionConflict("execution_run_deadline_exceeded")
            self._expire(conn, now)
            queued = conn.execute("SELECT COUNT(*) FROM code_executions WHERE status='queued'").fetchone()[0]
            own = conn.execute("SELECT COUNT(*) FROM code_executions WHERE owner=? AND status='queued'",
                               (request.identity.owner_user_id,)).fetchone()[0]
            if queued >= self.queue_limit or own >= self.owner_queue_limit:
                raise QueueFull("execution_queue_full")
            execution_id = uuid.uuid4().hex
            queue_deadline = min(now + queue_timeout_sec, run_deadline or math.inf)
            conn.execute("INSERT INTO code_executions(id,owner,session,task,workspace,operation,request_key,"
                "fingerprint,request,status,created,updated,deadline,run_deadline) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?,?)",
                (execution_id, request.identity.owner_user_id, request.identity.session_id, request.task_id,
                 request.workspace_id, request.operation_id, request.idempotency_key, request.fingerprint,
                 request.serialize(), now, now, queue_deadline, run_deadline))
            row = conn.execute("SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone()
            self._event(conn, row, "queued", now)
            return self._public(row)

    def find_submission(self, *, owner, session, workspace_id, operation_id, idempotency_key):
        """Internal exact-key replay lookup; the facade repeats authorization."""
        identifier(workspace_id)
        token(operation_id)
        token(idempotency_key)
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM code_executions WHERE owner=? AND session=? "
                "AND workspace=? AND operation=? AND request_key=?",
                (owner, session, workspace_id, operation_id, idempotency_key)).fetchone()
            return self._public(row)

    def run_executions(self, identity, *, limit=256):
        """Internal bounded exact-run lookup, never a session-wide cancellation."""
        from src.agent.identity import ExecutionIdentity
        if type(identity) is not ExecutionIdentity:
            raise ValueError("execution_identity_required")
        bounded_integer(limit, 1, 256, "run_query_limit")
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM code_executions WHERE owner=? AND session=? "
                "AND json_extract(request,'$.identity.run_id')=? "
                "AND json_extract(request,'$.identity.user_turn_id')=? ORDER BY created,id LIMIT ?",
                (identity.owner_user_id, identity.session_id, identity.run_id, identity.user_turn_id, limit + 1)).fetchall()
            if len(rows) > limit:
                raise ExecutionConflict("execution_run_query_limit_exceeded")
            return [self._public(row) for row in rows]

    def get(self, execution_id, *, owner, session):
        with self._connection() as conn:
            return self._public(self._scope(conn, execution_id, owner, session))

    def _expire(self, conn, now):
        rows = conn.execute("SELECT * FROM code_executions WHERE "
            "(status='queued' AND deadline<=?) OR "
            "(status IN ('preparing','running','collecting','cancel_requested') AND lease_until<=?)",
            (now, now)).fetchall()
        for row in rows:
            status = "timed_out" if row["status"] == "queued" else "unknown"
            reason = ("queue_deadline" if status == "timed_out" else
                      "cancel_worker_lease_expired" if row["status"] == "cancel_requested"
                      else "worker_lease_expired")
            conn.execute("UPDATE code_executions SET status=?,reason=?,updated=? WHERE id=?",
                         (status, reason, now, row["id"]))
            self._event(conn, row, status, now, {"reason": reason})

    def reconcile_expired(self):
        """Record uncertainty, never reclaim an unconfirmed running sandbox."""
        with self._connection(write=True) as conn:
            self._expire(conn, self._now())

    def claim(self, worker, *, lease_sec=30):
        token(worker)
        bounded_integer(lease_sec, 1, 300, "lease_seconds")
        now = self._now()
        with self._connection(write=True) as conn:
            self._expire(conn, now)
            active = conn.execute("SELECT COUNT(*) FROM code_executions WHERE status IN "
                "('preparing','running','collecting','cancel_requested','unknown')").fetchone()[0]
            if active >= self.slots:
                return None
            # One live/uncertain execution per owner and workspace. FIFO among eligible owners.
            row = conn.execute("SELECT q.* FROM code_executions q WHERE q.status='queued' AND NOT EXISTS "
                "(SELECT 1 FROM code_executions a WHERE a.status IN "
                "('preparing','running','collecting','cancel_requested','unknown') "
                "AND (a.owner=q.owner OR a.workspace=q.workspace)) ORDER BY q.created,q.rowid LIMIT 1").fetchone()
            if row is None:
                return None
            request = ExecutionRequest.parse(row["request"])
            conn.execute("UPDATE code_executions SET status='preparing',fence=fence+1,worker=?,"
                "lease_until=?,updated=?,deadline=? WHERE id=? AND status='queued'",
                (worker, now + lease_sec, now,
                 min(now + request.limits.timeout_sec, row["run_deadline"] or math.inf), row["id"]))
            current = conn.execute("SELECT * FROM code_executions WHERE id=?", (row["id"],)).fetchone()
            self._event(conn, current, "preparing", now)
            return self._public(current)

    def heartbeat(self, execution_id, worker, fence, *, lease_sec=30):
        bounded_integer(lease_sec, 1, 300, "lease_seconds")
        now = self._now()
        with self._connection(write=True) as conn:
            row = self._owned(conn, execution_id, worker, fence, now)
            if row["deadline"] <= now:
                raise ExecutionLeaseLost("execution_deadline_stop_required")
            conn.execute("UPDATE code_executions SET lease_until=?,updated=? WHERE id=?",
                         (now + lease_sec, now, execution_id))
            return row["status"]

    def transition(self, execution_id, worker, fence, status):
        if status not in {"running", "collecting"}:
            raise ValueError("invalid_execution_transition")
        now = self._now()
        with self._connection(write=True) as conn:
            row = self._owned(conn, execution_id, worker, fence, now)
            expected = "preparing" if status == "running" else "running"
            if row["status"] != expected:
                raise ExecutionConflict("execution_state_conflict")
            if row["deadline"] <= now:
                raise ExecutionLeaseLost("execution_deadline_stop_required")
            conn.execute("UPDATE code_executions SET status=?,updated=? WHERE id=?", (status, now, execution_id))
            self._event(conn, row, status, now)

    def cancel(self, execution_id, *, owner, session):
        now = self._now()
        with self._connection(write=True) as conn:
            row = self._scope(conn, execution_id, owner, session)
            if row["status"] in TERMINAL or row["status"] == "cancel_requested":
                return self._public(row)
            if row["status"] == "unknown":
                conn.execute("UPDATE code_executions SET reason='user_cancel_unknown',updated=? WHERE id=?",
                             (now, execution_id))
                self._event(conn, row, "cancel_requested", now, {"requires_reconciliation": True})
                return self._public(conn.execute("SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone())
            status = "cancelled" if row["status"] == "queued" else "cancel_requested"
            conn.execute("UPDATE code_executions SET status=?,updated=?,reason='user_cancel' WHERE id=?",
                         (status, now, execution_id))
            self._event(conn, row, status, now)
            return self._public(conn.execute("SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone())

    def finish(self, execution_id, worker, fence, *, status, receipt, process_stopped: bool,
               on_terminal=None):
        if status not in TERMINAL or process_stopped is not True:
            raise ValueError("execution_terminal_requires_confirmed_stop")
        if on_terminal is not None and not callable(on_terminal):
            raise ValueError("execution_terminal_callback_required")
        encoded = json_object(receipt)
        now = self._now()
        with self._connection(write=True) as conn:
            identifier(execution_id)
            token(worker)
            bounded_integer(fence, 1, 2**63 - 1, "fence")
            prior = conn.execute("SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone()
            if prior is not None and prior["status"] in TERMINAL:
                if (prior["worker"] == worker and prior["fence"] == fence
                        and prior["status"] == status and prior["receipt"] == encoded):
                    return  # Completion transport retries cannot duplicate an outbox event.
                raise ExecutionLeaseLost("execution_terminal_fence_conflict")
            row = self._owned(conn, execution_id, worker, fence, now)
            if status == "succeeded" and now > row["deadline"]:
                raise ExecutionConflict("execution_deadline_exceeded")
            if row["status"] == "cancel_requested" and status != "cancelled":
                raise ExecutionConflict("execution_cancel_must_be_confirmed")
            if status == "succeeded" and row["status"] != "collecting":
                raise ExecutionConflict("execution_results_not_collected")
            conn.execute("UPDATE code_executions SET status=?,receipt=?,updated=?,lease_until=NULL WHERE id=?",
                         (status, encoded, now, execution_id))
            self._event(conn, row, status, now, {"receipt": receipt})
            if on_terminal is not None:
                on_terminal(conn, self._public(conn.execute(
                    "SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone()))

    def complete_success(self, execution_id, worker, fence, *, accept_result, process_stopped: bool,
                         on_terminal=None):
        """Trusted host acceptance while cancellation is excluded by this lock.

        The callback commits an idempotent files-db acceptance receipt first.
        A crash before this queue commit leaves collecting/unknown, never an
        automatically repeated execution. Reconciliation may recover the same
        byte receipt; this API does not provide distributed exactly-once.
        on_terminal only writes trusted same-connection journals; no external I/O.
        """
        if process_stopped is not True or not callable(accept_result):
            raise ValueError("execution_terminal_requires_confirmed_stop")
        if on_terminal is not None and not callable(on_terminal):
            raise ValueError("execution_terminal_callback_required")
        now = self._now()
        with self._connection(write=True) as conn:
            row = self._owned(conn, execution_id, worker, fence, now)
            if row["status"] != "collecting":
                raise ExecutionConflict("execution_results_not_collected")
            if row["deadline"] <= now:
                raise ExecutionConflict("execution_deadline_exceeded")
            receipt = accept_result()
            encoded = json_object(receipt)
            finished = self._now()
            if finished > row["deadline"]:
                raise ExecutionConflict("execution_deadline_exceeded_during_acceptance")
            conn.execute("UPDATE code_executions SET status='succeeded',receipt=?,updated=?,lease_until=NULL "
                         "WHERE id=?", (encoded, finished, execution_id))
            self._event(conn, row, "succeeded", finished, {"receipt": receipt})
            if on_terminal is not None:
                on_terminal(conn, self._public(conn.execute(
                    "SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone()))
            return receipt

    def mark_unknown(self, execution_id, worker, fence, *, reason):
        """Controller records immediate uncertainty without freeing its slot."""
        token(reason)
        now = self._now()
        with self._connection(write=True) as conn:
            row = self._owned(conn, execution_id, worker, fence, now)
            conn.execute("UPDATE code_executions SET status='unknown',reason=?,updated=? WHERE id=?",
                         (reason, now, execution_id))
            self._event(conn, row, "unknown", now, {"reason": reason})

    def reconcile(self, execution_id, worker, fence, *, outcome, evidence: dict, on_reconciled=None):
        """Controller-only reconciliation; a caller must supply real stop/start evidence.

        A dead CLI, elapsed clock or missing heartbeat is not such evidence. This
        API intentionally cannot convert uncertainty into a successful result.
        on_reconciled is a trusted same-connection journal write, never external
        I/O. Its failure rolls back the queue transition and outbox together.
        """
        identifier(execution_id)
        token(worker)
        bounded_integer(fence, 1, 2**63 - 1, "fence")
        if on_reconciled is not None and not callable(on_reconciled):
            raise ValueError("execution_reconciliation_callback_required")
        if outcome not in {"not_started", "stopped", "cancelled"} or not evidence:
            raise ValueError("execution_reconciliation_evidence_required")
        encoded = json_object(evidence)
        now = self._now()
        with self._connection(write=True) as conn:
            row = conn.execute("SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone()
            if row is None or row["status"] != "unknown" or row["worker"] != worker or row["fence"] != fence:
                raise ExecutionLeaseLost("execution_reconciliation_fence_conflict")
            cancelled = outcome == "cancelled" or row["reason"] in {
                "user_cancel", "cancel_worker_lease_expired", "user_cancel_unknown", "authorization_revoked"}
            status = ("cancelled" if cancelled else
                      ("queued" if row["deadline"] > now else "timed_out") if outcome == "not_started" else "failed")
            conn.execute("UPDATE code_executions SET status=?,fence=fence+1,worker='',lease_until=NULL,"
                "receipt=?,updated=?,reason=? WHERE id=?",
                (status, encoded, now, "reconciled_" + outcome, execution_id))
            self._event(conn, row, "reconciled", now, {"outcome": outcome, "status": status, "evidence": evidence})
            current = self._public(conn.execute("SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone())
            if on_reconciled is not None:
                on_reconciled(conn, current)
            return current

    def revoke_owner(self, owner):
        """Revoke queued work and request confirmed stop for already claimed work.

        Call after durable account admission closes. An uncertain execution
        retains its slot until controller reconciliation, even without a lease.
        """
        if type(owner) is not int or owner <= 0:
            raise ValueError("execution_owner_required")
        now = self._now()
        with self._connection(write=True) as conn:
            rows = conn.execute("SELECT * FROM code_executions WHERE owner=? AND status IN "
                "('queued','preparing','running','collecting','cancel_requested','unknown')", (owner,)).fetchall()
            for row in rows:
                status = ("cancelled" if row["status"] == "queued" else
                          "unknown" if row["status"] == "unknown" else "cancel_requested")
                conn.execute("UPDATE code_executions SET status=?,reason='authorization_revoked',updated=? WHERE id=?",
                             (status, now, row["id"]))
                self._event(conn, row, "authorization_revoked", now, {"status": status})
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_execution_waits'").fetchone():
                conn.execute("UPDATE code_execution_waits SET state='revoked',claim_token='',claim_until=0,updated=? WHERE owner=?",
                             (now, owner))
            return sum(row["status"] != "queued" for row in rows)

    def events(self, *, after=0, limit=50):
        """Internal completion outbox. Public callers use an authorized execution view."""
        bounded_integer(after, 0, 2**63 - 1, "event_cursor")
        bounded_integer(limit, 1, 200, "event_limit")
        with self._connection() as conn:
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in conn.execute(
                "SELECT * FROM code_events WHERE sequence>? AND delivered=0 ORDER BY sequence LIMIT ?",
                (after, limit))]

    def acknowledge(self, sequence):
        bounded_integer(sequence, 1, 2**63 - 1, "event_cursor")
        with self._connection(write=True) as conn:
            conn.execute("UPDATE code_events SET delivered=1 WHERE sequence=?", (sequence,))


def cancel_owner_executions(owner: int) -> None:
    """Account-deletion hook: never create a queue merely to delete an account."""
    from src import config
    from src.core.account_leases import AccountDeletionPending
    path = config.PROJECT_ROOT / "state" / "code_executions.sqlite3"
    if not path.exists():
        return
    pending = ExecutionRepository(path).revoke_owner(owner)
    if pending:
        raise AccountDeletionPending("account still has unconfirmed code executions")

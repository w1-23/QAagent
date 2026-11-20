"""Durable per-session independent chat turn queue on the sessions database.

Contract: ``docs/plans/8043-durable-turn-queue-contract.md`` (baseline
``624c252124a1efa5dcdda257fa366dc6f10ec664``).

This repository is deliberately narrow. The A2 host has already authenticated
the owner/session, bounded the request, validated upload bytes and resolved
immutable references; it passes only that bounded payload here. This module
persists the admission, allocates a stable per-session sequence, fences the
single-agent gate with compare-and-swap transitions, and exposes owner scoped
reads. It never stores the raw browser request, tokens, cookies or host paths,
never uploads or decodes attachments, never starts/stops an Agent, and never
performs execution.

Layering note: ``task_jobs.status='queued'`` is the durable task row. The queue
record's ``queued/claimed/running/...`` states are a separate layer. Only a
persisted dispatch intent (``claimed -> running`` with ``dispatch_at``) makes a
turn unsafe to replay automatically; a queued or claimed record with no intent
can be released back to the queue in original order.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime

from src.core import jobs


QUEUED = "queued"
CLAIMED = "claimed"
RUNNING = "running"
AWAITING_INPUT = "awaiting_input"
SETTLING = "settling"
TERMINAL = "terminal"
RECONCILE_REQUIRED = "reconcile_required"

NONTERMINAL_STATES = frozenset({
    QUEUED, CLAIMED, RUNNING, AWAITING_INPUT, SETTLING, RECONCILE_REQUIRED,
})
TERMINAL_STATES = frozenset({TERMINAL})

# Same statuses the legacy navigation/recovery surfaces treat as live facts.
LEGACY_OPEN_JOB_STATUSES = (
    "running", "detached", "cancel_requested", "queued", "awaiting_input",
)

CANCELLED_REASON = "cancelled"

DEFAULT_MAX_PENDING_PER_SESSION = 10
DEFAULT_MAX_PENDING_PER_OWNER = 10
DEFAULT_MAX_QUEUED_BYTES_PER_SESSION = 64 * 1024 * 1024
DEFAULT_MAX_QUEUED_BYTES_PER_OWNER = 64 * 1024 * 1024
DEFAULT_MAX_PAYLOAD_BYTES = 512 * 1024
MAX_TERMINAL_EVIDENCE_BYTES = 64 * 1024
MAX_CLIENT_REQUEST_ID_LENGTH = 64
MAX_SESSION_ID_LENGTH = 200
MAX_REQUEST_ID_LENGTH = 128
MAX_LIST_LIMIT = 200

_NONTERMINAL_SQL_STATES = sorted(NONTERMINAL_STATES)
_NONTERMINAL_PLACEHOLDERS = ",".join("?" for _ in _NONTERMINAL_SQL_STATES)
_LEGACY_STATUS_PLACEHOLDERS = ",".join("?" for _ in LEGACY_OPEN_JOB_STATUSES)


# ---------------------------------------------------------------------------
# Errors. HTTP adapters map ``code`` / the class to 404/409; no error body is
# built here and no sensitive payload is echoed.
# ---------------------------------------------------------------------------

class TurnQueueError(ValueError):
    code = "turn_queue_error"

    def __init__(self, detail: str = "", *, job_id: str | None = None):
        self.detail = str(detail or "")
        self.job_id = job_id
        super().__init__(self.detail or self.code)


class TurnPayloadConflict(TurnQueueError):
    """Same owner-level client ID already identifies a different request."""

    code = "turn_payload_conflict"


class TurnQueueFull(TurnQueueError):
    """Pending count or queued attachment bytes would exceed a limit."""

    code = "turn_queue_full"


class TurnNotQueued(TurnQueueError):
    """The turn is no longer in the cancellable queued state."""

    code = "turn_not_queued"


class TurnReconcileRequired(TurnQueueError):
    """A dispatch/cleanup outcome is unknown; automatic successors stop."""

    code = "turn_reconcile_required"


class TurnQueueNotReady(TurnQueueError):
    """The session head cannot be claimed (busy, generation or legacy gate)."""

    code = "turn_queue_not_ready"


class TurnNotFound(TurnQueueError):
    """Missing under the authenticated owner/session (HTTP 404 isolation)."""

    code = "turn_not_found"


class TurnStateConflict(TurnQueueError):
    """A CAS transition was requested from an incompatible state."""

    code = "turn_state_conflict"


# ---------------------------------------------------------------------------
# Admission limits and validation.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QueueLimits:
    """Bounded admission limits supplied by the A2 capacity controller."""

    max_pending_per_session: int = DEFAULT_MAX_PENDING_PER_SESSION
    max_pending_per_owner: int = DEFAULT_MAX_PENDING_PER_OWNER
    max_queued_bytes_per_session: int = DEFAULT_MAX_QUEUED_BYTES_PER_SESSION
    max_queued_bytes_per_owner: int = DEFAULT_MAX_QUEUED_BYTES_PER_OWNER
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES

    def __post_init__(self) -> None:
        for name in (
            "max_pending_per_session",
            "max_pending_per_owner",
            "max_queued_bytes_per_session",
            "max_queued_bytes_per_owner",
            "max_payload_bytes",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


def _validate_identity(owner_user_id, session_id) -> None:
    if type(owner_user_id) is not int or owner_user_id <= 0:
        raise ValueError("owner_user_id must be a positive integer")
    if (not isinstance(session_id, str) or not session_id.strip()
            or len(session_id) > MAX_SESSION_ID_LENGTH):
        raise ValueError("session_id must be a non-empty bounded string")


def _validate_client_request_id(client_request_id) -> None:
    if (not isinstance(client_request_id, str) or not client_request_id.strip()
            or len(client_request_id) > MAX_CLIENT_REQUEST_ID_LENGTH):
        raise ValueError(
            "client_request_id must contain 1 to "
            f"{MAX_CLIENT_REQUEST_ID_LENGTH} characters")


def _validate_non_negative_int(value, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _request_id(value) -> str:
    if value in (None, ""):
        return uuid.uuid4().hex
    if not isinstance(value, str) or len(value) > MAX_REQUEST_ID_LENGTH:
        raise ValueError("request_id must be a bounded string")
    return value


def canonical_payload_digest(*, session_id: str, bounded_payload: dict,
                             attachment_bytes: int = 0) -> str:
    """Stable sorted-JSON fingerprint for idempotency and conflict checks.

    The session and attachment byte count are part of the fingerprint: the same
    owner-level client ID may not silently move to another session, another
    payload, or another ordered attachment set.
    """
    try:
        material = json.dumps(
            {
                "session_id": session_id,
                "attachment_bytes": int(attachment_bytes),
                "payload": bounded_payload,
            },
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("bounded_payload must be canonical JSON") from exc
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _encode_bounded_payload(bounded_payload, max_bytes: int) -> str:
    if not isinstance(bounded_payload, dict):
        raise ValueError("bounded_payload must be an object")
    try:
        encoded = json.dumps(
            bounded_payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("bounded_payload must be canonical JSON") from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("bounded_payload exceeds the configured byte limit")
    return encoded


def _encode_evidence(evidence) -> str:
    if evidence is None:
        evidence = {}
    if not isinstance(evidence, dict):
        raise ValueError("terminal_evidence must be an object")
    try:
        encoded = json.dumps(
            evidence, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("terminal_evidence must be canonical JSON") from exc
    if len(encoded.encode("utf-8")) > MAX_TERMINAL_EVIDENCE_BYTES:
        raise ValueError("terminal_evidence exceeds the configured byte limit")
    return encoded


def _decode_json(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Schema and short atomic transactions on the existing sessions database.
# ---------------------------------------------------------------------------

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS chat_turn_queue (
        owner_user_id INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        client_request_id TEXT NOT NULL,
        root_job_id TEXT NOT NULL UNIQUE,
        queue_seq INTEGER NOT NULL,
        state TEXT NOT NULL,
        generation INTEGER NOT NULL DEFAULT 0,
        active_child_job_id TEXT NOT NULL DEFAULT '',
        account_epoch INTEGER NOT NULL DEFAULT 0,
        dispatch_at REAL NOT NULL DEFAULT 0,
        accepted_at REAL NOT NULL,
        started_at REAL NOT NULL DEFAULT 0,
        finished_at REAL NOT NULL DEFAULT 0,
        payload_digest TEXT NOT NULL,
        bounded_payload TEXT NOT NULL,
        attachment_bytes INTEGER NOT NULL DEFAULT 0,
        clear_reason TEXT NOT NULL DEFAULT '',
        history_saved INTEGER,
        terminal_evidence TEXT NOT NULL DEFAULT '',
        updated_at REAL NOT NULL,
        PRIMARY KEY (owner_user_id, client_request_id),
        UNIQUE (owner_user_id, session_id, queue_seq)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chat_turn_queue_session "
    "ON chat_turn_queue(owner_user_id, session_id, state, queue_seq)",
    "CREATE INDEX IF NOT EXISTS idx_chat_turn_queue_child "
    "ON chat_turn_queue(active_child_job_id)",
    """CREATE TABLE IF NOT EXISTS chat_turn_queue_seq (
        owner_user_id INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        last_seq INTEGER NOT NULL,
        PRIMARY KEY (owner_user_id, session_id)
    )""",
)


def init_schema(conn) -> None:
    """Idempotent queue schema. Callers hold a write transaction.

    Two fresh processes may migrate the same legacy database together; the
    ''BEGIN IMMEDIATE'' in ``_transaction`` serializes these CREATE statements
    and the unique sequence allocator keeps monotonicity across restarts.
    """
    for statement in _SCHEMA:
        conn.execute(statement)


@contextmanager
def _transaction():
    """Short write transaction on the same sessions DB used by ``task_jobs``."""
    with closing(jobs._connect()) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        init_schema(conn)
        yield conn


def ensure_schema() -> None:
    """Open one short transaction so admission can rely on the queue tables."""
    with _transaction():
        pass


# ---------------------------------------------------------------------------
# Views and owner-scoped reads.
# ---------------------------------------------------------------------------

def _decode(row, *, include_payload: bool = False) -> dict:
    view = {
        "owner_user_id": row["owner_user_id"],
        "session_id": row["session_id"],
        "client_request_id": row["client_request_id"],
        "job_id": row["root_job_id"],
        "root_job_id": row["root_job_id"],
        "queue_seq": row["queue_seq"],
        "state": row["state"],
        "generation": row["generation"],
        "active_child_job_id": row["active_child_job_id"],
        "account_epoch": row["account_epoch"],
        "dispatch_at": row["dispatch_at"],
        "accepted_at": row["accepted_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "payload_digest": row["payload_digest"],
        "attachment_bytes": row["attachment_bytes"],
        "clear_reason": row["clear_reason"],
        "history_saved": (None if row["history_saved"] is None
                          else bool(row["history_saved"])),
        "terminal_evidence": _decode_json(row["terminal_evidence"], {}),
        "updated_at": row["updated_at"],
    }
    if include_payload:
        view["bounded_payload"] = _decode_json(row["bounded_payload"], {})
    return view


def _position(conn, owner_user_id: int, session_id: str, queue_seq: int,
              state: str) -> int:
    """1-based rank among this session's queued (not yet claimed) turns."""
    if state != QUEUED:
        return 0
    row = conn.execute(
        "SELECT COUNT(*) FROM chat_turn_queue WHERE owner_user_id=? "
        "AND session_id=? AND state=? AND queue_seq<=?",
        (owner_user_id, session_id, QUEUED, queue_seq),
    ).fetchone()
    return int(row[0])


def _with_position(conn, view: dict) -> dict:
    view["queue_position"] = _position(
        conn, view["owner_user_id"], view["session_id"],
        view["queue_seq"], view["state"])
    return view


def public_view(turn: dict) -> dict:
    """The bounded HTTP surface; URLs are added by the API adapter."""
    keys = (
        "session_id", "client_request_id", "job_id", "queue_seq",
        "queue_position", "state", "active_child_job_id", "dispatch_at",
        "started_at", "finished_at", "history_saved", "clear_reason",
    )
    return {key: turn[key] for key in keys if key in turn}


def _scoped(conn, owner_user_id, session_id, client_request_id):
    return conn.execute(
        "SELECT * FROM chat_turn_queue WHERE owner_user_id=? AND session_id=? "
        "AND client_request_id=?",
        (owner_user_id, session_id, client_request_id),
    ).fetchone()


def _require_root_job(conn, row):
    job = conn.execute("SELECT * FROM task_jobs WHERE job_id=?",
                       (row["root_job_id"],)).fetchone()
    if (job is None
            or job["owner_user_id"] != row["owner_user_id"]
            or job["session_id"] != row["session_id"]
            or job["client_request_id"] != row["client_request_id"]):
        raise TurnNotFound("turn_not_found", job_id=row["root_job_id"])
    return job


def _legacy_blocker(conn, owner_user_id: int, session_id: str):
    """Open facts not managed by this queue that still gate the session.

    A closed question segment keeps its immutable awaiting_input job status.
    Once answered, its validated child carries the outstanding work instead.
    Never suppress an open child, unresolved question or Code reconciliation.
    """
    row = conn.execute(
        "SELECT j.job_id,j.status,j.current_step FROM task_jobs j "
        "WHERE j.owner_user_id=? AND j.session_id=? "
        "AND j.job_id NOT IN (SELECT root_job_id FROM chat_turn_queue) "
        "AND (j.current_step='code_reconciliation_required' OR (j.status IN ("
        + _LEGACY_STATUS_PLACEHOLDERS + ") AND NOT (j.status='awaiting_input' AND EXISTS("
        "SELECT 1 FROM task_questions q LEFT JOIN task_jobs c ON c.job_id=q.child_job_id "
        "AND c.question_id=q.id AND c.parent_job_id=q.job_id "
        "AND c.owner_user_id=q.owner_user_id AND c.session_id=q.session_id "
        "WHERE q.job_id=j.job_id AND q.owner_user_id=j.owner_user_id AND q.session_id=j.session_id "
        "AND ((q.status IN ('cancelled','expired') AND q.child_job_id='') "
        "OR (q.status='answered' AND c.job_id IS NOT NULL)))))) "
        "ORDER BY j.created_at LIMIT 1",
        (owner_user_id, session_id, *LEGACY_OPEN_JOB_STATUSES),
    ).fetchone()
    if row is not None:
        return {
            "kind": "legacy_job",
            "job_id": row["job_id"],
            "status": row["status"],
            "current_step": row["current_step"],
        }
    question = conn.execute(
        "SELECT id,job_id FROM task_questions WHERE owner_user_id=? "
        "AND session_id=? AND status='pending' AND expires_at>? LIMIT 1",
        (owner_user_id, session_id, time.time()),
    ).fetchone()
    if question is not None:
        return {
            "kind": "pending_question",
            "question_id": question["id"],
            "job_id": question["job_id"],
        }
    return None


def _head(conn, owner_user_id: int, session_id: str):
    return conn.execute(
        "SELECT * FROM chat_turn_queue WHERE owner_user_id=? AND session_id=? "
        "AND state IN (" + _NONTERMINAL_PLACEHOLDERS + ") "
        "ORDER BY queue_seq LIMIT 1",
        (owner_user_id, session_id, *_NONTERMINAL_SQL_STATES),
    ).fetchone()


# ---------------------------------------------------------------------------
# Admission.
# ---------------------------------------------------------------------------

def enqueue_turn(*, owner_user_id: int, session_id: str,
                 client_request_id: str, bounded_payload: dict,
                 request_id: str = "", attachment_bytes: int = 0,
                 account_epoch: int = 0,
                 limits: QueueLimits | None = None) -> dict:
    """Admit one independent turn: root job ``queued`` + queue row, atomically.

    The caller passes only A2-verified bounded execution data (frozen text,
    explicit settings, immutable input refs). No raw browser request, blob,
    cookie, key or host path is accepted or stored here.

    Idempotency is checked before limits. A replay with the same owner-level
    client ID, session and canonical fingerprint returns the original root job
    without consuming quota or writing again. A changed session, payload or
    attachment byte count raises ``TurnPayloadConflict``.
    """
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    _validate_non_negative_int(attachment_bytes, "attachment_bytes")
    _validate_non_negative_int(account_epoch, "account_epoch")
    if not isinstance(bounded_payload, dict):
        raise ValueError("bounded_payload must be an object")
    if limits is None:
        limits = QueueLimits()
    if not isinstance(limits, QueueLimits):
        raise ValueError("limits must be a QueueLimits instance")
    request_id_value = _request_id(request_id)

    with _transaction() as conn:
        existing = conn.execute(
            "SELECT * FROM chat_turn_queue WHERE owner_user_id=? "
            "AND client_request_id=?",
            (owner_user_id, client_request_id),
        ).fetchone()
        if existing is not None:
            digest = canonical_payload_digest(
                session_id=session_id, bounded_payload=bounded_payload,
                attachment_bytes=attachment_bytes)
            if (existing["session_id"] != session_id
                    or existing["payload_digest"] != digest):
                raise TurnPayloadConflict(
                    "turn_payload_conflict", job_id=existing["root_job_id"])
            _require_root_job(conn, existing)
            view = _with_position(conn, _decode(existing, include_payload=True))
            view["created"] = False
            return view

        # A key already bound by the legacy admission path cannot be adopted.
        legacy = conn.execute(
            "SELECT job_id FROM task_jobs WHERE owner_user_id=? "
            "AND client_request_id=?",
            (owner_user_id, client_request_id),
        ).fetchone()
        if legacy is not None:
            raise TurnPayloadConflict("turn_payload_conflict",
                                      job_id=legacy["job_id"])

        encoded_payload = _encode_bounded_payload(
            bounded_payload, limits.max_payload_bytes)
        digest = canonical_payload_digest(
            session_id=session_id, bounded_payload=bounded_payload,
            attachment_bytes=attachment_bytes)

        pending_session = conn.execute(
            "SELECT COUNT(*) FROM chat_turn_queue WHERE owner_user_id=? "
            "AND session_id=? AND state IN (" + _NONTERMINAL_PLACEHOLDERS + ")",
            (owner_user_id, session_id, *_NONTERMINAL_SQL_STATES),
        ).fetchone()[0]
        if pending_session >= limits.max_pending_per_session:
            raise TurnQueueFull("session_pending_limit")
        pending_owner = conn.execute(
            "SELECT COUNT(*) FROM chat_turn_queue WHERE owner_user_id=? "
            "AND state IN (" + _NONTERMINAL_PLACEHOLDERS + ")",
            (owner_user_id, *_NONTERMINAL_SQL_STATES),
        ).fetchone()[0]
        if pending_owner >= limits.max_pending_per_owner:
            raise TurnQueueFull("owner_pending_limit")
        queued_bytes_session = conn.execute(
            "SELECT COALESCE(SUM(attachment_bytes),0) FROM chat_turn_queue "
            "WHERE owner_user_id=? AND session_id=? AND state=?",
            (owner_user_id, session_id, QUEUED),
        ).fetchone()[0]
        if queued_bytes_session + attachment_bytes > limits.max_queued_bytes_per_session:
            raise TurnQueueFull("session_attachment_bytes_limit")
        queued_bytes_owner = conn.execute(
            "SELECT COALESCE(SUM(attachment_bytes),0) FROM chat_turn_queue "
            "WHERE owner_user_id=? AND state=?",
            (owner_user_id, QUEUED),
        ).fetchone()[0]
        if queued_bytes_owner + attachment_bytes > limits.max_queued_bytes_per_owner:
            raise TurnQueueFull("owner_attachment_bytes_limit")

        seq_row = conn.execute(
            "SELECT last_seq FROM chat_turn_queue_seq WHERE owner_user_id=? "
            "AND session_id=?",
            (owner_user_id, session_id),
        ).fetchone()
        queue_seq = (int(seq_row["last_seq"]) if seq_row is not None else 0) + 1
        conn.execute(
            "INSERT INTO chat_turn_queue_seq(owner_user_id,session_id,last_seq) "
            "VALUES (?,?,?) ON CONFLICT(owner_user_id,session_id) "
            "DO UPDATE SET last_seq=excluded.last_seq",
            (owner_user_id, session_id, queue_seq),
        )

        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        now_iso = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO task_jobs (job_id, session_id, request_id, job_type, "
            "status, current_step, progress_summary, result_path, error, "
            "created_at, updated_at, finished_at, owner_user_id, "
            "client_request_id, payload_hash, parent_job_id, question_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, session_id, request_id_value, "chat", QUEUED,
             "turn_queue", "", "", "", now_iso, now_iso, "",
             owner_user_id, client_request_id, digest, "", ""),
        )
        conn.execute(
            "INSERT INTO chat_turn_queue (owner_user_id, session_id, "
            "client_request_id, root_job_id, queue_seq, state, generation, "
            "active_child_job_id, account_epoch, dispatch_at, accepted_at, "
            "started_at, finished_at, payload_digest, bounded_payload, "
            "attachment_bytes, clear_reason, history_saved, terminal_evidence, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (owner_user_id, session_id, client_request_id, job_id, queue_seq,
             QUEUED, 0, "", account_epoch, 0.0, now, 0.0, 0.0, digest,
             encoded_payload, attachment_bytes, "", None, "", now),
        )
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        view = _with_position(conn, _decode(row, include_payload=True))
        view["created"] = True
        return view


# ---------------------------------------------------------------------------
# Reads (owner + session scoped; never a cross-owner guess).
# ---------------------------------------------------------------------------

def get_turn(*, owner_user_id: int, session_id: str,
             client_request_id: str) -> dict | None:
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            return None
        return _with_position(conn, _decode(row, include_payload=True))


def find_turn_by_job(*, owner_user_id: int, session_id: str,
                     job_id: str) -> dict | None:
    """Resolve a root job or an active continuation child to the same turn."""
    _validate_identity(owner_user_id, session_id)
    if not isinstance(job_id, str) or not job_id:
        return None
    with _transaction() as conn:
        row = conn.execute(
            "SELECT * FROM chat_turn_queue WHERE owner_user_id=? "
            "AND session_id=? AND root_job_id=?",
            (owner_user_id, session_id, job_id),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM chat_turn_queue WHERE owner_user_id=? "
                "AND session_id=? AND active_child_job_id=? "
                "ORDER BY queue_seq DESC LIMIT 1",
                (owner_user_id, session_id, job_id),
            ).fetchone()
        if row is None:
            return None
        return _with_position(conn, _decode(row))


def list_turns(*, owner_user_id: int, session_id: str, limit: int = 50,
               include_terminal: bool = True, include_active: bool = True,
               after_seq: int | None = None) -> list[dict]:
    """Authoritative session view; active turns can never be hidden by a limit.

    Every non-terminal turn is always returned when ``include_active`` is true,
    regardless of ``limit``. Terminal turns are a bounded window:

    * ``after_seq is None`` -- the most recent ``limit`` terminal turns (the
      restore-queue bubble view).
    * ``after_seq is not None`` -- a forward cursor: terminal turns with
      ``queue_seq > after_seq`` in ascending order. The stable next cursor is
      the ``queue_seq`` of the last returned terminal turn.

    Admission limits bound the active set, so this stays a bounded read.
    """
    _validate_identity(owner_user_id, session_id)
    if type(limit) is not int or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if after_seq is not None and (type(after_seq) is not int or after_seq < 0):
        raise ValueError("after_seq must be a non-negative integer or None")
    limit = min(limit, MAX_LIST_LIMIT)
    with _transaction() as conn:
        turns = []
        if include_active:
            turns.extend(
                _with_position(conn, _decode(row))
                for row in conn.execute(
                    "SELECT * FROM chat_turn_queue WHERE owner_user_id=? "
                    "AND session_id=? AND state IN ("
                    + _NONTERMINAL_PLACEHOLDERS + ") ORDER BY queue_seq ASC",
                    (owner_user_id, session_id, *_NONTERMINAL_SQL_STATES))
            )
        if include_terminal and limit:
            if after_seq is None:
                rows = list(conn.execute(
                    "SELECT * FROM chat_turn_queue WHERE owner_user_id=? "
                    "AND session_id=? AND state=? ORDER BY queue_seq DESC "
                    "LIMIT ?",
                    (owner_user_id, session_id, TERMINAL, limit)))
                rows.reverse()
            else:
                rows = list(conn.execute(
                    "SELECT * FROM chat_turn_queue WHERE owner_user_id=? "
                    "AND session_id=? AND state=? AND queue_seq>? "
                    "ORDER BY queue_seq ASC LIMIT ?",
                    (owner_user_id, session_id, TERMINAL, after_seq, limit)))
            turns.extend(_with_position(conn, _decode(row)) for row in rows)
        turns.sort(key=lambda turn: turn["queue_seq"])
        return turns


def scan_open_turns(*, limit: int = 100,
                    after: tuple[float, str] | None = None) -> dict:
    """Trusted bounded cross-session scan for startup reconciliation.

    This is an internal lifespan/background API, never an HTTP view: it returns
    non-terminal turns across owners so one consumer can reconcile them. It
    excludes ``bounded_payload`` and must be called only by the trusted server
    consumer after its own authorization review. ``after`` is the opaque cursor
    returned by the previous call; ordering is durable acceptance order with a
    root-job tiebreak, so it is stable across restarts and does not depend on
    per-session queue sequence.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    limit = min(limit, MAX_LIST_LIMIT)
    accepted_after, job_after = (0.0, "") if after is None else after
    if type(accepted_after) not in (int, float) or accepted_after < 0:
        raise ValueError("after cursor is invalid")
    if not isinstance(job_after, str):
        raise ValueError("after cursor is invalid")
    with _transaction() as conn:
        rows = list(conn.execute(
            "SELECT * FROM chat_turn_queue WHERE state IN ("
            + _NONTERMINAL_PLACEHOLDERS + ") "
            "AND (accepted_at>? OR (accepted_at=? AND root_job_id>?)) "
            "ORDER BY accepted_at ASC, root_job_id ASC LIMIT ?",
            (*_NONTERMINAL_SQL_STATES, accepted_after, accepted_after,
             job_after, limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]
    turns = []
    for row in rows:
        view = _decode(row)
        view["dispatch_phase"] = dispatch_phase(view)
        turns.append(view)
    next_cursor = None
    if has_more and rows:
        next_cursor = (rows[-1]["accepted_at"], rows[-1]["root_job_id"])
    return {"turns": turns, "next_cursor": next_cursor, "has_more": has_more}


def queue_position(*, owner_user_id: int, session_id: str,
                   client_request_id: str) -> int:
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        return _position(conn, owner_user_id, session_id, row["queue_seq"],
                         row["state"])


def session_blocker(*, owner_user_id: int, session_id: str) -> dict | None:
    """Describe the fact that currently prevents a successor from starting.

    Reconcile-required turns block the whole session regardless of position.
    The lowest non-terminal turn blocks while it is not queued. Legacy open
    jobs and pending questions are returned as shields. ``None`` means the
    queued head may be claimed.
    """
    _validate_identity(owner_user_id, session_id)
    with _transaction() as conn:
        reconcile = conn.execute(
            "SELECT * FROM chat_turn_queue WHERE owner_user_id=? "
            "AND session_id=? AND state=? ORDER BY queue_seq LIMIT 1",
            (owner_user_id, session_id, RECONCILE_REQUIRED),
        ).fetchone()
        if reconcile is not None:
            return {"kind": "reconcile_required",
                    "turn": _decode(reconcile)}
        head = _head(conn, owner_user_id, session_id)
        if head is not None and head["state"] != QUEUED:
            return {"kind": head["state"], "turn": _decode(head)}
        legacy = _legacy_blocker(conn, owner_user_id, session_id)
        if legacy is not None:
            return legacy
        return None


# ---------------------------------------------------------------------------
# CAS transitions. Every caller passes the generation it observed.
# ---------------------------------------------------------------------------

def _check_generation(row, generation) -> None:
    if type(generation) is not int or generation < 0:
        raise ValueError("generation must be a non-negative integer")
    if row["generation"] != generation:
        raise TurnQueueNotReady("generation_conflict",
                                job_id=row["root_job_id"])


def _reread(conn, owner_user_id, session_id, client_request_id) -> dict:
    row = _scoped(conn, owner_user_id, session_id, client_request_id)
    if row is None:
        raise TurnNotFound("turn_not_found")
    return _with_position(conn, _decode(row))


def claim_head(*, owner_user_id: int, session_id: str,
               expected_generation: int | None = None) -> dict:
    """CAS the session's lowest outstanding queued turn into ``claimed``.

    Refuses when any reconcile-required turn exists, when the head is already
    claimed/running/awaiting/settling, or when a legacy open job/pending
    question still shields the session. The root ``task_jobs`` row must still
    be ``queued``: this is the shared fence against a second Agent.
    """
    _validate_identity(owner_user_id, session_id)
    with _transaction() as conn:
        reconcile = conn.execute(
            "SELECT * FROM chat_turn_queue WHERE owner_user_id=? "
            "AND session_id=? AND state=? ORDER BY queue_seq LIMIT 1",
            (owner_user_id, session_id, RECONCILE_REQUIRED),
        ).fetchone()
        if reconcile is not None:
            raise TurnReconcileRequired("reconcile_required",
                                        job_id=reconcile["root_job_id"])
        head = _head(conn, owner_user_id, session_id)
        if head is None:
            raise TurnQueueNotReady("no_queued_turn")
        if head["state"] != QUEUED:
            raise TurnQueueNotReady(head["state"], job_id=head["root_job_id"])
        legacy = _legacy_blocker(conn, owner_user_id, session_id)
        if legacy is not None:
            raise TurnQueueNotReady("session_blocked",
                                    job_id=legacy.get("job_id"))
        generation = head["generation"]
        if expected_generation is not None and expected_generation != generation:
            raise TurnQueueNotReady("generation_conflict",
                                    job_id=head["root_job_id"])
        job = _require_root_job(conn, head)
        if job["status"] != QUEUED:
            raise TurnQueueNotReady("root_job_not_queued",
                                    job_id=head["root_job_id"])
        now = time.time()
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "updated_at=? WHERE owner_user_id=? AND session_id=? "
            "AND client_request_id=? AND state=? AND generation=?",
            (CLAIMED, now, owner_user_id, session_id,
             head["client_request_id"], QUEUED, generation),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("claim_lost", job_id=head["root_job_id"])
        return _reread(conn, owner_user_id, session_id,
                       head["client_request_id"])


def release_claim(*, owner_user_id: int, session_id: str,
                  client_request_id: str, generation: int) -> dict:
    """Return a claimed-but-undispatched turn to its original queue position."""
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        if row["dispatch_at"]:
            raise TurnNotQueued("dispatch_intent_recorded",
                                job_id=row["root_job_id"])
        if row["state"] != CLAIMED:
            raise TurnNotQueued(row["state"], job_id=row["root_job_id"])
        _check_generation(row, generation)
        now = time.time()
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "updated_at=? WHERE owner_user_id=? AND session_id=? "
            "AND client_request_id=? AND state=? AND generation=? "
            "AND dispatch_at=0",
            (QUEUED, now, owner_user_id, session_id, client_request_id,
             CLAIMED, generation),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("release_lost", job_id=row["root_job_id"])
        return _reread(conn, owner_user_id, session_id, client_request_id)


def mark_dispatch_intent(*, owner_user_id: int, session_id: str,
                         client_request_id: str, generation: int,
                         current_step: str = "dispatched") -> dict:
    """Persist the dispatch fence *before* any Agent or side-effect segment.

    The root task row moves ``queued -> running`` in the same transaction as
    the queue record ``claimed -> running``. Once ``dispatch_at`` is set the
    turn is frozen: it must never be automatically replayed.
    """
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    if not isinstance(current_step, str) or not current_step.strip() \
            or len(current_step) > 64:
        raise ValueError("current_step must be a bounded non-empty string")
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        if row["state"] != CLAIMED:
            raise TurnNotQueued(row["state"], job_id=row["root_job_id"])
        _check_generation(row, generation)
        job = _require_root_job(conn, row)
        if job["status"] != QUEUED:
            raise TurnQueueNotReady("root_job_not_queued",
                                    job_id=row["root_job_id"])
        now = time.time()
        now_iso = datetime.now().isoformat()
        job_cursor = conn.execute(
            "UPDATE task_jobs SET status=?, current_step=?, updated_at=? "
            "WHERE job_id=? AND status=? AND owner_user_id=? AND session_id=?",
            (RUNNING, current_step, now_iso, row["root_job_id"], QUEUED,
             owner_user_id, session_id),
        )
        if job_cursor.rowcount != 1:
            raise TurnQueueNotReady("root_job_not_queued",
                                    job_id=row["root_job_id"])
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "dispatch_at=?, updated_at=? WHERE owner_user_id=? AND session_id=? "
            "AND client_request_id=? AND state=? AND generation=?",
            (RUNNING, now, now, owner_user_id, session_id, client_request_id,
             CLAIMED, generation),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("dispatch_lost", job_id=row["root_job_id"])
        return _reread(conn, owner_user_id, session_id, client_request_id)


def mark_started(*, owner_user_id: int, session_id: str,
                 client_request_id: str, generation: int) -> dict:
    """Record the actual execution start time without changing ownership."""
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        if row["state"] != RUNNING:
            raise TurnNotQueued(row["state"], job_id=row["root_job_id"])
        _check_generation(row, generation)
        if not row["dispatch_at"]:
            raise TurnStateConflict("dispatch_intent_missing",
                                    job_id=row["root_job_id"])
        if row["started_at"]:
            return _with_position(conn, _decode(row))
        now = time.time()
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET started_at=?, updated_at=? "
            "WHERE owner_user_id=? AND session_id=? AND client_request_id=? "
            "AND state=? AND generation=? AND dispatch_at>0 AND started_at=0",
            (now, now, owner_user_id, session_id, client_request_id, RUNNING,
             generation),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("start_marker_lost",
                                    job_id=row["root_job_id"])
        return _reread(conn, owner_user_id, session_id, client_request_id)


def mark_awaiting_input(*, owner_user_id: int, session_id: str,
                        client_request_id: str, generation: int,
                        active_child_job_id: str = "") -> dict:
    """CAS ``running -> awaiting_input``; the original turn stays unresolved."""
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    _validate_child_id(active_child_job_id)
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        if row["state"] == AWAITING_INPUT:
            if active_child_job_id in ("", row["active_child_job_id"]):
                return _with_position(conn, _decode(row))
            raise TurnStateConflict("active_child_conflict",
                                    job_id=row["root_job_id"])
        if row["state"] != RUNNING:
            raise TurnNotQueued(row["state"], job_id=row["root_job_id"])
        _check_generation(row, generation)
        if not row["dispatch_at"]:
            raise TurnStateConflict("dispatch_intent_missing",
                                    job_id=row["root_job_id"])
        now = time.time()
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "active_child_job_id=?, updated_at=? WHERE owner_user_id=? "
            "AND session_id=? AND client_request_id=? AND state=? AND generation=?",
            (AWAITING_INPUT, active_child_job_id, now, owner_user_id,
             session_id, client_request_id, RUNNING, generation),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("awaiting_lost", job_id=row["root_job_id"])
        return _reread(conn, owner_user_id, session_id, client_request_id)


def resume_continuation(*, owner_user_id: int, session_id: str,
                        client_request_id: str, generation: int,
                        active_child_job_id: str, deadline: float) -> dict:
    """Commit a cleaned-up head's new execution generation and child dispatch together."""
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    _validate_child_id(active_child_job_id)
    if not active_child_job_id or not deadline > time.time():
        raise ValueError("continuation child and execution deadline required")
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        head = _head(conn, owner_user_id, session_id)
        if row["state"] != AWAITING_INPUT or head is None or head["root_job_id"] != row["root_job_id"]:
            raise TurnNotQueued(row["state"], job_id=row["root_job_id"])
        _check_generation(row, generation)
        parent = row["active_child_job_id"] or row["root_job_id"]
        child = conn.execute(
            "SELECT 1 FROM task_jobs c JOIN task_questions q ON q.id=c.question_id "
            "AND q.child_job_id=c.job_id AND q.job_id=c.parent_job_id "
            "AND q.owner_user_id=c.owner_user_id AND q.session_id=c.session_id "
            "WHERE c.job_id=? AND c.owner_user_id=? AND c.session_id=? "
            "AND c.parent_job_id=? AND c.status='queued' AND q.status='answered'",
            (active_child_job_id, owner_user_id, session_id, parent),
        ).fetchone()
        if child is None:
            raise TurnStateConflict("continuation_child_conflict", job_id=row["root_job_id"])
        now = time.time()
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "active_child_job_id=?, updated_at=? WHERE owner_user_id=? "
            "AND session_id=? AND client_request_id=? AND state=? AND generation=?",
            (RUNNING, active_child_job_id, now, owner_user_id,
             session_id, client_request_id, AWAITING_INPUT, generation),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("continuation_lost", job_id=row["root_job_id"])
        child_update = conn.execute(
            "UPDATE task_jobs SET status='running',current_step='init',updated_at=? "
            "WHERE job_id=? AND status='queued'",
            (datetime.fromtimestamp(now).isoformat(), active_child_job_id),
        )
        if child_update.rowcount != 1:
            raise TurnQueueNotReady("continuation_child_lost", job_id=row["root_job_id"])
        conn.execute("UPDATE task_budgets SET deadline=? WHERE job_id=?",
                     (deadline, active_child_job_id))
        return _reread(conn, owner_user_id, session_id, client_request_id)


def _validate_child_id(active_child_job_id) -> None:
    if not isinstance(active_child_job_id, str) \
            or len(active_child_job_id) > 128:
        raise ValueError("active_child_job_id must be a bounded string")


def begin_settling(*, owner_user_id: int, session_id: str,
                   client_request_id: str, generation: int,
                   terminal_evidence: dict | None = None,
                   history_saved: bool | None = None,
                   clear_reason: str = "") -> dict:
    """Mark a real readable terminal/history result while graph release runs."""
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    if history_saved is not None and type(history_saved) is not bool:
        raise ValueError("history_saved must be a boolean or None")
    if not isinstance(clear_reason, str):
        raise ValueError("clear_reason must be a string")
    encoded_evidence = _encode_evidence(terminal_evidence)
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        if row["state"] == SETTLING:
            return _with_position(conn, _decode(row))
        if row["state"] not in (RUNNING, AWAITING_INPUT):
            raise TurnNotQueued(row["state"], job_id=row["root_job_id"])
        _check_generation(row, generation)
        final_reason = clear_reason.strip() or row["clear_reason"]
        if history_saved is False and not final_reason:
            raise ValueError(
                "clear_reason is required when history_saved is false")
        now = time.time()
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "terminal_evidence=?, history_saved=?, clear_reason=?, updated_at=? "
            "WHERE owner_user_id=? AND session_id=? AND client_request_id=? "
            "AND state IN (?,?) AND generation=?",
            (SETTLING, encoded_evidence,
             None if history_saved is None else int(history_saved),
             final_reason, now, owner_user_id, session_id, client_request_id,
             RUNNING, AWAITING_INPUT, generation),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("settling_lost", job_id=row["root_job_id"])
        return _reread(conn, owner_user_id, session_id, client_request_id)


def finalize_terminal(*, owner_user_id: int, session_id: str,
                      client_request_id: str, generation: int,
                      history_saved: bool, reason: str = "",
                      cleanup_confirmed: bool = True) -> dict:
    """CAS ``settling -> terminal`` after release/cleanup is confirmed."""
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    if type(history_saved) is not bool:
        raise ValueError("history_saved must be a boolean")
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    if cleanup_confirmed is not True:
        raise TurnReconcileRequired("cleanup_unconfirmed")
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        if row["state"] == TERMINAL:
            existing_reason = row["clear_reason"]
            if (row["history_saved"] == int(history_saved)
                    and (not reason.strip()
                         or existing_reason == reason.strip())):
                return _with_position(conn, _decode(row))
            raise TurnStateConflict("terminal_conflict",
                                    job_id=row["root_job_id"])
        if row["state"] != SETTLING:
            raise TurnNotQueued(row["state"], job_id=row["root_job_id"])
        _check_generation(row, generation)
        if (row["history_saved"] is not None
                and bool(row["history_saved"]) != history_saved):
            raise TurnStateConflict("history_saved_conflict",
                                    job_id=row["root_job_id"])
        final_reason = reason.strip() or row["clear_reason"]
        if not history_saved and not final_reason:
            raise ValueError(
                "reason is required when history_saved is false")
        now = time.time()
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "history_saved=?, clear_reason=?, finished_at=?, updated_at=? "
            "WHERE owner_user_id=? AND session_id=? AND client_request_id=? "
            "AND state=? AND generation=?",
            (TERMINAL, int(history_saved), final_reason, now, now,
             owner_user_id, session_id, client_request_id, SETTLING,
             generation),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("terminal_lost", job_id=row["root_job_id"])
        return _reread(conn, owner_user_id, session_id, client_request_id)


def mark_reconcile_required(*, owner_user_id: int, session_id: str,
                            client_request_id: str,
                            generation: int | None = None,
                            reason: str = "") -> dict:
    """Freeze a session whose dispatch/cleanup outcome cannot be proven."""
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        if row["state"] == RECONCILE_REQUIRED:
            return _with_position(conn, _decode(row))
        if row["state"] == TERMINAL:
            raise TurnStateConflict("terminal_turn",
                                    job_id=row["root_job_id"])
        expected = row["generation"] if generation is None else generation
        _check_generation(row, expected)
        now = time.time()
        final_reason = reason.strip() or row["clear_reason"] or "unknown"
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "clear_reason=?, updated_at=? WHERE owner_user_id=? "
            "AND session_id=? AND client_request_id=? "
            "AND state IN (" + _NONTERMINAL_PLACEHOLDERS + ") AND generation=?",
            (RECONCILE_REQUIRED, final_reason, now, owner_user_id, session_id,
             client_request_id, *_NONTERMINAL_SQL_STATES, expected),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("reconcile_lost",
                                    job_id=row["root_job_id"])
        return _reread(conn, owner_user_id, session_id, client_request_id)


def cancel_queued(*, owner_user_id: int, session_id: str,
                  client_request_id: str) -> dict:
    """Atomically cancel only a still-queued turn (never the running one).

    The queued root task row is cancelled in the same transaction via the
    existing internal primitive; no stop request or Agent call is made.
    Repeating the cancellation returns the same terminal fact. A claimed,
    running or otherwise non-queued turn raises ``TurnNotQueued`` (HTTP 409)
    and is left untouched.
    """
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        if row["state"] == TERMINAL:
            if row["clear_reason"] == CANCELLED_REASON:
                return _reread(conn, owner_user_id, session_id,
                               client_request_id)
            raise TurnNotQueued("terminal", job_id=row["root_job_id"])
        if row["state"] != QUEUED:
            raise TurnNotQueued(row["state"], job_id=row["root_job_id"])
        job = conn.execute("SELECT status FROM task_jobs WHERE job_id=?",
                           (row["root_job_id"],)).fetchone()
        if job is not None and job["status"] != QUEUED:
            raise TurnNotQueued(job["status"], job_id=row["root_job_id"])
        now = time.time()
        cursor = conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "clear_reason=?, history_saved=0, finished_at=?, updated_at=? "
            "WHERE owner_user_id=? AND session_id=? AND client_request_id=? "
            "AND state=?",
            (TERMINAL, CANCELLED_REASON, now, now, owner_user_id, session_id,
             client_request_id, QUEUED),
        )
        if cursor.rowcount != 1:
            raise TurnQueueNotReady("cancel_lost", job_id=row["root_job_id"])
        jobs.cancel_queued_job(conn, row["root_job_id"],
                               reason="turn_queue_cancelled")
        return _reread(conn, owner_user_id, session_id, client_request_id)


def reject_claim(*, owner_user_id: int, session_id: str,
                 client_request_id: str, generation: int, reason: str) -> dict:
    """Settle a proven pre-dispatch refusal without recording dispatch intent."""
    _validate_identity(owner_user_id, session_id)
    _validate_client_request_id(client_request_id)
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 64:
        raise ValueError("reason must be a bounded non-empty string")
    with _transaction() as conn:
        row = _scoped(conn, owner_user_id, session_id, client_request_id)
        if row is None:
            raise TurnNotFound("turn_not_found")
        if row["state"] != CLAIMED or row["dispatch_at"]:
            raise TurnNotQueued(row["state"], job_id=row["root_job_id"])
        _check_generation(row, generation)
        job = _require_root_job(conn, row)
        if job["status"] != QUEUED:
            raise TurnQueueNotReady("root_job_not_queued", job_id=row["root_job_id"])
        now = time.time()
        now_iso = datetime.now().isoformat()
        conn.execute(
            "UPDATE task_jobs SET status='failed', current_step='admission_failed', "
            "error=?, updated_at=?, finished_at=? WHERE job_id=?",
            (reason, now_iso, now_iso, row["root_job_id"]),
        )
        conn.execute(
            "UPDATE chat_turn_queue SET state=?, generation=generation+1, "
            "clear_reason=?, history_saved=0, finished_at=?, updated_at=? "
            "WHERE owner_user_id=? AND session_id=? AND client_request_id=?",
            (TERMINAL, reason, now, now, owner_user_id, session_id, client_request_id),
        )
        return _reread(conn, owner_user_id, session_id, client_request_id)


# ---------------------------------------------------------------------------
# Controller classification.
# ---------------------------------------------------------------------------

def dispatch_phase(turn: dict) -> str:
    """Classify a turn for the controller without implying execution.

    ``not_started``  -- queued/claimed; safe to release (no dispatch intent).
    ``started_frozen`` -- running/awaiting/settling with a persisted dispatch
                          intent; never auto-replay, use existing recovery.
    ``reconcile_required`` -- explicit unknown, or a broken invariant.
    ``terminal`` -- settled fact.
    """
    state = turn.get("state")
    if state in (QUEUED, CLAIMED):
        return "not_started"
    if state == TERMINAL:
        return "terminal"
    if state == RECONCILE_REQUIRED:
        return "reconcile_required"
    if state in (RUNNING, AWAITING_INPUT, SETTLING):
        return "started_frozen" if (turn.get("dispatch_at") or 0) > 0 \
            else "reconcile_required"
    raise ValueError(f"unknown turn state: {state!r}")

"""Durable outbox for a published capability batch, on the existing jobs DB.

This is not an operation ledger: HostTools owns effects, leases and full result
storage. Only the server identity, native-call digest and bounded published
observations live here. An intent is never a license to replay uncertain work.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Awaitable, Callable

from langchain_core.messages import AIMessage, ToolMessage

from src.core import task_events
from .identity import ExecutionIdentity
from .runtime import BatchObservation

# A checkpoint must stay bounded. Reject, rather than silently trim a published
# observation; the host's full-result references remain the source of truth.
MAX_BATCH_BYTES = 1024 * 1024
_MESSAGE_FIELDS = frozenset({"content", "tool_call_id", "name", "id", "status",
                             "artifact", "additional_kwargs", "response_metadata"})


class RecoveryRequiresReconciliation(task_events.TaskConflict):
    """Execution may have happened. Reconcile in the host; never retry here."""

    def __init__(self):
        self.reason = "recovery_requires_reconciliation"
        super().__init__(self.reason)


def _json(value):
    # No default encoder, model dumping, pickle, or import-based revival. Reject
    # objects (including private provider messages) even inside tool artifacts.
    def check(item):
        if item is None or type(item) in (str, int, float, bool):
            return
        # Product operation receipts contain immutable artifact sequences.
        # JSON preserves their ordered values as arrays; never revive classes.
        if type(item) in (list, tuple):
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise TypeError("batch_journal_requires_json_values")

    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _calls(response):
    if not isinstance(response, AIMessage):
        raise TypeError("batch_journal_requires_ai_message")
    calls = response.tool_calls
    ids = [call.get("id") for call in calls]
    if (not calls or response.invalid_tool_calls
            or any(not isinstance(cid, str) or not cid for cid in ids)
            or len(set(ids)) != len(ids)
            or any(not isinstance(c.get("name"), str) or not c["name"]
                   or not isinstance(c.get("args"), dict) for c in calls)):
        raise ValueError("invalid_batch_call_ids")
    # Snapshot only native calls, never the AIMessage or provider-private state.
    return json.loads(_json(calls))


def _validate(batch, calls):
    if not isinstance(batch, BatchObservation):
        raise TypeError("executor_must_return_batch_observation")
    if type(batch.pending_question) is not bool:
        raise TypeError("batch_pending_question_requires_bool")
    for values in (batch.context_notes, batch.unknown_operations):
        if not isinstance(values, (tuple, list)) or any(type(v) is not str for v in values):
            raise TypeError("batch_metadata_requires_strings")
    if any(not isinstance(m, ToolMessage) for m in batch.messages):
        raise TypeError("executor_observation_must_be_tool_message")
    names = {call["id"]: call["name"] for call in calls}
    ids = [m.tool_call_id for m in batch.messages]
    if len(ids) != len(names) or set(ids) != set(names):
        raise ValueError("executor_observation_pairing_mismatch")
    if any(m.name and m.name != names[m.tool_call_id] for m in batch.messages):
        raise ValueError("executor_observation_name_mismatch")


def _encode(batch, calls):
    _validate(batch, calls)
    data = {"version": 1, "messages": [
        {key: getattr(message, key) for key in _MESSAGE_FIELDS}
        for message in batch.messages],
        "pending_question": batch.pending_question,
        "unknown_operations": list(batch.unknown_operations),
        "context_notes": list(batch.context_notes)}
    encoded = _json(data)
    if len(encoded.encode("utf-8")) > MAX_BATCH_BYTES:
        raise ValueError("batch_observation_exceeds_checkpoint_limit")
    return encoded


def _decode(encoded, calls):
    try:
        if len(encoded.encode("utf-8")) > MAX_BATCH_BYTES:
            raise ValueError("oversized_batch_checkpoint")
        data = json.loads(encoded)
        if (set(data) != {"version", "messages", "pending_question",
                          "unknown_operations", "context_notes"}
                or type(data["version"]) is not int or data["version"] != 1
                or not isinstance(data["messages"], list)
                or any(not isinstance(m, dict) or set(m) != _MESSAGE_FIELDS
                       for m in data["messages"])
                or not isinstance(data["unknown_operations"], list)
                or not isinstance(data["context_notes"], list)):
            raise ValueError("invalid_batch_checkpoint")
        # ToolMessage is the only constructor allowed. JSON dictionaries that
        # resemble a serialized object stay inert dictionaries.
        batch = BatchObservation(
            tuple(ToolMessage(**message) for message in data["messages"]),
            pending_question=data["pending_question"],
            unknown_operations=tuple(data["unknown_operations"]),
            context_notes=tuple(data["context_notes"]))
        _encode(batch, calls)  # Recheck schema, JSON types, size and pairing.
        return batch
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RecoveryRequiresReconciliation() from exc


class BatchJournal:
    """Wrap one existing executor; completed batches can be read after restart.

    ``await BatchJournal(identity).execute(response, capability_executor)``
    accepts the executor's async ``(response, identity) -> BatchObservation``
    contract. The job must still be running/detached, including for cache reads.
    There is deliberately no takeover, delete, retry or effect-recovery API.
    """

    def __init__(self, identity: ExecutionIdentity):
        if not isinstance(identity, ExecutionIdentity):
            raise TypeError("batch_journal_requires_execution_identity")
        self.identity = identity

    def _authorize(self, conn):
        identity = self.identity
        job = conn.execute("SELECT owner_user_id,session_id,request_id,status "
                           "FROM task_jobs WHERE job_id=?", (identity.run_id,)).fetchone()
        if (job is None or job["owner_user_id"] != identity.owner_user_id
                or job["session_id"] != identity.session_id
                or job["request_id"] != identity.user_turn_id
                or job["status"] not in {"running", "detached"}):
            raise task_events.TaskConflict("agent_execution_identity_conflict")

    async def execute(self, response: AIMessage,
                      execute: Callable[[AIMessage, ExecutionIdentity], Awaitable[BatchObservation]]
                      ) -> BatchObservation:
        calls = _calls(response)
        identity = self.identity
        digest = hashlib.sha256(_json({
            "owner_user_id": identity.owner_user_id, "session_id": identity.session_id,
            "request_id": identity.user_turn_id, "calls": calls,
        }).encode("utf-8")).hexdigest()
        with task_events.transaction() as conn:
            self._authorize(conn)
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_batch_journal (
                run_id TEXT NOT NULL, batch_digest TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('intent','complete')),
                observation TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, completed_at REAL,
                PRIMARY KEY(run_id,batch_digest)
            )""")
            claimed = conn.execute(
                "INSERT INTO agent_batch_journal(run_id,batch_digest,status,created_at) "
                "VALUES (?,?,'intent',?) ON CONFLICT(run_id,batch_digest) DO NOTHING",
                (identity.run_id, digest, time.time())).rowcount
            if not claimed:
                row = conn.execute("SELECT status,observation FROM agent_batch_journal "
                                   "WHERE run_id=? AND batch_digest=?",
                                   (identity.run_id, digest)).fetchone()
                if row["status"] != "complete":
                    raise RecoveryRequiresReconciliation()
                return _decode(row["observation"], calls)
        # Intent is committed and the connection is closed before this await.
        # Any exception/cancellation leaves intent untouched and propagates.
        batch = await execute(response, identity)
        encoded = _encode(batch, calls)
        with task_events.transaction() as conn:
            self._authorize(conn)
            changed = conn.execute(
                "UPDATE agent_batch_journal SET status='complete',observation=?,completed_at=? "
                "WHERE run_id=? AND batch_digest=? AND status='intent'",
                (encoded, time.time(), identity.run_id, digest)).rowcount
            if changed != 1:
                raise RecoveryRequiresReconciliation()
        return batch

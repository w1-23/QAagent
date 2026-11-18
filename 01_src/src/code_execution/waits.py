"""Durable host-only execution waits for the Runtime integration.

A wait records an actual queue observation; it is not a user question, a new
model turn or permission to replay Code. Register/refresh uses the same queue
transaction as the completion outbox, including completion-before-registration.
Wake delivery is at least once, fenced, and acknowledged only by trusted Runtime
code AFTER its checkpoint commit. The caller must still acquire the existing
run lease and enforce original model budgets; this module never resumes a model.
"""
from __future__ import annotations

from contextlib import contextmanager
import uuid

from .protocol import ExecutionRequest, TERMINAL, bounded_integer, identifier, token

_SCHEMA = """CREATE TABLE IF NOT EXISTS code_execution_waits (
 id TEXT PRIMARY KEY, owner INTEGER NOT NULL, session TEXT NOT NULL,
 turn_id TEXT NOT NULL, run_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
 execution_id TEXT NOT NULL REFERENCES code_executions(id), fingerprint TEXT NOT NULL,
 account_epoch INTEGER NOT NULL, state TEXT NOT NULL,
 event_sequence INTEGER, event_fence INTEGER, execution_fence INTEGER,
 execution_status TEXT NOT NULL DEFAULT '', generation INTEGER NOT NULL DEFAULT 0,
 claim_token TEXT NOT NULL DEFAULT '', claim_until REAL NOT NULL DEFAULT 0,
 checkpoint_ref TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL,
 UNIQUE(owner,session,turn_id,run_id,tool_call_id))"""


class WaitConflict(ValueError):
    pass


class ExecutionWaits:
    def __init__(self, executions):
        self.executions = executions
        self.repository = executions.repository
        self.identity = executions.identity
        with self.repository._connection(write=True) as conn:
            conn.execute(_SCHEMA)
            conn.execute("CREATE INDEX IF NOT EXISTS code_waits_execution ON code_execution_waits(execution_id,state)")

    @contextmanager
    def _admit(self, execution_id):
        from src.core.account_leases import acquire_account_lease
        # _owned independently rechecks the visible task, account and session.
        current = self.executions._owned(execution_id)
        request = ExecutionRequest.parse(current["request"])
        lease = acquire_account_lease(self.identity.owner_user_id, expected_epoch=request.account_epoch)
        try:
            yield request
        finally:
            lease.release()

    def _wait(self, conn, wait_id):
        identifier(wait_id)
        identity = self.identity
        row = conn.execute("SELECT * FROM code_execution_waits WHERE id=? AND owner=? AND session=? AND turn_id=? AND run_id=?",
            (wait_id, identity.owner_user_id, identity.session_id, identity.user_turn_id, identity.run_id)).fetchone()
        if row is None:
            raise LookupError("execution_wait_not_found")
        return row

    def _execution_id(self, wait_id):
        with self.repository._connection() as conn:
            return self._wait(conn, wait_id)["execution_id"]

    def _refresh(self, conn, row):
        current = self.repository._scope(conn, row["execution_id"], self.identity.owner_user_id, self.identity.session_id)
        if current["fingerprint"] != row["fingerprint"]:
            raise WaitConflict("execution_wait_fingerprint_conflict")
        if row["state"] in {"acknowledged", "revoked"}:
            return row
        if current["status"] in TERMINAL:
            # Do not filter delivered=0: another outbox consumer or an earlier
            # subscriber cannot consume away this wait's completion fact.
            event = conn.execute("SELECT sequence,fence FROM code_events WHERE execution_id=? ORDER BY sequence DESC LIMIT 1",
                                 (row["execution_id"],)).fetchone()
            if event is None:
                raise WaitConflict("execution_completion_event_missing")
            # Reconciliation emits an event at the previous fence and increments
            # the execution fence in the SAME transaction. Retain both identities.
            conn.execute("UPDATE code_execution_waits SET state='ready',event_sequence=?,event_fence=?,execution_fence=?,"
                "execution_status=?,updated=? WHERE id=?",
                (event["sequence"], event["fence"], current["fence"], current["status"], self.repository._now(), row["id"]))
            row = self._wait(conn, row["id"])
        else:
            conn.execute("UPDATE code_execution_waits SET execution_fence=?,execution_status=?,updated=? WHERE id=?",
                         (current["fence"], current["status"], self.repository._now(), row["id"]))
            row = self._wait(conn, row["id"])
        return row

    @staticmethod
    def _public(row):
        result = {key: row[key] for key in ("execution_id", "tool_call_id", "run_id", "state", "generation", "execution_status")}
        result["wait_id"] = row["id"]
        if row["event_sequence"] is not None:
            result["completion"] = {key: row[key] for key in
                ("event_sequence", "event_fence", "execution_fence", "execution_status")}
        if row["state"] == "acknowledged":
            result["checkpoint_ref"] = row["checkpoint_ref"]
        # Never return a claim token from status/registration.
        return result

    def watch(self, execution_id, *, tool_call_id):
        """Idempotent by the issuing native call; never creates/resubmits Code."""
        token(tool_call_id)
        with self._admit(execution_id) as request:
            with self.repository._connection(write=True) as conn:
                identity = self.identity
                prior = conn.execute("SELECT * FROM code_execution_waits WHERE owner=? AND session=? AND turn_id=? AND run_id=? AND tool_call_id=?",
                    (identity.owner_user_id, identity.session_id, identity.user_turn_id, identity.run_id, tool_call_id)).fetchone()
                if prior is not None:
                    if prior["execution_id"] != execution_id or prior["fingerprint"] != request.fingerprint:
                        raise WaitConflict("execution_wait_call_conflict")
                    return self._public(self._refresh(conn, prior))
                if (conn.execute("SELECT COUNT(*) FROM code_execution_waits WHERE owner=? AND run_id=?",
                                 (identity.owner_user_id, identity.run_id)).fetchone()[0] >= 256
                        or conn.execute("SELECT COUNT(*) FROM code_execution_waits WHERE owner=?",
                                        (identity.owner_user_id,)).fetchone()[0] >= 2048):
                    raise WaitConflict("execution_wait_quota_exceeded")
                wait_id, now = uuid.uuid4().hex, self.repository._now()
                conn.execute("INSERT INTO code_execution_waits(id,owner,session,turn_id,run_id,tool_call_id,execution_id,"
                    "fingerprint,account_epoch,state,created,updated) VALUES(?,?,?,?,?,?,?,?,?,'waiting',?,?)",
                    (wait_id, identity.owner_user_id, identity.session_id, identity.user_turn_id, identity.run_id,
                     tool_call_id, execution_id, request.fingerprint, request.account_epoch, now, now))
                return self._public(self._refresh(conn, self._wait(conn, wait_id)))

    def status(self, wait_id):
        with self._admit(self._execution_id(wait_id)):
            with self.repository._connection(write=True) as conn:
                return self._public(self._refresh(conn, self._wait(conn, wait_id)))

    def claim(self, wait_id, *, lease_seconds=30):
        """One bounded wake claim. Expiry redelivers a notification, never Code."""
        bounded_integer(lease_seconds, 1, 60, "wake_lease")
        with self._admit(self._execution_id(wait_id)):
            with self.repository._connection(write=True) as conn:
                row = self._refresh(conn, self._wait(conn, wait_id))
                now = self.repository._now()
                if row["state"] != "ready" or row["claim_until"] > now:
                    return None
                generation, claim_token = row["generation"] + 1, uuid.uuid4().hex
                conn.execute("UPDATE code_execution_waits SET generation=?,claim_token=?,claim_until=?,updated=? WHERE id=?",
                             (generation, claim_token, now + lease_seconds, now, wait_id))
                return {**self._public(self._wait(conn, wait_id)), "claim_token": claim_token,
                        "claim_until": now + lease_seconds}

    def acknowledge(self, wait_id, *, generation, claim_token, checkpoint_ref):
        """Trusted Runtime callback only, after its actual checkpoint commit.

        This records a reference, not proof that arbitrary client data constitutes
        a checkpoint. Model tools must never expose claim/acknowledge parameters.
        """
        bounded_integer(generation, 1, 2**63 - 1, "wake_generation")
        identifier(claim_token)
        token(checkpoint_ref)
        with self._admit(self._execution_id(wait_id)):
            with self.repository._connection(write=True) as conn:
                row = self._refresh(conn, self._wait(conn, wait_id))
                if (row["generation"], row["claim_token"]) != (generation, claim_token):
                    raise WaitConflict("execution_wake_claim_lost")
                if row["state"] == "acknowledged":
                    if row["checkpoint_ref"] != checkpoint_ref:
                        raise WaitConflict("execution_wake_checkpoint_conflict")
                    return self._public(row)
                now = self.repository._now()
                if row["state"] != "ready" or row["claim_until"] <= now:
                    raise WaitConflict("execution_wake_claim_lost")
                conn.execute("UPDATE code_execution_waits SET state='acknowledged',checkpoint_ref=?,claim_until=0,updated=? WHERE id=?",
                             (checkpoint_ref, now, wait_id))
                return self._public(self._wait(conn, wait_id))

    def refresh(self, *, limit=50):
        """Bounded host scan for THIS run, with fresh authorization per item."""
        bounded_integer(limit, 1, 200, "wake_limit")
        identity = self.identity
        with self.repository._connection() as conn:
            ids = [row[0] for row in conn.execute("SELECT w.id FROM code_execution_waits w JOIN code_executions e ON e.id=w.execution_id "
                "WHERE w.owner=? AND w.session=? AND w.turn_id=? AND w.run_id=? "
                "AND w.state IN ('waiting','ready') AND w.claim_until<=? "
                "ORDER BY CASE WHEN e.status IN ('succeeded','failed','timed_out','cancelled') THEN 0 ELSE 1 END,w.updated,w.created,w.id LIMIT ?",
                (identity.owner_user_id, identity.session_id, identity.user_turn_id, identity.run_id, self.repository._now(), limit))]
        return [self.status(wait_id) for wait_id in ids]

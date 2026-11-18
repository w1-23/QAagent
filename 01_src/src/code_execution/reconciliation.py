"""Explicit operator recovery for unknown, already-intended offline OCI attempts.

Not a Web/Agent API, automatic reaper, success recovery or process-death detector.
The trusted operator MUST first stop/fence the original Worker and all its child
controllers, then supply its exact process token and an external evidence ref.
A PID, expired lease, missing container or JSON assertion from user code is NOT
such evidence. This module additionally verifies the owned OCI container is
physically stopped. It never executes/replays code or accepts new output bytes.

Queue terminal state and the recovery journal commit together. Exact workspace
and account cleanup follow idempotently; interrupted cleanup is resumable. Old
leases without either persisted process identity or a surviving exact ticket
remain blocked. Stopped containers and staging are retained for separate GC.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from src.core.account_leases import AccountUnavailable, current_store
from src.workspaces import TaskWorkspaceService

from .handles import parse_handle, stopped_identity
from .protocol import ExecutionRequest, TERMINAL, bounded_integer, identifier, json_object, token
from .repository import ExecutionConflict, ExecutionLeaseLost


@dataclass(frozen=True)
class WorkerStopEvidence:
    process_token: str
    evidence_ref: str
    confirmed_all_worker_processes_stopped: bool

    def __post_init__(self):
        if not isinstance(self.process_token, str) or not re.fullmatch(r"[a-f0-9]{48}", self.process_token):
            raise ValueError("exact_stopped_worker_process_token_required")
        token(self.evidence_ref)
        if self.confirmed_all_worker_processes_stopped is not True:
            raise ValueError("external_worker_stop_confirmation_required")

    @classmethod
    def parse(cls, payload):
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > 4096:
            raise ValueError("bounded_worker_stop_evidence_required")
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate_worker_stop_evidence_field")
                result[key] = value
            return result
        def constant(_):
            raise ValueError("invalid_worker_stop_evidence_number")
        parsed = json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)
        if type(parsed) is not dict or set(parsed) != {
                "process_token", "evidence_ref", "confirmed_all_worker_processes_stopped"}:
            raise ValueError("exact_worker_stop_evidence_fields_required")
        return cls(**parsed)


class ExecutionRecovery:
    def __init__(self, repository, backend, *, admission_store=None,
                 workspace_factory=TaskWorkspaceService, cleanup=None):
        self.repository, self.backend = repository, backend
        self.cleanup = cleanup
        if cleanup is not None and (cleanup.repository is not repository or cleanup.backend is not backend):
            raise ValueError("recovery_cleanup_backend_scope_required")
        self.admission = admission_store if admission_store is not None else current_store()
        self.workspace_factory = workspace_factory
        with repository._connection(write=True) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS code_recovery_journal (
                execution_id TEXT NOT NULL, fence INTEGER NOT NULL,
                owner INTEGER NOT NULL, session TEXT NOT NULL, fingerprint TEXT NOT NULL,
                worker TEXT NOT NULL, process_token TEXT NOT NULL, lease_id TEXT NOT NULL,
                evidence_ref TEXT NOT NULL, container_id TEXT NOT NULL,
                workspace_cleanup TEXT NOT NULL, account_cleanup TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(execution_id,fence))""")

    def _scope(self, execution_id, owner, session, fence):
        identifier(execution_id)
        bounded_integer(fence, 1, 2**63 - 1, "fence")
        row = self.repository.get(execution_id, owner=owner, session=session)
        request = ExecutionRequest.parse(row["request"])
        if (request.identity.owner_user_id != owner or request.identity.session_id != session
                or request.workspace_id != row["workspace"] or request.task_id != row["task"]
                or request.fingerprint != row["fingerprint"]):
            raise ExecutionConflict("recovery_request_identity_mismatch")
        with self.repository._connection() as conn:
            journal = conn.execute("SELECT * FROM code_recovery_journal WHERE execution_id=? AND fence=?",
                                   (execution_id, fence)).fetchone()
            journal = dict(journal) if journal else None
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_worker_attempts'").fetchone()
            attempt = (conn.execute("SELECT * FROM code_worker_attempts WHERE execution_id=? AND fence=?",
                                    (execution_id, fence)).fetchone() if exists else None)
        if journal is not None:
            if (journal["owner"] != owner or journal["session"] != session
                    or journal["fingerprint"] != request.fingerprint or row["fence"] != fence + 1
                    or row["status"] not in TERMINAL):
                raise ExecutionConflict("recovery_journal_scope_conflict")
        elif (row["status"] != "unknown" or row["fence"] != fence):
            raise ExecutionLeaseLost("recovery_unknown_fence_required")
        if attempt is None:
            raise ExecutionConflict("recovery_persisted_attempt_required")
        attempt = dict(attempt)
        if (attempt["owner"] != owner or attempt["worker"] != (journal["worker"] if journal else row["worker"])
                or (journal is not None and (attempt["lease_id"] != journal["lease_id"]
                    or (attempt.get("process_token") and attempt["process_token"] != journal["process_token"])))):
            raise ExecutionConflict("recovery_attempt_identity_mismatch")
        handle = parse_handle(json.loads(attempt["handle"]))
        if (handle.execution_id != execution_id or type(handle.fence) is not int or handle.fence != fence
                or handle.request.serialize() != request.serialize()):
            raise ExecutionConflict("recovery_handle_identity_mismatch")
        return row, request, attempt, handle, journal

    def snapshot(self, execution_id, *, owner, session, fence):
        """Private operator observation, no stop, queue change or lease release."""
        row, request, attempt, handle, journal = self._scope(execution_id, owner, session, fence)
        observed = self.backend.inspect(handle, read_summary=False) if journal is None else None
        return {"execution_id": execution_id, "status": row["status"], "fence": fence,
                "attempt_state": attempt["state"], "recovery_started": journal is not None,
                "container_state": observed.state if observed is not None else "previously_confirmed_stopped",
                "container_stopped": observed.stopped is True if observed is not None else None,
                "worker_stop_evidence_required": journal is None,
                "recovery_complete": bool(journal and journal["account_cleanup"] == "released"
                                          and journal["workspace_cleanup"] == "released")}

    def _ticket(self, request, attempt, proof):
        with self.admission.transaction() as conn:
            ticket = conn.execute("SELECT * FROM account_work_leases WHERE lease_id=?",
                                  (attempt["lease_id"],)).fetchone()
        known = attempt.get("process_token", "")
        if ticket is not None:
            if (ticket["owner"] != request.identity.owner_user_id or ticket["epoch"] != request.account_epoch
                    or ticket["refs"] != 1 or (known and known != ticket["process_token"])):
                raise ExecutionConflict("recovery_account_ticket_mismatch")
            known = ticket["process_token"]
        if not known or known != proof.process_token:
            raise ExecutionConflict("recovery_stopped_worker_identity_mismatch")
        return known

    @staticmethod
    def _accepted(request, execution_id, fence):
        # Privileged recovery reads only its exact prior acceptance metadata; it
        # does not grant file access or acquire new publication authority.
        from src.session import files
        with files._connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_execution_acceptance'").fetchone():
                return None
            accepted = conn.execute("SELECT * FROM task_execution_acceptance WHERE execution_id=? AND fence=?",
                                    (execution_id, fence)).fetchone()
        if accepted is None:
            return None
        if (accepted["owner"] != request.identity.owner_user_id or accepted["workspace_id"] != request.workspace_id
                or accepted["fingerprint"] != request.fingerprint):
            raise ExecutionConflict("recovery_prior_acceptance_mismatch")
        try:
            receipt = json.loads(accepted["receipt"])
            if (type(receipt) is not dict or receipt.get("execution_id") != execution_id
                    or type(receipt.get("fence")) is not int or receipt["fence"] != fence
                    or type(receipt.get("workspace_revision")) is not int
                    or receipt["workspace_revision"] != request.workspace_revision + bool(request.outputs)
                    or type(receipt.get("files")) is not list or len(receipt["files"]) != len(request.outputs)):
                raise ValueError("receipt_identity")
            content = []
            for ref, path in zip(receipt["files"], request.outputs):
                if (type(ref) is not dict or ref.get("path") != path
                        or not isinstance(ref.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])):
                    raise ValueError("receipt_file")
                identifier(ref.get("file_id"))
                bounded_integer(ref.get("version"), 1, 2**31 - 1, "file_version")
                bounded_integer(ref.get("size_bytes"), 0, request.limits.output_bytes, "output_size")
                content.append((path, ref["sha256"], ref["size_bytes"]))
            digest = hashlib.sha256(json.dumps(content, separators=(",", ":")).encode()).hexdigest()
            if digest != accepted["content_hash"] or sum(r[2] for r in content) > request.limits.output_bytes:
                raise ValueError("receipt_content")
        except (ValueError, TypeError, KeyError) as exc:
            raise ExecutionConflict("recovery_prior_acceptance_receipt_invalid") from exc
        return receipt

    def _mark(self, execution_id, fence, field, value):
        if field not in {"workspace_cleanup", "account_cleanup"}:
            raise ValueError("invalid_recovery_cleanup_field")
        with self.repository._connection(write=True) as conn:
            conn.execute("UPDATE code_recovery_journal SET " + field + "=?,updated=? WHERE execution_id=? AND fence=?",
                         (value, self.repository._now(), execution_id, fence))
            from .resources import ExecutionResources
            ExecutionResources.mirror_oci_progress(conn, execution_id, fence, field, value)

    def reconcile_stopped(self, execution_id, *, owner, session, fence,
                          proof: WorkerStopEvidence, stop_container=False):
        """Conservatively fail/cancel an interrupted attempt; never requeue it."""
        if type(proof) is not WorkerStopEvidence or type(stop_container) is not bool:
            raise ValueError("operator_worker_stop_evidence_required")
        row, request, attempt, handle, journal = self._scope(execution_id, owner, session, fence)
        if journal is None:
            process = self._ticket(request, attempt, proof)
            observed = (self.backend.kill(handle) if stop_container
                        else self.backend.inspect(handle, read_summary=False))
            try:
                identity = stopped_identity(handle, observed)
            except RuntimeError as exc:
                raise ExecutionConflict("recovery_container_stop_unconfirmed") from exc
            accepted = self._accepted(request, execution_id, fence)
            public_evidence = {"process_stopped": True, "worker_stop_attested": True,
                "worker_stop_evidence_ref": proof.evidence_ref, "accepted_outputs": accepted,
                "completion_status": "interrupted_after_acceptance" if accepted else "interrupted_without_acceptance",
                "request_fingerprint": request.fingerprint, "automatic_replay": False}
            json_object(public_evidence)
            resource_record = (self.cleanup.recovery_callback(row, handle, observed)
                               if self.cleanup is not None else None)
            def record(conn, terminal):
                now = self.repository._now()
                conn.execute("INSERT INTO code_recovery_journal VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (execution_id, fence, owner, session, request.fingerprint, attempt["worker"], process,
                     attempt["lease_id"], proof.evidence_ref, identity, "pending", "pending", now, now))
                conn.execute("UPDATE code_worker_attempts SET state='reconciled_stopped',updated=? "
                             "WHERE execution_id=? AND fence=?", (now, execution_id, fence))
                if resource_record is not None:
                    resource_record(conn, terminal)
                from .resources import ExecutionResources
                ExecutionResources.record_oci_recovery(conn, row, terminal, proof)
            self.repository.reconcile(execution_id, attempt["worker"], fence,
                outcome="stopped", evidence=public_evidence, on_reconciled=record)
            _, _, _, _, journal = self._scope(execution_id, owner, session, fence)
        if journal["process_token"] != proof.process_token or journal["evidence_ref"] != proof.evidence_ref:
            raise ExecutionConflict("recovery_evidence_conflict")
        # A durable journal proves the queue was fenced after a confirmed stop;
        # resume only cleanup, without issuing another container start/kill.
        if journal["workspace_cleanup"] == "pending":
            try:
                workspace = self.workspace_factory(owner, session)
                workspace.release_execution(request.workspace_id, operation_id="code_" + request.fingerprint)
            except AccountUnavailable:
                self._mark(execution_id, fence, "workspace_cleanup", "deferred_account_cleanup")
            else:
                self._mark(execution_id, fence, "workspace_cleanup", "released")
        if journal["account_cleanup"] != "released":
            self.admission.reconcile(owner, stopped_process_token=proof.process_token,
                lease_ids=(journal["lease_id"],), confirmed_stopped=True)
            self._mark(execution_id, fence, "account_cleanup", "released")
        resource_state = "retained_stopped"
        if self.cleanup is not None:
            try:
                resource_state = "removed" if self.cleanup.resume(execution_id, fence) else "pending"
            except LookupError:
                resource_state = "not_authorized"
            except (OSError, ValueError):
                resource_state = "pending"
        current, _, _, _, journal = self._scope(execution_id, owner, session, fence)
        return {"execution_id": execution_id, "status": current["status"], "receipt": current["receipt"],
                "recovery_complete": journal["account_cleanup"] == "released" and journal["workspace_cleanup"] == "released",
                "workspace_cleanup": journal["workspace_cleanup"], "account_cleanup": journal["account_cleanup"],
                "container_cleanup": resource_state}

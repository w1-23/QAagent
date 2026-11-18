"""Durable preparation/start gate and exact terminal resource completion.

Internal trusted-controller/operator API. It never infers Worker death from a
terminal row, expired lease, container absence or PID. Staging-only reclamation
requires a NEW protocol never-started seal and a bound private directory inode.
"""
from __future__ import annotations

import json
from pathlib import Path
import stat

from src.core.account_leases import AccountUnavailable, current_store
from src.workspaces import TaskWorkspaceService

from .protocol import ExecutionRequest, TERMINAL, identifier, bounded_integer
from .repository import ExecutionConflict
from .results import _no_links


class ExecutionResources:
    def __init__(self, repository, *, staging_root, admission_store=None,
                 workspace_factory=TaskWorkspaceService):
        self.repository = repository
        self.root = Path(staging_root)
        if not self.root.is_absolute():
            raise ValueError("execution_resource_private_root_required")
        _no_links(self.root)
        self.admission = admission_store
        self.workspace_factory = workspace_factory
        with repository._connection(write=True) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS code_execution_resources (
                execution_id TEXT NOT NULL, fence INTEGER NOT NULL,
                owner INTEGER NOT NULL, fingerprint TEXT NOT NULL, worker TEXT NOT NULL,
                lease_id TEXT NOT NULL, process_token TEXT NOT NULL, account_epoch INTEGER NOT NULL,
                staging_device TEXT NOT NULL DEFAULT '', staging_inode TEXT NOT NULL DEFAULT '',
                phase TEXT NOT NULL, terminal_queue_fence INTEGER NOT NULL DEFAULT 0,
                workspace_cleanup TEXT NOT NULL DEFAULT 'pending',
                account_cleanup TEXT NOT NULL DEFAULT 'pending',
                staging_cleanup TEXT NOT NULL DEFAULT 'pending',
                evidence TEXT NOT NULL DEFAULT '', updated REAL NOT NULL,
                PRIMARY KEY(execution_id,fence))""")

    def _scope(self, conn, execution_id, fence):
        resource = conn.execute("SELECT * FROM code_execution_resources WHERE execution_id=? AND fence=?",
                                (execution_id, fence)).fetchone()
        if resource is None:
            raise ExecutionConflict("execution_resource_record_required")
        parent = conn.execute("SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone()
        if parent is None:
            raise ExecutionConflict("execution_resource_parent_required")
        request = ExecutionRequest.parse(parent["request"])
        expected_fence = resource["terminal_queue_fence"] or fence
        if (parent["fence"] != expected_fence or parent["owner"] != resource["owner"]
                or parent["fingerprint"] != resource["fingerprint"]
                or request.fingerprint != resource["fingerprint"]
                or request.account_epoch != resource["account_epoch"]
                or request.workspace_id != parent["workspace"] or request.task_id != parent["task"]
                or request.identity.owner_user_id != parent["owner"]
                or request.identity.session_id != parent["session"]):
            raise ExecutionConflict("execution_resource_scope_conflict")
        if resource["terminal_queue_fence"]:
            if parent["status"] not in TERMINAL:
                raise ExecutionConflict("execution_resource_terminal_required")
        elif parent["worker"] != resource["worker"]:
            raise ExecutionConflict("execution_resource_worker_conflict")
        return dict(resource), dict(parent), request

    @staticmethod
    def _live(resource, row, lease):
        if (resource["worker"] != row["worker"] or resource["owner"] != row["owner"]
                or resource["fingerprint"] != row["fingerprint"]
                or resource["lease_id"] != lease.lease_id or resource["process_token"] != lease.process_token
                or resource["account_epoch"] != lease.epoch):
            raise ExecutionConflict("execution_resource_live_identity_conflict")

    def begin(self, row, lease):
        """Reserve exact process/lease before mkdir/copy/prepare, never adopt old trees."""
        request = ExecutionRequest.parse(row["request"])
        if lease.owner != row["owner"] or lease.epoch != request.account_epoch:
            raise ExecutionConflict("execution_resource_account_conflict")
        with self.repository._connection(write=True) as conn:
            self.repository._owned(conn, row["execution_id"], row["worker"], row["fence"], self.repository._now())
            conn.execute("INSERT INTO code_execution_resources "
                "(execution_id,fence,owner,fingerprint,worker,lease_id,process_token,account_epoch,phase,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)", (row["execution_id"], row["fence"], row["owner"],
                row["fingerprint"], row["worker"], lease.lease_id, lease.process_token, lease.epoch,
                "materializing", self.repository._now()))

    def bind_directory(self, row, lease):
        path = self.root / row["execution_id"]
        _no_links(path)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or path.is_mount():
            raise ExecutionConflict("execution_resource_staging_invalid")
        with self.repository._connection(write=True) as conn:
            self.repository._owned(conn, row["execution_id"], row["worker"], row["fence"], self.repository._now())
            resource, _, _ = self._scope(conn, row["execution_id"], row["fence"])
            self._live(resource, row, lease)
            if resource["phase"] != "materializing" or resource["staging_inode"]:
                raise ExecutionConflict("execution_resource_directory_already_bound")
            conn.execute("UPDATE code_execution_resources SET staging_device=?,staging_inode=?,updated=? "
                         "WHERE execution_id=? AND fence=?", (str(info.st_dev), str(info.st_ino),
                         self.repository._now(), row["execution_id"], row["fence"]))

    def prepared(self, conn, row, lease):
        resource, _, _ = self._scope(conn, row["execution_id"], row["fence"])
        self._live(resource, row, lease)
        if resource["phase"] != "materializing" or not resource["staging_inode"]:
            raise ExecutionConflict("execution_resource_bound_directory_required")
        conn.execute("UPDATE code_execution_resources SET phase='prepared',updated=? WHERE execution_id=? AND fence=?",
                     (self.repository._now(), row["execution_id"], row["fence"]))

    def intend_start(self, row, lease):
        """Irreversible committed gate before *any* backend create/start side effect."""
        with self.repository._connection(write=True) as conn:
            owned = self.repository._owned(conn, row["execution_id"], row["worker"], row["fence"], self.repository._now())
            resource, _, _ = self._scope(conn, row["execution_id"], row["fence"])
            self._live(resource, row, lease)
            if owned["status"] != "preparing" or resource["phase"] != "prepared":
                raise ExecutionConflict("execution_resource_start_gate_conflict")
            conn.execute("UPDATE code_execution_resources SET phase='start_intended',updated=? "
                         "WHERE execution_id=? AND fence=?", (self.repository._now(), row["execution_id"], row["fence"]))

    def terminal_callback(self, row, lease):
        def record(conn, terminal):
            resource, parent, _ = self._scope(conn, row["execution_id"], row["fence"])
            self._live(resource, row, lease)
            if (parent["status"] not in TERMINAL or terminal["fence"] != row["fence"]
                    or terminal["execution_id"] != row["execution_id"] or resource["terminal_queue_fence"]):
                raise ExecutionConflict("execution_resource_terminal_conflict")
            phase = resource["phase"]
            if phase in {"materializing", "prepared"}:
                phase = "never_started"
            elif phase != "start_intended":
                raise ExecutionConflict("execution_resource_phase_conflict")
            conn.execute("UPDATE code_execution_resources SET phase=?,terminal_queue_fence=?,updated=? "
                         "WHERE execution_id=? AND fence=?", (phase, terminal["fence"], self.repository._now(),
                         row["execution_id"], row["fence"]))
        return record

    def _mark(self, execution_id, fence, field, value):
        if field not in {"workspace_cleanup", "account_cleanup", "staging_cleanup"}:
            raise ValueError("execution_resource_cleanup_field_invalid")
        with self.repository._connection(write=True) as conn:
            resource, _, _ = self._scope(conn, execution_id, fence)
            if not resource["terminal_queue_fence"]:
                raise ExecutionConflict("execution_resource_terminal_required")
            conn.execute("UPDATE code_execution_resources SET " + field + "=?,updated=? WHERE execution_id=? AND fence=?",
                         (value, self.repository._now(), execution_id, fence))

    @staticmethod
    def record_oci_recovery(conn, row, terminal, proof):
        """Optional NEW resource record joins existing exact OCI recovery commit."""
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='code_execution_resources'").fetchone():
            return
        resource = conn.execute("SELECT * FROM code_execution_resources WHERE execution_id=? AND fence=?",
                                (row["execution_id"], row["fence"])).fetchone()
        if resource is None:
            return  # Legacy OCI attempts retain their existing recovery journal.
        request = ExecutionRequest.parse(row["request"])
        if (resource["owner"] != row["owner"] or resource["fingerprint"] != row["fingerprint"]
                or resource["worker"] != row["worker"] or resource["process_token"] != proof.process_token
                or resource["account_epoch"] != request.account_epoch or resource["terminal_queue_fence"]
                or terminal["execution_id"] != row["execution_id"] or terminal["fence"] != row["fence"] + 1
                or terminal["status"] not in TERMINAL):
            raise ExecutionConflict("execution_resource_oci_recovery_conflict")
        attempt = conn.execute("SELECT lease_id FROM code_worker_attempts WHERE execution_id=? AND fence=?",
                               (row["execution_id"], row["fence"])).fetchone()
        if attempt is None or attempt["lease_id"] != resource["lease_id"]:
            raise ExecutionConflict("execution_resource_oci_ticket_conflict")
        encoded = json.dumps({"process_token": proof.process_token, "evidence_ref": proof.evidence_ref}, sort_keys=True)
        conn.execute("UPDATE code_execution_resources SET terminal_queue_fence=?,evidence=? WHERE execution_id=? AND fence=?",
                     (terminal["fence"], encoded, row["execution_id"], row["fence"]))

    @staticmethod
    def mirror_oci_progress(conn, execution_id, fence, field, value):
        if field not in {"workspace_cleanup", "account_cleanup"}:
            raise ValueError("execution_resource_cleanup_field_invalid")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='code_execution_resources'").fetchone():
            conn.execute("UPDATE code_execution_resources SET " + field + "=? WHERE execution_id=? AND fence=? "
                         "AND terminal_queue_fence>0", (value, execution_id, fence))

    def _release_workspace(self, resource, request):
        if resource["workspace_cleanup"] == "released":
            return
        try:
            workspace = self.workspace_factory(resource["owner"], request.identity.session_id)
            workspace.release_execution(request.workspace_id, operation_id="code_" + request.fingerprint)
        except AccountUnavailable:
            state = "deferred_account_cleanup"
        else:
            state = "released"
        self._mark(resource["execution_id"], resource["fence"], "workspace_cleanup", state)

    def account_released(self, row, lease):
        with self.repository._connection() as conn:
            resource, _, _ = self._scope(conn, row["execution_id"], row["fence"])
            self._live(resource, row, lease)
        if resource["terminal_queue_fence"]:
            self._mark(row["execution_id"], row["fence"], "account_cleanup", "released")

    def cleanup_unstarted(self, execution_id, fence):
        with self.repository._connection() as conn:
            resource, _, _ = self._scope(conn, execution_id, fence)
        if not resource["terminal_queue_fence"] or resource["phase"] != "never_started":
            raise ExecutionConflict("execution_resource_never_started_seal_required")
        if resource["staging_cleanup"] == "removed":
            return True
        if not resource["staging_inode"]:
            # mkdir and SQLite cannot commit together. An unbound directory is
            # never adopted from its name, even with proof that the writer died.
            return False
        from .cleanup import _remove_staging
        _remove_staging(self.root / execution_id, resource["staging_device"], resource["staging_inode"])
        self._mark(execution_id, fence, "staging_cleanup", "removed")
        return True

    def reconcile(self, execution_id, *, owner, session, fence, proof, preparation=False):
        """Explicit stopped-writer repair; terminal receipts/events stay untouched.

        NEW pre-start abandoned preparations may be failed, never requeued. A
        start-intended attempt must use physical OCI reconciliation instead.
        """
        from .reconciliation import WorkerStopEvidence
        if type(proof) is not WorkerStopEvidence or type(preparation) is not bool:
            raise ValueError("operator_worker_stop_evidence_required")
        identifier(execution_id)
        bounded_integer(fence, 1, 2**63 - 1, "fence")
        self.repository.get(execution_id, owner=owner, session=session)
        with self.repository._connection() as conn:
            resource, parent, request = self._scope(conn, execution_id, fence)
        if resource["owner"] != owner or request.identity.session_id != session:
            raise ExecutionConflict("execution_resource_scope_conflict")
        if proof.process_token != resource["process_token"]:
            raise ExecutionConflict("execution_resource_stopped_writer_mismatch")
        admission = self.admission if self.admission is not None else current_store()
        with admission.transaction() as conn:
            ticket = conn.execute("SELECT * FROM account_work_leases WHERE lease_id=?", (resource["lease_id"],)).fetchone()
            if ticket is not None and (ticket["owner"] != owner or ticket["epoch"] != request.account_epoch
                    or ticket["process_token"] != proof.process_token or ticket["refs"] != 1):
                raise ExecutionConflict("execution_resource_account_ticket_mismatch")
        encoded = json.dumps({"process_token": proof.process_token, "evidence_ref": proof.evidence_ref}, sort_keys=True)
        def attest(conn):
            current, _, _ = self._scope(conn, execution_id, fence)
            if current["evidence"] and current["evidence"] != encoded:
                raise ExecutionConflict("execution_resource_evidence_conflict")
            conn.execute("UPDATE code_execution_resources SET evidence=?,updated=? WHERE execution_id=? AND fence=?",
                         (encoded, self.repository._now(), execution_id, fence))
        if not resource["terminal_queue_fence"]:
            if not preparation or parent["status"] != "unknown" or resource["phase"] not in {"materializing", "prepared"}:
                raise ExecutionConflict("execution_resource_prestart_unknown_required")
            def finish(conn, terminal):
                # The queue now has fence+1; bind resource fencing before scope.
                conn.execute("UPDATE code_execution_resources SET phase='never_started',terminal_queue_fence=?,updated=? "
                             "WHERE execution_id=? AND fence=?", (terminal["fence"], self.repository._now(), execution_id, fence))
                attest(conn)
            self.repository.reconcile(execution_id, resource["worker"], fence, outcome="stopped",
                evidence={"process_stopped": True, "never_started": True, "automatic_replay": False,
                          "worker_stop_evidence_ref": proof.evidence_ref}, on_reconciled=finish)
        else:
            with self.repository._connection(write=True) as conn:
                attest(conn)
        with self.repository._connection() as conn:
            resource, _, request = self._scope(conn, execution_id, fence)
        self._release_workspace(resource, request)
        # Revalidate the exact ticket under the same auth transaction as removal,
        # so a retained ref cannot sneak in between a snapshot and the delete.
        with admission.transaction() as conn:
            ticket = conn.execute("SELECT * FROM account_work_leases WHERE lease_id=?", (resource["lease_id"],)).fetchone()
            if ticket is not None:
                if (ticket["owner"] != owner or ticket["epoch"] != request.account_epoch
                        or ticket["process_token"] != proof.process_token or ticket["refs"] != 1):
                    raise ExecutionConflict("execution_resource_account_ticket_mismatch")
                conn.execute("DELETE FROM account_work_leases WHERE lease_id=? AND owner=? AND process_token=?",
                             (resource["lease_id"], owner, proof.process_token))
        self._mark(execution_id, fence, "account_cleanup", "released")
        if resource["phase"] == "never_started":
            self.cleanup_unstarted(execution_id, fence)
        with self.repository._connection() as conn:
            resource, _, _ = self._scope(conn, execution_id, fence)
        current = self.repository.get(execution_id, owner=owner, session=session)
        return {"execution_id": execution_id, "status": current["status"], "receipt": current["receipt"],
                "workspace_cleanup": resource["workspace_cleanup"], "account_cleanup": resource["account_cleanup"],
                "staging_cleanup": resource["staging_cleanup"],
                "recovery_complete": resource["workspace_cleanup"] == resource["account_cleanup"] == "released"}

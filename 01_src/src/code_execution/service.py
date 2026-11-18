"""Authenticated submission/status facade over task snapshots and the queue.

No code is executed here. Worker availability and policy support are explicit;
registering a new protocol must not quietly expose a nonfunctional Code tool.
"""
from __future__ import annotations

from src.agent.identity import ExecutionIdentity
from src.core.account_leases import account_lease
from src.workspaces import TaskWorkspaceService

from .protocol import ExecutionLimits, ExecutionRequest, FileSnapshot, TERMINAL, logical_path
from .repository import ExecutionConflict, ExecutionRepository
from .logs import ExecutionLogs


class ExecutionService:
    def __init__(self, identity: ExecutionIdentity, *, repository=None, workspace_service=None):
        if type(identity) is not ExecutionIdentity:
            raise ValueError("execution_identity_required")
        self.identity = identity
        if repository is None:
            from src.config import PROJECT_ROOT
            repository = ExecutionRepository(PROJECT_ROOT / "state" / "code_executions.sqlite3")
        self.repository = repository
        self.workspace = workspace_service or TaskWorkspaceService(identity.owner_user_id, identity.session_id)

    def submit(self, *, task_id, workspace_id, expected_revision, script_path,
               operation_id, idempotency_key, arguments=(), inputs=(), outputs=(),
               limits=None, environment_id="base", environment_revision=1,
               network_policy="offline", absolute_deadline=None):
        """Host-only submission; policy, limits and deadline are never model authority.

        absolute_deadline is the original run's wall deadline, not a fresh
        per-call budget. Omit it (or use +infinity) for legacy finite executions.
        Exact replay observes the original execution without resnapshotting.
        """
        with account_lease(self.identity.owner_user_id) as lease:
            existing = self.repository.find_submission(owner=self.identity.owner_user_id,
                session=self.identity.session_id, workspace_id=workspace_id,
                operation_id=operation_id, idempotency_key=idempotency_key)
            if existing is not None:
                # Replay observes the original immutable snapshot even after a
                # later write/output acceptance advanced the workspace revision.
                # Recheck live task access, then let the SAME submission path
                # compare every request field and the original run deadline.
                self._owned(existing["execution_id"])
                original = ExecutionRequest.parse(existing["request"])
                if (script_path != original.script.path or type(inputs) is not tuple
                        or inputs != tuple(item.path for item in original.inputs)):
                    raise ExecutionConflict("execution_idempotency_conflict")
                candidate = ExecutionRequest(identity=self.identity, task_id=task_id, workspace_id=workspace_id,
                    workspace_revision=expected_revision, operation_id=operation_id,
                    idempotency_key=idempotency_key, script=original.script, inputs=original.inputs,
                    account_epoch=original.account_epoch, arguments=arguments, outputs=outputs,
                    limits=ExecutionLimits() if limits is None else limits, environment_id=environment_id,
                    environment_revision=environment_revision, network_policy=network_policy)
                row = self.repository.submit(candidate, absolute_deadline=absolute_deadline)
                self._release_terminal(row)
                return row
            snapshot = self.workspace.snapshot(workspace_id, task_id=task_id,
                                                expected_revision=expected_revision)
            entries = {item["path"]: item for item in snapshot["files"]}
            logical_path(script_path, writable=True)
            if script_path not in entries:
                raise LookupError("execution_script_not_bound_to_task")
            if type(inputs) is not tuple:
                raise ValueError("execution_input_paths_required")
            selected = []
            for path in inputs:
                logical_path(path)
                if path not in entries:
                    raise LookupError("execution_input_not_bound_to_task")
                selected.append(FileSnapshot(**entries[path]))
            request = ExecutionRequest(identity=self.identity, task_id=task_id, workspace_id=workspace_id,
                workspace_revision=expected_revision, operation_id=operation_id,
                idempotency_key=idempotency_key, script=FileSnapshot(**entries[script_path]),
                account_epoch=lease.epoch, inputs=tuple(selected), arguments=arguments, outputs=outputs,
                limits=ExecutionLimits() if limits is None else limits, environment_id=environment_id,
                environment_revision=environment_revision, network_policy=network_policy)
            # Separate local databases cannot commit atomically. Establish the
            # conservative retention barrier FIRST, rechecking the revision.
            # A failed/uncertain submit leaves it for explicit reconciliation;
            # removing it on an exception could unpin a committed execution.
            self.workspace.retain_execution(workspace_id,
                operation_id=self._retention_key(request), task_id=task_id,
                expected_revision=expected_revision)
            row = self.repository.submit(request, absolute_deadline=absolute_deadline)
            self._release_terminal(row)
            return row

    @staticmethod
    def _retention_key(request):
        return "code_" + request.fingerprint

    def _release_terminal(self, row):
        if row["status"] in TERMINAL:
            request = ExecutionRequest.parse(row["request"])
            self.workspace.release_execution(request.workspace_id,
                operation_id=self._retention_key(request))

    def _owned(self, execution_id):
        row = self.repository.get(execution_id, owner=self.identity.owner_user_id,
                                  session=self.identity.session_id)
        request = ExecutionRequest.parse(row["request"])
        # Recheck task visibility independently of the untrusted execution id.
        current = self.workspace.open(request.workspace_id)
        if current["task_id"] != request.task_id:
            raise PermissionError("execution_task_scope_mismatch")
        return row

    def status(self, execution_id):
        with account_lease(self.identity.owner_user_id):
            row = self._owned(execution_id)
            self._release_terminal(row)
            return {"task_id": row["task"], "workspace_id": row["workspace"],
                    **{key: row[key] for key in ("execution_id", "status", "fence",
                    "created", "updated", "deadline", "reason", "receipt")}}

    def run_executions(self, *, limit=256):
        """Host-only exact identity view, including submissions without a watch.

        Exceeding the bound is a conflict, never evidence that omitted live
        executions stopped. Reads do not grant cancellation completion.
        """
        with account_lease(self.identity.owner_user_id):
            rows = self.repository.run_executions(self.identity, limit=limit)
            result = []
            for row in rows:
                if ExecutionRequest.parse(row["request"]).identity != self.identity:
                    raise ExecutionConflict("execution_run_identity_conflict")
                result.append(self.status(row["execution_id"]))
            return result

    def cancel_run(self, *, limit=256):
        """Request stop for this run only; caller must inspect all final states.

        Coordinator admission must stop new submissions before calling this.
        This local view is not an atomic barrier against a racing submitter.
        """
        with account_lease(self.identity.owner_user_id):
            rows = self.run_executions(limit=limit)
            for row in rows:
                self.cancel(row["execution_id"])
            return [self.status(row["execution_id"]) for row in rows]

    def read_log(self, execution_id, *, fence, stream, offset=0, limit=16384):
        """Read raw captured bytes with fresh account/session/task authorization."""
        with account_lease(self.identity.owner_user_id):
            self._owned(execution_id)
            return ExecutionLogs(self.repository).read(execution_id,
                owner=self.identity.owner_user_id, session=self.identity.session_id,
                fence=fence, stream=stream, offset=offset, limit=limit)

    def cancel(self, execution_id):
        with account_lease(self.identity.owner_user_id):
            self._owned(execution_id)
            row = self.repository.cancel(execution_id, owner=self.identity.owner_user_id,
                                         session=self.identity.session_id)
            self._release_terminal(row)
            return row

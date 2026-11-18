"""Independent, synchronous Code Worker controller; no model or Web execution.

Only this trusted controller sees the execution backend. Its durable intent is
written before any container side effect. Unknown attempts retain queue slots,
account admission references and workspace holds for explicit reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time

from src.core.account_leases import acquire_account_lease, AccountUnavailable, current_store
from src.workspaces import TaskWorkspaceService

from .protocol import ExecutionRequest, TERMINAL, token, json_object
from .repository import ExecutionConflict, ExecutionLeaseLost
from .results import OutputBlob, accept_outputs, _no_links
from .logs import ExecutionLogs


class ExecutionController:
    def __init__(self, repository, backend, *, staging_root: Path,
                 worker_id: str, workspace_factory=TaskWorkspaceService, interval=0.25):
        token(worker_id)
        root = Path(staging_root)
        if not root.is_absolute():
            raise ValueError("execution_private_staging_required")
        _no_links(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not 0.05 <= interval <= 2:
            raise ValueError("execution_poll_interval_invalid")
        self.repository, self.backend = repository, backend
        self.staging_root, self.worker_id = root, worker_id
        self.workspace_factory, self.interval = workspace_factory, interval
        self.logs = ExecutionLogs(repository)
        from .resources import ExecutionResources
        self.resources = ExecutionResources(repository, staging_root=root, workspace_factory=workspace_factory)
        # Handles are retained deliberately on uncertainty. The DB remains the
        # authority even if this process crashes and this dictionary disappears.
        self._uncertain_leases = {}
        with repository._connection(write=True) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS code_worker_attempts (
                execution_id TEXT NOT NULL, fence INTEGER NOT NULL, worker TEXT NOT NULL,
                owner INTEGER NOT NULL, lease_id TEXT NOT NULL, handle TEXT NOT NULL,
                state TEXT NOT NULL, updated REAL NOT NULL,
                process_token TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(execution_id,fence))""")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(code_worker_attempts)")}
            if "process_token" not in columns:
                conn.execute("ALTER TABLE code_worker_attempts ADD COLUMN process_token TEXT NOT NULL DEFAULT ''")

    def _record(self, row, lease, handle):
        encoded = json_object(handle.as_dict())
        with self.repository._connection(write=True) as conn:
            self.repository._owned(conn, row["execution_id"], self.worker_id, row["fence"],
                                   self.repository._now())
            self.logs.reserve(conn, row)
            self.resources.prepared(conn, row, lease)
            conn.execute("INSERT INTO code_worker_attempts "
                "(execution_id,fence,worker,owner,lease_id,handle,state,updated,process_token) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (row["execution_id"], row["fence"], self.worker_id, row["owner"], lease.lease_id,
                 encoded, "prepared", self.repository._now(), lease.process_token))

    def _record_state(self, row, state):
        with self.repository._connection(write=True) as conn:
            conn.execute("UPDATE code_worker_attempts SET state=?,updated=? "
                "WHERE execution_id=? AND fence=? AND worker=?",
                (state, self.repository._now(), row["execution_id"], row["fence"], self.worker_id))

    def _materialize(self, row, request, workspace, lease):
        root = self.staging_root / row["execution_id"]
        # No reuse of a directory after a controller crash or a previous attempt.
        root.mkdir(mode=0o755)
        self.resources.bind_directory(row, lease)
        for folder in ("input", "workspace", "outputs"):
            (root / folder).mkdir(mode=0o755)
        for snapshot in (request.script, *request.inputs):
            data = workspace.read_bytes(request.workspace_id, path=snapshot.path,
                version=snapshot.version, expected_sha256=snapshot.sha256)
            if len(data) != snapshot.size_bytes or hashlib.sha256(data).hexdigest() != snapshot.sha256:
                raise ValueError("execution_input_integrity_mismatch")
            path = root / snapshot.path.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            with path.open("xb") as stream:
                stream.write(data)
            if os.name != "nt":
                path.chmod(0o444)
        return root

    def _unknown(self, row, lease, reason):
        self._uncertain_leases[(row["execution_id"], row["fence"])] = lease
        try:
            self.repository.mark_unknown(row["execution_id"], self.worker_id, row["fence"], reason=reason)
        except ExecutionLeaseLost:
            self.repository.reconcile_expired()
        self._record_state(row, "unknown")

    @staticmethod
    def _log(data, *, already_truncated=False):
        if not isinstance(data, bytes):
            raise ValueError("execution_log_bytes_required")
        # Only an excerpt enters queue JSON; raw captured bytes live in the
        # private execution log table and are read by authorized byte pages.
        return {"text": data[:16384].decode("utf-8", errors="replace"),
                "captured_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "truncated": bool(already_truncated or len(data) > 16384)}

    def run_one(self):
        """Claim at most one job. Never execute locally if the backend fails."""
        row = self.repository.claim(self.worker_id)
        if row is None:
            return None
        request = ExecutionRequest.parse(row["request"])
        # An in-container timer is insufficient: user code can stop a same-UID
        # supervisor. Wall-clock adjustments cannot extend this host deadline.
        monotonic_deadline = time.monotonic() + max(0, min(request.limits.timeout_sec,
            row["deadline"] - self.repository._now()))
        lease, handle, workspace = None, None, None
        may_have_started, stopped, completed = False, False, False
        resources_recorded = False
        result, accepted_result = None, None
        status, reason, observation = "failed", "preparation_failed", {}
        try:
            lease = acquire_account_lease(row["owner"], expected_epoch=request.account_epoch)
            self.resources.begin(row, lease)
            resources_recorded = True
            workspace = self.workspace_factory(row["owner"], row["session"])
            manifest = workspace.open(request.workspace_id)
            if manifest["task_id"] != request.task_id:
                raise PermissionError("execution_task_scope_mismatch")
            root = self._materialize(row, request, workspace, lease)
            handle = self.backend.prepare(request, execution_id=row["execution_id"],
                                          fence=row["fence"], input_root=str(root))
            self._record(row, lease, handle)
            latest = self.repository.heartbeat(row["execution_id"], self.worker_id, row["fence"])
            if not current_store().check_epoch(row["owner"], request.account_epoch):
                raise AccountUnavailable("execution_authorization_revoked")
            if latest == "cancel_requested":
                status, reason = "cancelled", "cancelled_before_start"
                result = self._finish(row, status, reason, {}, workspace, process_stopped=True,
                                      on_terminal=self.resources.terminal_callback(row, lease))
                completed = True
                return result
            # Persisted handle + lease precede create/start. A transport exception
            # from here onward cannot prove the container never started.
            self.resources.intend_start(row, lease)
            may_have_started = True
            self.backend.start(handle)
            self._record_state(row, "started")
            self.repository.transition(row["execution_id"], self.worker_id, row["fence"], "running")
            while True:
                if time.monotonic() >= monotonic_deadline:
                    raise TimeoutError("execution_host_deadline_exceeded")
                live = self.repository.heartbeat(row["execution_id"], self.worker_id, row["fence"])
                if live == "cancel_requested":
                    status, reason = "cancelled", "user_cancel"
                    break
                if not current_store().check_epoch(row["owner"], request.account_epoch):
                    raise AccountUnavailable("execution_authorization_revoked")
                state = self.backend.inspect(handle)
                if state.state == "unknown":
                    reason = "backend_state_unknown"
                    break
                if state.state == "stopped":
                    stopped, reason = True, "container_stopped_without_result"
                    break
                if state.state == "retained":
                    self.repository.transition(row["execution_id"], self.worker_id, row["fence"], "collecting")
                    failed_child = (getattr(state, "child_exit_code", None) not in (None, 0)
                                    or getattr(state, "timed_out", False))
                    # Capture diagnostics first: rejected/missing output files
                    # must not erase the script's real stdout/stderr.
                    collected = self.backend.collect(handle, include_outputs=False)
                    observation = {"child_exit_code": collected.child_exit_code,
                        "stdout": self._log(collected.stdout, already_truncated=collected.logs_truncated),
                        "stderr": self._log(collected.stderr, already_truncated=collected.logs_truncated),
                        "resource_measurements": "unavailable",
                        "log_storage": "unavailable"}
                    references = self.logs.store(row, stdout=collected.stdout, stderr=collected.stderr,
                                                 capture_truncated=collected.logs_truncated)
                    for stream, reference in references.items():
                        observation[stream]["log_ref"] = reference
                    observation["log_storage"] = "captured_bytes_persisted"
                    outputs = {}
                    if not failed_child and request.outputs:
                        with_outputs = self.backend.collect(handle)
                        if (with_outputs.child_exit_code != collected.child_exit_code
                                or with_outputs.stdout != collected.stdout
                                or with_outputs.stderr != collected.stderr
                                or with_outputs.logs_truncated != collected.logs_truncated
                                or getattr(with_outputs, "timed_out", False) != getattr(collected, "timed_out", False)):
                            raise ValueError("execution_collection_diagnostics_changed")
                        outputs = with_outputs.outputs
                    # Collection is bounded per-file byte observation. It does
                    # not assert a simultaneous scientific multi-file snapshot.
                    ended = self.backend.kill(handle)
                    stopped = ended.stopped is True
                    if not stopped:
                        reason = "container_stop_unconfirmed"
                        break
                    self._record_state(row, "stopped")
                    if getattr(collected, "timed_out", False):
                        status, reason = "timed_out", "script_timeout"
                        break
                    if collected.child_exit_code != 0:
                        reason = "script_exit_nonzero"
                        break
                    if set(outputs) != set(request.outputs):
                        raise ValueError("execution_output_set_mismatch")
                    blobs = tuple(OutputBlob(path, outputs[path]) for path in request.outputs)
                    def accept():
                        nonlocal accepted_result
                        accepted = accept_outputs(request, execution_id=row["execution_id"],
                            fence=row["fence"], blobs=blobs, workspace_repository=workspace.repository)
                        accepted_result = accepted
                        return {**observation, **accepted, "request_fingerprint": request.fingerprint,
                                "network_policy": request.network_policy, "process_stopped": True}
                    receipt = self.repository.complete_success(row["execution_id"], self.worker_id,
                        row["fence"], accept_result=accept, process_stopped=True,
                        on_terminal=self._terminal_callback(row, handle, lease))
                    completed = True
                    result = {"execution_id": row["execution_id"], "status": "succeeded", "receipt": receipt}
                    return result
                time.sleep(self.interval)
        except AccountUnavailable:
            status, reason = "cancelled", "authorization_revoked"
        except ExecutionLeaseLost:
            status, reason = "timed_out", "lease_or_deadline_lost"
        except TimeoutError:
            status, reason = "timed_out", "execution_host_deadline_exceeded"
        except Exception as exc:
            # Do not leak host paths/connection credentials from exception text.
            observation = {**observation, "error_type": type(exc).__name__}
            from .oci import OCIError
            if isinstance(exc, OCIError):
                try:
                    observation["error_code"] = token(str(exc))
                except ValueError:
                    pass
            reason = "execution_controller_failure"
        finally:
            if not completed and accepted_result is not None:
                # The files DB may have committed before the queue/journal
                # transaction failed. Preserve those exact accepted references;
                # never describe the artifacts as absent or re-execute the code.
                observation["accepted_outputs"] = accepted_result
                observation["completion_status"] = "interrupted_after_acceptance"
            if lease is not None:
                if may_have_started and not stopped:
                    try:
                        stopped = self.backend.kill(handle).stopped is True
                    except Exception:
                        stopped = False
                if stopped or not may_have_started:
                    try:
                        if not completed:
                            if stopped:
                                self._record_state(row, "stopped")
                            callback = (self._terminal_callback(row, handle, lease) if stopped
                                        else self.resources.terminal_callback(row, lease) if resources_recorded else None)
                            result = self._finish(row, status, reason, observation, workspace,
                                                  process_stopped=True, on_terminal=callback)
                    finally:
                        if result is not None and result["status"] in TERMINAL and workspace is not None:
                            try:
                                state = self._release_hold(request, workspace)
                                if resources_recorded and state in {"released", "deferred_account_cleanup"}:
                                    self.resources._mark(row["execution_id"], row["fence"], "workspace_cleanup", state)
                            except Exception:
                                pass  # Durable terminal intent permits explicit retry; result stands.
                        if stopped and handle is not None:
                            self._cleanup_stopped(row, handle, lease)
                        elif resources_recorded:
                            try:
                                self.resources.cleanup_unstarted(row["execution_id"], row["fence"])
                            except Exception:
                                pass  # No bound inode/no terminal seal never authorizes deletion.
                        # Keep durable account ownership through the final queue
                        # and file-hold writes, not merely until container exit.
                        lease.release()
                        if resources_recorded:
                            try:
                                self.resources.account_released(row, lease)
                            except Exception:
                                pass  # Exact release is idempotently observed on operator recovery.
                else:
                    self._unknown(row, lease, "container_stop_unconfirmed")
        if may_have_started and not stopped:
            return {"execution_id": row["execution_id"], "status": "unknown"}
        if completed:
            return result
        return result or self._finish(row, status, reason, observation, workspace, process_stopped=True)

    def _terminal_callback(self, row, handle, lease):
        from .cleanup import ExecutionCleanup
        from .handles import supported_handle
        resource_record = self.resources.terminal_callback(row, lease)
        container_record = (ExecutionCleanup(self.repository, self.backend, staging_root=self.staging_root).terminal_callback(
            row, handle, lease) if supported_handle(handle) else None)
        def record(conn, terminal):
            resource_record(conn, terminal)
            if container_record is not None:
                container_record(conn, terminal)
        return record

    def _cleanup_stopped(self, row, handle, lease):
        from .cleanup import ExecutionCleanup
        from .handles import supported_handle
        if not supported_handle(handle):
            return
        try:
            cleanup = ExecutionCleanup(self.repository, self.backend, staging_root=self.staging_root)
            cleanup.resume(row["execution_id"], row["fence"])
        except Exception:
            # A committed execution result is independent of GC. Unknown/stale
            # fences fail authorization; ambiguous removal retains the staging.
            pass

    def _release_hold(self, request, workspace):
        try:
            workspace.release_execution(request.workspace_id, operation_id="code_" + request.fingerprint)
        except AccountUnavailable:
            return "deferred_account_cleanup"  # Never reopen closed admission.
        return "released"

    def _finish(self, row, status, reason, observation, workspace, *, process_stopped,
                on_terminal=None):
        try:
            # A cancellation that won while preparing/collecting stays cancelled.
            current = self.repository.get(row["execution_id"], owner=row["owner"], session=row["session"])
            if current["status"] == "cancel_requested":
                status = "cancelled"
            receipt = {**observation, "reason": reason, "process_stopped": process_stopped}
            self.repository.finish(row["execution_id"], self.worker_id, row["fence"],
                status=status, receipt=receipt, process_stopped=process_stopped, on_terminal=on_terminal)
            return {"execution_id": row["execution_id"], "status": status, "receipt": receipt}
        except ExecutionLeaseLost:
            self.repository.reconcile_expired()
            # Even when physically stopped, a stale fence cannot publish a
            # terminal result. Its durable attempt state guides reconciliation.
            if process_stopped:
                self._record_state(row, "stopped")
            return {"execution_id": row["execution_id"], "status": "unknown"}

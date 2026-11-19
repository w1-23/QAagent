"""Host-only facade for immutable offline environment installations.

This module composes existing authorities; it executes nothing and imports no
package code. Preparation reads the fixed installer as bytes, submission uses
the real ``ExecutionService`` queue, and publication delegates to
``EnvironmentStore``'s verified-success + revision CAS transaction.

Installer requests are always the fixed offline base-image request produced by
``prepare_install``. Callers cannot select a network policy, remote index,
environment or installer. Replaying the same operation observes the original
execution instead of staging or queueing duplicate work.
"""
from __future__ import annotations

import math

from src.agent.identity import ExecutionIdentity

from .environments import EnvironmentStore, inspect_overlay, prepare_install
from .protocol import ExecutionRequest, bounded_integer, logical_path, token
from .repository import ExecutionConflict
from .service import ExecutionService

_OFFLINE_FIXED = ("base", 1, "offline")
_PREPARED_FIELDS = frozenset({"workspace_id", "task_id", "expected_revision",
    "script_path", "arguments", "inputs", "outputs", "environment_id",
    "environment_revision", "network_policy"})


def _deadline(value):
    """The original run's wall deadline is host authority, never a fresh budget."""
    if (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
        raise ValueError("environment_run_deadline_required")
    return float(value)


class OfflineEnvironmentService:
    """Identity-bound internal facade; constructing it performs no I/O."""

    def __init__(self, identity, *, executions=None, store=None):
        if type(identity) is not ExecutionIdentity:
            raise ValueError("execution_identity_required")
        self.identity = identity
        self.executions = executions or ExecutionService(identity)
        if (self.executions.identity != identity
                or (self.executions.workspace.owner, self.executions.workspace.session)
                != (identity.owner_user_id, identity.session_id)):
            raise ValueError("environment_execution_scope_mismatch")
        self.store = store or EnvironmentStore(self.executions.workspace)

    def prepare(self, *, task_id, workspace_id, expected_revision, lock_path):
        """Stage the fixed installer and return the exact journalable request."""
        return prepare_install(self.executions.workspace, workspace_id=workspace_id,
            task_id=task_id, expected_revision=expected_revision, lock_path=lock_path)

    def submit_install(self, *, task_id, workspace_id, expected_revision,
                       lock_path, operation_id, idempotency_key, absolute_deadline):
        """Submit the fixed installer for a scoped, already-bound wheel lock.

        Exact replay (same workspace/operation/idempotency key) returns the
        original execution status without re-preparing, re-binding the installer
        or creating a second queue row. Callers must pass the host run deadline.
        """
        deadline = _deadline(absolute_deadline)
        token(task_id)
        token(operation_id)
        token(idempotency_key)
        logical_path(lock_path)
        bounded_integer(expected_revision, 1, 2**31 - 1, "workspace_revision")
        existing = self.executions.repository.find_submission(
            owner=self.identity.owner_user_id, session=self.identity.session_id,
            workspace_id=workspace_id, operation_id=operation_id,
            idempotency_key=idempotency_key)
        if existing is not None:
            return self._replay(existing, task_id=task_id, workspace_id=workspace_id,
                                lock_path=lock_path, deadline=deadline)
        prepared = self.prepare(workspace_id=workspace_id, task_id=task_id,
            expected_revision=expected_revision, lock_path=lock_path)
        return self._submit_prepared(prepared, task_id=task_id, workspace_id=workspace_id,
            operation_id=operation_id, idempotency_key=idempotency_key,
            absolute_deadline=deadline)

    def submit_prepared(self, prepared, *, operation_id, idempotency_key,
                        absolute_deadline):
        """Submit a preparation persisted by the caller's operation journal.

        ``prepared`` is exactly the dict returned by
        ``code_execution.environments.prepare_install``. This method never
        re-stages the installer, so a caller can safely retry an unknown queue
        outcome from its journal.
        """
        if not isinstance(prepared, dict):
            raise ValueError("environment_prepared_request_required")
        return self._submit_prepared(prepared, task_id=prepared.get("task_id"),
            workspace_id=prepared.get("workspace_id"), operation_id=operation_id,
            idempotency_key=idempotency_key,
            absolute_deadline=_deadline(absolute_deadline))

    def _replay(self, existing, *, task_id, workspace_id, lock_path, deadline):
        # status() rechecks the live account, session and task before the
        # original immutable request is inspected.
        status = self.executions.status(existing["execution_id"])
        row = self.executions.repository.get(existing["execution_id"],
            owner=self.identity.owner_user_id, session=self.identity.session_id)
        # The run deadline is host authority and immutable across retries.
        if row["run_deadline"] != deadline:
            raise ExecutionConflict("execution_idempotency_conflict")
        request = ExecutionRequest.parse(row["request"])
        if (request.workspace_id != workspace_id or request.task_id != task_id
                or (request.environment_id, request.environment_revision,
                    request.network_policy) != _OFFLINE_FIXED
                or len(request.arguments) != 2 or request.arguments[0] != lock_path):
            raise ExecutionConflict("environment_installer_replay_conflict")
        return status

    def _submit_prepared(self, prepared, *, task_id, workspace_id, operation_id,
                         idempotency_key, absolute_deadline):
        if set(prepared) != _PREPARED_FIELDS:
            raise ValueError("environment_prepared_request_invalid")
        if (prepared.get("task_id") != task_id
                or prepared.get("workspace_id") != workspace_id
                or (prepared.get("environment_id"), prepared.get("environment_revision"),
                    prepared.get("network_policy")) != _OFFLINE_FIXED):
            raise ValueError("environment_offline_fixed_installer_required")
        row = self.executions.submit(
            task_id=prepared["task_id"], workspace_id=prepared["workspace_id"],
            expected_revision=prepared["expected_revision"],
            script_path=prepared["script_path"], operation_id=operation_id,
            idempotency_key=idempotency_key,
            arguments=tuple(prepared["arguments"]), inputs=tuple(prepared["inputs"]),
            outputs=tuple(prepared["outputs"]),
            environment_id=prepared["environment_id"],
            environment_revision=prepared["environment_revision"],
            network_policy=prepared["network_policy"],
            absolute_deadline=absolute_deadline)
        return self.executions.status(row["execution_id"])

    def status(self, execution_id):
        """Observe the exact install attempt through the existing service."""
        return self.executions.status(execution_id)

    def get(self, workspace_id, *, environment_id, revision):
        """Return one exact immutable revision's manifest."""
        return self.store.get(workspace_id, environment_id=environment_id,
                              revision=revision)

    def read(self, workspace_id, *, environment_id, revision):
        """Read one exact immutable revision and re-verify its archive bytes."""
        token(environment_id)
        bounded_integer(revision, 1, 2**31 - 1, "environment_revision")
        manifest = self.get(workspace_id, environment_id=environment_id,
                            revision=revision)
        ref = manifest["archive"]
        data = self.executions.workspace.read_bytes(workspace_id, path=ref["path"],
            version=ref["version"], expected_sha256=ref["sha256"])
        if len(data) != ref["size_bytes"]:
            raise ValueError("environment_archive_size_mismatch")
        inspect_overlay(data, manifest["lock"], manifest["lock_sha256"])
        return manifest, data

    def read_overlay(self, request):
        """Worker-facing exact read: the request's scope must match the revision."""
        return self.store.read_overlay(request)

    def publish(self, *, execution_id, environment_id, expected_revision):
        """Publish only a verified successful attempt, with revision CAS."""
        return self.store.publish(self.executions, execution_id=execution_id,
            environment_id=environment_id, expected_revision=expected_revision)

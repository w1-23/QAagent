"""Focused host-side contracts for the offline environment facade.

These tests use temporary stores only. They never start a worker, contact a
package index or exercise the production runtime.
"""
import hashlib
import json
import time

import pytest

from src import auth
from src.agent.identity import ExecutionIdentity
from src.code_execution import environment_service
from src.code_execution.protocol import ExecutionRequest
from src.code_execution.repository import ExecutionConflict, ExecutionRepository
from src.code_execution.service import ExecutionService
from src.session import files
from src.workspaces import TaskWorkspaceService
from src.workspaces.repository import Repository

OWNER = 7
SESSION = "sess_environment_1"
TASK = "task_environment_1"
LOCK_PATH = "/input/env/lock.json"


class _SessionManager:
    def session_belongs_to(self, session_id, owner_user_id):
        return (session_id, owner_user_id) == (SESSION, OWNER)


def _lock(wheel):
    return {
        "schema": "citrus.wheel-lock/1",
        "image_id": "sha256:" + "a" * 64,
        "runtime": {"python": "3.12.3", "platform": "linux",
                    "machine": "x86_64", "pip": "24.0"},
        "variant": "cpu",
        "packages": [{"name": "demo-pkg", "version": "1.0.0",
                      "filename": "demo_pkg-1.0.0-none-any.whl",
                      "sha256": hashlib.sha256(wheel).hexdigest(),
                      "size_bytes": len(wheel)}],
        "checks": ["demo_pkg"],
    }


@pytest.fixture
def bound(tmp_path, monkeypatch):
    # Isolate every authority used by prepare/submit/read.
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "accounts.sqlite3")
    auth.init_store()
    with auth._connect() as conn:
        conn.execute(
            "INSERT INTO users(user_id,username,salt,password_hash,role,active,"
            "daily_limit,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (OWNER, "owner", b"salt", b"hash", "user", 1, 30, "2026-01-01"))

    monkeypatch.setattr(files, "DB_PATH", tmp_path / "session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "session_files")
    files.init_store()

    repository = Repository(session_manager=_SessionManager())
    workspace = TaskWorkspaceService(OWNER, SESSION, repository=repository)
    identity = ExecutionIdentity(OWNER, SESSION, "turn_1", "run_1")
    executions = ExecutionService(
        identity,
        repository=ExecutionRepository(tmp_path / "code_executions.sqlite3"),
        workspace_service=workspace)
    service = environment_service.OfflineEnvironmentService(
        identity, executions=executions)

    wheel = b"PK\x05\x06" + b"\x00" * 18
    lock = json.dumps(_lock(wheel), sort_keys=True).encode()
    lock_file = files.register_file(owner_user_id=OWNER, session_id=SESSION,
        filename="lock.json", content=lock, visibility="internal")
    wheel_file = files.register_file(owner_user_id=OWNER, session_id=SESSION,
        filename="demo_pkg-1.0.0-none-any.whl", content=wheel, visibility="internal")
    manifest = workspace.create(task_id=TASK, request_id="request_environment_1")
    workspace_id = manifest["workspace_id"]
    workspace.bind(workspace_id, path=LOCK_PATH, file_id=lock_file["file_id"],
                   expected_revision=1, expected_sha256=lock_file["sha256"])
    workspace.bind(workspace_id, path="/input/env/wheels/demo_pkg-1.0.0-none-any.whl",
                   file_id=wheel_file["file_id"], expected_revision=2,
                   expected_sha256=wheel_file["sha256"])
    return service, executions, workspace, workspace_id


def _submit(service, workspace_id, deadline):
    return service.submit_install(task_id=TASK, workspace_id=workspace_id,
        expected_revision=3, lock_path=LOCK_PATH, operation_id="op_environment_1",
        idempotency_key="idem_environment_1", absolute_deadline=deadline)


def test_submit_is_fixed_offline_and_replay_does_no_duplicate_work(bound):
    service, executions, workspace, workspace_id = bound
    deadline = time.time() + 3600

    first = _submit(service, workspace_id, deadline)
    assert first["status"] == "queued"
    row = executions.repository.get(first["execution_id"], owner=OWNER, session=SESSION)
    request = ExecutionRequest.parse(row["request"])
    assert (request.environment_id, request.environment_revision,
            request.network_policy) == ("base", 1, "offline")
    assert request.arguments[0] == LOCK_PATH
    assert request.script.path.startswith("/workspace/environment_")
    assert request.script.path.endswith(".py")
    assert len(request.inputs) == 2
    assert workspace.open(workspace_id)["revision"] == 4

    replay = _submit(service, workspace_id, deadline)
    assert replay["execution_id"] == first["execution_id"]
    assert replay["status"] == "queued"

    # A preparation persisted by the caller's journal is also replay-safe.
    prepared = service.prepare(task_id=TASK, workspace_id=workspace_id,
        expected_revision=4, lock_path=LOCK_PATH)
    journaled = service.submit_prepared(prepared,
        operation_id="op_environment_1", idempotency_key="idem_environment_1",
        absolute_deadline=deadline)
    assert journaled["execution_id"] == first["execution_id"]

    # The original run deadline is immutable across retries.
    with pytest.raises(ExecutionConflict, match="execution_idempotency_conflict"):
        _submit(service, workspace_id, deadline + 1)

    with executions.repository._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM code_executions").fetchone()[0] == 1
    assert workspace.open(workspace_id)["revision"] == 4


def test_publish_requires_a_verified_successful_attempt(bound):
    service, executions, workspace, workspace_id = bound
    submitted = _submit(service, workspace_id, time.time() + 3600)

    with pytest.raises(ValueError, match="environment_successful_installer_required"):
        service.publish(execution_id=submitted["execution_id"],
                        environment_id="env_1", expected_revision=0)
    assert service.status(submitted["execution_id"])["status"] == "queued"


def test_read_is_exact_revision_and_deadline_is_host_authority(bound):
    service, executions, workspace, workspace_id = bound
    with pytest.raises(LookupError, match="environment_revision_not_found"):
        service.read(workspace_id, environment_id="env_1", revision=1)

    with pytest.raises(ValueError, match="environment_run_deadline_required"):
        service.submit_install(task_id=TASK, workspace_id=workspace_id,
            expected_revision=3, lock_path=LOCK_PATH, operation_id="op_environment_2",
            idempotency_key="idem_environment_2", absolute_deadline=None)
    with executions.repository._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM code_executions").fetchone()[0] == 0

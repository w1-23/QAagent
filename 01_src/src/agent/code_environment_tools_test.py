"""Product integration: offline environment lifecycle through the real Code tools.

Every test runs against a throwaway CITRUSQA_PROJECT_ROOT. No worker, package
index, network, Apptainer image or model prompt is exercised here.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest

from src import auth
from src.agent import code_tools
from src.agent.identity import ExecutionIdentity
from src.code_execution.environments import _SCHEMA
from src.code_execution.protocol import ExecutionRequest
from src.code_execution.service import ExecutionService
from src.core import jobs, task_events, tracing
from src.session import files as session_files
from src.session import manager as session_manager
from src.workspaces import TaskWorkspaceService

LOCK = "/input/env/lock.json"
WHEEL = "/input/env/wheels/demo_pkg-1.0.0-none-any.whl"
WHEEL_BYTES = b"PK\x05\x06" + b"\x00" * 18


def _setup(monkeypatch):
    monkeypatch.setenv("CITRUS_CODE_EXECUTION", "offline-development")
    monkeypatch.setattr("src.code_execution.presence.worker_available",
                        lambda _path, **_kwargs: True)
    auth.init_store()
    user, _token, error = auth.register("u" + uuid.uuid4().hex[:12],
                                        "pw-" + uuid.uuid4().hex)
    assert error is None and user
    owner = int(user["user_id"])
    session = "sess-" + uuid.uuid4().hex
    session_manager.SessionManager()._create_session_sync(session, owner)
    claim = jobs.claim_job(session, "server-turn", owner_user_id=owner,
                           client_request_id="client-turn", payload={})
    identity = ExecutionIdentity(owner, session, "server-turn", claim.job_id)
    with task_events.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO task_budgets"
                     "(job_id,deadline,max_calls,max_tokens) VALUES(?,?,?,?)",
                     (claim.job_id, time.time() + 3600, 100, 100000))
    tracing.set_user_id(owner)
    tracing.set_session_id(session)
    tracing.set_job_id(claim.job_id)
    return identity


def _bind(identity, call_id, workspace, path, file, revision):
    with code_tools.code_execution_context(identity, call_id):
        content, _ = code_tools._dispatch("bind_input",
            task_id=workspace["task_id"], workspace_id=workspace["workspace_id"],
            expected_revision=revision, path=path, file_id=file["file_id"],
            sha256=file["sha256"])
    payload = json.loads(content)
    assert payload["_tool_outcome"]["status"] == "complete", payload
    return payload["revision"]


@pytest.fixture
def bound(monkeypatch):
    identity = _setup(monkeypatch)
    try:
        with code_tools.code_execution_context(identity, "call-workspace"):
            content, _ = code_tools._dispatch("workspace", workspace_id=None,
                                              offset=0, limit=30)
        workspace = json.loads(content)
        assert workspace["_tool_outcome"]["status"] == "complete"

        def register(name, data, mime):
            return session_files.register_file(
                session_id=identity.session_id, owner_user_id=identity.owner_user_id,
                filename=name, content=data, mime=mime,
                origin=session_files.ORIGIN_UPLOADED, visibility="public")

        lock = json.dumps({
            "schema": "citrus.wheel-lock/1",
            "image_id": "sha256:" + "a" * 64,
            "runtime": {"python": "3.12.3", "platform": "linux",
                        "machine": "x86_64", "pip": "24.0"},
            "variant": "cpu",
            "packages": [{"name": "demo-pkg", "version": "1.0.0",
                          "filename": "demo_pkg-1.0.0-none-any.whl",
                          "sha256": hashlib.sha256(WHEEL_BYTES).hexdigest(),
                          "size_bytes": len(WHEEL_BYTES)}],
            "checks": ["demo_pkg"],
        }, sort_keys=True).encode()
        lock_file = register("lock.json", lock, "application/json")
        wheel_file = register("demo_pkg-1.0.0-none-any.whl", WHEEL_BYTES,
                              "application/octet-stream")
        revision = _bind(identity, "bind-lock", workspace, LOCK, lock_file,
                         workspace["revision"])
        revision = _bind(identity, "bind-wheel", workspace, WHEEL, wheel_file,
                         revision)
        yield {"identity": identity, "workspace": workspace,
               "revision": revision, "lock_file": lock_file,
               "wheel_file": wheel_file}
    finally:
        tracing.set_user_id(None)
        tracing.set_session_id("")
        tracing.set_job_id("")


def _install(bound, call_id="install-1", revision=None):
    identity, workspace = bound["identity"], bound["workspace"]
    with code_tools.code_execution_context(identity, call_id):
        content, artifact = code_tools._dispatch("environment_install",
            task_id=workspace["task_id"], workspace_id=workspace["workspace_id"],
            expected_revision=bound["revision"] if revision is None else revision,
            lock_path=LOCK)
    return json.loads(content), artifact


def _write_script(bound):
    identity, workspace = bound["identity"], bound["workspace"]
    with code_tools.code_execution_context(identity, "write-script"):
        content, _ = code_tools._dispatch("write",
            task_id=workspace["task_id"], workspace_id=workspace["workspace_id"],
            expected_revision=bound["revision"] + 1, path="/workspace/main.py",
            content="print(1)\n")
    payload = json.loads(content)
    assert payload["_tool_outcome"]["status"] == "complete", payload
    return payload["revision"]


def _publish_synthetic(bound, environment_id="env_demo", revision=1, task_id=None):
    identity, workspace = bound["identity"], bound["workspace"]
    execution_id = hashlib.sha256(
        f"{environment_id}:{revision}".encode()).hexdigest()[:32]
    manifest = {"schema": "citrus.task-environment/1",
                "environment_id": environment_id, "revision": revision,
                "workspace_id": workspace["workspace_id"],
                "task_id": task_id or workspace["task_id"], "lock": {},
                "lock_sha256": "0" * 64,
                "archive": {"path": "/outputs/environment.zip", "file_id": "1" * 32,
                            "version": 1, "sha256": "2" * 64, "size_bytes": 1},
                "execution_id": execution_id, "fence": 1,
                "request_fingerprint": "4" * 64}
    service = TaskWorkspaceService(identity.owner_user_id, identity.session_id)
    with service.repository._transaction(identity.owner_user_id,
                                         identity.session_id) as (conn, _):
        service.repository._workspace(conn, identity.owner_user_id,
                                      identity.session_id, workspace["workspace_id"])
        conn.execute(_SCHEMA)
        conn.execute("INSERT INTO task_environment_revisions VALUES(?,?,?,?,?,?,?)",
            (workspace["workspace_id"], environment_id, revision, execution_id, 1,
             "4" * 64, json.dumps(manifest, sort_keys=True)))

def _execute(bound, revision, call_id, **environment):
    identity, workspace = bound["identity"], bound["workspace"]
    with code_tools.code_execution_context(identity, call_id):
        content, artifact = code_tools._dispatch("execute",
            task_id=workspace["task_id"], workspace_id=workspace["workspace_id"],
            expected_revision=revision, script_path="/workspace/main.py",
            inputs=[], outputs=[], argv=[], **environment)
    return json.loads(content), artifact


def test_install_queues_fixed_offline_and_replays_by_content(bound):
    payload, artifact = _install(bound)
    assert payload["_tool_outcome"]["status"] == "complete"
    assert payload["_tool_outcome"]["reason_code"] == "environment_install_submitted"
    assert payload["completion_scope"] == "submission_only"
    assert payload["_code_execution_id"] == artifact["_code_execution_id"]
    assert payload["installer"] == "fixed-offline-base"
    assert payload["workspace_revision"] == bound["revision"] + 1

    identity = bound["identity"]
    row = ExecutionService(identity).repository.get(payload["_code_execution_id"],
        owner=identity.owner_user_id, session=identity.session_id)
    request = ExecutionRequest.parse(row["request"])
    assert (request.environment_id, request.environment_revision,
            request.network_policy) == ("base", 1, "offline")
    assert request.arguments[0] == LOCK
    assert request.script.path.startswith("/workspace/environment_")
    assert [item.path for item in request.inputs] == [LOCK, WHEEL]

    # Crash window after preparation: the installer was staged exactly once.
    stale, _ = _install(bound, "install-2", bound["revision"])
    assert stale["_tool_outcome"]["status"] == "not_run"
    assert stale["_tool_outcome"]["reason_code"] == "workspace_revision_conflict"
    assert stale["latest_revision"] == bound["revision"] + 1

    # Replaying the same immutable lock content observes the original queue row.
    replay, _ = _install(bound, "install-3", bound["revision"] + 1)
    assert replay["_tool_outcome"]["status"] == "complete"
    assert replay["_code_execution_id"] == payload["_code_execution_id"]
    with ExecutionService(identity).repository._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM code_executions").fetchone()[0] == 1


def test_same_lock_can_be_submitted_by_a_later_run(bound):
    first, _ = _install(bound)
    identity = bound["identity"]
    ExecutionService(identity).cancel(first["_code_execution_id"])
    jobs.update_job(identity.run_id, status="cancelled")
    claim = jobs.claim_job(identity.session_id, "later-turn", owner_user_id=identity.owner_user_id,
                           client_request_id="later-client-turn", payload={})
    later = ExecutionIdentity(identity.owner_user_id, identity.session_id, "later-turn", claim.job_id)
    deadline = time.time() + 1800
    with task_events.transaction() as conn:
        conn.execute("INSERT INTO task_budgets(job_id,deadline,max_calls,max_tokens) VALUES(?,?,?,?)",
                     (later.run_id, deadline, 10, 10000))
    tracing.set_job_id(later.run_id)
    second, _ = _install({**bound, "identity": later}, "later-install", first["workspace_revision"])
    assert second["_tool_outcome"]["status"] == "complete", second
    assert second["_code_execution_id"] != first["_code_execution_id"]
    row = ExecutionService(later).repository.get(second["_code_execution_id"],
        owner=later.owner_user_id, session=later.session_id)
    assert row["run_deadline"] == deadline


def test_publish_requires_a_verified_successful_attempt(bound):
    payload, _ = _install(bound)
    with code_tools.code_execution_context(bound["identity"], "publish-1"):
        content, _ = code_tools._dispatch("environment_publish",
            execution_id=payload["_code_execution_id"],
            environment_id="env_demo", expected_revision=0)
    result = json.loads(content)
    assert result["_tool_outcome"]["status"] == "not_run"
    assert result["_tool_outcome"]["reason_code"] == "environment_successful_installer_required"


def test_execute_selects_only_exact_published_revision(bound):
    _install(bound)
    revision = _write_script(bound)
    _publish_synthetic(bound)
    identity = bound["identity"]

    payload, _ = _execute(bound, revision, "execute-env",
                          environment_id="env_demo", environment_revision=1)
    assert payload["_tool_outcome"]["status"] == "complete", payload
    assert payload["environment_id"] == "env_demo"
    assert payload["environment_revision"] == 1
    row = ExecutionService(identity).repository.get(payload["_code_execution_id"],
        owner=identity.owner_user_id, session=identity.session_id)
    request = ExecutionRequest.parse(row["request"])
    assert (request.environment_id, request.environment_revision) == ("env_demo", 1)

    missing, _ = _execute(bound, revision, "execute-missing",
                          environment_id="env_demo", environment_revision=2)
    assert missing["_tool_outcome"]["status"] == "not_run"
    assert missing["_tool_outcome"]["reason_code"] == "environment_revision_not_found"

    _publish_synthetic(bound, environment_id="env_other", task_id="other-task")
    foreign, _ = _execute(bound, revision, "execute-foreign",
                          environment_id="env_other", environment_revision=1)
    assert foreign["_tool_outcome"]["status"] == "not_run"
    assert foreign["_tool_outcome"]["reason_code"] == "environment_selection_scope_mismatch"


def test_read_refuses_an_unpublished_revision(bound):
    with code_tools.code_execution_context(bound["identity"], "read-1"):
        content, _ = code_tools._dispatch("environment_read",
            workspace_id=bound["workspace"]["workspace_id"],
            environment_id="env_demo", revision=7)
    payload = json.loads(content)
    assert payload["_tool_outcome"]["status"] == "not_run"
    assert payload["_tool_outcome"]["reason_code"] == "environment_revision_not_found"


def test_install_refuses_an_unbound_lock(bound):
    with code_tools.code_execution_context(bound["identity"], "install-missing"):
        content, _ = code_tools._dispatch("environment_install",
            task_id=bound["workspace"]["task_id"],
            workspace_id=bound["workspace"]["workspace_id"],
            expected_revision=bound["revision"],
            lock_path="/input/env/missing-lock.json")
    payload = json.loads(content)
    assert payload["_tool_outcome"]["status"] == "not_run"
    assert payload["_tool_outcome"]["reason_code"] == "environment_lock_not_bound"


def test_environment_tools_are_registered_with_explicit_policy(monkeypatch):
    monkeypatch.setenv("CITRUS_CODE_EXECUTION", "offline-development")
    from src.agent.capabilities import CapabilityCatalog

    catalog = CapabilityCatalog()
    code_tools.register_code_tools(catalog)
    described = {item["name"]: item for item in catalog.describe()}
    for name in ("code_environment_install", "code_environment_publish",
                 "code_environment_read"):
        assert name in code_tools.CODE_TOOL_NAMES
        assert described[name]["provider"] == "code"
    assert {name for name, item in described.items() if item["mutates"]} >= {
        "code_environment_install", "code_environment_publish"}
    assert described["code_environment_install"]["execution_mode"] == "sequential"
    assert described["code_environment_read"]["execution_mode"] == "parallel"
    assert "code_environment_install" in code_tools.EXECUTION_WAIT_TOOL_NAMES
    assert set(code_tools.EnvironmentInstallArgs.model_json_schema()["required"]) == {
        "task_id", "workspace_id", "expected_revision", "lock_path"}
    with pytest.raises(ValueError):
        code_tools.EnvironmentInstallArgs(task_id="code_" + "a" * 64,
            workspace_id="b" * 32, expected_revision=1,
            lock_path="/workspace/lock.json")
    with pytest.raises(ValueError):
        code_tools.ExecuteArgs(task_id="code_" + "a" * 64, workspace_id="b" * 32,
            expected_revision=1, script_path="/workspace/main.py",
            environment_id="env_demo")
    with pytest.raises(ValueError):
        code_tools.ExecuteArgs(task_id="code_" + "a" * 64, workspace_id="b" * 32,
            expected_revision=1, script_path="/workspace/main.py",
            environment_id="base", environment_revision=2)
def test_host_registers_existing_durable_wait_for_environment_install(bound, monkeypatch):
    import asyncio

    from src.agent.host_tools import HostTools
    from src.code_execution.waits import ExecutionWaits
    from src.tools import registry

    identity, workspace = bound["identity"], bound["workspace"]
    monkeypatch.setattr(registry, "_TOOL_SPECS", {})
    registry.register_tool(code_tools.code_environment_install, category="code",
        is_readonly=False, concurrency_key="code_workspace",
        write_scope="session_artifact", recovery_mode="never")
    host = HostTools(identity, {"query": "environment"})
    call = {"id": "install-host", "name": "code_environment_install", "args": {
        "task_id": workspace["task_id"], "workspace_id": workspace["workspace_id"],
        "expected_revision": bound["revision"], "lock_path": LOCK}}
    result = asyncio.run(host.invoke(code_tools.code_environment_install, call, identity))
    payload = json.loads(result.message.content)
    assert payload["_tool_outcome"]["status"] == "complete", payload
    assert len(result.execution_waits) == 1
    wait = ExecutionWaits(ExecutionService(identity)).status(result.execution_waits[0])
    assert wait["execution_id"] == payload["_code_execution_id"]
    assert wait["state"] in {"waiting", "ready"}

"""Focused regression: bind an authorized session file as a code /input snapshot.

The test process is expected to run with CITRUSQA_PROJECT_ROOT pointing at a
throwaway directory, so every runtime database lives outside the candidate tree.
"""
from __future__ import annotations

import json
import uuid

import pytest

from src import auth
from src.agent import code_tools
from src.agent.identity import ExecutionIdentity
from src.core import jobs, tracing
from src.session import files as session_files
from src.session import manager as session_manager
from src.workspaces import TaskWorkspaceService

OWNER_PASSWORD = "citrus-test-pass-123"


def _setup(monkeypatch):
    monkeypatch.setenv("CITRUS_CODE_EXECUTION", "offline-development")
    auth.init_store()
    user, _token, error = auth.register("u" + uuid.uuid4().hex[:12], OWNER_PASSWORD)
    assert error is None and user
    owner = int(user["user_id"])
    session = "sess-" + uuid.uuid4().hex
    session_manager.SessionManager()._create_session_sync(session, owner)
    claim = jobs.claim_job(session, "server-turn", owner_user_id=owner,
                           client_request_id="client-turn", payload={})
    identity = ExecutionIdentity(owner, session, "server-turn", claim.job_id)
    tracing.set_user_id(owner)
    tracing.set_session_id(session)
    tracing.set_job_id(claim.job_id)
    return identity


@pytest.fixture
def identity(monkeypatch):
    ident = _setup(monkeypatch)
    try:
        yield ident
    finally:
        tracing.set_user_id(None)
        tracing.set_session_id("")
        tracing.set_job_id("")


def _workspace(identity):
    with code_tools.code_execution_context(identity, "call-workspace"):
        content, _artifact = code_tools._dispatch(
            "workspace", workspace_id=None, offset=0, limit=30)
    payload = json.loads(content)
    assert payload["_tool_outcome"]["status"] == "complete"
    return payload


def _bind(identity, call_id, **payload):
    with code_tools.code_execution_context(identity, call_id):
        content, _artifact = code_tools._dispatch("bind_input", **payload)
    return json.loads(content)


def _payload(workspace, *, path="/input/data.csv", file_id, sha256, revision=None):
    return {"task_id": workspace["task_id"], "workspace_id": workspace["workspace_id"],
            "expected_revision": workspace["revision"] if revision is None else revision,
            "path": path, "file_id": file_id, "sha256": sha256}


def _upload(identity, *, name="data.csv", data=b"a,b\n1,2\n", visibility="public"):
    return session_files.register_file(
        session_id=identity.session_id, owner_user_id=identity.owner_user_id,
        filename=name, content=data, mime="text/csv",
        origin=session_files.ORIGIN_UPLOADED, visibility=visibility)


def test_binds_public_uploaded_file_and_keeps_immutable_input(identity):
    workspace = _workspace(identity)
    uploaded = _upload(identity)

    result = _bind(identity, "bind-1", **_payload(
        workspace, file_id=uploaded["file_id"], sha256=uploaded["sha256"]))

    assert result["_tool_outcome"]["status"] == "complete"
    assert result["_tool_outcome"]["reason_code"] == "code_input_bound"
    assert result["revision"] == workspace["revision"] + 1
    assert result["path"] == "/input/data.csv"
    assert result["file_id"] == uploaded["file_id"]
    assert result["version"] == 1
    assert result["sha256"] == uploaded["sha256"]
    assert result["size_bytes"] == len(b"a,b\n1,2\n")

    service = TaskWorkspaceService(identity.owner_user_id, identity.session_id)
    snapshot = service.snapshot(workspace["workspace_id"], task_id=workspace["task_id"],
                                expected_revision=result["revision"])
    refs = {item["path"]: item for item in snapshot["files"]}
    assert refs["/input/data.csv"]["file_id"] == uploaded["file_id"]
    assert refs["/input/data.csv"]["sha256"] == uploaded["sha256"]
    assert service.read_bytes(workspace["workspace_id"], path="/input/data.csv", version=1,
                              expected_sha256=uploaded["sha256"]) == b"a,b\n1,2\n"
    assert service.list_files(workspace["workspace_id"])["items"][0]["readonly"] is True

    # /input is append-only per path: a second binding at the same path is refused.
    again = _bind(identity, "bind-2", **_payload(
        workspace, revision=result["revision"], file_id=uploaded["file_id"],
        sha256=uploaded["sha256"]))
    assert again["_tool_outcome"]["status"] == "failed"


def test_refuses_internal_cross_session_wrong_hash_and_unsupported_provenance(identity):
    workspace = _workspace(identity)
    uploaded = _upload(identity, name="ok.csv", data=b"x,y\n")

    internal = session_files.register_file(
        session_id=identity.session_id, owner_user_id=identity.owner_user_id,
        filename="receipt.txt", content=b"host-only", mime="text/plain",
        origin=session_files.ORIGIN_UPLOADED, producer_tool="code_execute",
        visibility="internal")
    assert _bind(identity, "internal", **_payload(
        workspace, path="/input/receipt.txt", file_id=internal["file_id"],
        sha256=internal["sha256"]))["_tool_outcome"]["status"] == "failed"

    assert _bind(identity, "hash", **_payload(
        workspace, path="/input/ok.csv", file_id=uploaded["file_id"],
        sha256="0" * 64))["_tool_outcome"]["status"] == "failed"

    other_session = "sess-" + uuid.uuid4().hex
    session_manager.SessionManager()._create_session_sync(
        other_session, identity.owner_user_id)
    foreign = session_files.register_file(
        session_id=other_session, owner_user_id=identity.owner_user_id,
        filename="other.csv", content=b"p,q\n", mime="text/csv",
        origin=session_files.ORIGIN_UPLOADED, visibility="public")
    assert _bind(identity, "cross", **_payload(
        workspace, path="/input/other.csv", file_id=foreign["file_id"],
        sha256=foreign["sha256"]))["_tool_outcome"]["status"] == "failed"

    # An unsupported provenance row must not become an executable input.
    with session_files._connect() as conn:
        conn.execute("UPDATE session_files SET origin='legacy' WHERE file_id=?",
                     (uploaded["file_id"],))
    assert _bind(identity, "provenance", **_payload(
        workspace, path="/input/ok.csv", file_id=uploaded["file_id"],
        sha256=uploaded["sha256"]))["_tool_outcome"]["status"] == "failed"


def test_expected_revision_is_cas_and_task_is_bound(identity):
    workspace = _workspace(identity)
    uploaded = _upload(identity)

    stale = _bind(identity, "stale", **_payload(
        workspace, revision=workspace["revision"] + 10,
        file_id=uploaded["file_id"], sha256=uploaded["sha256"]))
    assert stale["_tool_outcome"]["status"] == "failed"
    assert stale["_tool_outcome"]["reason_code"] == "workspace_revision_conflict"
    assert stale["latest_revision"] == workspace["revision"]

    wrong_task = _payload(workspace, file_id=uploaded["file_id"],
                          sha256=uploaded["sha256"])
    wrong_task["task_id"] = "code_" + "f" * 64
    assert _bind(identity, "task", **wrong_task)["_tool_outcome"]["status"] == "failed"


def test_arguments_are_input_only_and_exact():
    valid = code_tools.BindInputArgs(
        task_id="code_" + "a" * 64, workspace_id="b" * 32, expected_revision=1,
        path="/input/nested/data.csv", file_id="c" * 32, sha256="d" * 64)
    assert valid.path == "/input/nested/data.csv"

    for bad_path in ("/workspace/x.py", "/outputs/x.csv", "/data/x.csv",
                     "input/x.csv", "/input", "/input/"):
        with pytest.raises(ValueError):
            code_tools.BindInputArgs(
                task_id="code_" + "a" * 64, workspace_id="b" * 32,
                expected_revision=1, path=bad_path, file_id="c" * 32, sha256="d" * 64)

    with pytest.raises(ValueError):
        code_tools.BindInputArgs(
            task_id="code_" + "a" * 64, workspace_id="b" * 32, expected_revision=1,
            path="/input/x.csv", file_id="zz", sha256="d" * 64)
    with pytest.raises(ValueError):
        code_tools.BindInputArgs(
            task_id="code_" + "a" * 64, workspace_id="b" * 32, expected_revision=1,
            path="/input/x.csv", file_id="c" * 32, sha256="short")
    with pytest.raises(ValueError):
        code_tools.BindInputArgs(
            task_id="code_" + "a" * 64, workspace_id="b" * 32, expected_revision=1,
            path="/input/x.csv", file_id="c" * 32, sha256="d" * 64,
            owner_user_id=1)


def test_tool_is_registered_as_a_mutating_capability():
    assert "code_bind_input" in code_tools.CODE_TOOL_NAMES
    assert "code_bind_input" in code_tools._MUTATES
    schema = code_tools.BindInputArgs.model_json_schema()
    assert set(schema["required"]) == {
        "task_id", "workspace_id", "expected_revision", "path", "file_id", "sha256"}


def test_stale_code_write_remains_a_preflight_refusal_in_host(identity, monkeypatch):
    import asyncio

    from src.agent.host_tools import HostTools
    from src.tools import registry

    workspace = _workspace(identity)
    monkeypatch.setattr(registry, '_TOOL_SPECS', {})
    registry.register_tool(code_tools.code_write, write_scope='session_artifact', recovery_mode='never')

    def unexpected_file_registration(**kwargs):
        pytest.fail('a stale revision must be rejected before registering a file')

    monkeypatch.setattr(session_files, 'register_file', unexpected_file_registration)
    host = HostTools(identity, {})
    call = {'id': 'stale-code-write', 'name': 'code_write', 'args': {
        'task_id': workspace['task_id'], 'workspace_id': workspace['workspace_id'],
        'expected_revision': workspace['revision'] + 1,
        'path': '/workspace/analysis.py', 'content': 'print(42)',
    }}
    result = asyncio.run(host.invoke(code_tools.code_write, call, identity))
    payload = json.loads(result.message.content)
    assert not result.uncertain
    assert payload['_tool_outcome']['status'] == 'not_run'
    assert payload['_tool_outcome']['reason_code'] == 'workspace_revision_conflict'
    assert payload['latest_revision'] == workspace['revision']

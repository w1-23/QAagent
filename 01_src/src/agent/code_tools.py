"""Offline Code capabilities; HostTools binds identity and native call IDs.

Submission/query completion is never execution or scientific completion. The
host must turn only the returned real execution reference into an ExecutionWaits
watch using the original call ID. This module neither blocks nor creates waits.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
import base64
import hashlib
import json
import math
import os
import re
from typing import Annotated, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.code_execution.protocol import ExecutionLimits, logical_path
from .identity import ExecutionIdentity

__all__ = ["CODE_TOOL_NAMES", "register_code_tools", "code_execution_context"]

_context: ContextVar = ContextVar("code_execution_context", default=None)
Id = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Task = Annotated[str, Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")]
Path = Annotated[str, Field(min_length=1, max_length=240)]
Argument = Annotated[str, Field(max_length=4096)]


@contextmanager
def code_execution_context(identity, tool_call_id):
    """Host-only binding, propagated through registry's copied ContextVars."""
    if type(identity) is not ExecutionIdentity:
        raise ValueError("execution_identity_required")
    if (not isinstance(tool_call_id, str) or not 1 <= len(tool_call_id) <= 256
            or tool_call_id != tool_call_id.strip() or any(ord(c) < 32 for c in tool_call_id)):
        raise ValueError("execution_native_tool_call_id_required")
    token = _context.set((identity, tool_call_id))
    try:
        yield
    finally:
        _context.reset(token)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WorkspaceArgs(_Args):
    workspace_id: Id | None = None
    offset: int = Field(default=0, ge=0, le=4096)
    limit: int = Field(default=30, ge=1, le=100)


class WriteArgs(_Args):
    task_id: Task
    workspace_id: Id
    expected_revision: int = Field(ge=1, le=2**31 - 1)
    path: Path
    content: str = Field(max_length=65536)

    @field_validator("path")
    @classmethod
    def writable_path(cls, value):
        return logical_path(value, writable=True)

    @field_validator("content")
    @classmethod
    def text_limit(cls, value):
        if len(value.encode("utf-8")) > 65536:
            raise ValueError("execution_text_byte_limit")
        return value


class ExecuteArgs(_Args):
    task_id: Task
    workspace_id: Id
    expected_revision: int = Field(ge=1, le=2**31 - 1)
    script_path: Path
    inputs: list[Path] = Field(default_factory=list, max_length=128)
    outputs: list[Path] = Field(default_factory=list, max_length=32)
    argv: list[Argument] = Field(default_factory=list, max_length=32)

    @field_validator("script_path")
    @classmethod
    def script(cls, value):
        logical_path(value, writable=True)
        if not value.endswith(".py"):
            raise ValueError("execution_python_script_required")
        return value

    @field_validator("inputs", "outputs")
    @classmethod
    def paths(cls, values, info):
        for value in values:
            logical_path(value, writable=info.field_name == "outputs")
        if len(values) != len(set(values)):
            raise ValueError("execution_duplicate_path")
        return values

    @field_validator("argv")
    @classmethod
    def arguments(cls, values):
        if any("\0" in value for value in values) or sum(len(v.encode("utf-8")) for v in values) > 8192:
            raise ValueError("invalid_execution_arguments")
        return values


class ExecutionArgs(_Args):
    execution_id: Id


class LogArgs(ExecutionArgs):
    fence: int = Field(ge=1, le=2**63 - 1)
    stream: Literal["stdout", "stderr"]
    offset: int = Field(default=0, ge=0, le=4 * 1024**2)
    limit: int = Field(default=16384, ge=1, le=65536)


class OutputArgs(ExecutionArgs):
    path: Path

    @field_validator("path")
    @classmethod
    def output_path(cls, value):
        return logical_path(value, writable=True)


class ReadOutputArgs(OutputArgs):
    start_byte: int = Field(default=0, ge=0, le=256 * 1024**2)
    max_bytes: int = Field(default=8192, ge=1, le=65536)


def _enabled():
    return os.environ.get("CITRUS_CODE_EXECUTION") == "offline-development"


def _identity():
    from src.core import jobs, tracing
    if not _enabled():
        raise PermissionError("code_execution_disabled")
    binding = _context.get()
    if binding is None:
        raise PermissionError("code_execution_context_required")
    identity, call_id = binding
    job = jobs.get_job(tracing.get_job_id())
    if (not job or tracing.get_job_id() != identity.run_id
            or tracing.get_user_id() != identity.owner_user_id
            or tracing.get_session_id() != identity.session_id
            or job["owner_user_id"] != identity.owner_user_id
            or job["session_id"] != identity.session_id
            or job["request_id"] != identity.user_turn_id
            or job["status"] not in {"running", "detached"}):
        raise PermissionError("code_execution_scope_unavailable")
    return identity, call_id


def _key(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _service(identity):
    from src.code_execution.service import ExecutionService
    return ExecutionService(identity)


def _deadline(identity):
    from src.core import task_events
    # Observe the stored original wall deadline. Never initialize/extend a
    # budget or derive a changing timeout/fingerprint from remaining seconds.
    with task_events.transaction() as conn:
        row = conn.execute("SELECT deadline FROM task_budgets WHERE job_id=?", (identity.run_id,)).fetchone()
    if row is None:
        raise ValueError("code_execution_budget_required")
    value = row[0]
    if type(value) not in (float, int) or math.isnan(value) or value <= 0:
        raise ValueError("code_execution_budget_invalid")
    return value


def _result(data, *, reason="code_query_complete", failed=False, artifacts=(), status=None):
    payload = {**data, "_tool_outcome": {"status": status or ("failed" if failed else "complete"),
                                        "reason_code": reason, "artifacts": list(artifacts)}}
    return json.dumps(payload, ensure_ascii=False, allow_nan=False), payload


def _public(row, *, receipt=False):
    keys = ("execution_id", "task_id", "workspace_id", "status", "fence", "created", "updated", "deadline")
    result = {key: row[key] for key in keys}
    # Worker/internal exception text is never an error diagnostic here.
    reason = row.get("reason", "")
    result["reason"] = reason if re.fullmatch(r"[a-z0-9_]{0,96}", reason) else "execution_detail_withheld"
    if receipt:
        result["receipt"] = row["receipt"]  # Existing service-authorized acceptance evidence.
    return result


@contextmanager
def _accepted_file(service, identity, execution_id, path, *, max_bytes=None):
    """Resolve only a real terminal acceptance through existing authorization."""
    from src.code_execution.protocol import ExecutionRequest, FileSnapshot
    from src.core.account_leases import acquire_account_lease
    from src.session import files
    service.status(execution_id)  # Rechecks the account, session and live task.
    row = service.repository.get(execution_id, owner=identity.owner_user_id, session=identity.session_id)
    request = ExecutionRequest.parse(row["request"])
    receipt = row["receipt"]
    if (row["status"] != "succeeded" or path not in request.outputs
            or request.identity.owner_user_id != identity.owner_user_id
            or request.identity.session_id != identity.session_id
            or not isinstance(receipt, dict) or receipt.get("execution_id") != execution_id
            or receipt.get("fence") != row["fence"]
            or receipt.get("request_fingerprint") != request.fingerprint
            or type(receipt.get("files")) is not list):
        raise PermissionError("code_accepted_output_required")
    matches = [item for item in receipt["files"] if isinstance(item, dict) and item.get("path") == path]
    if len(matches) != 1:
        raise LookupError("code_accepted_output_required")
    ref = matches[0]
    FileSnapshot(**{key: ref[key] for key in ("path", "file_id", "version", "sha256", "size_bytes")})
    if (ref["size_bytes"] > request.limits.output_bytes
            or max_bytes is not None and ref["size_bytes"] > max_bytes):
        raise ValueError("code_output_size_mismatch")
    lease = acquire_account_lease(identity.owner_user_id, expected_epoch=request.account_epoch)
    try:
        # Historical versions matter: later writes must not replace accepted
        # evidence. list_files remains bounded by the existing workspace quota.
        offset, bound = 0, None
        while True:
            page = service.workspace.list_files(request.workspace_id, history=True, offset=offset, limit=100)
            bound = next((item for item in page["items"] if item["path"] == path
                          and item["version"] == ref["version"]), None)
            if bound is not None or page["next_offset"] is None:
                break
            offset = page["next_offset"]
        if (bound is None or any(bound[key] != ref[key] for key in ("file_id", "sha256", "size_bytes"))
                or bound["source"].get("execution_id") != execution_id
                or bound["source"].get("fence") != row["fence"]
                or bound["source"].get("request_fingerprint") != request.fingerprint):
            raise PermissionError("code_output_binding_mismatch")
        file = files.get_file(identity.session_id, ref["file_id"],
                              owner_user_id=identity.owner_user_id, allow_internal=True)
        if file is None or any(file[key] != ref[key] for key in ("sha256", "size_bytes", "mime_type")):
            raise PermissionError("code_output_file_mismatch")
        data = service.workspace.read_bytes(request.workspace_id, path=path,
                                            version=ref["version"], expected_sha256=ref["sha256"])
        if len(data) != ref["size_bytes"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise ValueError("code_output_integrity_mismatch")
        yield request, ref, data
    finally:
        lease.release()


def _dispatch(operation, **args):
    from src.core.account_leases import account_lease
    from src.workspaces import WorkspaceConflict
    from src.code_execution.repository import ExecutionConflict, QueueFull
    try:
        identity, call_id = _identity()  # Fresh tracing/job check before any service/store.
        with account_lease(identity.owner_user_id):
            service = _service(identity)
            workspace = service.workspace
            if operation == "workspace":
                wid = args.pop("workspace_id")
                if wid is None:
                    key = "code_" + _key(identity.run_id)
                    current = workspace.create(task_id=key, request_id=key)
                else:
                    current = workspace.open(wid)
                page = workspace.list_files(current["workspace_id"], **args)
                fields = ("path", "version", "file_id", "sha256", "size_bytes")
                return _result({"task_id": current["task_id"], "workspace_id": current["workspace_id"],
                    "revision": current["revision"], "files": [{k: item[k] for k in fields} for item in page["items"]],
                    "next_offset": page["next_offset"]})
            if operation == "write":
                current = workspace.open(args["workspace_id"])
                if current["task_id"] != args["task_id"]:
                    raise PermissionError("execution_task_scope_mismatch")
                if current["revision"] != args["expected_revision"]:
                    raise WorkspaceConflict(current["revision"])
                from src.session import files
                file = files.register_file(owner_user_id=identity.owner_user_id,
                    session_id=identity.session_id, filename=args["path"].rsplit("/", 1)[-1],
                    content=args["content"].encode("utf-8"), mime="text/plain", visibility="internal",
                    producer_tool="code_write", client_request_id=_key(identity.run_id, call_id))
                result = workspace.bind(args["workspace_id"], path=args["path"], file_id=file["file_id"],
                    expected_revision=args["expected_revision"], expected_sha256=file["sha256"])
                ref = result["ref"]
                return _result({"task_id": args["task_id"], "workspace_id": args["workspace_id"],
                    "revision": result["revision"], **{k: ref[k] for k in ("path", "file_id", "version", "sha256", "size_bytes")}},
                    reason="code_workspace_written")
            if operation == "execute":
                from src.code_execution.presence import worker_available
                if not worker_available(service.repository.path):
                    return _result({"error": "code_worker_unavailable"},
                                   reason="code_worker_unavailable", status="not_run")
                key = "code_" + _key(identity.run_id, call_id)
                row = service.submit(task_id=args["task_id"], workspace_id=args["workspace_id"],
                    expected_revision=args["expected_revision"], script_path=args["script_path"],
                    inputs=tuple(args["inputs"]), outputs=tuple(args["outputs"]), arguments=tuple(args["argv"]),
                    operation_id=key, idempotency_key=key, network_policy="offline",
                    environment_id="base", environment_revision=1, limits=ExecutionLimits(),
                    absolute_deadline=_deadline(identity))
                status = _public(service.status(row["execution_id"]))
                return _result({**status, "_code_execution_id": row["execution_id"],
                    "completion_scope": "submission_only"}, reason="execution_submitted")
            execution_id = args.pop("execution_id")
            if operation in {"read", "deliver"}:
                with _accepted_file(service, identity, execution_id, args["path"]) as (request, ref, data):
                    if operation == "read":
                        start = args["start_byte"]
                        if start > len(data):
                            raise ValueError("code_output_offset_out_of_range")
                        chunk = data[start:start + args["max_bytes"]]
                        end = start + len(chunk)
                        return _result({"execution_id": execution_id, "path": ref["path"], "version": ref["version"],
                            "sha256": ref["sha256"], "size_bytes": len(data), "start_byte": start,
                            "returned_bytes": len(chunk), "next_start_byte": end if end < len(data) else None,
                            "eof": end == len(data), "data_base64": base64.b64encode(chunk).decode("ascii"),
                            "text_preview": chunk.decode("utf-8", errors="replace"),
                            "text_preview_encoding": "utf-8-replace"}, reason="code_output_read")
                    from src.research.tools import _save_artifact, validate_artifact_links
                    link = _save_artifact(ref["path"].rsplit("/", 1)[-1], data, ref["mime_type"],
                        source_file_id=ref["file_id"], source_sha256=ref["sha256"],
                        logical_task_id="code_" + _key(execution_id, ref["path"], ref["sha256"]))
                    if not re.fullmatch(r"/api/v2/session/[A-Za-z0-9_-]{8,128}/artifacts/[a-f0-9]{32}", link):
                        raise ValueError("code_artifact_url_unverified")
                    checked = validate_artifact_links([link], session_id=identity.session_id,
                                                       owner_user_id=identity.owner_user_id)
                    if len(checked) != 1 or not checked[0]["valid"]:
                        raise ValueError("code_artifact_url_unverified")
                    return _result({"execution_id": execution_id, "path": ref["path"],
                        "filename": checked[0]["filename"], "mime": checked[0]["mime"],
                        "artifact_url": link, "source_sha256": ref["sha256"],
                        "source_size_bytes": ref["size_bytes"]}, reason="code_output_delivered", artifacts=[link])
            if operation == "log":
                return _result(service.read_log(execution_id, **args))
            if operation == "cancel":
                service.cancel(execution_id)
            status = _public(service.status(execution_id), receipt=operation == "status")
            if operation == "wait":
                return _result({**status, "_code_execution_id": status["execution_id"],
                    "completion_scope": "wait_registration_only"}, reason="execution_wait_registered")
            return _result(status)
    except WorkspaceConflict as exc:
        return _result({"error": "workspace_revision_conflict", "latest_revision": exc.latest_revision},
                       reason="workspace_revision_conflict", failed=True)
    except ExecutionConflict:
        return _result({"error": "execution_conflict"}, reason="execution_conflict", failed=True)
    except QueueFull:
        return _result({"error": "execution_queue_full"}, reason="execution_queue_full", failed=True)
    except Exception:
        # Includes admission, file integrity, DB and OS errors. Never expose
        # physical paths, credentials, request JSON or internal exception text.
        return _result({"error": "code_unavailable_or_rejected"}, reason="code_unavailable_or_rejected", failed=True)


@tool(args_schema=WorkspaceArgs, response_format="content_and_artifact")
def code_workspace(workspace_id: str | None = None, offset: int = 0, limit: int = 30):
    """Create/open one persistent task workspace per current run, or open an authorized existing workspace_id. Return task_id, workspace_id, revision and bounded versioned logical files. Repeating without an ID reuses the same task, including after recovery. Never executes or publishes files."""
    return _dispatch("workspace", workspace_id=workspace_id, offset=offset, limit=limit)


@tool(args_schema=WriteArgs, response_format="content_and_artifact")
def code_write(task_id: str, workspace_id: str, expected_revision: int, path: str, content: str):
    """Write at most 64 KiB UTF-8 script/text to /workspace or /outputs of an authorized task using workspace revision CAS. Return the new revision, real file_id/version and SHA256. Scripts must be written before execution; outputs staging is not publication."""
    return _dispatch("write", task_id=task_id, workspace_id=workspace_id,
                     expected_revision=expected_revision, path=path, content=content)


@tool(args_schema=ExecuteArgs, response_format="content_and_artifact")
def code_execute(task_id: str, workspace_id: str, expected_revision: int, script_path: str,
                 inputs: list[str] | None = None, outputs: list[str] | None = None, argv: list[str] | None = None):
    """Submit a bound .py snapshot in fixed offline base environment revision 1, with existing Python standard library and worker-installed base packages only; no package installation. Select bound logical input paths and declared logical outputs; argv are Python script arguments, never a shell command. Default limits: 120 s, 1024 MiB, 1 CPU, 128 PIDs, 64 MiB scratch/output, 1 MiB logs, 32 outputs, constrained by the original task deadline. Worker controls image/backend/device. Complete means submission recorded, not code or analysis success. Use code_status/log/wait for real execution evidence."""
    return _dispatch("execute", task_id=task_id, workspace_id=workspace_id,
                     expected_revision=expected_revision, script_path=script_path,
                     inputs=inputs or [], outputs=outputs or [], argv=argv or [])


@tool(args_schema=ExecutionArgs, response_format="content_and_artifact")
def code_status(execution_id: str):
    """Query one real authorized execution's public status and existing acceptance receipt. A successful query does not mean the code succeeded. Only a succeeded execution receipt provides execution completion evidence; it does not prove scientific correctness."""
    return _dispatch("status", execution_id=execution_id)


@tool(args_schema=LogArgs, response_format="content_and_artifact")
def code_log(execution_id: str, fence: int, stream: str, offset: int = 0, limit: int = 16384):
    """Read an authorized execution's captured stdout/stderr page by fence and byte offset. Use next_offset for continuation. Base64 preserves bytes; text_preview is lossy. Missing, uncaptured or expired logs return a safe failure, never invented output."""
    return _dispatch("log", execution_id=execution_id, fence=fence, stream=stream, offset=offset, limit=limit)


@tool(args_schema=ExecutionArgs, response_format="content_and_artifact")
def code_cancel(execution_id: str):
    """Request cancellation of a real authorized execution. cancel_requested/unknown never prove physical stop; only a terminal state provides confirmed lifecycle evidence."""
    return _dispatch("cancel", execution_id=execution_id)


@tool(args_schema=ExecutionArgs, response_format="content_and_artifact")
def code_wait(execution_id: str):
    """Return one authorized real execution reference immediately for the host to register a durable watch using its native tool_call_id. Never blocks, creates a wait ID or executes code. Complete means registration/query accepted, not execution completion; terminal references may wake immediately."""
    return _dispatch("wait", execution_id=execution_id)


@tool(args_schema=ReadOutputArgs, response_format="content_and_artifact")
def code_read(execution_id: str, path: str, start_byte: int = 0, max_bytes: int = 8192):
    """Read an accepted output of a succeeded execution by its declared logical path. Rechecks the original account epoch, exact historical task binding, file owner/session and SHA256. Return at most 64 KiB as base64 and a lossy UTF-8 preview, continuing via next_start_byte. Does not publish or register a wait."""
    return _dispatch("read", execution_id=execution_id, path=path, start_byte=start_byte, max_bytes=max_bytes)


@tool(args_schema=OutputArgs, response_format="content_and_artifact")
def code_deliver(execution_id: str, path: str):
    """Publish one declared, accepted output from a succeeded execution through the existing artifact registry. Rechecks original account epoch, task binding, owner/session and file bytes/hash. Return only a verified /api/v2/session/.../artifacts/... URL and bounded source metadata. Repeated delivery of the same accepted source within this turn reuses publication; accepted private file IDs are never download URLs. Delivery does not certify scientific correctness or register a wait."""
    return _dispatch("deliver", execution_id=execution_id, path=path)


_TOOLS = (code_workspace, code_write, code_execute, code_status, code_log, code_cancel, code_wait,
          code_read, code_deliver)
CODE_TOOL_NAMES = frozenset(item.name for item in _TOOLS)
_MUTATES = frozenset({"code_workspace", "code_write", "code_execute", "code_cancel", "code_deliver"})


def _load(item, mutates):
    if not _enabled():
        raise PermissionError("code_execution_disabled")
    from src.tools.registry import register_tool
    register_tool(item, category="code", is_readonly=not mutates,
                  concurrency_key="code_workspace" if mutates else None,
                  write_scope="session_artifact" if mutates else None, recovery_mode="never")
    return item


def register_code_tools(catalog):
    """Opt-in descriptions only; no Host activation, worker or run is captured."""
    if not _enabled():
        return
    from .capabilities import CapabilityDefinition
    for item in _TOOLS:
        mutates = item.name in _MUTATES
        catalog.register(CapabilityDefinition(item.name, item.description,
            item.args_schema.model_json_schema(), partial(_load, item, mutates),
            provider="code", mutates=mutates,
            execution_mode="sequential" if mutates or item.name == "code_wait" else "parallel"))

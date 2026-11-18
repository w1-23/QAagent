"""Versioned, JSON-only execution snapshots. References never grant authority.

The host supplies identity, limits, policy and environment. The worker resolves
only an already-authorized snapshot; no request can carry a host filesystem path,
container option, secret, callable or executable serialized Python object.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
from pathlib import PurePosixPath
import re

from src.agent.identity import ExecutionIdentity

PROTOCOL_VERSION = "citrus.code/1"
_ID = re.compile(r"[a-f0-9]{32}")
_SHA = re.compile(r"[a-f0-9]{64}")
_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}")
TERMINAL = frozenset({"succeeded", "failed", "timed_out", "cancelled"})
OCCUPYING = frozenset({"preparing", "running", "collecting", "cancel_requested", "unknown"})


def token(value: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError("invalid_execution_token")
    return value


def identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("invalid_execution_id")
    return value


def logical_path(value: str, *, writable: bool = False) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 240:
        raise ValueError("invalid_execution_path")
    path = PurePosixPath(value)
    roots = {"workspace", "outputs"} if writable else {"input", "workspace", "outputs"}
    parts = value.split("/")
    if (not path.is_absolute() or len(parts) < 3 or parts[0] or parts[1] not in roots
            or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. -]{0,119}", p)
                   or ".." in p or p.endswith((".", " ")) for p in parts[2:])):
        raise ValueError("invalid_execution_path")
    return value


def bounded_integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid_execution_" + name)
    return value


def json_object(value: dict, *, max_bytes: int = 131072) -> str:
    if not isinstance(value, dict):
        raise ValueError("execution_json_object_required")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > max_bytes:
            raise ValueError("execution_json_limit")
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid_execution_json") from exc
    return encoded


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    file_id: str
    version: int
    sha256: str
    size_bytes: int

    def __post_init__(self):
        logical_path(self.path)
        identifier(self.file_id)
        bounded_integer(self.version, 1, 2**31 - 1, "file_version")
        bounded_integer(self.size_bytes, 0, 256 * 1024**2, "input_size")
        if not isinstance(self.sha256, str) or not _SHA.fullmatch(self.sha256):
            raise ValueError("invalid_execution_file_hash")


@dataclass(frozen=True)
class ExecutionLimits:
    timeout_sec: int = 120
    memory_mb: int = 1024
    cpu_millis: int = 1000
    pids: int = 128
    scratch_bytes: int = 64 * 1024**2
    log_bytes: int = 1024 * 1024
    output_bytes: int = 64 * 1024**2
    output_files: int = 32

    def __post_init__(self):
        for name, lower, upper in (("timeout_sec", 1, 3600), ("memory_mb", 128, 16384),
                ("cpu_millis", 100, 8000), ("pids", 16, 512),
                ("scratch_bytes", 1024**2, 1024**3), ("log_bytes", 1024, 4 * 1024**2),
                ("output_bytes", 1, 256 * 1024**2), ("output_files", 1, 128)):
            bounded_integer(getattr(self, name), lower, upper, name)
        if self.output_bytes > self.scratch_bytes:
            raise ValueError("execution_output_exceeds_scratch")


@dataclass(frozen=True)
class ExecutionRequest:
    identity: ExecutionIdentity
    task_id: str
    workspace_id: str
    workspace_revision: int
    operation_id: str
    idempotency_key: str
    script: FileSnapshot
    account_epoch: int = 1
    inputs: tuple[FileSnapshot, ...] = ()
    arguments: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    environment_id: str = "base"
    environment_revision: int = 1
    network_policy: str = "offline"
    limits: ExecutionLimits = ExecutionLimits()
    protocol: str = PROTOCOL_VERSION

    def __post_init__(self):
        if type(self.identity) is not ExecutionIdentity or type(self.script) is not FileSnapshot:
            raise ValueError("invalid_execution_snapshot")
        if type(self.limits) is not ExecutionLimits or self.protocol != PROTOCOL_VERSION:
            raise ValueError("unsupported_execution_protocol")
        token(self.task_id)
        identifier(self.workspace_id)
        token(self.operation_id)
        token(self.idempotency_key)
        token(self.environment_id)
        bounded_integer(self.account_epoch, 0, 2**63 - 1, "account_epoch")
        bounded_integer(self.workspace_revision, 0, 2**31 - 1, "workspace_revision")
        bounded_integer(self.environment_revision, 1, 2**31 - 1, "environment_revision")
        if self.network_policy not in {"offline", "research_https"}:
            raise ValueError("unsupported_execution_network_policy")
        logical_path(self.script.path, writable=True)
        if not self.script.path.endswith(".py") or self.script.size_bytes > 200000:
            raise ValueError("execution_python_script_required")
        if type(self.inputs) is not tuple or len(self.inputs) > 128:
            raise ValueError("execution_input_limit")
        if any(type(item) is not FileSnapshot for item in self.inputs):
            raise ValueError("invalid_execution_input")
        paths = [self.script.path, *(item.path for item in self.inputs)]
        if len(paths) != len(set(paths)):
            raise ValueError("execution_duplicate_path")
        if sum(item.size_bytes for item in self.inputs) > 256 * 1024**2:
            raise ValueError("execution_input_byte_limit")
        if (type(self.arguments) is not tuple or len(self.arguments) > 32
                or any(not isinstance(arg, str) or "\0" in arg or len(arg) > 4096
                       for arg in self.arguments)
                or sum(len(arg.encode("utf-8")) for arg in self.arguments) > 8192):
            raise ValueError("invalid_execution_arguments")
        if type(self.outputs) is not tuple or len(self.outputs) > self.limits.output_files:
            raise ValueError("execution_output_limit")
        for path in self.outputs:
            logical_path(path, writable=True)
        if len(set(self.outputs)) != len(self.outputs):
            raise ValueError("execution_duplicate_output")

    def as_dict(self) -> dict:
        return asdict(self)

    def serialize(self) -> str:
        return json_object(self.as_dict())

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.serialize().encode("utf-8")).hexdigest()

    @classmethod
    def parse(cls, payload: str) -> ExecutionRequest:
        if not isinstance(payload, str) or len(payload.encode("utf-8")) > 131072:
            raise ValueError("execution_request_limit")
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate_execution_json_key")
                value[key] = item
            return value
        raw = json.loads(payload, object_pairs_hook=unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json")))
        if not isinstance(raw, dict) or set(raw) != {f.name for f in fields(cls)}:
            raise ValueError("invalid_execution_request_fields")
        if any(type(raw[name]) is not list for name in ("inputs", "arguments", "outputs")):
            raise ValueError("execution_array_required")
        try:
            raw["identity"] = ExecutionIdentity(**raw["identity"])
            raw["script"] = FileSnapshot(**raw["script"])
            raw["inputs"] = tuple(FileSnapshot(**item) for item in raw["inputs"])
            raw["arguments"] = tuple(raw["arguments"])
            raw["outputs"] = tuple(raw["outputs"])
            raw["limits"] = ExecutionLimits(**raw["limits"])
            return cls(**raw)
        except (TypeError, KeyError) as exc:
            raise ValueError("invalid_execution_request") from exc

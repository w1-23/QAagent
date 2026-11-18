"""Protected, execution-bound LangGraph checkpoints (never public history).

The caller supplies an admitted identity, provider scope and runtime state root.
Reopening requires the SAME run identity; cross-run recovery/admission belongs to
execution policy. Only synthetic tests import this module without application config.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, AsyncIterator

import aiosqlite
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from langchain_core.messages import (
    AIMessage, AIMessageChunk, BaseMessage, ChatMessage, ChatMessageChunk,
    FunctionMessage, FunctionMessageChunk, HumanMessage, HumanMessageChunk,
    RemoveMessage, SystemMessage, SystemMessageChunk, ToolMessage, ToolMessageChunk,
)
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
# DeepAgentState uses this private LangGraph snapshot type; pin the SDK version.
from langgraph.checkpoint.serde.types import _DeltaSnapshot
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Interrupt, Send

from src.agent.identity import ExecutionIdentity
from src.core.provider_protocol import _DeepSeekMessage, _DeepSeekMessageChunk, _ProtocolState

_TAG = "__citrus_checkpoint_v1__"
_FORMAT = "citrus-checkpoint-v1"
_CIPHER = "aesgcm-v1"
_MESSAGES = (
    AIMessage, AIMessageChunk, ChatMessage, ChatMessageChunk,
    FunctionMessage, FunctionMessageChunk, HumanMessage, HumanMessageChunk,
    RemoveMessage, SystemMessage, SystemMessageChunk, ToolMessage, ToolMessageChunk,
)


class CheckpointProtectionError(ValueError):
    """A checkpoint cannot safely be written or resumed; contains no payload data."""


def _binding(identity: ExecutionIdentity, provider_scope: tuple[str, str]) -> dict:
    if not isinstance(identity, ExecutionIdentity):
        raise CheckpointProtectionError("checkpoint_identity_required")
    if (type(provider_scope) is not tuple or len(provider_scope) != 2
            or any(not isinstance(v, str) or not v or v != v.strip()
                   or any(ord(c) < 32 for c in v) for v in provider_scope)):
        raise CheckpointProtectionError("checkpoint_provider_scope_required")
    return {**asdict(identity), "provider": provider_scope[0], "model": provider_scope[1]}


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def checkpoint_scope(identity: ExecutionIdentity, provider_scope: tuple[str, str]) -> dict:
    """Stable LangGraph configurable mapping; no user-controlled filesystem names."""
    digest = hashlib.sha256(_canonical(_binding(identity, provider_scope))).hexdigest()
    return {"thread_id": digest, "checkpoint_ns": ""}


def checkpoint_path(root: Path, identity: ExecutionIdentity,
                    provider_scope: tuple[str, str]) -> Path:
    return Path(root) / "checkpoints" / checkpoint_scope(identity, provider_scope)["thread_id"] / "checkpoint.sqlite3"


class _PrivateMessages(SerializerProtocol):
    """Transform private messages explicitly; delegate ordinary types to JsonPlus.

    No application class is in the revival allowlist. Only the built-in safe
    JsonPlus types are enabled, pickle/legacy JSON decoding are never accepted.
    Tuple tags preserve tuples (JsonPlus otherwise projects them into lists).
    """

    def __init__(self, binding: dict):
        self.binding = binding
        self.scope = (binding["provider"], binding["model"])
        self.delegate = JsonPlusSerializer(
            pickle_fallback=False, allowed_json_modules=[], allowed_msgpack_modules=[])

    def _state(self, value: Any) -> _ProtocolState:
        if (type(value) is not dict or set(value) != {"scope", "reasoning"}
                or not isinstance(value["reasoning"], str)):
            raise CheckpointProtectionError("private_protocol_state_missing")
        if value["scope"] != list(self.scope):
            raise CheckpointProtectionError("private_protocol_scope_mismatch")
        return _ProtocolState(self.scope, value["reasoning"])

    def _walk(self, value: Any, *, reading: bool = False) -> Any:
        if type(value) is _DeltaSnapshot:
            if reading:
                raise CheckpointProtectionError("unexpected_delta_snapshot_revival")
            # Recurse before serialization so private protocol fields survive
            # inside snapshots without enabling JsonPlus class revival.
            return {_TAG: "delta_snapshot", "value": self._walk(value.value)}
        if type(value) in (_DeepSeekMessage, _DeepSeekMessageChunk):
            if reading:
                raise CheckpointProtectionError("unexpected_private_message_revival")
            state = value.provider_protocol
            private = None if state is None else {"scope": list(state.scope), "reasoning": state.reasoning}
            self._state(private)
            for name, field in value.__dict__.items():
                if name != "provider_protocol":
                    self._check_public_field(field)
            return {_TAG: "deepseek_chunk" if type(value) is _DeepSeekMessageChunk else "deepseek",
                    "public": self._walk(value.model_dump()), "private": private}
        if type(value) is dict:
            if _TAG in value:
                if not reading:
                    raise CheckpointProtectionError("reserved_checkpoint_tag")
                kind = value[_TAG]
                if kind == "delta_snapshot" and set(value) == {_TAG, "value"}:
                    return _DeltaSnapshot(self._walk(value["value"], reading=True))
                if kind == "bytearray" and set(value) == {_TAG, "value"} and type(value["value"]) is bytes:
                    return bytearray(value["value"])
                if kind in ("tuple", "set", "frozenset") and set(value) == {_TAG, "items"} and type(value["items"]) is list:
                    cls = {"tuple": tuple, "set": set, "frozenset": frozenset}[kind]
                    return cls(self._walk(v, reading=True) for v in value["items"])
                if kind == "message" and set(value) == {_TAG, "class", "public"}:
                    classes = {cls.__name__: cls for cls in _MESSAGES}
                    cls = classes.get(value["class"])
                    public = self._walk(value["public"], reading=True)
                    if cls is None or type(public) is not dict:
                        raise CheckpointProtectionError("invalid_public_message")
                    self._check_public_field(public)
                    return cls(**public)
                if kind in ("deepseek", "deepseek_chunk") and set(value) == {_TAG, "public", "private"}:
                    state = self._state(value["private"])
                    public = self._walk(value["public"], reading=True)
                    if type(public) is not dict or "provider_protocol" in public:
                        raise CheckpointProtectionError("invalid_private_message_public_fields")
                    cls = _DeepSeekMessage if kind == "deepseek" else _DeepSeekMessageChunk
                    try:
                        return cls(**public, provider_protocol=state)
                    except Exception:
                        raise CheckpointProtectionError("invalid_private_message") from None
                raise CheckpointProtectionError("invalid_checkpoint_tag")
            if any(type(k) not in (str, int, float, bool, bytes, type(None)) for k in value):
                raise CheckpointProtectionError("unsupported_checkpoint_key")
            return {k: self._walk(v, reading=reading) for k, v in value.items()}
        if type(value) is tuple:
            if reading:
                return tuple(self._walk(v, reading=True) for v in value)
            return {_TAG: "tuple", "items": [self._walk(v) for v in value]}
        if type(value) in (set, frozenset) and not reading:
            return {_TAG: type(value).__name__, "items": [self._walk(v) for v in value]}
        if type(value) in (list, set, frozenset):
            return type(value)(self._walk(v, reading=reading) for v in value)
        if type(value) is Send:
            return Send(value.node, self._walk(value.arg, reading=reading), timeout=value.timeout)
        if type(value) is Interrupt:
            return replace(value, value=self._walk(value.value, reading=reading))
        if type(value) in _MESSAGES:
            # Public message fields may contain data, but never embedded private
            # message instances whose excluded fields model_dump would discard.
            for field in value.__dict__.values():
                self._check_public_field(field)
            if reading:
                return value  # Compatibility with earlier public-message checkpoints.
            return {_TAG: "message", "class": type(value).__name__,
                    "public": self._walk(value.model_dump())}
        if type(value) is bytearray and not reading:
            return {_TAG: "bytearray", "value": bytes(value)}
        if value is None or type(value) in (str, bool, int, float, bytes, bytearray):
            return value
        raise CheckpointProtectionError("unsupported_checkpoint_value")

    def _check_public_field(self, value: Any) -> None:
        if isinstance(value, BaseMessage):
            raise CheckpointProtectionError("nested_message_in_public_fields")
        if type(value) is dict:
            if _TAG in value or "provider_protocol" in value:
                raise CheckpointProtectionError("private_state_in_public_fields")
            for child in value.values():
                self._check_public_field(child)
        elif type(value) in (list, tuple, set, frozenset):
            for child in value:
                self._check_public_field(child)
        elif value is not None and type(value) not in (str, bool, int, float, bytes, bytearray):
            raise CheckpointProtectionError("unsupported_public_message_field")

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        envelope = {"version": 1, "binding": self.binding, "value": self._walk(obj)}
        typ, payload = self.delegate.dumps_typed(envelope)
        if typ != "msgpack":
            raise CheckpointProtectionError("unsupported_checkpoint_format")
        return _FORMAT, payload

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        if data[0] != _FORMAT:
            raise CheckpointProtectionError("unsupported_checkpoint_format")
        try:
            envelope = self.delegate.loads_typed(("msgpack", data[1]))
            if (type(envelope) is not dict or set(envelope) != {"version", "binding", "value"}
                    or envelope["version"] != 1 or envelope["binding"] != self.binding):
                raise CheckpointProtectionError("checkpoint_binding_mismatch")
            return self._walk(envelope["value"], reading=True)
        except CheckpointProtectionError:
            raise
        except Exception:
            raise CheckpointProtectionError("invalid_checkpoint_payload") from None


class _AesGcm:
    def __init__(self, key: bytes, binding: dict):
        self.aes = AESGCM(key)
        self.aad = _FORMAT.encode() + b"\0" + _canonical(binding)

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        nonce = os.urandom(12)
        return _CIPHER, nonce + self.aes.encrypt(nonce, plaintext, self.aad)

    def decrypt(self, ciphername: str, ciphertext: bytes) -> bytes:
        if ciphername != _CIPHER or len(ciphertext) < 28:
            raise CheckpointProtectionError("invalid_checkpoint_ciphertext")
        try:
            return self.aes.decrypt(ciphertext[:12], ciphertext[12:], self.aad)
        except InvalidTag:
            raise CheckpointProtectionError("checkpoint_authentication_failed") from None


class ProtectedCheckpointSerializer(EncryptedSerializer):
    """Encrypted-only public serializer, also used for every pending write."""

    def __init__(self, key: bytes, identity: ExecutionIdentity, provider_scope: tuple[str, str]):
        binding = _binding(identity, provider_scope)
        super().__init__(_AesGcm(key, binding), _PrivateMessages(binding))

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        # Upstream EncryptedSerializer accepts plaintext. This boundary does not.
        if data[0] != f"{_FORMAT}+{_CIPHER}":
            raise CheckpointProtectionError("encrypted_checkpoint_required")
        return super().loads_typed(data)


def _directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not stat.S_ISDIR(path.lstat().st_mode) or path.is_symlink():
        raise CheckpointProtectionError("unsafe_checkpoint_directory")
    if os.name == "posix":
        path.chmod(0o700)


def _read_key(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if path.is_symlink():
        raise CheckpointProtectionError("unsafe_checkpoint_key")
    with os.fdopen(os.open(path, flags), "rb") as stream:
        mode = os.fstat(stream.fileno()).st_mode
        if not stat.S_ISREG(mode) or (os.name == "posix" and mode & 0o077):
            raise CheckpointProtectionError("unsafe_checkpoint_key_permissions")
        key = stream.read(33)
    if len(key) != 32:
        raise CheckpointProtectionError("invalid_checkpoint_key")
    return key


def _load_key(directory: Path) -> bytes:
    path = directory / "storage.key"
    try:
        return _read_key(path)
    except FileNotFoundError:
        if any(directory.glob("*/checkpoint.sqlite3")):
            # Another creator may have published the key and opened its database
            # between our read and glob. Never replace a genuinely missing key.
            try:
                return _read_key(path)
            except FileNotFoundError:
                raise CheckpointProtectionError("checkpoint_key_missing") from None
    # Publish a fully written key with an atomic EXCLUSIVE hard link. O_EXCL on
    # the final file alone would expose an empty/partial key to another process.
    # NTFS and Linux support this; unsupported filesystems fail closed.
    fd, name = tempfile.mkstemp(prefix=".key-", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(AESGCM.generate_key(bit_length=256))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
            if os.name == "posix":
                directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except FileExistsError:
            pass
    finally:
        temporary.unlink(missing_ok=True)
    return _read_key(path)


class _ProtectedSqliteSaver(AsyncSqliteSaver):
    async def aput(self, config, checkpoint, metadata, new_versions):
        # SQLite's metadata column bypasses serde. Persist only fixed control
        # fields, NEVER arbitrary metadata/config strings, messages or reasoning.
        safe_metadata = {}
        if type(metadata.get("source")) is str and metadata["source"] in {"input", "loop", "update", "fork"}:
            safe_metadata["source"] = metadata["source"]
        if type(metadata.get("step")) is int:
            safe_metadata["step"] = metadata["step"]
        safe_metadata["parents"] = {}
        safe_config = {"configurable": {k: v for k, v in config["configurable"].items()
                                        if k in {"thread_id", "checkpoint_ns", "checkpoint_id"}}}
        return await super().aput(safe_config, checkpoint, safe_metadata, new_versions)


def _prepare(root: Path, identity: ExecutionIdentity, provider_scope: tuple[str, str]):
    directory = Path(root) / "checkpoints"
    _directory(directory)
    key = _load_key(directory)
    path = checkpoint_path(root, identity, provider_scope)
    _directory(path.parent)
    # Precreate with a restrictive mode so SQLite doesn't use umask defaults.
    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        # Never open/close a second raw descriptor on an active SQLite file:
        # POSIX close can release that process's advisory SQLite locks.
        mode = path.lstat().st_mode
        if path.is_symlink() or not stat.S_ISREG(mode):
            raise CheckpointProtectionError("unsafe_checkpoint_database")
        if os.name == "posix" and mode & 0o077:
            raise CheckpointProtectionError("unsafe_checkpoint_database_permissions")
    else:
        os.close(fd)
    return path, key


@asynccontextmanager
async def open_checkpoint(root: Path, identity: ExecutionIdentity,
                          provider_scope: tuple[str, str]) -> AsyncIterator[AsyncSqliteSaver]:
    """Open protected storage under caller-owned state root; closes on failure.

    Use config={"configurable": checkpoint_scope(identity, provider_scope)}.
    Root must be a trusted, local service state directory (Windows ACLs remain
    the deployment owner's responsibility). Metadata is limited to source/step;
    arbitrary metadata and parent-namespace indexes are intentionally omitted.
    """
    _binding(identity, provider_scope)  # Validate before creating any state.
    path, key = await asyncio.to_thread(_prepare, Path(root), identity, provider_scope)
    async with aiosqlite.connect(str(path)) as connection:
        yield _ProtectedSqliteSaver(
            connection, serde=ProtectedCheckpointSerializer(key, identity, provider_scope))

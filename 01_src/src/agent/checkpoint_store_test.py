"""Offline tests of the production checkpoint boundary, using synthetic data only."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest
from pydantic import Field
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.load import dumpd
from langchain_core.messages import (
    AIMessage, AIMessageChunk, HumanMessage, RemoveMessage, SystemMessage,
    ToolMessage, messages_to_dict,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Interrupt, Send

from src.agent.checkpoint_store import (
    CheckpointProtectionError, ProtectedCheckpointSerializer,
    checkpoint_path, checkpoint_scope, open_checkpoint,
)
from src.agent.identity import ExecutionIdentity
from src.core.provider_protocol import _DeepSeekMessage, _DeepSeekMessageChunk, _ProtocolState

IDENTITY = ExecutionIdentity(17, "synthetic-session", "synthetic-turn", "synthetic-run")
SCOPE = ("https://api.deepseek.com/v1", "deepseek-reasoner")
PRIVATE = "synthetic-P3-private-reasoning-never-public"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Windows event-loop initialization needs its own loopback socketpair.
    loop = asyncio.new_event_loop()

    def forbidden(*args, **kwargs):
        raise AssertionError("Checkpoint test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"):
        monkeypatch.setenv(name, "false")
    yield loop
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.run_until_complete(loop.shutdown_default_executor())
    loop.close()


def _message(reasoning=PRIVATE):
    return _DeepSeekMessage(content="", id="synthetic-assistant", tool_calls=[{
        "name": "lookup", "args": {}, "id": "synthetic-call", "type": "tool_call",
    }], provider_protocol=_ProtocolState(SCOPE, reasoning))


def _config():
    return {"configurable": checkpoint_scope(IDENTITY, SCOPE)}


def _checkpoint():
    value = empty_checkpoint()
    value["channel_values"] = {"messages": [HumanMessage(content="Task"), _message()]}
    value["channel_versions"] = {"messages": 1}
    return value


def _assert_hidden(message):
    assert message.provider_protocol.reasoning == PRIVATE
    for public in (message.model_dump_json(), repr(message),
                   json.dumps(messages_to_dict([message])), json.dumps(dumpd(message))):
        assert PRIVATE not in public
        assert "provider_protocol" not in public


class _Model(BaseChatModel):
    observed: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "synthetic-checkpoint-model"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.observed.append(list(messages))
        message = AIMessage(content="Synthetic done") if isinstance(messages[-1], ToolMessage) else _message()
        return ChatResult(generations=[ChatGeneration(message=message)])


@tool
def lookup() -> str:
    """Return a synthetic local tool result."""
    return "Synthetic local result"


def test_real_agent_interrupt_close_reopen_resume(tmp_path, offline):
    async def exercise():
        async with open_checkpoint(tmp_path, IDENTITY, SCOPE) as saver:
            assert isinstance(saver, AsyncSqliteSaver)
            assert isinstance(saver.serde, (SerializerProtocol, EncryptedSerializer))
            graph = create_agent(_Model(), tools=[lookup], checkpointer=saver, interrupt_before=["tools"])
            result = await graph.ainvoke({"messages": [HumanMessage(content="Synthetic task")]}, _config())
            _assert_hidden(result["messages"][-1])
            state = await graph.aget_state(_config())
            assert state.next == ("tools",)
        model = _Model()
        async with open_checkpoint(tmp_path, IDENTITY, SCOPE) as saver:
            graph = create_agent(model, tools=[lookup], checkpointer=saver, interrupt_before=["tools"])
            result = await graph.ainvoke(None, _config())
            assert result["messages"][-1].content == "Synthetic done"
            assert len(model.observed) == 1
            restored = next(m for m in model.observed[0] if isinstance(m, _DeepSeekMessage))
            _assert_hidden(restored)
            assert model.observed[0][-1].tool_call_id == "synthetic-call"
            history = [entry async for entry in saver.alist(_config())]
            assert len(history) >= 4
            for entry in history:
                assert PRIVATE not in json.dumps(entry.metadata)
        for path in tmp_path.rglob("*"):
            if path.is_file():
                assert PRIVATE.encode() not in path.read_bytes()

    offline.run_until_complete(exercise())


def test_pending_writes_encrypted_restored_and_metadata_omitted(tmp_path, offline):
    async def exercise():
        config = _config()
        config["metadata"] = {"reasoning_content": PRIVATE}
        config["configurable"]["arbitrary_secret"] = PRIVATE
        async with open_checkpoint(tmp_path, IDENTITY, SCOPE) as saver:
            saved = await saver.aput(config, _checkpoint(), {
                "source": "input", "step": 0, "reasoning_content": PRIVATE,
                "parents": {PRIVATE: PRIVATE}, "messages": [_message()],
            }, {})
            await saver.aput_writes(saved, [("messages", [_message()])], "synthetic-task")
        async with open_checkpoint(tmp_path, IDENTITY, SCOPE) as saver:
            restored = await saver.aget_tuple(saved)
            assert restored.metadata == {"source": "input", "step": 0, "parents": {}}
            _assert_hidden(restored.pending_writes[0][2][0])
        path = checkpoint_path(tmp_path, IDENTITY, SCOPE)
        with sqlite3.connect(path) as connection:
            typ, blob = connection.execute("SELECT type, value FROM writes").fetchone()
            assert typ == "citrus-checkpoint-v1+aesgcm-v1"
            assert PRIVATE.encode() not in blob
            connection.execute("UPDATE writes SET value = ?", (blob[:-1] + bytes([blob[-1] ^ 1]),))
        async with open_checkpoint(tmp_path, IDENTITY, SCOPE) as saver:
            with pytest.raises(CheckpointProtectionError, match="authentication_failed"):
                await saver.aget_tuple(saved)
            with pytest.raises(CheckpointProtectionError, match="authentication_failed"):
                _ = [entry async for entry in saver.alist(_config())]

    offline.run_until_complete(exercise())


@pytest.mark.parametrize("field", ["owner_user_id", "session_id", "user_turn_id", "run_id", "provider", "model"])
def test_each_binding_field_is_authenticated(field):
    key = os.urandom(32)
    identity, scope = IDENTITY, SCOPE
    if field in ("provider", "model"):
        scope = ("https://synthetic.invalid", SCOPE[1]) if field == "provider" else (SCOPE[0], "other-model")
    else:
        identity = replace(identity, **{field: 18 if field == "owner_user_id" else "other"})
    encoded = ProtectedCheckpointSerializer(key, IDENTITY, SCOPE).dumps_typed(_checkpoint())
    with pytest.raises(CheckpointProtectionError, match="authentication_failed"):
        ProtectedCheckpointSerializer(key, identity, scope).loads_typed(encoded)
    assert checkpoint_scope(identity, scope) != checkpoint_scope(IDENTITY, SCOPE)


def test_wrong_key_tamper_plaintext_and_format_rejected():
    key = os.urandom(32)
    serde = ProtectedCheckpointSerializer(key, IDENTITY, SCOPE)
    typ, blob = serde.dumps_typed(_message())
    with pytest.raises(CheckpointProtectionError, match="authentication_failed"):
        ProtectedCheckpointSerializer(os.urandom(32), IDENTITY, SCOPE).loads_typed((typ, blob))
    with pytest.raises(CheckpointProtectionError, match="authentication_failed"):
        serde.loads_typed((typ, blob[:-1] + bytes([blob[-1] ^ 1])))
    for plaintext in (JsonPlusSerializer().dumps_typed({"reasoning": PRIVATE}),
                      ("pickle", b"never-unpickle"), (typ.replace("v1", "v2"), blob)):
        with pytest.raises(CheckpointProtectionError, match="encrypted_checkpoint_required"):
            serde.loads_typed(plaintext)


@pytest.mark.parametrize("table,column", [("checkpoints", "checkpoint"), ("writes", "value")])
def test_sqlite_plaintext_injection_rejected(tmp_path, offline, table, column):
    async def exercise():
        async with open_checkpoint(tmp_path, IDENTITY, SCOPE) as saver:
            saved = await saver.aput(_config(), _checkpoint(), {}, {})
            await saver.aput_writes(saved, [("messages", [_message()])], "synthetic-task")
        with sqlite3.connect(checkpoint_path(tmp_path, IDENTITY, SCOPE)) as connection:
            connection.execute(f"UPDATE {table} SET type = 'json', {column} = ?", (b'{}',))
        async with open_checkpoint(tmp_path, IDENTITY, SCOPE) as saver:
            with pytest.raises(CheckpointProtectionError, match="encrypted_checkpoint_required"):
                await saver.aget_tuple(saved)
    offline.run_until_complete(exercise())


def test_native_checkpoint_shapes_and_private_chunks():
    serde = ProtectedCheckpointSerializer(os.urandom(32), IDENTITY, SCOPE)
    values = {
        "messages": [SystemMessage(content="System"), HumanMessage(content="Task"),
                     AIMessage(content="Public", tool_calls=[{"name": "t", "args": {}, "id": "t"}]),
                     AIMessageChunk(content="chunk"), ToolMessage(content="Result", tool_call_id="t"),
                     RemoveMessage(id="old"), _message(""),
                     _DeepSeekMessageChunk(content="chunk", provider_protocol=_ProtocolState(SCOPE, PRIVATE))],
        "tuple": (1, b"bytes", {"nested": (2, 3)}), "set": {(1, 2), (3, 4)},
        "frozenset": frozenset({1, 2}), "bytes": b"raw", "bytearray": bytearray(b"raw"),
        "send": Send("tools", {"messages": [_message()]}),
        "interrupt": Interrupt(value={"message": _message()}, id="synthetic-interrupt"),
    }
    restored = serde.loads_typed(serde.dumps_typed(values))
    assert restored["messages"] == values["messages"]
    for name in ("tuple", "set", "frozenset", "bytes", "bytearray"):
        assert restored[name] == values[name]
        assert type(restored[name]) is type(values[name])
    _assert_hidden(restored["send"].arg["messages"][0])
    _assert_hidden(restored["interrupt"].value["message"])
    _assert_hidden(restored["messages"][-1])


@pytest.mark.parametrize("defect", ["missing", "scope", "tag", "unsupported"])
def test_unsafe_values_refused_before_checkpoint_or_writes(tmp_path, offline, defect):
    message = _message()
    if defect == "missing":
        message.provider_protocol = None
    elif defect == "scope":
        message.provider_protocol = _ProtocolState((SCOPE[0], "other-model"), PRIVATE)
    elif defect == "tag":
        message = {"__citrus_checkpoint_v1__": "deepseek", "private": PRIVATE}
    else:
        message = object()

    async def exercise():
        async with open_checkpoint(tmp_path, IDENTITY, SCOPE) as saver:
            checkpoint = empty_checkpoint()
            saved = await saver.aput(_config(), checkpoint, {}, {})
            checkpoint["channel_values"] = {"messages": [message]}
            with pytest.raises(CheckpointProtectionError):
                await saver.aput(saved, checkpoint, {}, {})
            with pytest.raises(CheckpointProtectionError):
                await saver.aput_writes(saved, [("messages", [_message()]), ("bad", message)], "task")
            restored = await saver.aget_tuple(saved)
            assert restored.checkpoint["channel_values"] == {}
            assert restored.pending_writes == []
    offline.run_until_complete(exercise())


def test_authenticated_malformed_private_state_rejected():
    serde = ProtectedCheckpointSerializer(os.urandom(32), IDENTITY, SCOPE)
    _, packed = serde.serde.dumps_typed(_message())
    envelope = serde.serde.delegate.loads_typed(("msgpack", packed))
    del envelope["value"]["private"]
    _, malformed = serde.serde.delegate.dumps_typed(envelope)
    cipher, blob = serde.cipher.encrypt(malformed)
    with pytest.raises(CheckpointProtectionError, match="invalid_checkpoint_tag"):
        serde.loads_typed(("citrus-checkpoint-v1+" + cipher, blob))


def test_key_missing_corrupt_and_permissions_fail_closed(tmp_path, offline):
    async def use():
        async with open_checkpoint(tmp_path, IDENTITY, SCOPE) as saver:
            await saver.aput(_config(), _checkpoint(), {}, {})
    offline.run_until_complete(use())
    key_path = tmp_path / "checkpoints" / "storage.key"
    original = key_path.read_bytes()
    assert len(original) == 32
    if os.name == "posix":
        assert key_path.stat().st_mode & 0o777 == 0o600
        assert key_path.parent.stat().st_mode & 0o777 == 0o700
        key_path.chmod(0o644)
        with pytest.raises(CheckpointProtectionError, match="permissions"):
            offline.run_until_complete(use())
        key_path.chmod(0o600)
    key_path.write_bytes(b"incomplete")
    with pytest.raises(CheckpointProtectionError, match="invalid_checkpoint_key"):
        offline.run_until_complete(use())
    key_path.unlink()
    with pytest.raises(CheckpointProtectionError, match="checkpoint_key_missing"):
        offline.run_until_complete(use())
    assert not key_path.exists()


def test_concurrent_process_key_creation_and_stable_scope(tmp_path):
    # Each child uses only a new synthetic directory, never application config.
    code = '''
import json, pathlib, sys
sys.path.insert(0, sys.argv[3])
from src.agent.checkpoint_store import _directory, _load_key, ProtectedCheckpointSerializer, checkpoint_scope
from src.agent.identity import ExecutionIdentity
root = pathlib.Path(sys.argv[1]); _directory(root)
identity = ExecutionIdentity(17, "synthetic-session", "synthetic-turn", "synthetic-run")
scope = ("https://api.deepseek.com/v1", "deepseek-reasoner")
key = _load_key(root)
serde = ProtectedCheckpointSerializer(key, identity, scope)
typ, blob = serde.dumps_typed(checkpoint_scope(identity, scope))
(root / (sys.argv[2] + ".blob")).write_bytes(blob)
assert "src.config" not in sys.modules
'''
    children = [subprocess.Popen([sys.executable, "-I", "-B", "-c", code, str(tmp_path), str(i), str(Path(__file__).resolve().parents[2])],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(4)]
    for child in children:
        stdout, stderr = child.communicate(timeout=45)
        assert child.returncode == 0, stderr.decode(errors="replace")
        assert not stdout
    key = (tmp_path / "storage.key").read_bytes()
    serde = ProtectedCheckpointSerializer(key, IDENTITY, SCOPE)
    for path in tmp_path.glob("*.blob"):
        assert serde.loads_typed(("citrus-checkpoint-v1+aesgcm-v1", path.read_bytes())) == checkpoint_scope(IDENTITY, SCOPE)
    assert not list(tmp_path.glob(".key-*"))

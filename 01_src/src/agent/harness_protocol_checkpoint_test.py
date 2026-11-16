"""Offline P2 probes, not production persistence or trust-boundary adapters.

Only synthetic private values, explicit synthetic clients, and temporary SQLite
files. Public serializer/middleware hooks are exercised without monkey-patching
production protocol code. Loss/refusal tests intentionally pin unresolved gaps.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
from contextlib import contextmanager

import httpx
import pytest
from pydantic import Field

pytest.importorskip("langchain")
pytest.importorskip("langgraph.checkpoint.sqlite")

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.load import dumpd
from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, RemoveMessage, ToolMessage,
    messages_from_dict, messages_to_dict,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime

from src.core.provider_protocol import (
    DeepSeekChatOpenAI, _DeepSeekMessage, _DeepSeekMessageChunk, _ProtocolState,
)

PRIVATE = "synthetic-private-checkpoint-reasoning"
SCOPE = ("https://api.deepseek.com/v1", "deepseek-reasoner")
CONFIG = {"configurable": {"thread_id": "synthetic-owner/session/turn", "checkpoint_ns": ""}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    # Windows creates a loopback socketpair for its event-loop wakeup pipe.
    # Initialize that public loop before blocking all subsequent connections.
    loop = asyncio.new_event_loop()

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline P2 test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for name in ("LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING"):
        monkeypatch.setenv(name, "false")
    try:
        yield loop
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


@pytest.fixture
def protocol():
    def forbidden(request):
        raise AssertionError("Only local payload construction is permitted")

    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        yield DeepSeekChatOpenAI(
            model=SCOPE[1], base_url=SCOPE[0], api_key="synthetic-unused",
            organization="", openai_proxy="", http_client=client, max_retries=0,
        )


def _conversation():
    return [
        HumanMessage(content="Synthetic task", id="user", additional_kwargs={
            "context_trusted": True, "context_origin": "user", "context_turn_id": "turn-1",
        }),
        _DeepSeekMessage(content="", id="assistant", tool_calls=[{
            "name": "lookup", "args": {}, "id": "call-1", "type": "tool_call",
        }], provider_protocol=_ProtocolState(SCOPE, PRIVATE)),
        ToolMessage(content="Synthetic result", tool_call_id="call-1", id="tool"),
    ]


def _serde():
    # Explicit narrow allowlist, avoiding dependency-default permissive imports.
    return JsonPlusSerializer(allowed_msgpack_modules=[
        ("src.core.provider_protocol", "_DeepSeekMessage"),
        ("src.core.provider_protocol", "_DeepSeekMessageChunk"),
    ])


@contextmanager
def _saver(path, serde=None):
    connection = sqlite3.connect(path)
    try:
        yield SqliteSaver(connection, serde=serde)
    finally:
        connection.close()


def _checkpoint(messages):
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": messages}
    checkpoint["channel_versions"] = {"messages": 1}
    return checkpoint


def _roundtrip(path, messages, serde_factory):
    with _saver(path, serde_factory()) as saver:
        saved = saver.put(CONFIG, _checkpoint(messages), {"source": "input", "step": 0}, {"messages": 1})
    # Fresh saver AND serializer, after closing the original connection.
    with _saver(path, serde_factory()) as saver:
        restored = saver.get_tuple(saved)
        assert restored is not None
        return restored.checkpoint["channel_values"]["messages"]


def _reasoning(protocol, messages):
    return [m.get("reasoning_content") for m in protocol._get_request_payload(messages)["messages"]
            if m["role"] == "assistant"]


def _assert_private_is_hidden(message):
    for visible in (message.model_dump_json(), repr(message),
                    json.dumps(messages_to_dict([message])), json.dumps(dumpd(message))):
        assert PRIVATE not in visible


def test_ordinary_sqlite_checkpoint_loses_excluded_private_state(tmp_path, protocol):
    messages = _conversation()
    assert _reasoning(protocol, messages) == [PRIVATE]
    _assert_private_is_hidden(messages[1])
    restored = _roundtrip(tmp_path / "ordinary.sqlite", messages, lambda: None)
    assert isinstance(restored[1], _DeepSeekMessage)
    assert restored[1].provider_protocol is None
    assert restored[1].tool_calls == messages[1].tool_calls
    assert restored[2].tool_call_id == "call-1"
    # Current production payload construction does NOT fail closed on reload.
    assert _reasoning(protocol, restored) == [None]
    assert PRIVATE.encode() not in (tmp_path / "ordinary.sqlite").read_bytes()


class _RefusePrivateStateLoss(SerializerProtocol):
    """Test-only conservative public adapter; refuses persistence, not recovery.

    Real production policy must also cover plain AIMessage projections, active
    turn/provider scope and read/resume boundaries. This deliberately does not
    reconstruct reasoning or claim to be that policy.
    """
    def __init__(self):
        self.delegate = _serde()

    def dumps_typed(self, obj):
        def check(value):
            if isinstance(value, _DeepSeekMessage) and (
                value.provider_protocol is not None or value.tool_calls
            ):
                raise ValueError("private_protocol_checkpoint_unsupported")
            if isinstance(value, dict):
                for child in value.values():
                    check(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    check(child)

        check(obj)
        return self.delegate.dumps_typed(obj)

    def loads_typed(self, data):
        return self.delegate.loads_typed(data)


@pytest.mark.parametrize("state_kind", ["live", "already-lost", "chunk"])
def test_public_serializer_can_refuse_loss_before_checkpoint_insert(tmp_path, state_kind):
    messages = _conversation()
    if state_kind == "already-lost":
        messages[1] = messages[1].model_copy(update={"provider_protocol": None})
    elif state_kind == "chunk":
        messages[1] = _DeepSeekMessageChunk(
            content="", provider_protocol=_ProtocolState(SCOPE, PRIVATE))
    serde = _RefusePrivateStateLoss()
    assert isinstance(serde, SerializerProtocol)
    with _saver(tmp_path / "refused.sqlite", serde) as saver:
        saver.setup()
        with pytest.raises(ValueError, match="^private_protocol_checkpoint_unsupported$"):
            saver.put(CONFIG, _checkpoint(messages), {"source": "input", "step": 0}, {})
        assert list(saver.list(CONFIG)) == []
        assert saver.conn.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 0


def test_refusal_adapter_also_blocks_pending_writes_and_allows_public_state(tmp_path):
    with _saver(tmp_path / "writes.sqlite", _RefusePrivateStateLoss()) as saver:
        saved = saver.put(CONFIG, _checkpoint([HumanMessage(content="Public synthetic input")]),
                          {"source": "input", "step": 0}, {})
        with pytest.raises(ValueError, match="private_protocol_checkpoint_unsupported"):
            saver.put_writes(saved, [("messages", _conversation())], "synthetic-task")
        restored = saver.get_tuple(saved)
        assert restored.checkpoint["channel_values"]["messages"][0].content == "Public synthetic input"
        assert restored.pending_writes == []


class _SyntheticAesGcmCipher:
    """Public CipherProtocol implementation using the installed cryptography API.

    Per-test random keys only. PyCryptodome's optional built-in helper is absent
    in the probed environment; no packages or real key configuration are used.
    """
    def __init__(self, key):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.aes = AESGCM(key)

    def encrypt(self, plaintext):
        nonce = os.urandom(12)
        return "synthetic-aesgcm", nonce + self.aes.encrypt(nonce, plaintext, None)

    def decrypt(self, ciphername, ciphertext):
        if ciphername != "synthetic-aesgcm":
            raise ValueError("unsupported_synthetic_cipher")
        return self.aes.decrypt(ciphertext[:12], ciphertext[12:], None)


def _encrypted(key, serde=None):
    pytest.importorskip("cryptography.hazmat.primitives.ciphers.aead")
    return EncryptedSerializer(_SyntheticAesGcmCipher(key), serde or _serde())


def test_encryption_alone_does_not_restore_excluded_state(tmp_path, protocol):
    key = os.urandom(32)
    path = tmp_path / "encrypted.sqlite"
    restored = _roundtrip(path, _conversation(), lambda: _encrypted(key))
    assert restored[1].provider_protocol is None
    assert _reasoning(protocol, restored) == [None]
    assert PRIVATE.encode() not in path.read_bytes()


def test_encrypted_public_extension_carries_explicit_synthetic_envelope(tmp_path):
    """Envelope viability only: no message hydration, scope binding or key store."""
    from cryptography.exceptions import InvalidTag

    key = os.urandom(32)
    envelope = {"version": 1, "synthetic_private": PRIVATE}
    path = tmp_path / "envelope.sqlite"
    assert _roundtrip(path, [envelope], lambda: _encrypted(key)) == [envelope]
    assert PRIVATE.encode() not in path.read_bytes()
    with _saver(path, _encrypted(os.urandom(32))) as saver:
        with pytest.raises(InvalidTag):
            saver.get_tuple(CONFIG)
    with sqlite3.connect(path) as connection:
        blob = connection.execute("SELECT checkpoint FROM checkpoints").fetchone()[0]
        tampered = blob[:-1] + bytes([blob[-1] ^ 1])
        connection.execute("UPDATE checkpoints SET checkpoint = ?", (tampered,))
    with _saver(path, _encrypted(key)) as saver:
        with pytest.raises(InvalidTag):
            saver.get_tuple(CONFIG)


def test_encrypted_wrapper_accepts_legacy_plaintext_and_keeps_refusal(tmp_path):
    key = os.urandom(32)
    plaintext = _serde().dumps_typed({"synthetic_private": PRIVATE})
    # Upstream behavior: encryption wrapper alone is not an encrypted-only policy.
    assert _encrypted(key).loads_typed(plaintext) == {"synthetic_private": PRIVATE}
    with _saver(tmp_path / "encrypted-refusal.sqlite",
                _encrypted(key, _RefusePrivateStateLoss())) as saver:
        with pytest.raises(ValueError, match="private_protocol_checkpoint_unsupported"):
            saver.put(CONFIG, _checkpoint(_conversation()), {"source": "input", "step": 0}, {})


BINDING = {"owner": "synthetic-owner", "session": "synthetic-session", "turn": "turn-1",
           "provider": SCOPE[0], "model": SCOPE[1]}


class _PrivateStateCodec(SerializerProtocol):
    """Small fixture-only JSON codec: explicit extraction, no pickle/import revival.

    Binding is supplied by the synthetic authority, not by checkpoint contents.
    Only complete messages and JSON-shaped checkpoint values are supported.
    No production key management, migrations, streaming or P3 integration.
    """
    def __init__(self, binding):
        self.binding = dict(binding)

    def _state(self, value):
        if not isinstance(value, dict) or not isinstance(value.get("reasoning"), str):
            raise ValueError("private_protocol_state_missing")
        if value.get("scope") != [self.binding["provider"], self.binding["model"]]:
            raise ValueError("private_protocol_scope_mismatch")
        return _ProtocolState(tuple(value["scope"]), value["reasoning"])

    def _pack(self, obj):
        if type(obj) is _DeepSeekMessage:
            state = obj.provider_protocol
            private = None if state is None else {"scope": list(state.scope), "reasoning": state.reasoning}
            self._state(private)
            return {"__fixture_message__": "deepseek", "public": obj.model_dump(mode="json"),
                    "private": private}
        if isinstance(obj, BaseMessage):
            # In this single-provider fixture, plain tool-call projections cannot
            # prove that private state survived; reject rather than fabricate it.
            if isinstance(obj, AIMessage) and obj.tool_calls:
                raise ValueError("private_protocol_state_missing")
            if type(obj) not in (HumanMessage, AIMessage, ToolMessage):
                raise TypeError("unsupported_fixture_message")
            return {"__fixture_message__": "public", "public": messages_to_dict([obj])[0]}
        if isinstance(obj, dict):
            if "__fixture_message__" in obj:
                raise ValueError("reserved_fixture_marker")
            return {key: self._pack(value) for key, value in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._pack(value) for value in obj]
        if obj is None or isinstance(obj, (str, bool, int, float)):
            return obj
        raise TypeError("unsupported_fixture_value")

    def _unpack(self, obj):
        if isinstance(obj, dict):
            kind = obj.get("__fixture_message__")
            if kind == "deepseek":
                state = self._state(obj.get("private"))
                if "provider_protocol" in obj["public"]:
                    raise ValueError("private_state_in_public_fields")
                return _DeepSeekMessage(**obj["public"], provider_protocol=state)
            if kind == "public":
                message = messages_from_dict([obj["public"]])[0]
                self._pack(message)  # same conservative missing-state check on read
                return message
            if kind is not None:
                raise ValueError("unsupported_fixture_marker")
            return {key: self._unpack(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self._unpack(value) for value in obj]
        return obj

    def dumps_typed(self, obj):
        return "fixture-protocol-v1", json.dumps({
            "version": 1, "binding": self.binding, "value": self._pack(obj),
        }, allow_nan=False).encode()

    def loads_typed(self, data):
        if data[0] != "fixture-protocol-v1":
            raise ValueError("unsupported_fixture_format")
        envelope = json.loads(data[1])
        if envelope.get("version") != 1 or envelope.get("binding") != self.binding:
            raise ValueError("private_checkpoint_binding_mismatch")
        return self._unpack(envelope["value"])


class _EncryptedOnly(EncryptedSerializer):
    def loads_typed(self, data):
        if data[0] != "fixture-protocol-v1+synthetic-aesgcm":
            raise ValueError("encrypted_checkpoint_required")
        return super().loads_typed(data)


def _bound_encrypted(key, binding=None):
    return _EncryptedOnly(_SyntheticAesGcmCipher(key), _PrivateStateCodec(binding or BINDING))


@pytest.mark.parametrize("reasoning", [PRIVATE, ""])
def test_bound_encrypted_codec_restores_current_tool_reasoning(tmp_path, protocol, reasoning):
    key = os.urandom(32)
    messages = _conversation()
    messages[1].provider_protocol = _ProtocolState(SCOPE, reasoning)
    path = tmp_path / "bound.sqlite"
    restored = _roundtrip(path, messages, lambda: _bound_encrypted(key))
    assert restored[1].provider_protocol == messages[1].provider_protocol
    assert restored[1].tool_calls == messages[1].tool_calls
    assert restored[2].tool_call_id == "call-1"
    assert _reasoning(protocol, restored) == [reasoning]
    _assert_private_is_hidden(restored[1])
    assert PRIVATE.encode() not in path.read_bytes()
    assert _reasoning(protocol, [*restored, HumanMessage(content="New real turn")]) == [None]


@pytest.mark.parametrize("field", list(BINDING))
def test_bound_encrypted_codec_refuses_cross_scope_read(tmp_path, field):
    key = os.urandom(32)
    path = tmp_path / "scope.sqlite"
    _roundtrip(path, _conversation(), lambda: _bound_encrypted(key))
    wrong_binding = {**BINDING, field: "different-synthetic-scope"}
    with _saver(path, _bound_encrypted(key, wrong_binding)) as saver:
        with pytest.raises(ValueError, match="private_checkpoint_binding_mismatch"):
            saver.get_tuple(CONFIG)


def test_bound_codec_refuses_wrong_key_tampering_and_plaintext(tmp_path):
    from cryptography.exceptions import InvalidTag

    key = os.urandom(32)
    path = tmp_path / "bound-reject.sqlite"
    _roundtrip(path, _conversation(), lambda: _bound_encrypted(key))
    with _saver(path, _bound_encrypted(os.urandom(32))) as saver:
        with pytest.raises(InvalidTag):
            saver.get_tuple(CONFIG)
    with sqlite3.connect(path) as connection:
        blob = connection.execute("SELECT checkpoint FROM checkpoints").fetchone()[0]
        connection.execute("UPDATE checkpoints SET checkpoint = ?",
                           (blob[:-1] + bytes([blob[-1] ^ 1]),))
    with _saver(path, _bound_encrypted(key)) as saver:
        with pytest.raises(InvalidTag):
            saver.get_tuple(CONFIG)
    with pytest.raises(ValueError, match="encrypted_checkpoint_required"):
        _bound_encrypted(key).loads_typed(_PrivateStateCodec(BINDING).dumps_typed(_conversation()))


@pytest.mark.parametrize("defect", ["missing", "wrong-provider", "plain-projection"])
def test_bound_codec_refuses_missing_or_mismatched_state_on_write(tmp_path, defect):
    messages = _conversation()
    if defect == "missing":
        messages[1].provider_protocol = None
    elif defect == "wrong-provider":
        messages[1].provider_protocol = _ProtocolState(("https://synthetic.invalid", SCOPE[1]), PRIVATE)
    else:
        messages[1] = AIMessage(**messages[1].model_dump())
    with _saver(tmp_path / "missing.sqlite", _bound_encrypted(os.urandom(32))) as saver:
        with pytest.raises(ValueError, match="private_protocol_(state_missing|scope_mismatch)"):
            saver.put(CONFIG, _checkpoint(messages), {"source": "input", "step": 0}, {})
        assert list(saver.list(CONFIG)) == []


def test_bound_codec_refuses_authenticated_but_missing_state_on_read(tmp_path):
    key = os.urandom(32)
    path = tmp_path / "missing-read.sqlite"
    _roundtrip(path, _conversation(), lambda: _bound_encrypted(key))
    with sqlite3.connect(path) as connection:
        typ, blob = connection.execute("SELECT type, checkpoint FROM checkpoints").fetchone()
        cipher = _SyntheticAesGcmCipher(key)
        envelope = json.loads(cipher.decrypt("synthetic-aesgcm", blob))
        del envelope["value"]["channel_values"]["messages"][1]["private"]
        # A valid authenticated envelope from a buggy writer must still fail schema checks.
        _, replacement = cipher.encrypt(json.dumps(envelope).encode())
        connection.execute("UPDATE checkpoints SET checkpoint = ?", (replacement,))
    with _saver(path, _bound_encrypted(key)) as saver:
        with pytest.raises(ValueError, match="private_protocol_state_missing"):
            saver.get_tuple(CONFIG)


class _OfflineModel(BaseChatModel):
    observed: list = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "synthetic-p2-no-network"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.observed.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Synthetic done"))])


def test_public_model_hook_refuses_reloaded_missing_state_before_dispatch(tmp_path):
    class RefuseLostProtocol(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            # Conservative fixture policy, not a full provider/turn-aware guard.
            if any(isinstance(m, _DeepSeekMessage) and m.tool_calls
                   and m.provider_protocol is None for m in request.messages):
                raise ValueError("private_protocol_state_missing_on_resume")
            return handler(request)

    restored = _roundtrip(tmp_path / "resume.sqlite", _conversation(), _serde)
    model = _OfflineModel()
    graph = create_agent(model, middleware=[RefuseLostProtocol()])
    with pytest.raises(ValueError) as error:
        graph.invoke({"messages": restored})
    # LangGraph attaches task notes; compare the actual exception arguments.
    assert error.value.args == ("private_protocol_state_missing_on_resume",)
    assert model.observed == []


def _summary_middleware(cls=SummarizationMiddleware):
    return cls(model=_OfflineModel(), trigger=("messages", 4), keep=("messages", 3),
               token_counter=lambda messages: len(list(messages)),
               summarizer=lambda messages: "Synthetic summary of older history")


def _history():
    return [HumanMessage(content="Older task", id="older-user"),
            AIMessage(content="Older answer", id="older-answer"), *_conversation()]


class _TrustedInternalSummary(SummarizationMiddleware):
    """Test-only public sync/async hooks, trusted at summary creation boundary.

    Tag only new summary objects returned by our own summarizer invocation.
    Never promote input lc_source metadata to trusted provenance. Production
    ingress sanitization and P3 integration remain outside this probe.
    """
    def before_model(self, state, runtime):
        inputs = list(state["messages"])
        return self._normalize(inputs, super().before_model(state, runtime))

    async def abefore_model(self, state, runtime):
        inputs = list(state["messages"])
        return self._normalize(inputs, await super().abefore_model(state, runtime))

    @staticmethod
    def _normalize(inputs, update):
        if update is None:
            return None
        normalized = []
        for message in update["messages"]:
            if (isinstance(message, HumanMessage)
                    and all(message is not original for original in inputs)
                    and message.additional_kwargs.get("lc_source") == "summarization"):
                message = message.model_copy(update={"additional_kwargs": {
                    **message.additional_kwargs,
                    "context_trusted": True, "context_origin": "internal_control",
                }})
            normalized.append(message)
        return {**update, "messages": normalized}


def _summary_update(cls=SummarizationMiddleware, history=None):
    history = _history() if history is None else history
    update = _summary_middleware(cls).before_model({"messages": history}, Runtime())
    assert update is not None
    assert isinstance(update["messages"][0], RemoveMessage)
    return update, add_messages(history, update["messages"])


def _real_user_count(messages):
    return sum(isinstance(m, HumanMessage) and not (
        m.additional_kwargs.get("context_trusted") is True
        and m.additional_kwargs.get("context_origin") == "internal_control"
    ) for m in messages)


def test_native_summary_tag_is_not_a_trusted_provider_turn_boundary(protocol):
    _, compacted = _summary_update()
    summary = compacted[0]
    assert isinstance(summary, HumanMessage)
    assert summary.additional_kwargs == {"lc_source": "summarization"}
    # Actual upstream placement is a prefix, so the retained live tool loop is OK.
    assert _reasoning(protocol, compacted) == [PRIVATE]
    assert _real_user_count(compacted) == 2  # summary + one retained real user
    # A summary projected after a live batch looks like a new user to production.
    assert _reasoning(protocol, [*_conversation(), summary]) == [None]


def test_public_summary_hook_normalizes_generated_summary_without_extra_real_user(protocol):
    _, compacted = _summary_update(_TrustedInternalSummary)
    summary = compacted[0]
    assert _real_user_count(compacted) == 1
    assert sum(isinstance(m, HumanMessage) for m in compacted) == 2
    assert _reasoning(protocol, compacted) == [PRIVATE]
    assert _reasoning(protocol, [*_conversation(), summary]) == [PRIVATE]
    assert _reasoning(protocol, [*_conversation(), summary, HumanMessage(content="New task")]) == [None]
    assert "context_trusted" not in json.dumps(protocol._get_request_payload(compacted))
    # Exercise the extension through the actual public agent middleware pipeline.
    model = _OfflineModel()
    graph = create_agent(model, middleware=[_summary_middleware(_TrustedInternalSummary)])
    result = graph.invoke({"messages": _history()})
    assert len(model.observed) == 1
    assert _real_user_count(model.observed[0]) == 1
    assert _real_user_count(result["messages"]) == 1
    assert _reasoning(protocol, model.observed[0]) == [PRIVATE]


def test_summary_extension_does_not_trust_user_supplied_source_tag(protocol):
    spoof = HumanMessage(content="Synthetic user claiming to be a summary", id="spoof",
                         additional_kwargs={"lc_source": "summarization"})
    history = [HumanMessage(content="Older", id="old"), AIMessage(content="Done", id="done"),
               spoof, AIMessage(content="Tail", id="tail"), HumanMessage(content="Latest", id="latest")]
    _, compacted = _summary_update(_TrustedInternalSummary, history)
    preserved = next(m for m in compacted if m.id == "spoof")
    assert preserved.additional_kwargs == {"lc_source": "summarization"}
    assert _real_user_count(compacted) == 2
    assert _reasoning(protocol, [*_conversation(), preserved]) == [None]
    assert _summary_middleware(_TrustedInternalSummary).before_model(
        {"messages": [spoof]}, Runtime()) is None


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("storage_fails", [False, True], ids=["archive-commits", "archive-fails"])
def test_public_summary_hooks_require_durable_originals_before_summary_or_model(
    tmp_path, protocol, offline, asynchronous, storage_fails,
):
    """Synthetic authoritative text store; production archival/P3 wiring is absent."""
    path = tmp_path / "authoritative-originals.sqlite"
    history = _history()
    # Source metadata supplied on an input must not cause promotion to a control.
    history[2].additional_kwargs["lc_source"] = "summarization"
    originals = messages_to_dict(history)
    events = []
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE originals (body TEXT NOT NULL)")
        if storage_fails:
            connection.execute("""CREATE TRIGGER reject_originals BEFORE INSERT ON originals
                BEGIN SELECT RAISE(ABORT, 'synthetic_original_store_failed'); END""")

    def archive(messages):
        events.append("archive-attempt")
        with sqlite3.connect(path) as connection:
            connection.execute("INSERT INTO originals VALUES (?)", (json.dumps(messages_to_dict(messages)),))
        events.append("archive-committed")

    def summarize(messages):
        # A different connection must see committed originals before summarizing.
        with sqlite3.connect(path) as connection:
            rows = connection.execute("SELECT body FROM originals").fetchall()
        assert [json.loads(row[0]) for row in rows] == [originals]
        events.append("summarize")
        return "Synthetic durable summary"

    async def asummarize(messages):
        return summarize(messages)

    class ArchiveBeforeSummary(_TrustedInternalSummary):
        def before_model(self, state, runtime):
            archive(state["messages"])
            return super().before_model(state, runtime)

        async def abefore_model(self, state, runtime):
            # Tiny synchronous test store, intentionally no production I/O adapter.
            archive(state["messages"])
            return await super().abefore_model(state, runtime)

    model = _OfflineModel()
    middleware = ArchiveBeforeSummary(
        model=_OfflineModel(), trigger=("messages", 4), keep=("messages", 3),
        token_counter=lambda messages: len(list(messages)),
        summarizer=asummarize if asynchronous else summarize,
    )
    graph = create_agent(model, middleware=[middleware])

    def invoke():
        if asynchronous:
            return offline.run_until_complete(graph.ainvoke({"messages": history}))
        return graph.invoke({"messages": history})

    if storage_fails:
        with pytest.raises(sqlite3.IntegrityError, match="synthetic_original_store_failed"):
            invoke()
        assert events == ["archive-attempt"]
        assert model.observed == []
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT count(*) FROM originals").fetchone()[0] == 0
        return

    result = invoke()
    assert events == ["archive-attempt", "archive-committed", "summarize"]
    assert len(model.observed) == 1
    compacted = model.observed[0]
    assert _real_user_count(compacted) == _real_user_count(result["messages"]) == 1
    summary = compacted[0]
    assert summary.additional_kwargs["context_origin"] == "internal_control"
    assert summary.additional_kwargs["context_trusted"] is True
    retained_user = next(message for message in compacted if message.id == "user")
    assert retained_user.additional_kwargs["context_origin"] == "user"
    assert _reasoning(protocol, compacted) == [PRIVATE]
    assert _reasoning(protocol, [*_conversation(), summary]) == [PRIVATE]
    assert _reasoning(protocol, [*_conversation(), summary, HumanMessage(content="New turn")]) == [None]
    assert PRIVATE.encode() not in path.read_bytes()

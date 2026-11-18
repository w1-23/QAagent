"""Private, live-message state for DeepSeek's thinking/tool protocol.

Only the official DeepSeek Chat Completions endpoint is opted in. Compatible
proxies need their own verified capability contract before being added here.
The state is deliberately excluded from message/trace serialization; it is not
conversation history and cannot yet survive a persisted-session reload.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import Field


def uses_deepseek_protocol(model: str, base_url: str) -> bool:
    url = urlsplit(str(base_url))
    return (url.scheme == "https" and url.hostname == "api.deepseek.com"
            and url.port in (None, 443) and url.path.rstrip("/") in ("", "/v1")
            and str(model).lower().startswith("deepseek-"))


@dataclass(frozen=True, repr=False)
class _ProtocolState:
    scope: tuple[str, str]
    reasoning: str


class _DeepSeekMessage(AIMessage):
    # Field exclusion covers Pydantic dumps, messages_to_dict and LangChain's
    # Serializable.to_json (callbacks/traces). Do not put this in kwargs/content.
    provider_protocol: _ProtocolState | None = Field(default=None, exclude=True, repr=False)


class _DeepSeekMessageChunk(_DeepSeekMessage, AIMessageChunk):
    # LangChain's message_chunk_to_message uses the first parent as the full
    # message class, so the order of these bases is part of the adapter contract.
    type: Literal["AIMessageChunk"] = "AIMessageChunk"

    def __add__(self, other):
        merged = super().__add__(other)
        others = other if isinstance(other, (list, tuple)) else [other]
        states = [m.provider_protocol for m in [self, *others]
                  if isinstance(m, _DeepSeekMessage) and m.provider_protocol is not None]
        if states and isinstance(merged, _DeepSeekMessage):
            if any(state.scope != states[0].scope for state in states):
                raise ValueError("Cannot merge chunks from different provider protocols")
            merged.provider_protocol = _ProtocolState(
                states[0].scope, "".join(state.reasoning for state in states))
        return merged


def current_tool_loop_start(messages) -> int:
    """Locate the current real user turn, shared by admission and wire encoding.

    Runtime controls cannot reset a tool loop. Repeated projections stamped with
    the same trusted turn identity also retain its complete protocol chain.
    """
    start, turn_id = 0, None
    for i, message in enumerate(messages):
        if not isinstance(message, HumanMessage):
            continue
        metadata = message.additional_kwargs
        trusted = metadata.get("context_trusted") is True
        identity = metadata.get("context_turn_id") if trusted else None
        if not isinstance(identity, str) or not identity.strip():
            identity = None
        if trusted and metadata.get("context_origin") == "internal_control":
            if identity is not None and turn_id is not None and identity != turn_id:
                raise ValueError("control_turn_mismatch: internal control does not match the current user turn")
            continue
        if identity is None or identity != turn_id:
            start = i + 1
        turn_id = identity
    return start


class DeepSeekChatOpenAI(ChatOpenAI):
    """Instance-scoped ingress/egress hooks; never patches LangChain globals."""

    def _protocol_scope(self, payload=None):
        model = (payload or {}).get("model", self.model_name)
        base_url = str(self.openai_api_base or "")
        if not uses_deepseek_protocol(model, base_url) or self.use_responses_api:
            return None
        options = (payload or {}).get("extra_body", self.extra_body) or {}
        thinking = options.get("thinking")
        if (isinstance(thinking, dict) and thinking.get("type") == "disabled") or options.get("enable_thinking") is False:
            return None
        return (base_url.rstrip("/"), model)

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        scope = self._protocol_scope()
        if scope is None:
            return result
        # The SDK retains unknown response fields in model_extra/model_dump.
        choices = response.get("choices", []) if isinstance(response, dict) else response.choices
        for generation, choice in zip(result.generations, choices):
            wire = choice["message"] if isinstance(choice, dict) else choice.message.model_dump()
            reasoning = wire.get("reasoning_content")
            if isinstance(generation.message, AIMessage) and isinstance(reasoning, str):
                generation.message = _DeepSeekMessage(
                    **generation.message.model_dump(),
                    provider_protocol=_ProtocolState(scope, reasoning))
        return result

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        # The upstream converter compares default classes by equality. Passing
        # our subclass would lose tool deltas that omit the assistant role.
        if issubclass(default_chunk_class, _DeepSeekMessageChunk):
            default_chunk_class = AIMessageChunk
        generation = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info)
        scope = self._protocol_scope()
        if generation is None or scope is None or not isinstance(generation.message, AIMessageChunk):
            return generation
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        delta = (choices[0].get("delta") or {}) if choices else {}
        reasoning = delta.get("reasoning_content")
        generation.message = _DeepSeekMessageChunk(
            **generation.message.model_dump(),
            provider_protocol=_ProtocolState(scope, reasoning) if isinstance(reasoning, str) else None)
        return generation

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        scope = self._protocol_scope(payload)
        if scope is None or "messages" not in payload:
            return payload
        # Admission and wire encoding use the same real-user boundary.
        start = current_tool_loop_start(messages)
        for message, wire in zip(messages[start:], payload["messages"][start:]):
            state = message.provider_protocol if isinstance(message, _DeepSeekMessage) else None
            if state is not None and state.scope == scope and wire.get("role") == "assistant":
                wire["reasoning_content"] = state.reasoning
        return payload

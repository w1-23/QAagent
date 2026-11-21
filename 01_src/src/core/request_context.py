"""One request estimate for admission, reservations and display.

Counts the converted request once. Estimates are never provider usage. Image costs
are conservative configurable placeholders, not a claim about any vendor tokenizer.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
from dataclasses import dataclass, field

_observation = contextvars.ContextVar("model_request_observation", default=None)
_epoch = contextvars.ContextVar("model_context_epoch", default=None)
_logical_call = contextvars.ContextVar("model_logical_call", default="")


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def text_tokens(text):
    # Shared character heuristic; no tool arguments are counted a second time.
    text = str(text or "")
    if not text:
        return 0
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    return max(1, int(cjk * 1.2 + (len(text) - cjk) / 4))


def message_dict(message):
    if isinstance(message, dict):
        return message
    try:
        from langchain_openai.chat_models.base import _convert_message_to_dict
        return _convert_message_to_dict(message)
    except (TypeError, AttributeError, ValueError):
        data = {"role": str(getattr(message, "type", "unknown")),
                "content": getattr(message, "content", "")}
        calls = getattr(message, "tool_calls", None)
        if isinstance(calls, list) and calls:
            data["tool_calls"] = calls
        return data


def estimate_request(messages, tools=None, output_tokens=0, model_window=None):
    """Accept converted payload or messages; always include schemas and images."""
    payload = messages if isinstance(messages, dict) else {
        "messages": [message_dict(m) for m in messages], "tools": tools or []}
    parts = {"system": 0, "messages": 0, "tool_arguments": 0,
             "tool_schemas": 0, "images": 0, "protocol": 0}
    image_count = 0
    for msg in payload.get("messages", []):
        content = msg.get("content", "")
        text = ""
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    text += str(block)
                elif block.get("type") in {"image_url", "input_image", "image"}:
                    image_count += 1
                    # Unknown resolution/model: reserve high-detail allowance.
                    parts["images"] += 4096
                else:
                    text += _json(block)
        else:
            text = str(content or "")
        key = "system" if msg.get("role") in {"system", "developer"} else "messages"
        parts[key] += text_tokens(text)
        calls = msg.get("tool_calls") or msg.get("function_call")
        if calls:
            parts["tool_arguments"] += text_tokens(_json(calls))
        if msg.get("reasoning_content"):
            parts["messages"] += text_tokens(msg["reasoning_content"])
        parts["protocol"] += 12 + text_tokens(msg.get("name", "")) + text_tokens(msg.get("tool_call_id", ""))
    definitions = payload.get("tools") or payload.get("functions") or []
    if definitions:
        parts["tool_schemas"] = text_tokens(_json(definitions))
    for option in ("response_format", "tool_choice"):
        if payload.get(option):
            parts["protocol"] += text_tokens(_json(payload[option]))
    output = max(0, int(output_tokens or payload.get("max_completion_tokens") or payload.get("max_tokens") or 0))
    total = sum(parts.values())
    return {"estimated_input_tokens": total, "output_tokens": output,
            "estimated_total_tokens": total + output, "components": parts,
            "estimate_scope": "full_request_estimate", "estimate_method": "character_heuristic",
            "image_count": image_count, "image_estimate_status": "conservative_unknown_model" if image_count else "none",
            "tool_message_count": sum(msg.get("role") in {"tool", "function"} for msg in payload.get("messages", [])),
            "model_window": model_window}


def model_request(model, messages, output_tokens=None):
    primary = getattr(model, "primary", model)
    base = getattr(primary, "bound", primary)
    kwargs = getattr(primary, "kwargs", {})
    kwargs = dict(kwargs) if isinstance(kwargs, dict) else {}
    output = output_tokens if output_tokens is not None else kwargs.get("max_tokens")
    if output is None:
        value = getattr(base, "max_tokens", None)
        output = value if isinstance(value, (int, float)) else 512
    output = max(1, int(output))
    # This is the same LangChain conversion used at its SDK boundary. Observation
    # is disabled here; admission is not a physical provider attempt.
    method = getattr(base, "_get_request_payload", None)
    if callable(method) and isinstance(getattr(base, "model_name", None), str):
        token = _observation.set(None)
        try:
            payload = method(messages, **(kwargs | {"max_tokens": output}))
        finally:
            _observation.reset(token)
    else:
        payload = {"messages": [message_dict(m) for m in messages],
                   "tools": kwargs.get("tools", []), "max_tokens": output}
    return payload, estimate_request(payload, output_tokens=output)


@dataclass
class ContextEpoch:
    """Request-local, bounded deterministic projections, frozen until a checkpoint."""
    number: int = 0
    layout: str = ""
    reason: str = "initial"
    projections: dict = field(default_factory=dict)
    covered_count: int = 0
    covered_hash: str = ""
    frozen_prefix: list = field(default_factory=list)
    working_state: dict = field(default_factory=dict)
    # Populated from the session loader; absence is not evidence of success.
    history_load_status: str = "unknown"
    history_load_reason: str = ""

    def apply_prefix(self, messages):
        if not self.covered_count:
            return list(messages)
        digest = hashlib.sha256(_json([message_dict(m) for m in messages[:self.covered_count]]).encode()).hexdigest()
        if len(messages) >= self.covered_count and digest == self.covered_hash:
            return self.frozen_prefix + list(messages[self.covered_count:])
        self.covered_count = 0
        self.frozen_prefix = []
        self.number += 1
        self.reason = "instructions_changed"
        return list(messages)

    def freeze(self, original, compacted):
        self.covered_count = len(original)
        self.covered_hash = hashlib.sha256(_json([message_dict(m) for m in original]).encode()).hexdigest()
        self.frozen_prefix = list(compacted)
        self.checkpoint()

    def project(self, messages, layout):
        from src.core.model_context import project_tool_message
        if self.layout and layout != self.layout:
            self.number += 1
            self.reason = "request_layout_changed"
            self.projections.clear()
        self.layout = layout
        result = []
        for message in messages:
            if getattr(message, "type", "") == "tool":
                key = hashlib.sha256(_json(message_dict(message)).encode()).hexdigest()
                if key not in self.projections:
                    self.projections[key] = project_tool_message(message)
                message = self.projections[key]
            result.append(message)
        return result

    def checkpoint(self):
        self.number += 1
        self.reason = "working_set_compacted"
        self.projections.clear()


def observe_prepared_request(payload):
    """Called only by the actual model client while a counted attempt is active."""
    scope = _observation.get()
    if scope is None:
        return
    from src.core.turn_context import capture_request
    scope["snapshot"] = capture_request(payload, model=str(payload.get("model", "")),
        purpose=scope["purpose"], attempt_id=scope["attempt_id"],
        epoch=scope.get("epoch", 0), logical_call_id=scope.get("logical_call_id", ""),
        change_reason=scope.get("change_reason", ""))
    prepared = estimate_request(payload, model_window=(scope.get("estimate") or {}).get("model_window"))
    changed = prepared != scope.get("estimate")
    scope["estimate"] = prepared
    scope["model"] = str(payload.get("model", scope.get("model", "")))
    if changed and scope.get("publish_context"):
        scope["publish_context"]()

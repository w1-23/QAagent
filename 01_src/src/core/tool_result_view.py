"""Bound the first model delivery without modifying stored tool evidence."""
from __future__ import annotations

import hashlib
import json

INLINE_CHARS = 16000
FIRST_DELIVERY_BYTES = 3072
# Readers have a separate per-page ceiling: an already-encoded 16 KB JSON
# page can need up to twice that space once escaped as a message string.
READER_DELIVERY_BYTES = 2 * INLINE_CHARS + 2
PREVIEW_CHARS = 768
TAIL_PREVIEW_CHARS = 256
_READERS = frozenset({"read_tool_result", "read_candidate", "read_skill", "load_skill", "read_context_evidence"})
_WORKSPACE_READS = frozenset({"read", "read_run", "read_skill", "read_skill_resource", "context_source"})
_CONTROL_PRIORITY = (
    "status", "next_action", "terminal", "review_required", "can_resume", "export_ready",
    "audit_id", "review_id", "_tool_outcome", "dataset_ref", "analysis_result_ref",
    "continuation", "counts", "coverage", "export_requests", "review", "files", "exports",
)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _content_bytes(value) -> int:
    # Include the escaping required when the provider serializes content.
    return len(_json(value).encode("utf-8"))


def _verified_reference(message, text: str):
    reference = message.additional_kwargs.get("result_reference")
    storage = reference.get("storage") if isinstance(reference, dict) else None
    if not isinstance(storage, dict) or storage.get("complete") is not True:
        return None
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (reference.get("tool_call_id") != message.tool_call_id
            or reference.get("original_chars") != len(text) or reference.get("full_sha256") != digest):
        return None
    from src.core.tool_results import fetch_for_current_request, storage_integrity

    row = fetch_for_current_request(message.tool_call_id)
    if (not row or row["tool_name"] != message.name or row["full_sha256"] != digest
            or not storage_integrity(row)["complete"]):
        return None
    return {"tool_call_id": message.tool_call_id, "full_sha256": digest,
            "original_chars": len(text), "stored_chars": len(text)}


def _failure(message, code: str, *, verified: bool = False):
    metadata = dict(message.additional_kwargs)
    metadata.pop("first_delivery_reference", None)
    if not verified:
        metadata.pop("result_reference", None)
    notice = ("请求较小的原文页或更窄的字段范围，不要重做原操作。" if code == "result_page_too_large"
              else "未交付原文。这是回执交付限制，不代表工具未执行；不要据此重复执行。")
    return message.model_copy(update={"content": _json({"delivery": code, "complete": False, "notice": notice}),
                                      "additional_kwargs": metadata})


def first_delivery(message, *, call: dict, reader_available: bool):
    text = message.content
    is_reader = message.name in _READERS or (
        message.name == "research_workspace" and (call.get("args") or {}).get("operation") in _WORKSPACE_READS)
    try:
        size = _content_bytes(text)
    except (ValueError, TypeError, RecursionError):
        return _failure(message, "result_storage_unavailable")
    if size <= (READER_DELIVERY_BYTES if is_reader else FIRST_DELIVERY_BYTES):
        return message
    reference = _verified_reference(message, text) if isinstance(text, str) else None
    if is_reader:
        return _failure(message, "result_page_too_large", verified=reference is not None)
    if not reference:
        return _failure(message, "result_storage_unavailable")
    if not reader_available:
        return _failure(message, "result_readback_unavailable", verified=True)

    index = []
    control = {}
    total_controls = 0
    try:
        # Keep the original spelling of numbers; the reference, not this view,
        # is authoritative evidence for scientific quantities and units.
        value = json.loads(text, parse_float=str, parse_int=str, parse_constant=str)
        if isinstance(value, dict):
            index = [{"pointer": "/" + key.replace("~", "~0").replace("/", "~1"),
                      "type": "object" if isinstance(item, dict) else "array" if isinstance(item, list) else "scalar"}
                     for key, item in list(value.items())[:12] if len(key) <= 80]
            try:
                _content_bytes(index)
            except UnicodeError:
                index = []
            for key in _CONTROL_PRIORITY:
                if key in value:
                    total_controls += 1
                    try:
                        if _content_bytes(_json(value[key])) <= FIRST_DELIVERY_BYTES:
                            control[key] = value[key]
                    except (ValueError, TypeError, RecursionError):
                        continue
    except (ValueError, TypeError, RecursionError):
        pass
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    receipt = artifact.get("op_receipt")
    status = receipt.get("status") if isinstance(receipt, dict) else None
    if not isinstance(status, str) or len(status) > 64 or not status.isascii():
        status = "unknown"
    head_end = min(PREVIEW_CHARS, len(text))
    tail_start = max(head_end, len(text) - TAIL_PREVIEW_CHARS)
    previews = [{"start_char": 0, "end_char": head_end, "text": text[:head_end]}]
    if tail_start < len(text):
        previews.append({"start_char": tail_start, "end_char": len(text), "text": text[tail_start:]})
    payload = {
        "tool_result_ref": reference,
        "delivery": "stored_result_with_partial_preview",
        "execution_status": status,
        "read_with": {"tool": "read_tool_result", "tool_call_id": message.tool_call_id, "start_char": 0},
        "complete": False,
        "field_index": index,
        "control_observation": control,
        "previews": previews,
        "notice": "原文已完整保存。这里不是完整检查结果；按字段或字符分页回读，不要重复执行。",
    }

    def render():
        payload["control_fields_omitted"] = total_controls - len(control)
        return _json(payload)

    rendered = render()
    if _content_bytes(rendered) > FIRST_DELIVERY_BYTES:
        payload["delivery"] = "stored_result_reference"
        payload.pop("previews")
        rendered = render()
    for field in ("field_index", "notice"):
        if _content_bytes(rendered) <= FIRST_DELIVERY_BYTES:
            break
        payload.pop(field)
        rendered = render()
    while _content_bytes(rendered) > FIRST_DELIVERY_BYTES and control:
        control.pop(next(reversed(control)))
        rendered = render()
    if _content_bytes(rendered) > FIRST_DELIVERY_BYTES:
        return _failure(message, "result_reference_too_large", verified=True)
    metadata = {**message.additional_kwargs, "first_delivery_reference": True}
    return message.model_copy(update={"content": rendered, "additional_kwargs": metadata})

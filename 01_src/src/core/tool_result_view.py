"""Bound the first model delivery without modifying stored tool evidence."""
from __future__ import annotations

import hashlib
import json
import re

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


def content_wire_bytes(value) -> int:
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
    try:
        if (not row or row.get("tool_name") != message.name or row.get("full_sha256") != digest
                or not storage_integrity(row)["complete"]):
            return None
    except (ValueError, TypeError):
        return None
    return {"tool_call_id": message.tool_call_id, "full_sha256": digest,
            "original_chars": len(text), "stored_chars": len(text)}


def delivery_issue(message):
    """Expose only known delivery states, without publishing tool output."""
    code = message.additional_kwargs.get("model_delivery_issue")
    messages = {
        "result_storage_unavailable": "回执原文暂时无法验证或读取。请保留现有操作记录。",
        "result_page_too_large": "该页超过单次交付上限。请缩小原文页或字段范围。",
        "result_reference_too_large": "回执引用超过交付上限。",
        "result_readback_unavailable": "回执已保存，当前任务无法调用原文读取工具。",
    }
    return (code, messages[code]) if isinstance(code, str) and code in messages else None


def last_readback_range(observations):
    """Range of the latest verified read page for a budget-stop notice."""
    for message in reversed(observations):
        if message.name != "read_tool_result" or not message.additional_kwargs.get("host_publication"):
            continue
        try:
            data = json.loads(message.content)
        except (TypeError, ValueError, RecursionError):
            continue
        if not isinstance(data, dict) or data.get("status") != "ok":
            continue
        storage, source, page = (data.get(key) for key in ("storage", "tool_result_ref", "page"))
        if not all(isinstance(part, dict) for part in (storage, source, page)) or storage.get("complete") is not True:
            continue
        digest, call_id = source.get("full_sha256"), source.get("tool_call_id")
        start, end, total = (page.get(key) for key in ("start_char", "end_char", "total_chars"))
        basis = page.get("offset_basis")
        if (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or not isinstance(call_id, str) or not 0 < len(call_id) <= 256
                or any(type(value) is not int for value in (start, end, total))
                or not 0 <= start < end <= total or basis not in ("stored_text", "selected_fields_json")):
            continue
        cursor = {"tool_call_id": call_id, "full_sha256": digest, "start_char": start,
                  "end_char": end, "total_chars": total, "offset_basis": basis,
                  "next_start_char": end if end < total else None}
        max_chars = min(4000, max(1, (end - start) // 2))
        if basis == "stored_text":
            cursor["retry"] = {"start_char": start, "max_chars": max_chars}
        else:
            # Public progress carries no private JSON field names. Reuse the
            # original selection; these offsets cannot address stored text.
            cursor["retry_same_selection"] = {"start_char": start, "max_chars": max_chars}
        return cursor
    return None


def _failure(message, code: str, *, verified: bool = False):
    metadata = dict(message.additional_kwargs)
    metadata.pop("first_delivery_reference", None)
    metadata["model_delivery_issue"] = code
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
        size = content_wire_bytes(text)
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
                content_wire_bytes(index)
            except UnicodeError:
                index = []
            for key in _CONTROL_PRIORITY:
                if key in value:
                    total_controls += 1
                    try:
                        if content_wire_bytes(_json(value[key])) <= FIRST_DELIVERY_BYTES:
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
    if content_wire_bytes(rendered) > FIRST_DELIVERY_BYTES:
        payload["delivery"] = "stored_result_reference"
        payload.pop("previews")
        rendered = render()
    for field in ("field_index", "notice"):
        if content_wire_bytes(rendered) <= FIRST_DELIVERY_BYTES:
            break
        payload.pop(field)
        rendered = render()
    while content_wire_bytes(rendered) > FIRST_DELIVERY_BYTES and control:
        control.pop(next(reversed(control)))
        rendered = render()
    if content_wire_bytes(rendered) > FIRST_DELIVERY_BYTES:
        return _failure(message, "result_reference_too_large", verified=True)
    metadata = {**message.additional_kwargs, "first_delivery_reference": True}
    metadata.pop("model_delivery_issue", None)
    return message.model_copy(update={"content": rendered, "additional_kwargs": metadata})

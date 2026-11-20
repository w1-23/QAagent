"""First delivery of large tool outputs: verified storage reference and exact preview.

This runs once, before an execution result enters model context. It never rewrites
messages already seen by the model; paginated readers and skill text stay intact.
"""
from __future__ import annotations

import hashlib
import json

INLINE_CHARS = 16000
FIRST_DELIVERY_BYTES = 3072
PREVIEW_CHARS = 768
TAIL_PREVIEW_CHARS = 256
_READERS = frozenset({"read_tool_result", "read_candidate", "read_skill", "load_skill", "read_context_evidence"})
_WORKSPACE_READS = frozenset({"read", "read_run", "read_skill", "read_skill_resource", "context_source"})


def _content_bytes(text: str) -> int:
    return len(json.dumps(text, ensure_ascii=False).encode("utf-8"))


def first_delivery(message, *, call: dict, reader_available: bool):
    text = message.content
    if (not reader_available or not isinstance(text, str) or message.name in _READERS
            or (message.name == "research_workspace" and call.get("args", {}).get("operation") in _WORKSPACE_READS)):
        return message
    if _content_bytes(text) <= FIRST_DELIVERY_BYTES:
        return message
    reference = message.additional_kwargs.get("result_reference")
    if not isinstance(reference, dict):
        return message
    storage = reference.get("storage") or {}
    if (not storage.get("complete") or reference.get("tool_call_id") != message.tool_call_id
            or reference.get("original_chars") != len(text)
            or reference.get("full_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest()):
        return message
    # Parse solely to list readable paths/types. Never reserialize evidence
    # numbers through floats or manufacture a scientific summary from a preview.
    index = []
    control = {}
    try:
        value = json.loads(text, parse_float=str, parse_int=str)
        if isinstance(value, dict):
            index = [{"pointer": "/" + key.replace("~", "~0").replace("/", "~1"),
                      "type": "object" if isinstance(item, dict) else "array" if isinstance(item, list) else "scalar"}
                     for key, item in list(value.items())[:12] if len(key) <= 80]
            # Keep the small machine-facing receipt visible even when long rows
            # and provider evidence are externalized. Completion/next-action and
            # export IDs must not disappear behind an arbitrary character slice.
            for key in ("audit_id", "review_id", "status", "terminal", "review_required", "next_action",
                        "can_resume", "export_ready", "continuation", "coverage", "counts", "export_requests", "review",
                        "_tool_outcome", "dataset_ref", "analysis_result_ref", "files", "exports"):
                if key in value and len(json.dumps(value[key], ensure_ascii=False)) <= 3000:
                    control[key] = value[key]
    except (ValueError, TypeError):
        pass
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    receipt = artifact.get("op_receipt") or {}
    head_end = min(PREVIEW_CHARS, len(text))
    tail_start = max(head_end, len(text) - TAIL_PREVIEW_CHARS)
    previews = [{"start_char": 0, "end_char": head_end, "text": text[:head_end]}]
    if tail_start < len(text):
        previews.append({"start_char": tail_start, "end_char": len(text), "text": text[tail_start:]})
    payload = {
        "tool_result_ref": reference,
        "delivery": "stored_result_with_partial_preview",
        "execution_status": receipt.get("status", "unknown"),
        "read_with": {"tool": "read_tool_result", "tool_call_id": message.tool_call_id,
                      "start_char": 0},
        "field_index": index,
        "control_observation": control,
        "previews": previews,
        "notice": "原始结果已完整保存；这里只显示开头与结尾，不代表完整检查结果。按字段 JSON Pointer 或字符分页读取所需证据，不要为了取回结果重新执行工具。",
    }
    # Account for JSON escaping and multibyte characters in the final view.
    # Keep control fields intact if no smaller complete representation fits.
    def serialized():
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    rendered = serialized()
    if _content_bytes(rendered) > FIRST_DELIVERY_BYTES:
        payload["delivery"] = "stored_result_reference"
        payload["field_index"] = []
        payload["previews"] = []
        payload["notice"] = "原文已保存。按工具调用 ID 和字段或字符范围读取所需记录，无需重新执行。"
        rendered = serialized()
    if _content_bytes(rendered) > FIRST_DELIVERY_BYTES:
        return message
    metadata = dict(message.additional_kwargs)
    metadata["first_delivery_reference"] = True
    return message.model_copy(update={"content": rendered,
                                      "additional_kwargs": metadata})

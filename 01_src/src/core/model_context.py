"""Pure model send views; saved transcripts and operation results stay intact."""
from __future__ import annotations

from collections import Counter
import json
import re

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

PROJECTION_VERSION = 1
# Set by server message construction, never inferred from XML in user content.
_ENVELOPE_ORIGINS = frozenset({"generated_user", "server_envelope", "build_human_message", "generated_context"})
_ROW_KEYS = frozenset({"included_row_ids", "candidate_row_ids", "not_run_row_ids",
                       "excluded_rows", "row_ids", "original_row_ids",
                       "excluded_row_ids", "rows"})
_REF_KEYS = frozenset({"analysis_result_ref", "dataset_ref", "result_refs",
                       "artifact_id", "file_id", "source", "result_ref", "script_ref", "run_id"})


def _data(message):
    try:
        value = json.loads(message.content)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _refs(data):
    # Exact structured references only: never mine arbitrary script/document args.
    return {key: value for key, value in data.items() if key in _REF_KEYS}


def statistical_send_view(message):
    """Compact row bookkeeping while retaining every numerical result and warning.

    A durable analysis reference is required. Reasons are counted across the
    entire omitted list, not just its preview. Ordinary result tables stay whole.
    """
    if not isinstance(message, ToolMessage) or message.name != "analyze_research_data":
        return message
    data = _data(message)
    if not data.get("analysis_result_ref"):
        return message

    def compact(value, parent=""):
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                row_list = key in _ROW_KEYS and (key != "rows" or parent == "exclusions")
                if row_list and isinstance(item, list) and len(item) > 12:
                    out[key + "_count"] = len(item)
                    out[key + "_preview"] = [compact(entry) for entry in item[:3]]
                    reasons = Counter(str(entry.get("reason", "unspecified"))
                                      for entry in item if isinstance(entry, dict))
                    if reasons:
                        out[key + "_reason_counts"] = dict(reasons)
                else:
                    out[key] = compact(item, key)
            return out
        if isinstance(value, list):
            return [compact(item, parent) for item in value]
        return value

    result = compact(data)
    if result == data:
        return message
    result["model_view_notice"] = (
        "仅精简发送给模型的逐行定位清单；全精度统计、警告、覆盖、排除原因计数保留。"
        "完整分析与数据通过 analysis_result_ref/dataset_ref 读取，不从预览重建数据。")
    return message.model_copy(update={"content": json.dumps(result, ensure_ascii=False)})


def project_tool_message(message):
    """First-entry projection API, idempotent and free of storage/model calls.

    ``additional_kwargs.context_projection`` records version, kind, result_refs
    and completeness. Storage availability is unknown unless the caller supplies
    trusted ``result_storage`` metadata after recording the full result.
    Statistics bypass generic clamps entirely, including historical projection.
    """
    if not isinstance(message, ToolMessage):
        return message
    metadata = dict(message.additional_kwargs or {})
    prior = metadata.get("context_projection")
    if isinstance(prior, dict) and prior.get("version") == PROJECTION_VERSION:
        return message
    from src.core.tool_results import clamp_for_model
    raw = message.content
    projected = statistical_send_view(message)
    statistical = message.name == "analyze_research_data"
    clipped = False
    if not statistical and isinstance(projected.content, str):
        content, clipped = clamp_for_model(projected.content, message.name or "")
        projected = projected.model_copy(update={"content": content})
    refs = _refs(_data(message))
    result_refs = [{"kind": "tool_result", "tool_call_id": message.tool_call_id}]
    if refs:
        result_refs.append(refs)
    storage = metadata.get("result_storage")
    metadata["context_projection"] = {
        "version": PROJECTION_VERSION,
        "semantic_details": _semantic_details(message) if not statistical else [],
        "execution_status": _outcome(message, _data(message)),
        "kind": "statistics" if statistical else "tool_result",
        "result_refs": result_refs,
        "completeness": {
            "model_view_complete": projected.content == raw,
            "original_chars": len(raw) if isinstance(raw, str) else None,
            "generic_clamped": clipped,
            "storage": dict(storage) if isinstance(storage, dict) else {"state": "unknown"},
        },
    }
    return projected.model_copy(update={"additional_kwargs": metadata})


def historical_send_view(messages: list) -> list:
    """One output per input in the same order, suitable for row-ID checkpoints.

    Only trusted metadata unwraps server-generated user envelopes. Legacy rows
    and literal XML requests are preserved. Group folding is a separate API.
    """
    from src.core.context_budget import _keep_identifiers
    view = []
    for message in messages:
        if isinstance(message, ToolMessage):
            message = project_tool_message(message)
            if (message.name != "analyze_research_data" and isinstance(message.content, str)
                    and len(message.content) > 1800
                    and not message.additional_kwargs["context_projection"].get("history_digest")):
                refs = message.additional_kwargs["context_projection"]["result_refs"]
                content = (json.dumps({"result_refs": refs}, ensure_ascii=False) + "\n"
                           + _keep_identifiers(message.content, max_chars=1100)
                           + "\n[历史工具结果摘要；不是本轮重新执行。回读可能受存储截断或轮转限制。]")
                meta = dict(message.additional_kwargs)
                projection = dict(meta["context_projection"])
                projection["completeness"] = dict(projection["completeness"], model_view_complete=False)
                projection["history_digest"] = True
                meta["context_projection"] = projection
                message = message.model_copy(update={"content": content, "additional_kwargs": meta})
        elif isinstance(message, HumanMessage):
            meta = message.additional_kwargs or {}
            if meta.get("context_trusted") is True and meta.get("context_origin") == "internal_control":
                message = AIMessage(
                    content="[历史内部控制已省略；不是用户要求]",
                    additional_kwargs=dict(meta), id=message.id, name=message.name,
                    response_metadata=dict(message.response_metadata or {}))
            elif (meta.get("context_trusted") is True and meta.get("context_origin") in _ENVELOPE_ORIGINS
                    and isinstance(meta.get("user_text"), str)):
                content = meta["user_text"]
                refs = meta.get("attachment_refs")
                if refs:
                    content += "\n[历史附件引用]\n" + json.dumps(refs, ensure_ascii=False)
                message = message.model_copy(update={"content": content})
        view.append(message)
    return view


def _semantic_details(message):
    """Keep warnings/rejections/coverage outside a length-limited digest."""
    data = _data(message)
    keys = {"warning", "warnings", "error", "errors", "reason", "reason_code",
            "coverage", "limitations", "truncation_reasons", "status", "success"}
    details = []

    def visit(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keys:
                    details.append({"path": path + [key], "value": item})
                else:
                    visit(item, path + [key])
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, path + [index])

    visit(data, [])
    if not data and isinstance(message.content, str):
        warnings = [line for line in message.content.splitlines()
                    if re.search(r"warning|error|failed|not.applicable|警告|失败|不适用|截断|排除", line, re.I)]
        if warnings:
            details.append({"path": ["text_warnings"], "value": warnings})
    return details


def _outcome(message, data):
    if getattr(message, "status", None) == "error":
        return "error"
    status = data.get("status")
    if isinstance(status, str):
        return status[:80]
    for key in ("_tool_outcome", "execution_summary", "result_summary", "summary"):
        summary = data.get(key)
        if isinstance(summary, dict) and isinstance(summary.get("status"), str):
            return summary["status"][:80]
    if data.get("success") is True:
        return "success"
    if data.get("success") is False or data.get("error"):
        return "error"
    if isinstance(message.content, str) and re.match(r"\[(?:[A-Z_]*(?:ERROR|EMPTY|ARG|FAIL)[A-Z_]*)\]", message.content):
        return "error"
    return "returned; execution outcome not reported"


def fold_completed_tool_groups(messages: list) -> list:
    """Replace only complete contiguous AI/tool groups by historical AI facts.

    Leaves pending/malformed groups and protocol messages intact. No tool calls
    or synthetic ToolMessages are emitted, no source message is mutated, and
    script/document arguments are never copied into the historical summary.
    """
    from src.core.context_budget import _keep_identifiers
    output = []
    index = 0
    while index < len(messages):
        message = messages[index]
        calls = getattr(message, "tool_calls", None) if isinstance(message, AIMessage) else None
        ids = [call.get("id") for call in calls or []]
        end = index + 1
        while end < len(messages) and isinstance(messages[end], ToolMessage):
            end += 1
        results = messages[index + 1:end]
        complete = (bool(ids) and all(ids) and len(ids) == len(set(ids))
                    and len(results) == len(ids)
                    and {item.tool_call_id for item in results} == set(ids))
        if not complete:
            output.append(message)
            index += 1
            continue
        names = {call["id"]: str(call.get("name") or "unknown")[:120] for call in calls}
        facts = []
        for result in results:
            projected = project_tool_message(result)
            data = _data(projected)
            fact = {"tool_name": names[result.tool_call_id], "tool_call_id": result.tool_call_id,
                    "status": projected.additional_kwargs["context_projection"]["execution_status"],
                    "result_refs": projected.additional_kwargs["context_projection"]["result_refs"],
                    "completeness": projected.additional_kwargs["context_projection"]["completeness"]}
            if result.name == "analyze_research_data":
                fact["result"] = data or projected.content
            else:
                text = projected.content if isinstance(projected.content, str) else json.dumps(projected.content, ensure_ascii=False)
                fact["result_digest"] = _keep_identifiers(text, max_chars=1100)
                details = projected.additional_kwargs["context_projection"].get("semantic_details", [])
                if details:
                    fact["semantic_details"] = details
            facts.append(fact)
        summary = {"notice": "历史已返回的工具结果事实摘要；不是本轮工具调用或重新执行。",
                   "results": facts}
        if message.content:
            summary["assistant_note"] = _keep_identifiers(str(message.content), max_chars=800)
        output.append(AIMessage(content=json.dumps(summary, ensure_ascii=False),
                                additional_kwargs={"context_origin": "completed_tool_group",
                                                   "context_trusted": True,
                                                   "context_projection_version": PROJECTION_VERSION}))
        index = end
    return output

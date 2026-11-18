"""Pure model send views; saved transcripts and operation results stay intact."""
from __future__ import annotations

from collections import Counter
import json
import re

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

PROJECTION_VERSION = 2
_SEMANTIC_LIMIT = 8000
_STATISTICS_LIMIT = 40000
_SKILL_TOOLS = frozenset({"research_workspace", "read_skill", "read_skill_resource", "load_skill"})
# Set by server message construction, never inferred from XML in user content.
_ENVELOPE_ORIGINS = frozenset({"generated_user", "server_envelope", "build_human_message", "generated_context"})
_ROW_KEYS = frozenset({"included_row_ids", "candidate_row_ids", "not_run_row_ids",
                       "excluded_rows", "row_ids", "original_row_ids",
                       "excluded_row_ids", "rows"})
_REF_KEYS = frozenset({"analysis_result_ref", "dataset_ref", "result_refs",
                       "artifact_id", "file_id", "source", "result_ref", "script_ref", "run_id",
                       "doi", "pmid", "evidence_id", "chunk_id", "source_id", "url", "link",
                       "sha256", "source_sha256", "file_hash", "figure_ref", "figure_id",
                       "artifact_ref", "output_path", "file_path", "skill_id", "resource"})
_FACT_KEYS = frozenset({
    "warning", "warnings", "error", "errors", "reason", "reason_code", "coverage",
    "limitations", "source_limitations", "truncation_reasons", "status", "success",
    "findings", "conclusion", "conclusions", "claim", "claims", "conflicts",
    "verification", "verification_results", "verified", "checks", "check",
    "audit_scope", "scope", "execution", "execution_receipt", "summary",
    "statistics", "tests", "comparisons", "estimates", "grouped", "assumptions",
    "analysis", "methods", "metrics", "n", "p", "p_value", "estimate", "effect_size",
    "confidence_interval", "units", "design", "mapping", "transformations",
    "files", "artifacts", "artifact_validations", "panels", "research_intent",
    "results", "papers", "evidence", "sources", "matches", "title", "abstract",
})


def _bounded_value(value, *, depth=0):
    """A preview never impersonates a complete object or changes numeric values."""
    if isinstance(value, str):
        return value if len(value) <= 600 else value[:600] + " [OMITTED: read source]"
    if isinstance(value, (dict, list)) and depth >= 5:
        return {"omitted": True, "items": len(value)}
    if isinstance(value, list):
        preview = [_bounded_value(item, depth=depth + 1) for item in value[:6]]
        return preview if len(value) <= 6 else {"preview": preview, "total_items": len(value), "omitted_items": len(value) - 6}
    if isinstance(value, dict):
        # Put identities and caveats ahead of narrative or long table data.
        keys = sorted(value, key=lambda key: (key not in _REF_KEYS, key not in _FACT_KEYS))
        out = {key: _bounded_value(value[key], depth=depth + 1) for key in keys[:24]}
        if len(keys) > 24:
            out["_omitted_fields"] = len(keys) - 24
        return out
    return value


def _bounded_entries(entries, limit):
    output, used, omitted = [], 2, 0
    for entry in entries:
        preview = _bounded_value(entry)
        size = len(json.dumps(preview, ensure_ascii=False)) + 2
        if used + size > limit - 180:
            # Large collections retain a small, explicitly partial preview.
            preview = {"path": entry.get("path", []), "preview": str(entry.get("value", ""))[:400],
                       "omitted": True}
            size = len(json.dumps(preview, ensure_ascii=False)) + 2
        if used + size <= limit - 180:
            output.append(preview)
            used += size
        else:
            omitted += 1
    if omitted:
        output.append({"omitted_entries": omitted, "notice": "Read result_refs before drawing conclusions about omitted findings."})
    return output


def _data(message):
    try:
        value = json.loads(message.content)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _refs(data):
    # Exact result fields only; sibling hashes/versions stay bound to their IDs.
    entries = []
    def visit(value, path):
        if isinstance(value, dict):
            refs = {key: item for key, item in value.items()
                    if key in _REF_KEYS or (key in {"version", "id", "kind", "start_char", "next_start_char", "content_origin"}
                                          and any(k in value for k in _REF_KEYS))}
            if refs:
                entries.append({"path": path, "value": refs})
            for key, item in value.items():
                if key not in _REF_KEYS:
                    visit(item, path + [key])
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, path + [index])
    visit(data, [])
    return _bounded_entries(entries, 4000)


def is_skill_result(message):
    if not isinstance(message, ToolMessage) or message.name not in _SKILL_TOOLS:
        return False
    data = _data(message)
    if message.name == "load_skill":
        pages = data.get("skill_load")
        return bool(isinstance(pages, list) and pages and all(
            isinstance(item, dict) and isinstance(item.get("page"), dict)
            and item["page"].get("content_origin") == "installed_skill_guidance" for item in pages))
    resource = data.get("page", data.get("entrypoint", data))
    return isinstance(resource, dict) and resource.get("content_origin") == "installed_skill_guidance"


def _history_payload(message):
    projected = project_tool_message(message)
    metadata = projected.additional_kwargs["context_projection"]
    text = projected.content if isinstance(projected.content, str) else json.dumps(projected.content, ensure_ascii=False)
    result = {"result_digest": _keep_digest(text),
              "semantic_details": metadata.get("semantic_details", []),
              "notice": "Partial historical facts; read result_refs for omitted details. Not a new execution."}
    if metadata.get("skill_guidance"):
        result["skill_reload_required"] = "Read the referenced skill/resource and remaining pages before applying its method; this historical digest is not the full guidance."
    return result


def _keep_digest(text):
    from src.core.context_budget import _keep_identifiers
    return _keep_identifiers(text, max_chars=1100)


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
    """Historical projection API, idempotent and free of storage/model calls.

    ``additional_kwargs.context_projection`` records version, kind, result_refs
    and completeness. Storage availability is unknown unless the caller supplies
    trusted ``result_storage`` metadata after recording the full result.
    Small statistics stay exact. Large tables use explicit bounded previews;
    references never imply that truncated or rotated source data is recoverable.
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
    if statistical and isinstance(projected.content, str) and len(projected.content) > _STATISTICS_LIMIT:
        # Small statistics stay exact; large tables must not bypass admission.
        projected = projected.model_copy(update={"content": json.dumps({
            "result_refs": _refs(_data(message)), "semantic_details": _semantic_details(projected),
            "notice": "Partial statistics preview. Numeric values shown are unchanged; omitted tests and rows require readback by tool_call_id/analysis_result_ref.",
        }, ensure_ascii=False)})
    elif not statistical and isinstance(projected.content, str):
        content, clipped = clamp_for_model(projected.content, message.name or "")
        projected = projected.model_copy(update={"content": content})
    refs = _refs(_data(message))
    result_refs = [{"kind": "tool_result", "tool_call_id": message.tool_call_id}]
    if refs:
        result_refs.extend(refs)
    previous = prior if isinstance(prior, dict) else {}
    old_completeness = previous.get("completeness") or {}
    for ref in previous.get("result_refs", []):
        if ref not in result_refs:
            result_refs.append(ref)
    # Old persisted projections cannot become complete again after upgrading.
    storage = metadata.get("result_storage") or old_completeness.get("storage")
    details = previous.get("semantic_details")
    details = _bounded_entries(details, _SEMANTIC_LIMIT) if isinstance(details, list) else _semantic_details(message)
    metadata["context_projection"] = {
        "version": PROJECTION_VERSION,
        "semantic_details": details,
        "skill_guidance": previous.get("skill_guidance") or is_skill_result(message),
        "execution_status": previous.get("execution_status") or _outcome(message, _data(message)),
        "kind": "statistics" if statistical else "tool_result",
        "result_refs": result_refs,
        "completeness": {
            "model_view_complete": projected.content == raw and old_completeness.get("model_view_complete", True),
            "original_chars": old_completeness.get("original_chars", len(raw) if isinstance(raw, str) else None),
            "generic_clamped": clipped or old_completeness.get("generic_clamped", False),
            "storage": dict(storage) if isinstance(storage, dict) else {"state": "unknown"},
        },
    }
    return projected.model_copy(update={"additional_kwargs": metadata})


def historical_send_view(messages: list) -> list:
    """One output per input in the same order, suitable for row-ID checkpoints.

    Only trusted metadata unwraps server-generated user envelopes. Legacy rows
    and literal XML requests are preserved. Group folding is a separate API.
    """
    view = []
    for message in messages:
        if isinstance(message, ToolMessage):
            message = project_tool_message(message)
            if (message.name != "analyze_research_data" and isinstance(message.content, str)
                    and len(message.content) > 1800
                    and not message.additional_kwargs["context_projection"].get("history_digest")):
                refs = message.additional_kwargs["context_projection"]["result_refs"]
                content = json.dumps({"result_refs": refs, **_history_payload(message)}, ensure_ascii=False)
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
    """Bound facts with their identities and caveats, not identifiers alone."""
    data = _data(message)
    keys = _FACT_KEYS
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
    return _bounded_entries(details, _SEMANTIC_LIMIT)


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


def fold_completed_tool_groups(messages: list, *, preserve_latest_results: bool = False) -> list:
    """Replace only complete contiguous AI/tool groups by historical AI facts.

    Leaves pending/malformed groups and protocol messages intact. No tool calls
    or synthetic ToolMessages are emitted, no source message is mutated, and
    script/document arguments are never copied into the historical summary.
    Active compaction can retain the latest batch's projected results so a fresh
    response is not reduced to a digest before its first model read.
    """
    from src.core.context_budget import _keep_identifiers
    output = []
    latest_group = None
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
        result_payloads = []
        for result in results:
            projected = project_tool_message(result)
            data = _data(projected)
            result_payloads.append(data or projected.content)
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
        latest_group = (len(output), summary, result_payloads)
        output.append(AIMessage(content=json.dumps(summary, ensure_ascii=False),
                                additional_kwargs={"context_origin": "completed_tool_group",
                                                   "context_trusted": True,
                                                   "context_projection_version": PROJECTION_VERSION}))
        index = end
    if preserve_latest_results and latest_group is not None:
        position, summary, payloads = latest_group
        # Frozen epochs may already contain a previously protected batch. Once
        # newer results arrive, that older batch can become a digest as usual;
        # otherwise preserved payloads would accumulate across every checkpoint.
        for earlier, item in enumerate(output[:position]):
            meta = item.additional_kwargs or {}
            if (not isinstance(item, AIMessage) or not meta.get("context_latest_results")
                    or meta.get("context_origin") != "completed_tool_group"
                    or meta.get("context_trusted") is not True):
                continue
            prior = _data(item)
            for fact in prior.get("results", []):
                if "result" in fact and fact.get("tool_name") != "analyze_research_data":
                    value = fact.pop("result")
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                    fact["result_digest"] = _keep_identifiers(text, max_chars=1100)
            prior["notice"] = "历史已返回的工具结果事实摘要；不是本轮工具调用或重新执行。"
            output[earlier] = item.model_copy(update={
                "content": json.dumps(prior, ensure_ascii=False),
                "additional_kwargs": dict(meta, context_latest_results=False)})
        for fact, payload in zip(summary["results"], payloads):
            fact.pop("result_digest", None)
            fact["result"] = payload
        summary["notice"] = "最近一批已返回工具结果；保留完整发送视图，仅省略调用参数。不是重新执行。"
        output[position] = output[position].model_copy(update={
            "content": json.dumps(summary, ensure_ascii=False),
            "additional_kwargs": dict(output[position].additional_kwargs, context_latest_results=True)})
    return output

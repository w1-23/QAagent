"""Reuse host receipts across clarification segments, without replaying actions."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re


def restore_continuation(state, tables, *, validate_artifacts):
    """Return logical-task facts and dedup identities, never execution events.

    ``continuation_context`` is supplied by the authorized question store, not
    model output. Still require matching receipts, current source bytes, and
    fresh owner/session/hash validation for every retained artifact.
    """
    context = state.get("continuation_context")
    parent = str(state.get("parent_job_id") or "")
    if not isinstance(context, dict) or not parent or not state.get("no_replay"):
        return [], {}
    hashes = {}
    for index, table in enumerate(tables):
        raw = table.get("data") if isinstance(table, dict) else None
        if isinstance(raw, bytes):
            hashes[index] = hashlib.sha256(raw).hexdigest()

    def same_source(index, digest):
        return type(index) is int and bool(digest) and hashes.get(index) == digest

    records, signatures = [], {}
    for raw in context.get("tool_status") or []:
        if not isinstance(raw, dict):
            continue
        receipt = raw.get("op_receipt")
        name, call_id, status = raw.get("name"), raw.get("tool_call_id"), raw.get("status")
        if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1
                or not call_id or receipt.get("tool_call_id") != call_id
                or receipt.get("tool_name") != name or receipt.get("status") != status
                or name == "request_user_input" or status not in {"complete", "partial", "no_results"}):
            continue
        record = deepcopy(raw)
        payload = record["op_receipt"].get("coverage") or {}
        if name == "audit_uploaded_data":
            items = payload.get("results") or []
            if not items or any(not same_source(item.get("file_index"), item.get("source_sha256")) for item in items):
                continue
        elif name == "verify_uploaded_references":
            scope = payload.get("coverage") or {}
            if scope.get("source_kind") == "attachment" and not same_source(scope.get("file_index"), scope.get("source_sha256")):
                continue
        old_validations = record.get("artifact_validations") or []
        links = [v.get("link") or v.get("artifact_id") for v in old_validations if isinstance(v, dict)]
        fresh = validate_artifacts([link for link in links if link]) if links else []
        record["artifact_validations"] = fresh
        record["artifact_count"] = sum(v.get("valid") is True for v in fresh)
        # Figure coverage is usable only if its published exports still exist.
        if name in {"run_research_figure_script", "create_research_figure"} or (
                name == "research_workspace" and receipt.get("reason_code") == "figure_edit_saved"):
            valid_links = {v.get("link") for v in fresh if v.get("valid") is True}
            figures = payload.get("figures") if isinstance(payload.get("figures"), list) else [payload]
            for figure in figures:
                figure["exports"] = [e for e in figure.get("exports", [])
                                     if isinstance(e, dict) and e.get("artifact_url") in valid_links]
        record["inherited_from_job_id"] = str(raw.get("inherited_from_job_id") or parent)
        records.append(record)
        logical = str(record.get("logical_key") or "")
        prefix = str(name) + ":"
        signature = logical[len(prefix):] if logical.startswith(prefix) else ""
        if re.fullmatch(r"[a-f0-9]{64}", signature):
            signatures[signature] = {"name": name, "tool_call_id": call_id, "status": status,
                "retryable": False, "attempts": max(1, int(record.get("attempt") or 1))}
    return records, signatures

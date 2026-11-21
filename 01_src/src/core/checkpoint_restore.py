"""Validate persisted post-turn navigation before hiding any raw history.

Only deterministic host checkpoints are trusted. Legacy or model-authored prose
is never injected: an unrecognized document leaves every raw message in the
send view. Validation binds owner, session, coverage watermark, source
inventory identity, pinned tool evidence and exact excerpts to the live store.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA = "citrus-turn-checkpoint/2"
GENERATOR = "deterministic-host"
MAX_EXCERPTS = 12
MAX_EXCERPT_CHARS = 2000


def structured_checkpoint(text):
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None
    except (ValueError, TypeError):
        return None


def source_inventory(sources) -> list[dict]:
    """Stable, bounded identity for every covered raw-message prefix."""
    rows = []
    for row in sources:
        preview = str(row.get("preview") or "")
        rows.append({
            "source_id": str(row.get("source_id")),
            "role": str(row.get("role") or ""),
            "total_chars": int(row.get("total_chars") or 0),
            "preview_sha256": hashlib.sha256(preview.encode("utf-8")).hexdigest(),
            "row_sha256": row["row_sha256"],
        })
    return rows


def inventory_digest(inventory) -> str:
    canonical = json.dumps(inventory, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def navigation_view(data: dict, *, limit: int = 6000) -> str:
    """Render only the verified, explicitly bounded navigation the model sees."""
    nav = data.get("navigation") or {}
    inventory = data.get("source_inventory") or []
    view = {
        "goal": str(nav.get("goal") or "")[:2000],
        "decisions": [str(item)[:1000] for item in (nav.get("decisions") or [])[:20]],
        "unfinished": [str(item)[:1000] for item in (nav.get("unfinished") or [])[:20]],
        "host_task_status": str(data.get("host_task_status") or ""),
        "covered": {
            "first_source_id": inventory[0].get("source_id") if inventory else None,
            "last_source_id": inventory[-1].get("source_id") if inventory else None,
            "message_count": len(inventory),
        },
        "tool_result_call_ids": [str(item) for item in
                                 (data.get("tool_result_call_ids") or [])][:50],
        "exact_excerpts": [{
            "source_id": item.get("source_id"),
            "start_char": item.get("start_char"),
            "end_char": item.get("end_char"),
            "quote": str(item.get("quote") or "")[:MAX_EXCERPT_CHARS],
        } for item in (data.get("exact_excerpts") or [])[:MAX_EXCERPTS]],
        "read_originals_with": {
            "tool": "research_workspace",
            "operation": "context_source",
            "arguments": "message_id=<source_id>, start_char, max_chars",
            "tool_results": "read_tool_result(tool_call_id=<tool_result_call_id>)",
            "notice": "Raw messages are authoritative; read them before relying on omitted details.",
        },
    }
    text = json.dumps(view, ensure_ascii=False)
    while len(text) > limit and view["exact_excerpts"]:
        view["exact_excerpts"].pop()
        text = json.dumps(view, ensure_ascii=False)
    while len(text) > limit and view["unfinished"]:
        view["unfinished"].pop()
        text = json.dumps(view, ensure_ascii=False)
    while len(text) > limit and view["decisions"]:
        view["decisions"].pop()
        text = json.dumps(view, ensure_ascii=False)
    # Enforce the caller's hard bound even when the goal itself is large.
    if len(text) > limit:
        view["goal"] = ""
        text = json.dumps(view, ensure_ascii=False)
    if len(text) > limit:
        view["tool_result_call_ids"] = view["tool_result_call_ids"][:10]
        text = json.dumps(view, ensure_ascii=False)
    if len(text) > limit:
        view["exact_excerpts"], view["decisions"], view["unfinished"] = [], [], []
        view["read_originals_with"] = {}
        text = json.dumps(view, ensure_ascii=False)
    if len(text) > limit:
        return ""
    return text


def checkpoint_rejection(data, manager, session_id, cutoff, *,
                         rejected_sources: set[str] | None = None) -> str | None:
    """Validate history navigation and return a stable, content-free rejection code."""
    try:
        from src.guardrails.turn_checkpoint import validate_pairs
        from src.core import tool_results
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return "legacy_checkpoint_untrusted"
        if data.get("generator") != GENERATOR:
            return "legacy_checkpoint_untrusted"
        if data.get("navigation_authoritative") is not False:
            return "legacy_checkpoint_untrusted"
        owner = data.get("owner_user_id")
        if type(owner) is not int or owner <= 0 or data.get("session_id") != session_id:
            return "owner_scope_mismatch"
        from src.core.tracing import get_session_id, get_user_id
        from src.core.account_leases import current_account_lease
        lease = current_account_lease()
        current = getattr(lease, "owner", None) if lease is not None else None
        current = int(current) if current else int(get_user_id() or 0)
        if current != owner or get_session_id() != session_id:
            return "owner_scope_mismatch"
        if not manager.session_belongs_to(session_id, owner):
            return "owner_scope_mismatch"
        status = data["host_task_status"]
        if status not in {"completed", "partial"}:
            return "source_integrity_failed"
        host = data.get("host_task_state") or {}
        if (host.get("turn_outcome") or {}).get("status") != status:
            return "source_integrity_failed"
        sources = manager.get_checkpoint_sources(session_id, cutoff)
        if not sources or int(sources[-1]["source_id"]) != int(cutoff):
            return "raw_history_incomplete"
        inventory = source_inventory(sources)
        expected = data.get("source_inventory")
        if not isinstance(expected, list) or data.get("inventory_sha256") != inventory_digest(expected):
            return "source_integrity_failed"
        expected_by_id = {row["source_id"]: row for row in expected}
        current_ids = {row["source_id"] for row in inventory}
        if rejected_sources is not None:
            rejected_sources.update(row["source_id"] for row in inventory
                                    if expected_by_id.get(row["source_id"]) != row)
        if expected_by_id.keys() - current_ids:
            return "raw_history_incomplete"
        if inventory != expected:
            return "source_integrity_failed"
        call_ids = validate_pairs(sources)
        if call_ids != data.get("tool_result_call_ids"):
            return "source_integrity_failed"
        expected_hashes = data.get("tool_result_sha256")
        if not isinstance(expected_hashes, dict) or expected_hashes.keys() != set(call_ids):
            return "source_integrity_failed"
        for call_id in call_ids:
            row = tool_results.fetch(call_id)
            if row and row.get("session_id") != session_id:
                return "owner_scope_mismatch"
            if not row or not row.get("pinned"):
                return "raw_history_incomplete"
            integrity = tool_results.storage_integrity(row)
            if integrity["state"] == "truncated":
                return "raw_history_incomplete"
            if not integrity["complete"] or integrity["full_sha256"] != expected_hashes[call_id]:
                return "source_integrity_failed"
        excerpts = data.get("exact_excerpts")
        if not isinstance(excerpts, list) or len(excerpts) > MAX_EXCERPTS:
            return "source_integrity_failed"
        index = {row["source_id"]: row for row in inventory}
        seen = set()
        for item in excerpts:
            if not isinstance(item, dict):
                return "source_integrity_failed"
            sid, start, end = item.get("source_id"), item.get("start_char"), item.get("end_char")
            quote = item.get("quote")
            if (sid not in index or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= index[sid]["total_chars"]
                    or end - start > MAX_EXCERPT_CHARS
                    or not isinstance(quote, str) or not quote):
                return "source_integrity_failed"
            key = (sid, start, end)
            if key in seen:
                return "source_integrity_failed"
            seen.add(key)
            if item.get("quote_sha256") != hashlib.sha256(quote.encode("utf-8")).hexdigest():
                return "source_integrity_failed"
            try:
                original = manager.get_context_source(session_id, int(sid), start, end - start)
            except LookupError:
                return "raw_history_incomplete"
            if original["text"] != quote:
                return "source_integrity_failed"
        return None
    except PermissionError:
        return "owner_scope_mismatch"
    except Exception:
        return "source_integrity_failed"


def validate_checkpoint(data, manager, session_id, cutoff) -> bool:
    return checkpoint_rejection(data, manager, session_id, cutoff) is None

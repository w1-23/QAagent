"""Validate persisted post-turn navigation before hiding any raw history.

Only deterministic host checkpoints are trusted. Legacy or model-authored prose
is never injected: an unrecognized document leaves every raw message in the
send view. Validation binds owner, session, coverage watermark, source
inventory identity, pinned tool evidence and exact excerpts to the live store.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA = "citrus-turn-checkpoint/1"
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


def validate_checkpoint(data, manager, session_id, cutoff):
    """Return True only for a deterministic checkpoint bound to this session.

    Any mismatch is a safe failure: the caller keeps all raw history and never
    injects unverified prose. Legacy checkpoints without the deterministic
    marker are intentionally rejected (fail-safe restore).
    """
    try:
        from src.guardrails.turn_checkpoint import validate_pairs
        from src.core import tool_results
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            return False
        if data.get("generator") != GENERATOR:
            return False
        if data.get("navigation_authoritative") is not False:
            return False
        owner = data.get("owner_user_id")
        if type(owner) is not int or owner <= 0 or data.get("session_id") != session_id:
            return False
        from src.core.tracing import get_user_id
        from src.core.account_leases import current_account_lease
        lease = current_account_lease()
        current = getattr(lease, "owner", None) if lease is not None else None
        current = int(current) if current else int(get_user_id() or 0)
        if current != owner:
            return False
        if hasattr(manager, "session_belongs_to") and not manager.session_belongs_to(session_id, owner):
            return False
        status = data["host_task_status"]
        if status not in {"completed", "partial"}:
            return False
        host = data.get("host_task_state") or {}
        if (host.get("turn_outcome") or {}).get("status") != status:
            return False
        sources = manager.get_checkpoint_sources(session_id, cutoff)
        if not sources or int(sources[-1]["source_id"]) != int(cutoff):
            return False
        inventory = source_inventory(sources)
        if inventory != data.get("source_inventory"):
            return False
        if data.get("inventory_sha256") != inventory_digest(inventory):
            return False
        call_ids = validate_pairs(sources)
        if call_ids != data.get("tool_result_call_ids"):
            return False
        for call_id in call_ids:
            row = tool_results.fetch(call_id)
            if (not row or row.get("session_id") != session_id or not row.get("pinned")
                    or not tool_results.storage_integrity(row)["complete"]):
                return False
        excerpts = data.get("exact_excerpts")
        if not isinstance(excerpts, list) or len(excerpts) > MAX_EXCERPTS:
            return False
        index = {row["source_id"]: row for row in inventory}
        seen = set()
        for item in excerpts:
            if not isinstance(item, dict):
                return False
            sid, start, end = item.get("source_id"), item.get("start_char"), item.get("end_char")
            quote = item.get("quote")
            if (sid not in index or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= index[sid]["total_chars"]
                    or end - start > MAX_EXCERPT_CHARS
                    or not isinstance(quote, str) or not quote):
                return False
            key = (sid, start, end)
            if key in seen:
                return False
            seen.add(key)
            if item.get("quote_sha256") != hashlib.sha256(quote.encode("utf-8")).hexdigest():
                return False
            original = manager.get_context_source(session_id, int(sid), start, end - start)
            if original["text"] != quote:
                return False
        return True
    except Exception:
        return False

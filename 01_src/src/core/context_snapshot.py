"""Metadata-only context projection for one logical call and physical attempt.

Estimates describe the prepared input, usage describes only the identified
attempt. Neither configured working allowances nor saved conversation text are
substitutes for a known model context window or an observed request.
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)

COMPONENTS = ("system", "messages", "tool_arguments", "tool_schemas", "images", "protocol")


def _count(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def build_context_snapshot(estimate, *, logical_call_id, attempt_id="", purpose="",
                           model="", epoch=0, change_reason="",
                           history_load_status="unknown", input_limit=None,
                           working_soft_tokens=None, working_target_tokens=None,
                           usage=None, status="estimated", error=""):
    """Build a self-contained event; missing counters remain unknown, never zero.

    Allocate logical_call_id before estimation. Set attempt_id after admission,
    and emit again with the same IDs after settlement. Pass model_window in the
    estimate only when known for this actual model, not from a task budget.
    """
    estimate = estimate if isinstance(estimate, dict) else {}
    source = estimate.get("components") or {}
    components = {key: _count(source.get(key)) for key in COMPONENTS}
    input_tokens = _count(estimate.get("estimated_input_tokens"))
    if input_tokens is None and all(value is not None for value in components.values()):
        input_tokens = sum(components.values())
    window = _count(estimate.get("model_window")) or None
    history = str(history_load_status or "unknown")
    # ContextManager's existing success value predates the wire vocabulary.
    if history == "ok":
        history = "loaded"
    if history not in {"success", "loaded", "degraded", "failed", "empty", "unknown"}:
        history = "unknown"
    reported = {}
    if attempt_id and isinstance(usage, dict):
        for key in ("input_tokens", "output_tokens", "total_tokens", "cache_read",
                    "cache_write", "cache_uncached", "cache_hit", "cache_miss"):
            value = _count(usage.get(key))
            if value is not None:
                reported[key] = value
    measured = any(key in reported for key in ("input_tokens", "output_tokens", "total_tokens"))
    if reported:
        reported.update(scope="single_call", logical_call_id=str(logical_call_id),
                        attempt_id=str(attempt_id))
    return {
        "snapshot_version": 1, "scope": "context_snapshot",
        "logical_call_id": str(logical_call_id or ""), "attempt_id": str(attempt_id or ""),
        "source": str(purpose or ""), "model": str(model or ""), "epoch": epoch,
        "status": str(status), "change_reason": str(change_reason or ""),
        "history_load_status": history,
        "estimate_scope": estimate.get("estimate_scope", "full_request_estimate"),
        "estimate_method": estimate.get("estimate_method", "unknown"),
        "estimated_input_tokens": input_tokens, "estimated_tokens": input_tokens,
        "estimated_total_tokens": _count(estimate.get("estimated_total_tokens")),
        "components": components, "model_window": window,
        "model_window_status": "known" if window else "unknown",
        "input_limit": _count(input_limit), "output_tokens": _count(estimate.get("output_tokens")),
        "working_soft_tokens": _count(working_soft_tokens),
        "working_target_tokens": _count(working_target_tokens),
        "tool_message_count": _count(estimate.get("tool_message_count")),
        "image_count": _count(estimate.get("image_count")),
        "image_estimate_status": estimate.get("image_estimate_status", "unknown"),
        "provider_usage": reported, "usage_status": "reported" if measured else "unknown",
        "error": str(error or ""),
    }


def emit_context_snapshot(snapshot):
    """Use the request's durable SSE queue, keeping notification failure isolated."""
    from src.core.progress_bus import emit_encoded, get_turn_scope, get_request_layouts
    session_id, request_id = get_turn_scope()
    try:
        layouts = get_request_layouts()
        key = ("__context_snapshot__", session_id, request_id)
        previous = layouts.get(key) if layouts is not None else None
        if previous:
            same_attempt = (snapshot["logical_call_id"], snapshot["attempt_id"]) == (previous["logical_call_id"], previous["attempt_id"])
            if same_attempt:
                snapshot["change_reason"] = previous["change_reason"]
            elif snapshot["change_reason"] == "protocol_fallback":
                pass
            elif snapshot["logical_call_id"] == previous["logical_call_id"]:
                snapshot["change_reason"] = "retry"
            elif snapshot["source"].startswith("final_synthesis") and not snapshot["components"].get("tool_schemas"):
                snapshot["change_reason"] = "final_synthesis"
            elif snapshot["epoch"] != previous["epoch"]:
                pass  # Keep the explicit checkpoint/layout reason.
            elif (snapshot.get("tool_message_count") or 0) > (previous.get("tool_message_count") or 0):
                snapshot["change_reason"] = "tool_results_added"
            elif snapshot["components"] != previous["components"]:
                snapshot["change_reason"] = "prepared_payload_changed"
            else:
                snapshot["change_reason"] = "unchanged"
        if layouts is not None:
            layouts[key] = dict(snapshot)
        emit_encoded("context_snapshot", dict(snapshot, session_id=session_id, request_id=request_id))
    except Exception as exc:
        # Context delivery must never turn a successful model call into failure.
        logger.warning("[context_snapshot] notification failed: %s", type(exc).__name__)

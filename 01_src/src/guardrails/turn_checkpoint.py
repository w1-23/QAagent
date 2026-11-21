"""Deterministic, fail-closed post-response navigation checkpoints.

A checkpoint is not a model summary. It records the host retention watermark,
the identity/hash of every covered raw message prefix, pinned tool-evidence
references, deterministic exact excerpts and host-derived navigation. No model
is called. Raw messages are never edited or deleted; later loads inject only a
bounded navigation view, and originals stay readable through the existing
authorized read tools (`research_workspace(operation="context_source")` for
conversation rows, `read_tool_result` for tool evidence).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

logger = logging.getLogger(__name__)

MAX_EXCERPTS = 12
MAX_EXCERPT_CHARS = 2000
MAX_DOCUMENT_CHARS = 4_000_000
_AI_ROLES = frozenset({"ai", "AIMessageChunk", "assistant"})
_USER_ROLES = frozenset({"human", "user"})


def eligible(state: dict) -> bool:
    outcome = state.get("turn_outcome") or {}
    status = outcome.get("status")
    return bool(state.get("answer")) and status in {"completed", "partial"} and not any(
        state.get(key) for key in ("cancelled", "pending_question", "awaiting_input"))


def validate_pairs(sources: list[dict]) -> list[str]:
    pending, seen, calls = set(), set(), []
    for row in sources:
        role = row["role"]
        if role == "tool":
            call_id = row["tool_call_id"]
            if call_id not in pending:
                raise ValueError("checkpoint_orphan_tool")
            pending.remove(call_id)
        else:
            if pending:
                raise ValueError("checkpoint_incomplete_tool_group")
            if role in {"ai", "AIMessageChunk"}:
                for call in row["tool_calls"]:
                    call_id = call.get("id")
                    if not isinstance(call_id, str) or not call_id or call_id in seen:
                        raise ValueError("checkpoint_duplicate_tool_id")
                    pending.add(call_id)
                    seen.add(call_id)
                    calls.append(call_id)
    if pending or not sources or sources[-1]["role"] not in {"ai", "AIMessageChunk"}:
        raise ValueError("checkpoint_unfinished_turn")
    return calls


def _reject(reason: str) -> bool:
    logger.warning("Post-turn checkpoint deferred: %s", reason)
    return False


def _owner() -> int:
    """Authenticated account identity for this maintenance task."""
    try:
        from src.core.account_leases import current_account_lease
        lease = current_account_lease()
        if lease is not None and int(lease.owner) > 0:
            return int(lease.owner)
    except Exception:
        pass
    try:
        from src.core.tracing import get_user_id
        return int(get_user_id() or 0)
    except Exception:
        return 0


def _navigation(state: dict, sources: list[dict]) -> dict:
    """Host-derived navigation only; never a model narrative or completion claim."""
    goal = str(state.get("query") or "")[:MAX_EXCERPT_CHARS]
    if not goal:
        for row in sources:
            meta = row.get("metadata") or {}
            if (str(row.get("role")) in _USER_ROLES
                    and meta.get("context_origin") != "internal_control"):
                goal = str(meta.get("user_text") or row.get("preview") or "")[:MAX_EXCERPT_CHARS]
                break
    outcome = state.get("turn_outcome") or {}
    completion = outcome.get("completion") if isinstance(outcome, dict) else None
    remaining = (completion or {}).get("remaining") if isinstance(completion, dict) else None
    if remaining is None and isinstance(outcome, dict):
        remaining = outcome.get("remaining")
    unfinished = []
    if isinstance(remaining, list):
        for item in remaining[:20]:
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            text = str(text).strip()
            if text:
                unfinished.append(text[:1000])
    return {"goal": goal, "decisions": [], "unfinished": unfinished}


def _excerpt(manager, session_id: str, source: dict, *, tail: bool) -> dict | None:
    total = int(source.get("total_chars") or 0)
    if total <= 0:
        return None
    length = min(MAX_EXCERPT_CHARS, total)
    start = max(0, total - length) if tail else 0
    try:
        got = manager.get_context_source(session_id, int(source["source_id"]), start, length)
    except Exception:
        return None
    quote = str(got.get("text") or "")
    if not quote:
        return None
    return {"source_id": str(source["source_id"]), "start_char": start,
            "end_char": start + len(quote), "quote": quote,
            "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest()}


def _deterministic_excerpts(manager, session_id: str, sources: list[dict]) -> list[dict]:
    humans = [row for row in sources if str(row.get("role")) in _USER_ROLES
              and (row.get("metadata") or {}).get("context_origin") != "internal_control"]
    answers = [row for row in sources if str(row.get("role")) in _AI_ROLES]
    candidates = []
    if humans:
        candidates.append((humans[0], False))
        if humans[-1] is not humans[0]:
            candidates.append((humans[-1], False))
    if answers:
        candidates.append((answers[-1], True))
    selected = []
    for source, tail in candidates:
        item = _excerpt(manager, session_id, source, tail=tail)
        if item is not None:
            selected.append(item)
    return selected


def _inherited_excerpts(manager, session_id: str, prior: dict,
                        sources_by_id: dict[str, dict]) -> list[dict]:
    """Carry forward only previously verified deterministic precision."""
    if prior.get("generator") != "deterministic-host":
        return []
    kept = []
    for item in prior.get("exact_excerpts") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("source_id"))
        start, end = item.get("start_char"), item.get("end_char")
        quote = item.get("quote")
        source = sources_by_id.get(sid)
        if (source is None or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= int(source.get("total_chars") or 0)
                or end - start > MAX_EXCERPT_CHARS
                or not isinstance(quote, str) or not quote):
            continue
        try:
            got = manager.get_context_source(session_id, int(sid), start, end - start)
        except Exception:
            continue
        if str(got.get("text") or "") != quote:
            continue
        kept.append({**item, "quote_sha256":
                     hashlib.sha256(quote.encode("utf-8")).hexdigest()})
    return kept


async def build_checkpoint(manager, session_id: str, state: dict, snapshot: dict) -> bool:
    """Run under the originating account lease, independently of delivery.

    The build is deterministic and model-free. Every failure leaves the prior
    checkpoint untouched and preserves all raw history for the next load.
    """
    if not eligible(state):
        return False
    try:
        from src.core import tool_results
        from src.core.checkpoint_restore import (GENERATOR, SCHEMA, inventory_digest,
                                                 source_inventory)

        owner = _owner()
        if owner <= 0:
            return _reject("checkpoint_owner_unavailable")
        from src.core.tracing import get_session_id
        if get_session_id() != session_id or not await asyncio.to_thread(manager.session_belongs_to, session_id, owner):
            return _reject("checkpoint_owner_scope_mismatch")
        watermark = int(snapshot["message_watermark"])
        sources = await asyncio.to_thread(manager.get_checkpoint_sources, session_id, watermark)
        call_ids = validate_pairs(sources)
        if int(sources[-1]["source_id"]) != watermark:
            return _reject("source_watermark_mismatch")
        request_id = str(state.get("request_id") or
                         (state.get("turn_envelope") or {}).get("request_id") or
                         state.get("idempotency_key") or "")
        if not request_id or sources[-1]["metadata"].get("request_id") != request_id:
            return _reject("request_identity_mismatch")
        # Pin before writing: legacy missing/truncated evidence is not compressible.
        if call_ids and not await asyncio.to_thread(tool_results.pin_references, session_id, call_ids):
            return _reject("evidence_not_pinnable")
        tool_hashes = {}
        for call_id in call_ids:
            row = await asyncio.to_thread(tool_results.fetch, call_id)
            if not row or row.get("session_id") != session_id or not row.get("pinned"):
                return _reject("checkpoint_evidence_unavailable")
            integrity = tool_results.storage_integrity(row)
            if not integrity["complete"]:
                return _reject("checkpoint_evidence_unavailable")
            tool_hashes[call_id] = integrity["full_sha256"]
        from copy import deepcopy
        host_state = deepcopy({key: state[key] for key in
                      ("task_contract", "coverage", "turn_outcome") if key in state})
        # Query is a raw source, not a duplicate task requirement; obligations stay exact.
        contract = host_state.get("task_contract")
        if isinstance(contract, dict):
            contract.pop("query", None)
        status = state["turn_outcome"]["status"]
        prior = {}
        if snapshot.get("summary"):
            try:
                parsed = json.loads(snapshot["summary"])
                if isinstance(parsed, dict) and parsed.get("schema") == SCHEMA:
                    prior = parsed
            except (TypeError, ValueError):
                pass  # Legacy prose is never promoted to authoritative evidence.
        inventory = source_inventory(sources)
        sources_by_id = {str(row["source_id"]): row for row in sources}
        excerpts = _inherited_excerpts(manager, session_id, prior, sources_by_id)
        excerpts.extend(_deterministic_excerpts(manager, session_id, sources))
        seen, exact = set(), []
        for item in excerpts:
            key = (item["source_id"], item["start_char"], item["end_char"])
            if key in seen:
                continue
            seen.add(key)
            exact.append(item)
            if len(exact) >= MAX_EXCERPTS:
                break
        document = {
            "schema": SCHEMA,
            "generator": GENERATOR,
            "navigation_authoritative": False,
            "owner_user_id": owner,
            "session_id": session_id,
            "navigation": _navigation(state, sources),
            "host_task_status": status,
            "host_task_state": host_state,
            "source_inventory": inventory,
            "inventory_sha256": inventory_digest(inventory),
            "tool_result_call_ids": call_ids,
            "tool_result_sha256": tool_hashes,
            "exact_excerpts": exact,
        }
        encoded = json.dumps(document, ensure_ascii=False)
        if len(encoded) > MAX_DOCUMENT_CHARS:
            return _reject("checkpoint_document_too_large")
        # No cancellable thread hand-off at publication: cancellation before this
        # point leaves the prior checkpoint; the small CAS transaction is atomic.
        saved = manager.set_checkpoint(session_id, watermark, encoded,
            expected_revision=snapshot["revision"],
            expected_message_watermark=watermark)
        return True if saved else _reject("stale_source_revision")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        import re
        detail = str(exc)
        reason = detail if re.fullmatch(r"(?:checkpoint|context)_[a-z_]+", detail) else type(exc).__name__
        return _reject(reason)


def schedule_checkpoint(manager, session_id: str, state: dict, lease) -> None:
    """Only authenticated account-leased jobs may produce a checkpoint."""
    if lease is None or not eligible(state):
        return
    from copy import deepcopy
    from src.core.account_leases import create_account_task
    from src.core.background import adopt
    snapshot = manager.get_checkpoint(session_id)
    payload = deepcopy({key: state[key] for key in (
        "answer", "query", "request_id", "turn_envelope", "idempotency_key",
        "task_contract", "coverage", "turn_outcome", "cancelled",
        "pending_question", "awaiting_input") if key in state})
    adopt(create_account_task(lease, build_checkpoint(manager, session_id, payload, snapshot)))

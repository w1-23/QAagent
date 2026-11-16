"""Explicit same-run recovery from protected server state, never client inputs.

Admission does not replay operations or reserve another quota. The API owns the
account lease and worker lifecycle; the jobs transaction owns the dispatch CAS.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import time

from .identity import ExecutionIdentity
from .checkpoint_store import checkpoint_path, checkpoint_scope, open_checkpoint
from src.core.task_events import TaskConflict

# Only stable server-prepared request/context fields are recoverable. Runtime
# messages are already authoritative in the inner graph checkpoint.
_REQUEST_FIELDS = frozenset({
    "query", "request_id", "client_request_id", "session_id", "user_id", "job_id",
    "idempotency_key", "visualization_enabled", "context_compression", "selected_skills", "attachment_refs", "attachment_context",
    "image_attachments", "has_paper_attachment", "request_tables", "mode",
    "agent_no_prefetch_hints", "parent_job_id", "question_answer", "continuation_context",
    "no_replay", "script_budget_scope", "context_working_state", "history_summary",
    "long_term_memory", "resident_cards", "user_preferences", "account_files",
    "history_evidence_block", "history_load_status", "history_load_error", "format_hint",
    "candidate_pool", "candidate_pool_notice", "request_plan", "task_contract",
})


def capture_original_request_state(state: dict) -> dict:
    """Called once by the host before mutable task updates are restored/applied."""
    return deepcopy({key: value for key, value in state.items() if key in _REQUEST_FIELDS})


def validate_original_request_state(value, identity: ExecutionIdentity) -> dict:
    if type(value) is not dict or set(value) - _REQUEST_FIELDS:
        raise TaskConflict("recovery_original_request_missing")
    if (type(value.get("user_id")) is not int
            or value["user_id"] != identity.owner_user_id
            or value.get("session_id") != identity.session_id
            or value.get("request_id") != identity.user_turn_id
            or value.get("job_id") != identity.run_id):
        raise TaskConflict("recovery_identity_mismatch")
    if (not isinstance(value.get("query"), str) or not value["query"].strip()
            or not isinstance(value.get("client_request_id"), str)
            or not value["client_request_id"]
            or not isinstance(value.get("idempotency_key"), str)
            or not value["idempotency_key"]):
        raise TaskConflict("recovery_original_request_missing")
    return deepcopy(value)


async def load_recovery_state(root: Path, identity: ExecutionIdentity,
                              provider_scope: tuple[str, str]) -> dict:
    """Read the latest authenticated checkpoint using only server identity/scope."""
    if not checkpoint_path(root, identity, provider_scope).is_file():
        raise TaskConflict("recovery_checkpoint_missing")
    async with open_checkpoint(root, identity, provider_scope) as saver:
        saved = await saver.aget_tuple({"configurable": checkpoint_scope(identity, provider_scope)})
    if saved is None:
        raise TaskConflict("recovery_checkpoint_missing")
    values = saved.checkpoint.get("channel_values", {})
    if not isinstance(values, dict) or not values.get("messages"):
        raise TaskConflict("recovery_checkpoint_invalid")
    status = values.get("stop_status")
    answered = status == "complete" and values.get("stop_reason") == "answered"
    if status and not answered:
        raise TaskConflict("recovery_checkpoint_terminal")
    if values.get("batch_status") in {"unknown", "waiting"} or values.get("unknown_operations"):
        raise TaskConflict("recovery_requires_reconciliation")
    host_state = values.get("host_state")
    if not isinstance(host_state, dict):
        raise TaskConflict("recovery_original_request_missing")
    state = validate_original_request_state(host_state.get("original_request_state"), identity)
    # Candidate bytes were process-local and are not present in the protected
    # snapshot. Never pretend those search handles survived a restart.
    if (state.get("candidate_pool") or {}).get("pool_id") and not answered:
        raise TaskConflict("recovery_candidate_pool_unavailable")
    state["agent_resume"] = True
    state["messages"] = []  # Harness resumes with None; no new HumanMessage.
    state["answer"] = ""
    state["agent_recovery_answered"] = answered
    return state


def restore_deadline(state: dict, budget: dict) -> dict:
    """Translate the ORIGINAL wall deadline; never reuse pre-restart monotonic time."""
    wall_deadline = float(budget["deadline"])
    if math.isnan(wall_deadline) or wall_deadline == -math.inf:
        raise TaskConflict("recovery_budget_invalid")
    result = deepcopy(state)
    result["request_deadline"] = (math.inf if wall_deadline == math.inf else
                                  time.monotonic() + max(0.0, wall_deadline - time.time()))
    result["request_started_at"] = time.perf_counter()
    return result

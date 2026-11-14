"""Legacy display adapter to the structured task-result reducer.

Answer wording is content, never a completion signal. New callers pass the
explicit TurnOutcome through TurnEnvelope; older callers reduce actual records.
"""
from __future__ import annotations
from .task_result import build_task_result

VALID_TURN_STATUSES = ("completed", "partial", "failed", "cancelled", "awaiting_input")
SUCCESS_STATUSES = frozenset({"complete", "no_results"})
DEGRADED_STATUSES = frozenset({"partial", "unknown"})
NOT_EXECUTED_STATUSES = frozenset({"skipped", "not_run"})
FAILED_STATUSES = frozenset({"failed"})
CANCELLED_STATUSES = frozenset({"cancelled"})


def derive_turn_status(answer: str = "", tool_status: list[dict] | None = None,
                       missing_groups: list | None = None) -> str:
    task = build_task_result(tool_status, missing_groups)
    if task.status in {"cancelled", "awaiting_input"}:
        return task.status
    return task.status if str(answer or "").strip() else "failed"

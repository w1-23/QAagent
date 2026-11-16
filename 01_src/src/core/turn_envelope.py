"""One terminal snapshot for SSE, jobs, and history restoration.

``derive_turn_status`` remains the authoritative display-state reducer and
``build_task_result`` remains the authoritative execution/acceptance reducer.
This module only composes their results with job identity and truthful notices;
it does not infer success from answer prose or from a disconnected browser.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from src.core.task_result import build_task_result
from src.core.turn_status import derive_turn_status

SCHEMA_VERSION = 1
VALID_DISPLAY_STATUSES = frozenset({"completed", "partial", "failed", "cancelled", "awaiting_input"})


@dataclass(frozen=True)
class TurnEnvelope:
    schema_version: int = SCHEMA_VERSION
    display_status: str = "failed"
    execution_status: str = "failed"
    acceptance_status: str = "not_evaluated"
    acceptance_reason: str = ""
    job_status: str = ""
    stop_reason: str = ""
    history_saved: bool = False
    notices: tuple[str, ...] = ()
    verified_artifacts: int = 0
    artifact_ids: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    terminal_seq: int = 0
    task_snapshot: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_turn_envelope(
    *,
    answer: str = "",
    tool_status: list[dict] | None = None,
    missing_groups: list | None = None,
    requested_status: str = "",
    display_status: str = "",
    job_status: str = "",
    stop_reason: str = "",
    history_saved: bool = False,
    notices: list[str] | tuple[str, ...] | None = None,
    terminal_seq: int = 0,
    task_snapshot: dict[str, Any] | None = None,
) -> TurnEnvelope:
    """Compose the single terminal view from structured records only.

    ``display_status`` is an explicit override for the user-cancel path, where
    the answer is intentionally empty and the stop marker is the terminal
    content.  It still has to be one of the four real display states.
    """
    # Both reducers and artifact validation must see the same attempt journal,
    # including when a caller supplies a one-shot iterator.
    tool_status = list(tool_status or ())
    missing_groups = list(missing_groups or ())
    # Structured terminal state takes precedence. Legacy callers still use the
    # adapter, but answer wording cannot upgrade a budget stop to completed.
    outcome = (task_snapshot or {}).get("turn_outcome")
    reviewed = isinstance(outcome, dict) and outcome.get("reason_code") in {
        "reviewed_completion", "reviewed_incomplete"}
    from src.core.turn_outcome import reviewed_completion_valid
    valid_review = reviewed and reviewed_completion_valid(outcome, tool_status, answer=answer)
    if reviewed and requested_status not in {"cancelled", "failed", "awaiting_input"}:
        requested_status = str(outcome.get("status")) if valid_review else "partial"
    if not requested_status and isinstance(outcome, dict):
        requested_status = str(outcome.get("status") or "")
    display = (str(display_status) if str(display_status or "") in VALID_DISPLAY_STATUSES
               else str(requested_status) if str(requested_status or "") in VALID_DISPLAY_STATUSES
               else derive_turn_status(answer, tool_status, missing_groups))
    if display not in VALID_DISPLAY_STATUSES:
        display = "failed"
    if display == "awaiting_input":
        requested_status = "awaiting_input"
    task = build_task_result(tool_status, missing_groups,
                             requested_status=requested_status, turn_outcome=outcome)
    if display == "completed" and task.status != "completed":
        display = task.status
    if display == "completed" and not str(answer or "").strip():
        display = "failed"
    outcome_notices = [item for key in ("notices", "operation_notices")
                       for item in (outcome.get(key) or ())] if isinstance(outcome, dict) else []
    clean_notices = tuple(dict.fromkeys(str(item).strip()
                          for item in [*(notices or ()), *outcome_notices]
                          if str(item or "").strip()))
    # The job table has three terminal states; a partial turn closes as an
    # incomplete (failed) job, never as completed.
    resolved_job_status = str(job_status or (
        "cancelled" if display == "cancelled"
        else "awaiting_input" if display == "awaiting_input"
        else "completed" if display == "completed" else "failed"))
    if requested_status == "awaiting_input":
        display = "awaiting_input"
        resolved_job_status = "awaiting_input"
    execution_status = task.execution_status
    acceptance_status = task.acceptance_status
    acceptance_reason = task.acceptance_reason
    if reviewed and not valid_review:
        acceptance_status = "partial"
        acceptance_reason = "completion_proof_invalid"
    # ``build_task_result`` aggregates tool records; it cannot see an empty
    # answer or an answer-level incomplete marker.  Keep the two reducers in
    # one direction: a failed/cancelled/partial display may lower the
    # execution/acceptance view, never the other way around.
    if display == "cancelled" and execution_status != "cancelled":
        execution_status = "cancelled"
        acceptance_status = "rejected"
        acceptance_reason = "cancelled"
    elif display == "failed" and execution_status == "completed":
        execution_status = "failed"
        acceptance_status = "rejected"
        acceptance_reason = "no_answer"
    elif display == "partial" and execution_status == "completed" and not reviewed:
        execution_status = "partial"
        acceptance_status = "partial"
        acceptance_reason = "execution_incomplete"
    # Artifact identity is carried explicitly so a client attributes files to
    # the turn that actually validated them, never to "whatever appeared
    # afterwards" in the account-level library.
    artifact_ids: list[str] = []
    seen_ids: set[str] = set()
    for validation in task.artifact_validations:
        artifact_id = str(validation.artifact_id or "").strip()
        if validation.valid and artifact_id and artifact_id not in seen_ids:
            seen_ids.add(artifact_id)
            artifact_ids.append(artifact_id)
    snapshot = dict(task_snapshot or {})
    if reviewed and display != outcome.get("status"):
        snapshot["turn_outcome"] = {**outcome, "status": display,
            "reason_code": acceptance_reason, "completion": None}
    # Proofs bind the internal journal and were validated above. The wire view
    # carries the resulting status/notices, never the internal answer envelope.
    snapshot = public_task_snapshot(snapshot)
    return TurnEnvelope(
        display_status=display,
        execution_status=execution_status,
        acceptance_status=acceptance_status,
        acceptance_reason=acceptance_reason,
        job_status=resolved_job_status,
        stop_reason=str(stop_reason or ""),
        history_saved=bool(history_saved),
        notices=clean_notices,
        verified_artifacts=task.verified_artifacts,
        artifact_ids=tuple(artifact_ids),
        failures=task.failures,
        missing_requirements=task.missing_requirements,
        terminal_seq=max(0, int(terminal_seq or 0)),
        task_snapshot=snapshot,
    )


def public_task_snapshot(snapshot: dict) -> dict:
    """Project terminal state without exposing private completion declarations."""
    result = dict(snapshot)
    if isinstance(result.get("turn_outcome"), dict):
        result["turn_outcome"] = {key: value for key, value in result["turn_outcome"].items()
                                  if key != "completion"}
    return result


def public_terminal_snapshot(envelope: dict) -> dict:
    """Apply the same boundary to old persisted terminal envelopes on read."""
    result = dict(envelope)
    if isinstance(result.get("task_snapshot"), dict):
        result["task_snapshot"] = public_task_snapshot(result["task_snapshot"])
    return result


__all__ = ["SCHEMA_VERSION", "TurnEnvelope", "build_turn_envelope"]

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
VALID_DISPLAY_STATUSES = frozenset({"completed", "partial", "failed", "cancelled"})


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
    display = (str(display_status or "")
               if str(display_status or "") in VALID_DISPLAY_STATUSES
               else derive_turn_status(answer, tool_status, missing_groups))
    if display not in VALID_DISPLAY_STATUSES:
        display = "failed"
    task = build_task_result(tool_status, missing_groups,
                             requested_status=requested_status)
    clean_notices = tuple(str(item).strip() for item in (notices or ())
                          if str(item or "").strip())
    # The job table has three terminal states; a partial turn closes as an
    # incomplete (failed) job, never as completed.
    resolved_job_status = str(job_status or (
        "cancelled" if display == "cancelled"
        else "completed" if display == "completed" else "failed"))
    execution_status = task.execution_status
    acceptance_status = task.acceptance_status
    acceptance_reason = task.acceptance_reason
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
    elif display == "partial" and execution_status == "completed":
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
    )


__all__ = ["SCHEMA_VERSION", "TurnEnvelope", "build_turn_envelope"]

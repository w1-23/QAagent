"""Internal execution, artifact-validation, and task-acceptance contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


VALID_TOOL_STATUSES = frozenset({
    "complete", "partial", "failed", "not_run", "no_results",
    "cancelled", "skipped",
})

_SUCCESS = frozenset({"complete", "no_results"})
_PARTIAL = frozenset({"partial"})
_NOT_EXECUTED = frozenset({"skipped", "not_run"})
_FAILED = frozenset({"failed"})
_CANCELLED = frozenset({"cancelled"})


@dataclass(frozen=True)
class ExecutionResult:
    name: str
    status: str
    reason_code: str = ""
    summary: str = ""
    artifact_links: tuple[str, ...] = ()
    verified_artifact_count: int = 0

    @classmethod
    def from_record(cls, row: dict[str, Any]) -> "ExecutionResult":
        status = str(row.get("status") or "").strip()
        if status not in VALID_TOOL_STATUSES:
            status = "failed"
        return cls(
            name=str(row.get("name") or "tool"),
            status=status,
            reason_code=str(row.get("reason") or row.get("reason_code") or ""),
            summary=str(row.get("summary") or ""),
            artifact_links=tuple(str(x) for x in (row.get("artifact_links") or ())),
            verified_artifact_count=max(0, int(row.get("artifact_count") or 0)),
        )


@dataclass(frozen=True)
class ArtifactValidation:
    artifact_id: str
    valid: bool
    exists: bool
    owner_matches: bool
    hash_matches: bool
    format_matches: bool = False
    reason_code: str = ""


@dataclass(frozen=True)
class TaskAcceptance:
    status: str
    reason_code: str
    unsatisfied: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskResult:
    status: str
    executed_steps: int
    verified_artifacts: int
    failures: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    execution_status: str = ""
    acceptance_status: str = "not_evaluated"
    acceptance_reason: str = ""
    artifact_validations: tuple[ArtifactValidation, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_validations"] = [
            item for item in payload.get("artifact_validations", ())
        ]
        return payload


def _aggregate_status(statuses: list[str], failures: tuple[str, ...],
                      missing: tuple[str, ...], requested_status: str) -> str:
    if requested_status in {"cancelled", "failed", "partial", "completed"}:
        return requested_status
    if any(value in _CANCELLED for value in statuses):
        return "partial" if failures else "cancelled"
    if failures and not any(value in _SUCCESS | _PARTIAL for value in statuses):
        return "failed"
    if failures or missing or any(value in _PARTIAL for value in statuses):
        return "partial"
    return "completed"


def _acceptance(status: str, failures: tuple[str, ...],
                missing: tuple[str, ...], has_execution: bool) -> TaskAcceptance:
    unsatisfied = tuple(sorted(set(failures) | set(missing)))
    if missing:
        return TaskAcceptance("rejected", "required_steps_unmet", missing)
    if status in {"failed", "cancelled"}:
        return TaskAcceptance("rejected", status, failures)
    if status == "partial" or failures:
        return TaskAcceptance("partial", "execution_incomplete", unsatisfied)
    if has_execution:
        return TaskAcceptance("accepted", "execution_and_requirements_satisfied")
    return TaskAcceptance("not_evaluated", "no_explicit_acceptance_criteria")


def build_task_result(tool_status: Iterable[dict] | None = None,
                      missing_groups: Iterable[Any] | None = None,
                      *, requested_status: str = "") -> TaskResult:
    """Aggregate terminal tool records and independently validated artifacts."""
    records = [ExecutionResult.from_record(row)
               for row in (tool_status or ()) if isinstance(row, dict)]
    statuses = [row.status for row in records]
    missing = tuple(str(x) for x in (missing_groups or ()))
    executed = sum(status not in _NOT_EXECUTED for status in statuses)
    verified = sum(row.verified_artifact_count for row in records)
    failures = tuple(sorted({
        f"{row.name}:{row.reason_code or row.status}"
        for row in records
        if row.status in _FAILED | _NOT_EXECUTED
    }))
    status = _aggregate_status(
        statuses, failures, missing, str(requested_status or ""))
    acceptance = _acceptance(status, failures, missing, bool(records))
    validations: list[ArtifactValidation] = []
    for row in (tool_status or ()):
        if not isinstance(row, dict):
            continue
        for item in row.get("artifact_validations") or ():
            if not isinstance(item, dict):
                continue
            validations.append(ArtifactValidation(
                artifact_id=str(item.get("artifact_id") or ""),
                valid=bool(item.get("valid")),
                exists=bool(item.get("exists")),
                owner_matches=bool(item.get("owner_matches")),
                hash_matches=bool(item.get("hash_matches")),
                format_matches=bool(item.get("format_matches")),
                reason_code=str(item.get("reason_code") or ""),
            ))
    return TaskResult(
        status=status,
        executed_steps=executed,
        verified_artifacts=verified,
        failures=failures,
        missing_requirements=missing,
        execution_status=status,
        acceptance_status=acceptance.status,
        acceptance_reason=acceptance.reason_code,
        artifact_validations=tuple(validations),
    )

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
            name=str(row.get("name") or row.get("tool_name") or "tool"),
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


def effective_tool_records(rows: Iterable[dict]) -> list[dict]:
    """Reduce identified retries without erasing the caller's attempt journal.

    Records without an operation identity remain independent. Tool names alone
    are not identities: a successful call on one input cannot repair another.
    Duplicate skips retain the proven source outcome, including its failures;
    a standalone skip never supplies evidence of success.
    """
    rows = [row for row in rows if isinstance(row, dict)]

    def identity(row):
        name = str(row.get("name") or row.get("tool_name") or "tool")
        key = str(row.get("logical_key") or "")
        return (name, key) if key and key != name else None

    def attempt(row):
        try:
            return max(1, int(row.get("attempt") or 1))
        except (TypeError, ValueError):
            return 1

    executed_statuses = _SUCCESS | _PARTIAL | _FAILED | _CANCELLED
    executed_ids = {
        (str(row.get("name") or row.get("tool_name") or "tool"), str(row["tool_call_id"]))
        for row in rows if row.get("tool_call_id") and row.get("status") in executed_statuses
    }
    executed_keys = {identity(row) for row in rows
                     if identity(row) is not None and row.get("status") in executed_statuses}
    effective: list[dict] = []
    positions: dict[tuple[str, str], int] = {}
    for row in rows:
        key = identity(row)
        reason = str(row.get("reason") or row.get("reason_code") or "")
        reused = (str(row.get("name") or row.get("tool_name") or "tool"),
                  str(row.get("reused_from_tool_call_id") or ""))
        if (row.get("status") == "skipped" and reason == "duplicate_request"
                and (reused in executed_ids or key in executed_keys)):
            continue
        if key is None:
            effective.append(row)
            continue
        position = positions.get(key)
        if position is None:
            positions[key] = len(effective)
            effective.append(row)
        elif attempt(row) >= attempt(effective[position]):
            effective[position] = row
    return effective


def build_task_result(tool_status: Iterable[dict] | None = None,
                      missing_groups: Iterable[Any] | None = None,
                      *, requested_status: str = "") -> TaskResult:
    """Aggregate terminal tool records and independently validated artifacts."""
    # Materialize once: generators must also reach artifact validation below.
    attempts = [row for row in (tool_status or ()) if isinstance(row, dict)]
    records = [ExecutionResult.from_record(row) for row in effective_tool_records(attempts)]
    statuses = [row.status for row in records]
    missing = tuple(str(x) for x in (missing_groups or ()))
    # Keep actual execution counts/audit, even when an earlier failure is repaired.
    executed = sum(ExecutionResult.from_record(row).status not in _NOT_EXECUTED
                   for row in attempts)
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
    for row in attempts:
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

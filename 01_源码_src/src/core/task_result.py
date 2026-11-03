"""Internal task result contract derived from executed, validated operations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class TaskResult:
    status: str
    executed_steps: int
    verified_artifacts: int
    failures: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_SUCCESS = frozenset({"complete", "no_results"})
_PARTIAL = frozenset({"partial"})
_NOT_EXECUTED = frozenset({"skipped", "not_run"})
_FAILED = frozenset({"failed"})
_CANCELLED = frozenset({"cancelled"})


def build_task_result(tool_status: Iterable[dict] | None = None,
                      missing_groups: Iterable[Any] | None = None,
                      *, requested_status: str = "") -> TaskResult:
    """Aggregate actual tool terminal records and delivery validation signals.

    Tool records are produced only after execution ends. ``artifact_count`` is
    added by the artifact publication path after integrity validation, so a
    model's prose cannot claim that a file was delivered.
    """
    records = [row for row in (tool_status or ()) if isinstance(row, dict)]
    statuses = [str(row.get("status") or "") for row in records]
    missing = tuple(str(x) for x in (missing_groups or ()))
    executed = sum(status not in _NOT_EXECUTED for status in statuses)
    verified = sum(max(0, int(row.get("artifact_count") or 0)) for row in records)
    failures = tuple(sorted({
        str(row.get("name") or "tool") + ":" + str(row.get("reason") or status)
        for row, status in zip(records, statuses)
        if status in _FAILED | _NOT_EXECUTED
    }))
    if requested_status in {"cancelled", "failed", "partial", "completed"}:
        status = requested_status
    elif any(value in _CANCELLED for value in statuses):
        status = "partial" if failures else "cancelled"
    elif failures and not any(value in _SUCCESS | _PARTIAL for value in statuses):
        status = "failed"
    elif failures or missing or any(value in _PARTIAL for value in statuses):
        status = "partial"
    else:
        status = "completed"
    return TaskResult(status, executed, verified, failures, missing)

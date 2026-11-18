"""Internal execution, artifact-validation, and task-acceptance contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


VALID_TOOL_STATUSES = frozenset({
    "complete", "partial", "failed", "not_run", "no_results",
    "cancelled", "skipped", "unknown",
})

_SUCCESS = frozenset({"complete", "no_results"})
_PARTIAL = frozenset({"partial", "unknown"})
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
    if requested_status in {"cancelled", "failed", "partial", "awaiting_input"}:
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
    if status == "awaiting_input":
        return TaskAcceptance("pending", "awaiting_user_input")
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


def _operation_identity(row):
    name = str(row.get("name") or row.get("tool_name") or "tool")
    key = str(row.get("logical_key") or "")
    receipt = row.get("op_receipt") or {}
    repair = receipt.get("repair_identity") if isinstance(receipt, dict) else None
    if (name == "run_research_figure_script" and isinstance(repair, str)
            and len(repair) == 64 and all(c in "0123456789abcdef" for c in repair)
            and receipt.get("schema_version") == 1 and receipt.get("run_id")
            and receipt.get("tool_name") == name
            and receipt.get("tool_call_id") == row.get("tool_call_id")
            and receipt.get("status") == row.get("status")
            and row.get("status") in {"failed", "partial", "complete"}
            and receipt.get("reason_code") == (row.get("reason") or row.get("reason_code"))):
        key = "script_repair:" + repair
    return (name, key) if key and key != name else None


def resolved_tool_attempts(rows: Iterable[dict], turn_outcome=None) -> list[dict]:
    """Public reconciliation links; original attempts and errors remain intact.

    A name match is never proof. Only an identified later successful operation
    or an unchanged host-validated completion proof can resolve an earlier row.
    Unknown/cancelled writes remain unresolved even if another call succeeds.
    """
    attempts = [row for row in rows if isinstance(row, dict)]
    effective = effective_tool_records(attempts)
    positions = {row.get("tool_call_id"): i for i, row in enumerate(attempts)}
    current = {_operation_identity(row): row for row in effective
               if _operation_identity(row) is not None}
    links = {}
    for row in attempts:
        cid = row.get("tool_call_id")
        replacement = current.get(_operation_identity(row), {})
        target = replacement.get("tool_call_id")
        if (cid and target and cid != target and row.get("status") in {"failed", "partial", "not_run"}
                and replacement.get("status") == "complete"
                and positions.get(target, -1) > positions.get(cid, -1)
                and not any(not v.get("valid") for v in replacement.get("artifact_validations", []))):
            links[cid] = {"tool_call_id": cid, "resolved_by_tool_call_id": target, "resolution": "repaired"}
    from src.core.turn_outcome import reviewed_completion_valid
    outcome = turn_outcome.as_dict() if hasattr(turn_outcome, "as_dict") else turn_outcome
    if reviewed_completion_valid(outcome, attempts):
        statement = outcome["completion"]["statement"]
        for claim in statement.get("proposal_repairs", []):
            cid = claim["rejected_call_id"]
            links[cid] = {"tool_call_id": cid, "resolved_by_tool_call_id": claim["corrected_call_id"],
                          "resolution": "repaired"}
        for claim in statement.get("incidental_partials", []):
            cid = claim["tool_call_id"]
            links[cid] = {"tool_call_id": cid, "resolved_by_tool_call_id": claim["replacement_call_ids"][0],
                          "resolution": "source_replaced"}
    return list(links.values())


def effective_tool_records(rows: Iterable[dict]) -> list[dict]:
    """Reduce identified retries without erasing the caller's attempt journal.

    Records without an operation identity remain independent. Tool names alone
    are not identities: a successful call on one input cannot repair another.
    Duplicate skips retain the proven source outcome, including its failures;
    a standalone skip never supplies evidence of success.
    """
    rows = [row for row in rows if isinstance(row, dict)]
    # A later batch can close an earlier overflow gap even though batching
    # deliberately gives those operations different logical keys. Reconcile
    # only receipt-proven coverage; preserve raw statuses and execution counts.
    if any((row.get("name") or row.get("tool_name")) == "verify_uploaded_references"
           and row.get("status") == "partial"
           and (row.get("reason") or row.get("reason_code")) in {"coverage_incomplete", "reference_coverage_incomplete"}
           and isinstance(row.get("op_receipt"), dict) for row in rows):
        from src.graph.coverage import reconcile_coverage_records
        rows = reconcile_coverage_records(rows)

    identity = _operation_identity

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


def incidental_partial_candidate(row: dict) -> bool:
    """Recognize a bounded exploratory partial from an intact host receipt.

    This is only eligibility, never proof of task completion. Coverage-bearing
    operations, output validation, authorization and unknown reasons stay hard
    constraints even when another operation fulfilled the task's obligations.
    """
    receipt = row.get("op_receipt")
    name = row.get("name") or row.get("tool_name")
    reason = str(row.get("reason") or row.get("reason_code") or "")
    if (row.get("status") != "partial" or not row.get("tool_call_id")
            or not isinstance(receipt, dict) or receipt.get("schema_version") != 1
            or receipt.get("tool_name") != name
            or receipt.get("tool_call_id") != row["tool_call_id"]
            or receipt.get("status") != "partial"
            or str(receipt.get("reason_code") or "") != reason
            or reason not in {"", "source_unavailable", "network", "timeout",
                              "dependency", "quota", "translation_failed"}
            or row.get("reason_class") not in (None, "", "source_unavailable", "transient")
            or any(row.get(key) for key in
                   ("artifact_count", "artifact_links", "artifact_validations"))):
        return False
    coverage = receipt.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != {"_tool_outcome"}:
        return False
    outcome = coverage["_tool_outcome"]
    return bool(isinstance(outcome, dict) and outcome.get("status") == "partial"
                and str(outcome.get("reason_code") or "") == reason
                and outcome.get("reason_class") in (None, "", "source_unavailable", "transient")
                and not outcome.get("artifacts"))


def _incidental_partial_ids(turn_outcome, records=()) -> set[str]:
    # Only the host's completed contract decision can grant this narrow
    # exception. A requested_status='completed' alone never grants it.
    if turn_outcome is None:
        return set()
    from src.core.turn_outcome import DEGRADED_STOP_REASONS, TurnOutcome
    outcome = turn_outcome.as_dict() if isinstance(turn_outcome, TurnOutcome) else turn_outcome
    if not isinstance(outcome, dict):
        return set()
    stop = str(outcome.get("stop_reason") or "")
    from src.core.turn_outcome import reviewed_completion_valid
    proven = outcome.get("reason_code") == "obligations_satisfied" or (
        outcome.get("reason_code") == "reviewed_completion"
        and reviewed_completion_valid(outcome, records))
    if (outcome.get("status") != "completed"
            or not proven
            or outcome.get("gaps") or stop in DEGRADED_STOP_REASONS
            or stop in {"cancelled", "awaiting_input"} or stop.startswith("model_error:")):
        return set()
    ids = outcome.get("incidental_partial_call_ids")
    return {v for v in ids if isinstance(v, str) and v} if isinstance(ids, (list, tuple)) else set()


def build_task_result(tool_status: Iterable[dict] | None = None,
                      missing_groups: Iterable[Any] | None = None,
                      *, requested_status: str = "", turn_outcome: Any = None) -> TaskResult:
    """Aggregate terminal tool records and independently validated artifacts."""
    # Materialize once: generators must also reach artifact validation below.
    attempts = [row for row in (tool_status or ()) if isinstance(row, dict)]
    effective = effective_tool_records(attempts)
    records = [ExecutionResult.from_record(row) for row in effective]
    incidental_ids = _incidental_partial_ids(turn_outcome, attempts)
    from src.core.turn_outcome import reviewed_completion_valid
    outcome = turn_outcome.as_dict() if hasattr(turn_outcome, "as_dict") else turn_outcome
    valid_review = reviewed_completion_valid(outcome, attempts)
    repaired_ids = set()
    if valid_review:
        repaired_ids = {c["rejected_call_id"] for c in outcome["completion"]["statement"].get("proposal_repairs", [])}
        repaired_ids.update(outcome["completion"].get("superseded_proposal_ids", ()))
    # Keep the raw status and execution count; omit only proven incidental
    # partials from the task-level status calculation.
    statuses = [result.status for row, result in zip(effective, records)
                if row.get("tool_call_id") not in repaired_ids
                and not (row.get("tool_call_id") in incidental_ids
                         and incidental_partial_candidate(row))]
    missing = tuple(str(x) for x in (missing_groups or ()))
    # Keep actual execution counts/audit, even when an earlier failure is repaired.
    executed = sum(ExecutionResult.from_record(row).status not in _NOT_EXECUTED
                   and str(row.get("name") or row.get("tool_name")) != "request_user_input"
                   for row in attempts)
    verified = sum(row.verified_artifact_count for row in records)
    failures = tuple(sorted({
        f"{row.name}:{row.reason_code or row.status}"
        for source, row in zip(effective, records)
        if row.status in _FAILED | _NOT_EXECUTED
        and source.get("tool_call_id") not in repaired_ids
        and row.reason_code != "awaiting_user_input"
    }))
    status = _aggregate_status(
        statuses, failures, missing, str(requested_status or ""))
    acceptance = _acceptance(status, failures, missing, bool(records))
    execution_status = status
    if isinstance(outcome, dict) and outcome.get("reason_code") in {"reviewed_completion", "reviewed_incomplete"}:
        # All attempts remain execution facts, including proven repaired ones.
        raw = [ExecutionResult.from_record(row) for row in attempts]
        raw_failures = tuple(row.reason_code or row.status for row in raw
                             if row.status in _FAILED | _NOT_EXECUTED
                             and row.reason_code != "awaiting_user_input")
        execution_status = _aggregate_status([row.status for row in raw], raw_failures, (), "")
        if not valid_review:
            status = "partial"
            acceptance = TaskAcceptance("partial", "completion_proof_invalid")
        elif outcome["status"] == "partial":
            status = "partial"
            acceptance = TaskAcceptance("partial", "agent_reported_incomplete")
        elif not failures and not missing and status == "completed":
            acceptance = TaskAcceptance("accepted", "reviewed_completion")
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
        execution_status=execution_status,
        acceptance_status=acceptance.status,
        acceptance_reason=acceptance.reason_code,
        artifact_validations=tuple(validations),
    )

"""One terminal decision for a turn, from structured facts only.

Three reducers used to answer "did this turn finish?" independently: the loop's
stop reason, ``build_task_result`` over tool records, and ``derive_turn_status``
over answer prose. They disagreed in exactly the cases users noticed — a figure
request that produced no figure closed as completed because a script had
returned ``complete``, and a provider timeout closed as completed because the
answer text happened to look whole.

This module makes that decision once. It consumes the task contract
(:mod:`src.graph.task_contract`), the coverage account (:mod:`src.graph.coverage`)
and the export-contract result that the loop already computed. Its status is fed
back as ``requested_status`` to the existing reducers, so there is still one
terminal state, not a second one.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _FinalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CompletionGoal(_FinalModel):
    goal: str = Field(min_length=1)
    kind: Literal["answer", "deliverable"]
    evidence_call_ids: list[str]
    artifact_links: list[str]


class IncidentalClaim(_FinalModel):
    tool_call_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    replacement_call_ids: list[str] = Field(min_length=1)


class ProposalRepair(_FinalModel):
    rejected_call_id: str = Field(min_length=1)
    corrected_call_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class FinalReview(_FinalModel):
    facts: str = Field(min_length=1)
    sources: str = Field(min_length=1)
    numbers: str = Field(min_length=1)
    conditions: str = Field(min_length=1)
    delivery: str = Field(min_length=1)


class CompletionEnvelope(_FinalModel):
    """The main agent's claims, never host identity, permissions or proof."""
    schema_version: Literal[1]
    answer: str = Field(min_length=1)
    status: Literal["completed", "partial"]
    goals: list[CompletionGoal]
    remaining: list[str]
    incidental_partials: list[IncidentalClaim]
    proposal_repairs: list[ProposalRepair] = Field(default_factory=list)
    review: FinalReview


def completion_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def parse_completion(text: str) -> CompletionEnvelope:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_completion_key")
            result[key] = value
        return result
    return CompletionEnvelope.model_validate(json.loads(text, object_pairs_hook=unique))


def repairable_proposal_reason(reason: str) -> bool:
    """Admission failures only; the host must separately prove zero dispatch.

    A load failure may be substituted only for a read according to code-owned
    capability metadata. This helper never grants that exemption by itself.
    """
    return reason in {"invalid_tool_arguments", "required_skill_not_loaded",
                      "preparation_required", "script_execution_rejected",
                      "workspace_version_conflict"} or reason.startswith("load_failed:")


def completion_from_answer(answer: str, records) -> CompletionEnvelope:
    """Bind a normal answer to execution receipts without another model call.

    The legacy envelope remains an internal persistence format. These fields
    describe host checks, and never claim a separate scientific review occurred.
    """
    from src.core.task_result import effective_tool_records
    refs = [row["tool_call_id"] for row in effective_tool_records(records)
            if row.get("tool_call_id") and row.get("status") == "complete"
            and (row.get("name") or row.get("tool_name")) not in
                {"load_skill", "read_skill", "update_task_plan"}]
    return CompletionEnvelope(schema_version=1, answer=answer, status="completed",
        goals=[CompletionGoal(goal="当前用户请求", kind="answer",
            evidence_call_ids=refs, artifact_links=[])], remaining=[],
        incidental_partials=[], proposal_repairs=[],
        review=FinalReview(**{key: "宿主仅校验实际执行和交付回执；未执行额外模型科学复核。"
            for key in ("facts", "sources", "numbers", "conditions", "delivery")}))


def completion_errors(statement: CompletionEnvelope, records, missing=(), *, verified_repairs=(), superseded_proposals=()) -> tuple[str, ...]:
    """Check references and hard facts; this does not certify scientific truth."""
    from src.core.task_result import effective_tool_records, incidental_partial_candidate
    rows = effective_tool_records(records)
    superseded = set(superseded_proposals)
    # Only identity-bound host proof may supply these IDs. Stale proposals
    # rejected before dispatch after a user update are not new obligations.
    if any(not any(row.get("tool_call_id") == cid and row.get("status") == "not_run"
                       and (row.get("reason") or row.get("reason_code")) == "preparation_required"
                       for row in records) for cid in superseded):
        return ("unproven_superseded_proposal",)
    rows = [row for row in rows if row.get("tool_call_id") not in superseded]
    by_id = {row.get("tool_call_id"): row for row in rows if row.get("tool_call_id")}
    errors = []
    if len(by_id) != sum(bool(row.get("tool_call_id")) for row in rows):
        errors.append("ambiguous_evidence_identity")
    # Method loading is not evidence that the user's substantive task was done.
    evidence = {cid for cid, row in by_id.items() if row.get("status") == "complete"
                and (row.get("name") or row.get("tool_name")) not in {"load_skill", "read_skill", "update_task_plan"}}
    referenced = set()
    for goal in statement.goals:
        refs = goal.evidence_call_ids
        if len(refs) != len(set(refs)) or any(cid not in evidence for cid in refs):
            errors.append("invalid_goal_evidence:" + goal.goal)
        if rows and not refs:
            errors.append("missing_goal_evidence:" + goal.goal)
        referenced.update(refs)
        if goal.kind == "deliverable" and not goal.artifact_links:
            errors.append("required_deliverable_missing:" + goal.goal)
        verified_links = {v.get("link") for cid in refs for v in
                          by_id.get(cid, {}).get("artifact_validations", []) if v.get("valid")}
        if any(link not in verified_links or link not in statement.answer for link in goal.artifact_links):
            errors.append("unverified_delivery:" + goal.goal)
    # Failed/unknown executions are never proposal repairs. A code-owned pure
    # admission rejection may be superseded only after the host proves zero
    # dispatch and the agent names a later successful correction explicitly.
    repaired = set()
    for claim in statement.proposal_repairs:
        rejected = next((r for r in records if r.get("tool_call_id") == claim.rejected_call_id), {})
        corrected = by_id.get(claim.corrected_call_id, {})
        if (claim.rejected_call_id in repaired or claim.rejected_call_id not in verified_repairs
                or rejected.get("status") != "not_run"
                or not repairable_proposal_reason(str(rejected.get("reason") or rejected.get("reason_code") or ""))
                or corrected.get("status") != "complete"
                or claim.corrected_call_id not in referenced):
            errors.append("unproven_proposal_repair:" + claim.rejected_call_id)
        repaired.add(claim.rejected_call_id)
    ids = [claim.tool_call_id for claim in statement.incidental_partials]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_incidental_claim")
    for claim in statement.incidental_partials:
        if not incidental_partial_candidate(by_id.get(claim.tool_call_id, {})):
            errors.append("ineligible_incidental_partial:" + claim.tool_call_id)
        if (len(claim.replacement_call_ids) != len(set(claim.replacement_call_ids))
                or any(cid not in evidence or cid not in referenced
                       for cid in claim.replacement_call_ids)):
            errors.append("invalid_replacement_evidence:" + claim.tool_call_id)
    if statement.status == "completed":
        if not statement.goals or statement.remaining or missing:
            errors.append("required_work_unmet")
        for row in rows:
            status = row.get("status")
            if status not in {"complete", "no_results"} and row.get("tool_call_id") not in repaired and not (
                    row.get("tool_call_id") in ids and incidental_partial_candidate(row)):
                errors.append("execution_blocker:" + str(row.get("tool_call_id") or row.get("name")))
            if any(not v.get("valid") for v in row.get("artifact_validations", [])):
                errors.append("artifact_validation_failed")
    # A partial declaration may honestly report unmet deliveries, but cannot
    # invent references or ask to exempt an ineligible operation.
    if statement.status == "partial":
        errors = [e for e in errors if not e.startswith("required_deliverable_missing:")]
    return tuple(dict.fromkeys(errors))


def reviewed_completion_valid(outcome, records, *, answer=None) -> bool:
    """Revalidate a host-generated decision against the unchanged attempt journal.

    Only trusted host/storage callers supply outcomes; hashes bind facts, they
    are not an authentication mechanism for arbitrary client-supplied objects.
    """
    data = outcome.as_dict() if isinstance(outcome, TurnOutcome) else outcome
    if not isinstance(data, dict) or data.get("reason_code") not in {
            "reviewed_completion", "reviewed_incomplete"}:
        return False
    proof = data.get("completion")
    if not isinstance(proof, dict) or proof.get("schema_version") != 1:
        return False
    if proof.get("records_hash") != completion_digest(list(records)):
        return False
    if answer is not None and proof.get("answer_hash") != completion_digest(answer):
        return False
    try:
        if proof.get("superseded_proposal_ids") and (
                type(proof.get("request_version")) is not int or proof["request_version"] <= 0):
            return False
        statement = CompletionEnvelope.model_validate(proof["statement"])
        if (statement.status != data.get("status") or completion_errors(statement, records,
                verified_repairs=proof.get("verified_proposal_repairs", ()),
                superseded_proposals=proof.get("superseded_proposal_ids", ()))
                or proof.get("answer_hash") != completion_digest(statement.answer)):
            return False
        expected = tuple(claim.tool_call_id for claim in statement.incidental_partials)
        return tuple(data.get("incidental_partial_call_ids") or ()) == expected
    except (ValueError, KeyError, TypeError):
        return False


def reviewed_turn_outcome(statement, records, *, identity, request_version, request_hash, verified_repairs=(), superseded_proposals=()):
    """Called by the host only, after schema/reference/link and request checks."""
    complete = statement.status == "completed"
    return TurnOutcome(statement.status,
        "reviewed_completion" if complete else "reviewed_incomplete", "answered",
        gaps=tuple({"reason_code": "agent_reported_unmet", "detail": text}
                   for text in statement.remaining),
        incidental_partial_call_ids=tuple(c.tool_call_id for c in statement.incidental_partials),
        operation_notices=tuple(f"辅助操作 {c.tool_call_id} 未完整完成：{c.reason}" for c in statement.incidental_partials),
        completion={"schema_version": 1, "identity": dict(identity),
            "request_version": request_version, "request_hash": request_hash,
            "records_hash": completion_digest(list(records)),
            "verified_proposal_repairs": list(verified_repairs),
            "superseded_proposal_ids": list(superseded_proposals),
            "answer_hash": completion_digest(statement.answer), "statement": statement.model_dump()})

VALID_STATUSES = frozenset({"completed", "partial", "failed", "cancelled", "awaiting_input"})

# Stop reasons that mean "the work was cut short", never "the work is done".
# ``finalization_budget`` and ``no_new_evidence`` were missing here, which is how
# a pre-emptive budget cut could present a completed turn.
DEGRADED_STOP_REASONS = frozenset({
    "request_deadline", "tool_turn_limit", "model_timeout", "invalid_tool_protocol", "output_truncated",
    "finalization_budget", "no_new_evidence", "tool_protocol_rejected", "model_budget_exhausted", "context_budget_exhausted",
})


@dataclass(frozen=True)
class TurnOutcome:
    status: str = "failed"
    reason_code: str = ""
    stop_reason: str = ""
    gaps: tuple[dict[str, Any], ...] = ()
    notices: tuple[str, ...] = ()
    incidental_partial_call_ids: tuple[str, ...] = ()
    operation_notices: tuple[str, ...] = ()
    completion: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _evidence_exists(tool_records: Any, answer: str) -> bool:
    if any(isinstance(row, dict) and str(row.get("status") or "") in {
            "complete", "partial", "no_results"} for row in (tool_records or ())):
        return True
    return False


def _notice(gap: dict[str, Any]) -> str:
    reason = str(gap.get("reason_code") or "")
    subject = gap.get("subject") if isinstance(gap.get("subject"), dict) else {}
    if reason == "figure_not_produced":
        label = str(subject.get("label") or "").strip()
        return ("本轮没有生成用户要求的图件"
                + (f"「{label}」" if label else "") + "。")
    if reason == "figure_not_openable":
        return "图件已记录，但没有可打开或已登记的导出文件。"
    if reason == "verification_not_started":
        return "本轮尚未对文献记录执行核验。"
    if reason == "verification_records_unresolved":
        return (f"文献核验仍未完成：{gap.get('unresolved', 0)} 条未解决，"
                f"已核对 {gap.get('checked', 0)} 条。")
    if reason == "audit_not_executed":
        return "本轮尚未完成任何文件的数据审查。"
    if reason == "audit_files_uncovered":
        return (f"用户上传了 {gap.get('requested', 0)} 个表格，"
                f"本轮实际完成 {gap.get('covered', 0)} 个；其余文件未覆盖。")
    return str(gap.get("detail") or "") or reason


def format_gaps(gaps: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[str]:
    """User-readable statements for unmet obligations, one per object class."""
    notices: list[str] = []
    for gap in gaps or ():
        text = _notice(gap if isinstance(gap, dict) else {})
        if text and text not in notices:
            notices.append(text)
    return notices


def decide_turn_outcome(
    *,
    stop_reason: str = "",
    contract: Any = None,
    coverage: Any = None,
    deliverable_needed: bool = False,
    deliverables_satisfied: bool = False,
    tool_records: Any = (),
    missing_requirements: Any = (),
    answer: str = "",
    cancelled: bool = False,
) -> TurnOutcome:
    """Resolve the single terminal state for this turn."""
    stop = str(stop_reason or "")
    # Evidence inspection and terminal aggregation both consume these records.
    # Materialize once so a generator cannot lose its receipts between reducers.
    tool_records = [row for row in (tool_records or ()) if isinstance(row, dict)]
    gaps: list[dict[str, Any]] = []
    if coverage is not None and contract is not None:
        gaps = list(coverage.gaps(contract))
    notices = tuple(format_gaps(gaps))
    evidence = _evidence_exists(tool_records, answer)

    if cancelled or stop == "cancelled":
        return TurnOutcome("cancelled", "user_stop", stop, tuple(gaps), ())
    if stop == "awaiting_input":
        return TurnOutcome("awaiting_input", "awaiting_user_input", stop, (), ())

    degraded = stop in DEGRADED_STOP_REASONS or stop.startswith("model_error:")
    if gaps:
        reason = str(gaps[0].get("reason_code") or "obligation_unmet")
        return TurnOutcome("partial" if evidence else "failed", reason, stop,
                           tuple(gaps), notices)
    required_exports = bool(contract is not None and any(
        o.required and o.kind in {"figure", "deliverable"} for o in contract.obligations))
    if (deliverable_needed or required_exports) and not deliverables_satisfied:
        return TurnOutcome("partial" if evidence else "failed",
                           "deliverable_unmet", stop, (), ())
    if degraded:
        return TurnOutcome("partial" if evidence else "failed",
                           stop or "stopped_early", stop, (), ())
    from src.core.task_result import (
        build_task_result, effective_tool_records, incidental_partial_candidate,
    )
    from src.graph.coverage import CoverageLedger
    incidental = []
    if (isinstance(coverage, CoverageLedger) and contract is not None
            and coverage.required_obligations_satisfied(
                contract, deliverables_satisfied=deliverables_satisfied)):
        effective = effective_tool_records(tool_records)
        names = {str(row.get("name") or row.get("tool_name") or "") for row in effective}
        required_tools = {name for obligation in contract.obligations if obligation.required
                          for name in obligation.repair_tools(names)}
        incidental = [row for row in effective
                      if (row.get("name") or row.get("tool_name")) not in required_tools
                      and incidental_partial_candidate(row)]
    incidental_ids = tuple(dict.fromkeys(row["tool_call_id"] for row in incidental))
    incidental_notices = tuple(
        f"辅助操作 {row.get('name') or row.get('tool_name')} 仅部分完成："
        f"{row.get('summary') or row.get('reason') or row.get('reason_code') or 'partial'}。"
        for row in incidental)
    # This provisional proof can exempt only the identified partial receipts.
    # The reducer still checks failures, required steps and all other operations.
    proof = TurnOutcome("completed", "obligations_satisfied", stop,
                        incidental_partial_call_ids=incidental_ids)
    task = build_task_result(tool_records, missing_requirements, turn_outcome=proof)
    status = task.status if str(answer or "").strip() else "failed"
    # Runtime renders obligation notices with an incomplete-task prefix.
    # Operation notices are separate so a fulfilled task never gets that label.
    return TurnOutcome(status, "obligations_satisfied" if status == "completed"
                       else "execution_incomplete", stop,
                       incidental_partial_call_ids=incidental_ids if status == "completed" else (),
                       operation_notices=incidental_notices)


__all__ = [
    "DEGRADED_STOP_REASONS", "TurnOutcome", "VALID_STATUSES",
    "decide_turn_outcome", "format_gaps",
]

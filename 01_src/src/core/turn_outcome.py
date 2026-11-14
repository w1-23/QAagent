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
from typing import Any

VALID_STATUSES = frozenset({"completed", "partial", "failed", "cancelled", "awaiting_input"})

# Stop reasons that mean "the work was cut short", never "the work is done".
# ``finalization_budget`` and ``no_new_evidence`` were missing here, which is how
# a pre-emptive budget cut could present a completed turn.
DEGRADED_STOP_REASONS = frozenset({
    "request_deadline", "tool_turn_limit", "model_timeout", "invalid_tool_protocol",
    "finalization_budget", "no_new_evidence", "tool_protocol_rejected", "model_budget_exhausted", "context_budget_exhausted",
})


@dataclass(frozen=True)
class TurnOutcome:
    status: str = "failed"
    reason_code: str = ""
    stop_reason: str = ""
    gaps: tuple[dict[str, Any], ...] = ()
    notices: tuple[str, ...] = ()

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
    if deliverable_needed and not deliverables_satisfied:
        return TurnOutcome("partial" if evidence else "failed",
                           "deliverable_unmet", stop, (), ())
    if degraded:
        return TurnOutcome("partial" if evidence else "failed",
                           stop or "stopped_early", stop, (), ())
    from src.core.task_result import build_task_result
    task = build_task_result(tool_records, missing_requirements)
    status = task.status if str(answer or "").strip() else "failed"
    return TurnOutcome(status, "obligations_satisfied" if status == "completed"
                       else "execution_incomplete", stop, (), ())


__all__ = [
    "DEGRADED_STOP_REASONS", "TurnOutcome", "VALID_STATUSES",
    "decide_turn_outcome", "format_gaps",
]

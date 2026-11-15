"""Completion follows host obligations without erasing exploratory failures."""
from copy import deepcopy
from dataclasses import replace

import pytest

from src.core.task_result import build_task_result
from src.core.turn_envelope import build_turn_envelope
from src.core.turn_outcome import DEGRADED_STOP_REASONS, decide_turn_outcome
from src.graph.coverage import CoverageLedger
from src.graph.task_contract import Obligation, TaskContract


def exploratory_partial(name="discover_candidates", reason=""):
    outcome = {"status": "partial", "reason_code": reason, "artifacts": []}
    return {"name": name, "status": "partial", "reason": reason,
            "summary": "one provider returned candidates; another was unavailable",
            "tool_call_id": "discovery-1", "op_receipt": {
                "schema_version": 1, "tool_name": name, "tool_call_id": "discovery-1",
                **outcome, "coverage": {"_tool_outcome": outcome}}}


def fulfilled(kind="verification"):
    ledger = CoverageLedger()
    if kind == "verification":
        subject = {"file_indexes": [], "record_count": 1}
        name = "verify_uploaded_references"
        ledger.observe(name, artifact={"coverage": {
            "source_kind": "supplied_records", "source_sha256": "source-a", "submitted": 1},
            "rows": [{"record_id": "record-a", "record_index": 0,
                      "source_sha256": "source-a", "verification_rule": "identity-v1",
                      "status": "metadata_matched"}]})
    else:
        subject = {"file_indexes": [0]}
        name = "audit_uploaded_data"
        ledger.observe(name, artifact={"results": [{
            "file_index": 0, "source_sha256": "source-a", "checks_sha256": "checks-a",
            "audit_mode": "table", "sheets": ["Sheet1"], "requested_sheet": "", "ok": True,
            "coverage": {"input_rows": 3, "processed_rows": 3,
                         "input_columns": 2, "processed_columns": 2}}]})
    contract = TaskContract(obligations=(Obligation("required-1", kind, subject),))
    return contract, ledger, [{"name": name, "status": "complete", "tool_call_id": "required-1"}]


def decide(contract, ledger, records, **kwargs):
    options = dict(stop_reason="model_finished", answer="Evidence and limitations.")
    options.update(kwargs)
    return decide_turn_outcome(contract=contract, coverage=ledger, tool_records=records, **options)


@pytest.mark.parametrize("kind", ["verification", "audit"])
@pytest.mark.parametrize("reason", ["", "source_unavailable", "timeout"])
def test_host_proven_obligations_complete_despite_incidental_partial(kind, reason):
    contract, ledger, records = fulfilled(kind)
    records.insert(0, exploratory_partial(reason=reason))
    journal = deepcopy(records)
    outcome = decide(contract, ledger, iter(records))
    assert outcome.status == "completed"
    assert outcome.reason_code == "obligations_satisfied"
    assert outcome.incidental_partial_call_ids == ("discovery-1",)
    assert not outcome.notices  # no unmet-task banner for an incidental limitation
    assert outcome.operation_notices and "discover_candidates" in outcome.operation_notices[0]
    result = build_task_result(iter(records), requested_status=outcome.status, turn_outcome=outcome)
    assert result.status == result.execution_status == "completed"
    assert result.acceptance_status == "accepted"
    assert result.executed_steps == 2
    envelope = build_turn_envelope(answer="Evidence and limitations.", tool_status=records,
        task_snapshot={"task_contract": contract.as_dict(), "coverage": ledger.as_dict(),
                       "turn_outcome": outcome.as_dict()})
    assert envelope.display_status == envelope.execution_status == envelope.job_status == "completed"
    assert envelope.acceptance_status == "accepted"
    assert envelope.notices == outcome.operation_notices
    assert records == journal  # operation statuses/receipts are never rewritten


def test_requested_completed_alone_cannot_erase_partial():
    records = [exploratory_partial()]
    assert build_task_result(records, requested_status="completed").status == "partial"
    assert build_turn_envelope(answer="Done", tool_status=records,
                              requested_status="completed").display_status == "partial"


@pytest.mark.parametrize("blocker", [
    {"name": "required_source", "status": "failed", "reason": "source_scope_denied"},
    {"name": "required_source", "status": "skipped", "reason": "source_scope_denied"},
    {"name": "required_source", "status": "not_run", "reason": "not_authorized"},
    {"name": "required_source", "status": "unknown"},
    {"name": "required_source", "status": "cancelled"},
    {"name": "required_source", "status": "partial"},
])
def test_actual_failures_unknowns_and_unreceipted_partials_remain_blockers(blocker):
    contract, ledger, records = fulfilled()
    records.extend([exploratory_partial(), blocker])
    outcome = decide(contract, ledger, records)
    assert outcome.status != "completed"
    assert not outcome.incidental_partial_call_ids
    assert build_task_result(records, turn_outcome=outcome).status != "completed"


@pytest.mark.parametrize("reason", ["auth", "blocked_destination", "coverage_incomplete",
                                    "tool_error", "deliverable_rejected", "new_unknown_reason"])
def test_non_exploratory_reasons_cannot_be_exempted(reason):
    contract, ledger, records = fulfilled()
    records.append(exploratory_partial(reason=reason))
    assert decide(contract, ledger, records).status == "partial"


@pytest.mark.parametrize("mutation", [
    lambda row: row.pop("op_receipt"),
    lambda row: row["op_receipt"].update(tool_call_id="different-call"),
    lambda row: row["op_receipt"].update(status="complete"),
    lambda row: row["op_receipt"]["coverage"].update(rows=[]),
    lambda row: row.update(artifact_count=1),
    lambda row: row.update(artifact_validations=[{"artifact_id": "bad", "valid": False}]),
    lambda row: row["op_receipt"]["coverage"]["_tool_outcome"].update(artifacts=["file"]),
    lambda row: row.update(reason_class="internal_error"),
])
def test_output_or_ambiguous_receipts_are_never_exploratory(mutation):
    contract, ledger, records = fulfilled()
    partial = exploratory_partial()
    mutation(partial)
    records.append(partial)
    assert decide(contract, ledger, records).status == "partial"


def test_required_operation_partial_is_not_hidden_by_coverage():
    contract, ledger, records = fulfilled()
    records.append(exploratory_partial(name="verify_uploaded_references"))
    assert ledger.gaps(contract) == []
    assert decide(contract, ledger, records).status == "partial"


@pytest.mark.parametrize("missing", ["unmet:required_operation", "unavailable:authorized_source",
                                     "unattempted:required_source", "deliverable_unmet:report"])
def test_required_steps_remain_authoritative(missing):
    contract, ledger, records = fulfilled()
    records.append(exploratory_partial())
    outcome = decide(contract, ledger, records, missing_requirements=iter([missing]))
    assert outcome.status == "partial"
    assert not outcome.incidental_partial_call_ids


def test_unmet_coverage_and_unknown_contracts_cannot_prove_completion():
    contract, ledger, records = fulfilled()
    records.append(exploratory_partial())
    for unproven, coverage in [
        (contract, CoverageLedger()),
        (replace(contract, understanding_status="unavailable"), ledger),
        (TaskContract(), ledger),
        (None, ledger),
        (contract, None),
        (TaskContract(obligations=(Obligation("new", "unrecognized", {}),)), ledger),
    ]:
        outcome = decide(unproven, coverage, records)
        assert outcome.status != "completed"
        assert not outcome.incidental_partial_call_ids
    assert ledger.gaps(TaskContract(obligations=(Obligation("new", "unrecognized", {}),)))[0][
        "reason_code"] == "unsupported_obligation"


def test_only_required_obligations_determine_coverage():
    contract, ledger, records = fulfilled()
    contract = replace(contract, obligations=contract.obligations + (
        Obligation("optional-audit", "audit", {"file_indexes": [4]}, required=False),))
    records.append(exploratory_partial())
    assert not ledger.gaps(contract)
    assert decide(contract, ledger, records).status == "completed"


@pytest.mark.parametrize("stop", sorted(DEGRADED_STOP_REASONS) + ["model_error:provider"])
def test_degraded_stops_remain_incomplete(stop):
    contract, ledger, records = fulfilled()
    records.append(exploratory_partial())
    outcome = decide(contract, ledger, records, stop_reason=stop)
    assert outcome.status == "partial"
    assert outcome.reason_code == stop
    assert not outcome.incidental_partial_call_ids


@pytest.mark.parametrize("stop, expected", [("cancelled", "cancelled"), ("awaiting_input", "awaiting_input")])
def test_control_stops_are_preserved(stop, expected):
    contract, ledger, records = fulfilled()
    records.append(exploratory_partial())
    assert decide(contract, ledger, records, stop_reason=stop).status == expected


def test_export_gate_remains_independent_even_when_required_flag_is_missing():
    contract, ledger, records = fulfilled()
    contract = replace(contract, obligations=contract.obligations + (
        Obligation("report", "deliverable", {"requirement": {"formats": ["html"]}}),))
    records.append(exploratory_partial())
    outcome = decide(contract, ledger, records)
    assert outcome.status == "partial"
    assert outcome.reason_code == "deliverable_unmet"
    assert decide(contract, ledger, records, deliverables_satisfied=True).status == "completed"


def test_empty_answer_cannot_complete_with_proof():
    contract, ledger, records = fulfilled()
    records.append(exploratory_partial())
    assert decide(contract, ledger, records, answer="").status == "failed"
    outcome = decide(contract, ledger, records)
    envelope = build_turn_envelope(answer="", tool_status=records,
                                  task_snapshot={"turn_outcome": outcome.as_dict()})
    assert envelope.display_status == envelope.execution_status == envelope.job_status == "failed"
    assert envelope.acceptance_status == "rejected"
    assert envelope.acceptance_reason == "no_answer"


def test_completion_proof_cannot_exempt_a_changed_or_new_operation():
    contract, ledger, records = fulfilled()
    records.append(exploratory_partial())
    outcome = decide(contract, ledger, records)
    records[-1]["status"] = "failed"
    assert build_task_result(records, turn_outcome=outcome).status == "partial"
    records[-1] = exploratory_partial()
    records[-1]["tool_call_id"] = "new-call"
    records[-1]["op_receipt"]["tool_call_id"] = "new-call"
    assert build_task_result(records, turn_outcome=outcome).status == "partial"

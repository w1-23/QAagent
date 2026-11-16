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


def reviewed_fixture():
    from src.core.turn_outcome import CompletionEnvelope
    records = [exploratory_partial(reason="source_unavailable"),
               {"name": "fetch_source", "status": "complete", "tool_call_id": "replacement"}]
    statement = CompletionEnvelope.model_validate({
        "schema_version": 1, "answer": "Supported answer with limitations.", "status": "completed",
        "goals": [{"goal": "Explain with verifiable sources", "kind": "answer",
                   "evidence_call_ids": ["replacement"], "artifact_links": []}],
        "remaining": [], "incidental_partials": [{"tool_call_id": "discovery-1",
            "reason": "Optional source unavailable; the fetched replacement supports the goal.",
            "replacement_call_ids": ["replacement"]}],
        "review": {key: "Checked; self-review does not certify correctness."
                   for key in ("facts", "sources", "numbers", "conditions", "delivery")}})
    return statement, records


def reviewed_proof(statement, records):
    from src.core.turn_outcome import reviewed_turn_outcome
    return reviewed_turn_outcome(statement, records,
        identity={"owner_user_id": 1, "session_id": "s", "user_turn_id": "t", "run_id": "r"},
        request_version=0, request_hash="host-only-request-hash")


def test_reviewed_goal_acceptance_keeps_raw_partial_execution_and_notices():
    from src.core.turn_outcome import completion_errors
    statement, records = reviewed_fixture()
    assert not completion_errors(statement, records)
    proof = reviewed_proof(statement, records)
    journal = deepcopy(records)
    result = build_task_result(records, turn_outcome=proof)
    assert result.status == "completed" and result.execution_status == "partial"
    assert result.acceptance_status == "accepted" and result.executed_steps == 2
    envelope = build_turn_envelope(answer=statement.answer, tool_status=records,
        requested_status="partial", task_snapshot={"turn_outcome": proof.as_dict()})
    assert envelope.display_status == envelope.job_status == "completed"
    assert envelope.execution_status == "partial" and envelope.notices
    assert records == journal and proof.reason_code != "obligations_satisfied"
    assert "completion" not in envelope.task_snapshot["turn_outcome"]
    assert proof.completion  # Internal proof remains available to trusted consumers.


@pytest.mark.parametrize("mutation", [
    lambda s, r: s.update(remaining=["Required source still unread"]),
    lambda s, r: s.update(goals=[]),
    lambda s, r: s["goals"][0].update(evidence_call_ids=["invented"]),
    lambda s, r: s["incidental_partials"][0].update(replacement_call_ids=["invented"]),
    lambda s, r: s["goals"][0].update(kind="deliverable"),
    lambda s, r: s["goals"][0].update(kind="deliverable", artifact_links=["/fake.pdf"]),
    lambda s, r: r.append({"name": "write", "status": "unknown", "tool_call_id": "unknown"}),
    lambda s, r: r.append({"name": "required", "status": "not_run", "tool_call_id": "not-run"}),
    lambda s, r: r.append({"name": "required", "status": "failed", "tool_call_id": "failed"}),
    lambda s, r: r[0].update(reason="not_authorized"),
    lambda s, r: r[0]["op_receipt"]["coverage"].update(required_coverage={"missing": 1}),
    lambda s, r: r[0].update(artifact_validations=[{"valid": False}]),
])
def test_typed_completion_cannot_erase_hard_blockers_or_invent_evidence(mutation):
    from src.core.turn_outcome import CompletionEnvelope, completion_errors
    statement, records = reviewed_fixture()
    data = statement.model_dump()
    mutation(data, records)
    statement = CompletionEnvelope.model_validate(data)
    assert completion_errors(statement, records)
    assert build_task_result(records, turn_outcome=reviewed_proof(statement, records)).status != "completed"


def test_reviewed_proof_rejects_changed_journal_and_changed_public_answer():
    statement, records = reviewed_fixture()
    proof = reviewed_proof(statement, records)
    altered = build_turn_envelope(answer="different answer", tool_status=records,
        task_snapshot={"turn_outcome": proof.as_dict()})
    assert altered.display_status == altered.task_snapshot["turn_outcome"]["status"] == "partial"
    assert altered.acceptance_reason == "completion_proof_invalid"
    records.append({"name": "later_source", "tool_call_id": "new", "status": "complete"})
    assert build_task_result(records, turn_outcome=proof).acceptance_reason == "completion_proof_invalid"


def test_honest_partial_does_not_change_successful_execution_into_failed_work():
    statement, records = reviewed_fixture()
    records = records[1:]
    statement = statement.model_copy(update={"status": "partial", "remaining": ["required file missing"],
                                           "incidental_partials": []})
    proof = reviewed_proof(statement, records)
    envelope = build_turn_envelope(answer=statement.answer, tool_status=records,
        task_snapshot={"turn_outcome": proof.as_dict()})
    assert envelope.display_status == "partial" and envelope.job_status == "failed"
    assert envelope.execution_status == "completed" and envelope.acceptance_status == "partial"


@pytest.mark.parametrize("status", ["not_run", "failed", "unknown"])
def test_unconfirmed_repair_claims_never_remove_execution_blockers(status):
    from src.core.turn_outcome import CompletionEnvelope, completion_errors
    statement, _ = reviewed_fixture()
    data = statement.model_dump()
    data.update(incidental_partials=[], proposal_repairs=[{
        "rejected_call_id": "rejected", "corrected_call_id": "replacement", "reason": "claim only"}])
    statement = CompletionEnvelope.model_validate(data)
    records = [{"name": "fetch_source", "tool_call_id": "rejected", "status": status,
                "reason": "script_execution_rejected"},
               {"name": "fetch_source", "tool_call_id": "replacement", "status": "complete"}]
    assert completion_errors(statement, records)
    proof = reviewed_proof(statement, records)
    assert build_task_result(records, turn_outcome=proof).status != "completed"

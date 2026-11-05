from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPTS = HERE.parents[2]
JUDGE_DIR = HERE.parents[1]
sys.path.insert(0, str(SCRIPTS))
from common import redact_error


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


aggregate = load_module("citrus_judge_aggregate", SCRIPTS / "aggregate_llm_judges.py")
adjudicate = load_module(
    "citrus_judge_adjudicate",
    JUDGE_DIR / "30_adjudicate_disagreements.py",
)
packet_builder = load_module(
    "citrus_judge_packet_builder",
    JUDGE_DIR / "10_build_blind_packets.py",
)


def valid_judgment() -> dict:
    return {
        "status": "completed",
        "answerability_correct": True,
        "answer_correct": True,
        "evidence_traceable": True,
        "fatal_error": False,
        "reason_codes_by_field": {
            "answerability_correct": [],
            "answer_correct": [],
            "evidence_traceable": [],
            "fatal_error": [],
        },
        "short_reason": "",
        "answer_span_quote": "",
        "evidence_span_locators": [],
    }


def test_missing_judge_is_not_silently_converted(tmp_path):
    result = aggregate.read_judge(tmp_path / "missing.json")
    assert result["status"] == "MISSING"
    assert "file_missing" in result["schema_errors"]


def test_model_written_decision_is_rejected(tmp_path):
    payload = valid_judgment()
    payload["decision"] = "PASS"
    path = tmp_path / "judge.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = aggregate.read_judge(path)
    assert result["status"] == "INVALID_SCHEMA"
    assert "model_written_decision_forbidden" in result["schema_errors"]


def test_field_scoped_reason_is_required_for_false_field(tmp_path):
    payload = valid_judgment()
    payload["answer_correct"] = False
    payload["answer_span_quote"] = "wrong unit"
    payload["short_reason"] = "The unit changes the reported quantity."
    path = tmp_path / "judge.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = aggregate.read_judge(path, "ANSWERABLE")
    assert result["status"] == "INVALID_SCHEMA"
    assert "answer_correct_reason_required" in result["schema_errors"]


def test_answer_span_quote_is_required_for_false_field(tmp_path):
    payload = valid_judgment()
    payload["evidence_traceable"] = False
    payload["reason_codes_by_field"]["evidence_traceable"] = ["NO_CITATION"]
    payload["short_reason"] = "No final citation is present."
    path = tmp_path / "judge.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = aggregate.read_judge(path, "ANSWERABLE")
    assert result["status"] == "INVALID_SCHEMA"
    assert "answer_span_quote_required" in result["schema_errors"]


def test_unanswerable_na_and_correct_refusal_rules():
    payload = valid_judgment()
    payload["answer_correct"] = "not_applicable"
    payload["evidence_traceable"] = "not_applicable"
    assert aggregate.validate_judgment(payload, "UNANSWERABLE") == []
    assert aggregate.deterministic_egts(payload, "UNANSWERABLE") == 1


def test_fatal_error_false_does_not_require_a_reason_or_quote():
    payload = valid_judgment()
    assert payload["fatal_error"] is False
    assert payload["reason_codes_by_field"]["fatal_error"] == []
    assert aggregate.validate_judgment(payload, "ANSWERABLE") == []


def test_answerable_rejects_na():
    payload = valid_judgment()
    payload["answer_correct"] = "not_applicable"
    errors = aggregate.validate_judgment(payload, "ANSWERABLE")
    assert "answer_correct_not_applicable_on_answerable" in errors


def test_metric_conflict_requires_adjudication_even_when_decisions_agree():
    first = valid_judgment()
    second = valid_judgment()
    second["evidence_traceable"] = False
    second["reason_codes_by_field"]["evidence_traceable"] = ["NO_CITATION"]
    second["short_reason"] = "No final citation."
    second["answer_span_quote"] = "No source."
    assert adjudicate.judgments_disagree(first, second)


def test_identical_judgments_do_not_require_adjudication():
    assert not adjudicate.judgments_disagree(
        valid_judgment(),
        valid_judgment(),
    )


def test_j3_returns_only_disputed_fields():
    payload = {
        "disputed_values": {"answer_correct": True},
        "reason_codes_by_field": {"answer_correct": []},
        "short_reason": "",
        "answer_span_quote": "",
        "evidence_span_locators": [],
    }
    assert aggregate.validate_adjudication(
        payload, {"answer_correct"}, "ANSWERABLE"
    ) == []
    payload["disputed_values"]["evidence_traceable"] = True
    errors = aggregate.validate_adjudication(
        payload, {"answer_correct"}, "ANSWERABLE"
    )
    assert "disputed_values_field_set_mismatch" in errors


def test_j3_fatal_reason_must_be_field_scoped():
    payload = {
        "disputed_values": {"fatal_error": True},
        "reason_codes_by_field": {"fatal_error": []},
        "short_reason": "A fabricated value is present.",
        "answer_span_quote": "Ct = 0.0",
        "evidence_span_locators": ["E1"],
    }
    errors = aggregate.validate_adjudication(
        payload, {"fatal_error"}, "ANSWERABLE"
    )
    assert "fatal_error_reason_required" in errors


def test_j3_false_field_requires_short_reason_and_answer_quote():
    payload = {
        "disputed_values": {"answer_correct": False},
        "reason_codes_by_field": {
            "answer_correct": ["FACTUAL_ERROR"],
        },
        "short_reason": "",
        "answer_span_quote": "",
        "evidence_span_locators": ["E1"],
    }
    errors = aggregate.validate_adjudication(
        payload, {"answer_correct"}, "ANSWERABLE"
    )
    assert "short_reason_required" in errors
    assert "answer_span_quote_required" in errors


def test_fabricated_value_and_missing_evidence_fail_closed():
    fabricated = valid_judgment()
    fabricated["answerability_correct"] = False
    fabricated["answer_correct"] = "not_applicable"
    fabricated["evidence_traceable"] = "not_applicable"
    fabricated["fatal_error"] = True
    fabricated["reason_codes_by_field"]["answerability_correct"] = [
        "UNANSWERABLE_ANSWERED"
    ]
    fabricated["reason_codes_by_field"]["fatal_error"] = [
        "FABRICATED_DATA_OR_VALUE"
    ]
    fabricated["short_reason"] = "The response invents the requested Ct value."
    fabricated["answer_span_quote"] = "Ct = 0.0"
    assert aggregate.validate_judgment(fabricated, "UNANSWERABLE") == []
    assert aggregate.deterministic_egts(fabricated, "UNANSWERABLE") == 0

    no_evidence = valid_judgment()
    no_evidence["evidence_traceable"] = False
    no_evidence["reason_codes_by_field"]["evidence_traceable"] = ["NO_CITATION"]
    no_evidence["short_reason"] = "No final citation is present."
    no_evidence["answer_span_quote"] = "No source."
    assert aggregate.validate_judgment(no_evidence, "ANSWERABLE") == []
    assert aggregate.deterministic_egts(no_evidence, "ANSWERABLE") == 0


def test_invalid_json_and_latest_attempt_are_not_silent(tmp_path):
    directory = tmp_path / "judge_1" / "BLIND_000001"
    directory.mkdir(parents=True)
    (directory / "attempt_01.json").write_text("{bad json", encoding="utf-8")
    assert aggregate.read_judge(directory / "attempt_01.json")["status"] == "UNREADABLE"
    first = directory / "attempt_01.json"
    later = directory / "attempt_03.json"
    later.write_text(json.dumps(valid_judgment()), encoding="utf-8")
    assert aggregate.latest_attempt(directory) == later
    assert first.exists()


def test_template_matches_aggregation_schema_and_run_plan():
    template = SCRIPTS.parent / "07_scoring/judge/qa_judge_TEMPLATE_180.csv"
    with template.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    assert header == aggregate.OUTPUT_FIELDS
    assert len(rows) == 180
    assert len({row["run_id"] for row in rows}) == 180
    assert {
        condition: sum(row["condition"] == condition for row in rows)
        for condition in ("B0", "B1", "B2")
    } == {"B0": 60, "B1": 60, "B2": 60}


def test_exception_redaction_removes_secret_values():
    secret = "_".join(("oc", "sk", "synthetic", "test", "value", "123456789"))
    message = f"request failed Authorization: Bearer {secret}"
    redacted = redact_error(message, (secret,))
    assert secret not in redacted
    assert "[REDACTED" in redacted


def test_blind_packet_leak_scan_is_recursive():
    assert packet_builder.first_forbidden_key(
        {"reference": [{"source_id": "S1", "condition": "B2"}]}
    ) == "condition"

from __future__ import annotations

import csv
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPTS = HERE.parents[2]
JUDGE_DIR = HERE.parents[1]
EXPERIMENT_ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
from common import redact_error


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


aggregate = load_module("citrus_judge_aggregate", SCRIPTS / "aggregate_llm_judges.py")
dispatch = load_module(
    "citrus_judge_dispatch",
    JUDGE_DIR / "20_dispatch_judge_1_and_2.py",
)
adjudicate = load_module(
    "citrus_judge_adjudicate",
    JUDGE_DIR / "30_adjudicate_disagreements.py",
)
packet_builder = load_module(
    "citrus_judge_packet_builder",
    JUDGE_DIR / "10_build_blind_packets.py",
)
authorization = load_module(
    "citrus_judge_authorization",
    SCRIPTS / "execution_authorization.py",
)
static_audit = load_module(
    "citrus_static_audit",
    SCRIPTS / "audit_static_package.py",
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


def test_j3_rejects_extra_top_level_fields_and_preserves_locator_validation():
    payload = {
        "disputed_values": {"answer_correct": False},
        "reason_codes_by_field": {"answer_correct": ["FACTUAL_ERROR"]},
        "short_reason": "The answer changes the reported unit.",
        "answer_span_quote": "2.5 mg/L",
        "evidence_span_locators": [""],
        "answer_correct": False,
    }
    errors = aggregate.validate_adjudication(
        payload,
        {"answer_correct"},
        "ANSWERABLE",
    )
    assert "adjudication_extra_fields:answer_correct" in errors
    assert "evidence_span_locators_invalid" in errors


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


def test_static_audit_credential_names_cover_env_variants():
    assert static_audit.credential_filename(".env")
    assert static_audit.credential_filename(".env.production")
    assert static_audit.credential_filename("service.env")
    assert static_audit.credential_filename("credentials.json")
    assert not static_audit.credential_filename("environment.md")


def test_retry_policy_is_content_free_only():
    assert dispatch.retryable_record({"status": "JUDGE_TRANSPORT_ERROR"})
    assert dispatch.retryable_record(
        {"status": "JUDGE_HTTP_ERROR", "http_status": 503}
    )
    assert not dispatch.retryable_record(
        {"status": "JUDGE_HTTP_ERROR", "http_status": 400}
    )
    assert not dispatch.retryable_record({"status": "JUDGE_INVALID_JSON"})
    assert not dispatch.retryable_record({"status": "INVALID_SCHEMA"})
    assert not adjudicate.retryable_record({"status": "MODEL_ID_MISMATCH"})


def test_execution_authorization_requires_all_three_signals():
    config = {
        "status": "UNCONFIGURED",
        "execution_authorized": False,
        "review_status": "AWAITING_EXTERNAL_GPT_REVIEW",
    }
    assert authorization.authorization_errors(config) == [
        "status_not_READY",
        "execution_authorized_not_true",
        "review_status_not_approved",
    ]
    config.update(
        {
            "status": "READY",
            "execution_authorized": True,
            "review_status": "APPROVED_FOR_EXECUTION",
        }
    )
    assert authorization.authorization_errors(config) == []


def test_actual_model_identity_must_match_allowlist(tmp_path):
    payload = valid_judgment()
    payload["actual_model"] = "different-family-model"
    path = tmp_path / "judge.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = aggregate.read_judge(
        path,
        "ANSWERABLE",
        {"glm-5.3-flash"},
    )
    assert result["status"] == "MODEL_ID_MISMATCH"
    assert result["model_identity_status"] == "MODEL_ID_MISMATCH"

    payload["actual_model"] = "glm-5.3-flash"
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = aggregate.read_judge(
        path,
        "ANSWERABLE",
        {"glm-5.3-flash"},
    )
    assert result["status"] == "completed"
    assert result["model_identity_status"] == "MATCH"


def test_dispatch_model_identity_status_distinguishes_missing_and_mismatch():
    assert dispatch.status_for_identity("MODEL_ID_MISSING", []) == "MODEL_ID_MISSING"
    assert (
        dispatch.status_for_identity("MODEL_ID_MISMATCH", [])
        == "MODEL_ID_MISMATCH"
    )
    assert dispatch.status_for_identity("MATCH", ["bad"]) == "INVALID_SCHEMA"
    assert dispatch.status_for_identity("MATCH", []) == "completed"


def test_blind_packet_leak_scan_is_recursive():
    assert packet_builder.first_forbidden_key(
        {"reference": [{"source_id": "S1", "condition": "B2"}]}
    ) == "condition"


def _write_packet_builder_fixture(
    tmp_path: Path,
    question_count: int,
) -> tuple[Path, Path, Path]:
    l1_root = tmp_path / "l1"
    raw_root = l1_root / "06_runs" / "raw"
    with (EXPERIMENT_ROOT / "03_benchmarks/qa60/qa60_CANDIDATE.csv").open(
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        questions = list(csv.DictReader(handle))[:question_count]
    plan_rows: list[dict] = []
    for question_index, question in enumerate(questions, start=1):
        reference_evidence = [
            {
                "source_id": f"SOURCE_{question_index:03d}",
                "sha256": "a" * 64,
                "locator": "p.1",
                "excerpt": f"Frozen evidence {question_index}",
            }
        ]
        for condition in ("B0", "B1", "B2"):
            run_id = f"TEST_{question_index:03d}_{condition}"
            raw_path = raw_root / condition / f"{run_id}.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(
                json.dumps(
                    {
                        "actual_response": {
                            "status": "completed",
                            "final_answer": f"Synthetic answer for {question['题号']}",
                            "citations": [],
                            "evidence_excerpts": [],
                        },
                        "reference_evidence": reference_evidence,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            plan_rows.append(
                {
                    "run_id": run_id,
                    "kind": "qa",
                    "case_id": question["题号"],
                    "condition": condition,
                    "trial": "1",
                    "prompt": question["完整题目"],
                    "source_scope": question["来源约束"],
                    "attachment": "",
                    "required_groups": "",
                    "forbidden_groups": "",
                    "source_ids": question["题号"],
                    "stratum": question["组别"],
                    "run_order": str(len(plan_rows) + 1),
                }
            )
    plan_path = tmp_path / "run_plan.csv"
    with plan_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(plan_rows[0]))
        writer.writeheader()
        writer.writerows(plan_rows)
    return l1_root, raw_root, plan_path


def test_blind_builder_validates_180_and_pilot_36_batches(tmp_path):
    for question_count, expected_runs in ((60, 180), (12, 36)):
        case_root = tmp_path / f"case_{expected_runs}"
        l1_root, raw_root, plan_path = _write_packet_builder_fixture(
            case_root,
            question_count,
        )
        env = os.environ.copy()
        env["CITRUS_EXPERIMENT_L1_ROOT"] = str(l1_root)
        result = subprocess.run(
            [
                sys.executable,
                str(JUDGE_DIR / "10_build_blind_packets.py"),
                "--run-plan",
                str(plan_path),
                "--raw-root",
                str(raw_root),
                "--packet-root",
                "judge_packets",
                "--mapping-path",
                "judge_blinding/BLIND_ID_MAP.csv",
                "--order-root",
                "judge_blinding/reviewer_orders",
                "--expected-runs",
                str(expected_runs),
            ],
            cwd=EXPERIMENT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        manifest = json.loads(
            (l1_root / "judge_packets/manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["status"] == "READY"
        assert manifest["expected_runs"] == expected_runs
        assert manifest["packet_count"] == expected_runs
        assert manifest["planned_runs_exactly_three_conditions_per_question"] is True
        assert manifest["same_question_prompt_identical"] is True
        assert (
            manifest["same_question_reference_evidence_sha256_identical"] is True
        )
        assert manifest["raw_run_sha256_packet_excluded"] is True
        with (l1_root / "judge_blinding/BLIND_ID_MAP.csv").open(
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            mapping = list(csv.DictReader(handle))
        assert len(mapping) == expected_runs
        for row in mapping:
            packet_text = (
                l1_root / "judge_packets" / f"{row['blind_id']}.json"
            ).read_text(encoding="utf-8")
            assert row["raw_run_sha256"] not in packet_text


def test_blind_builder_rejects_condition_specific_reference_evidence(tmp_path):
    l1_root, raw_root, plan_path = _write_packet_builder_fixture(tmp_path, 1)
    raw_path = raw_root / "B2" / "TEST_001_B2.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["reference_evidence"][0]["excerpt"] = "A different B2-only reference"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    env = os.environ.copy()
    env["CITRUS_EXPERIMENT_L1_ROOT"] = str(l1_root)
    result = subprocess.run(
        [
            sys.executable,
            str(JUDGE_DIR / "10_build_blind_packets.py"),
            "--run-plan",
            str(plan_path),
            "--raw-root",
            str(raw_root),
            "--expected-runs",
            "3",
        ],
        cwd=EXPERIMENT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "different frozen reference evidence" in result.stderr

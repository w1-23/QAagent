#!/usr/bin/env python3
"""Aggregate two frozen LLM judges and a field-level adjudicator. Fail closed."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from common import ROOT, csvrows, sha256_file, utc

JUDGMENT_FIELDS = (
    "answerability_correct",
    "answer_correct",
    "evidence_traceable",
    "fatal_error",
)
NA_FIELDS = {"answer_correct", "evidence_traceable"}
FAILURE_FIELDS = ("answerability_correct", "answer_correct", "evidence_traceable")
TERMINAL_RUN_STATUSES = {
    "completed",
    "partial",
    "no_results",
    "failed",
    "cancelled",
    "skipped",
}
SEMANTIC_JUDGE_STATUSES = {"completed", "partial", "no_results"}
INFRASTRUCTURE_STATUSES = {"failed", "cancelled", "skipped"}
ADJUDICATION_FIELDS = {
    "disputed_values",
    "reason_codes_by_field",
    "short_reason",
    "answer_span_quote",
    "evidence_span_locators",
}
REASON_CODES = {
    "NO_RESPONSE",
    "ANSWERABLE_REFUSED",
    "UNANSWERABLE_ANSWERED",
    "MISSING_REQUIRED_CONTENT",
    "FACTUAL_ERROR",
    "SOURCE_CONTRADICTION",
    "UNSUPPORTED_MATERIAL_CLAIM",
    "NO_CITATION",
    "INVALID_OR_UNRESOLVABLE_LOCATOR",
    "SOURCE_SCOPE_VIOLATION",
    "FABRICATED_SOURCE_OR_DOI",
    "FABRICATED_DATA_OR_VALUE",
    "SIMULATION_PRESENTED_AS_REAL",
    "SEVERE_OVERGENERALIZATION",
    "PRIVACY_OR_SECRET_DISCLOSURE",
}
OUTPUT_FIELDS = [
    "run_id",
    "question_id",
    "condition",
    "source_cluster",
    "expected_answerability",
    "run_status",
    "failure_class",
    "raw_run_sha256",
    "judge_1_status",
    "judge_1_answerability_correct",
    "judge_1_answer_correct",
    "judge_1_evidence_traceable",
    "judge_1_fatal_error",
    "judge_1_reason_codes_by_field_json",
    "judge_1_short_reason",
    "judge_1_answer_span_quote",
    "judge_1_evidence_span_locators_json",
    "judge_1_request_sha256",
    "judge_1_response_sha256",
    "judge_1_actual_model",
    "judge_1_model_identity_status",
    "judge_1_model_version_or_fingerprint",
    "judge_1_endpoint_host",
    "judge_1_response_id",
    "judge_1_http_status",
    "judge_1_attempt_count",
    "judge_1_tokens_in",
    "judge_1_tokens_out",
    "judge_1_cost",
    "judge_1_list_cost",
    "judge_1_allowance",
    "judge_1_elapsed_ms",
    "judge_2_status",
    "judge_2_answerability_correct",
    "judge_2_answer_correct",
    "judge_2_evidence_traceable",
    "judge_2_fatal_error",
    "judge_2_reason_codes_by_field_json",
    "judge_2_short_reason",
    "judge_2_answer_span_quote",
    "judge_2_evidence_span_locators_json",
    "judge_2_request_sha256",
    "judge_2_response_sha256",
    "judge_2_actual_model",
    "judge_2_model_identity_status",
    "judge_2_model_version_or_fingerprint",
    "judge_2_endpoint_host",
    "judge_2_response_id",
    "judge_2_http_status",
    "judge_2_attempt_count",
    "judge_2_tokens_in",
    "judge_2_tokens_out",
    "judge_2_cost",
    "judge_2_list_cost",
    "judge_2_allowance",
    "judge_2_elapsed_ms",
    "j3_required",
    "j3_status",
    "j3_disputed_fields_json",
    "j3_disputed_values_json",
    "j3_reason_codes_by_field_json",
    "j3_short_reason",
    "j3_answer_span_quote",
    "j3_evidence_span_locators_json",
    "j3_request_sha256",
    "j3_response_sha256",
    "j3_actual_model",
    "j3_model_identity_status",
    "j3_model_version_or_fingerprint",
    "j3_endpoint_host",
    "j3_response_id",
    "j3_http_status",
    "j3_attempt_count",
    "j3_tokens_in",
    "j3_tokens_out",
    "j3_cost",
    "j3_list_cost",
    "j3_allowance",
    "j3_elapsed_ms",
    "machine_consensus_answerability_correct",
    "machine_consensus_answer_correct",
    "machine_consensus_evidence_traceable",
    "machine_consensus_fatal_error",
    "machine_consensus_egts",
    "machine_consensus_status",
    "strict_judge_status",
    "aggregated_utc",
    "analysis_manifest_sha256",
    "notes",
]


def write_csv_path(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _invalid(reason: str, **extra) -> dict:
    return {
        "status": "INVALID_SCHEMA",
        "schema_errors": [reason],
        **extra,
    }


def _requires_explanation(values: dict) -> bool:
    return any(values.get(field) is False for field in FAILURE_FIELDS) or (
        values.get("fatal_error") is True
    )


def _has_no_response(reasons) -> bool:
    return (
        isinstance(reasons, dict)
        and "NO_RESPONSE" in reasons.get("answerability_correct", [])
    )


def _valid_reason_map(value, fields: tuple[str, ...]) -> bool:
    if not isinstance(value, dict) or set(value) != set(fields):
        return False
    for field in fields:
        codes = value.get(field)
        if not isinstance(codes, list):
            return False
        if any(not isinstance(code, str) or code not in REASON_CODES for code in codes):
            return False
    return True


def validate_judgment(
    data: dict,
    expected_answerability: str | None = None,
) -> list[str]:
    errors: list[str] = []
    forbidden = {"decision", "score", "pass_fail", "final_result"}
    if forbidden.intersection(data):
        errors.append("model_written_decision_forbidden")

    for field in ("answerability_correct", "fatal_error"):
        if not isinstance(data.get(field), bool):
            errors.append(f"{field}_not_boolean")

    for field in ("answer_correct", "evidence_traceable"):
        value = data.get(field)
        if isinstance(value, bool):
            continue
        if value == "not_applicable":
            if expected_answerability not in {None, "UNANSWERABLE"}:
                errors.append(f"{field}_not_applicable_on_answerable")
            continue
        errors.append(f"{field}_invalid")

    if expected_answerability == "ANSWERABLE":
        for field in NA_FIELDS:
            if data.get(field) == "not_applicable":
                errors.append(f"{field}_not_applicable_on_answerable")
    if expected_answerability == "UNANSWERABLE":
        for field in NA_FIELDS:
            if data.get(field) != "not_applicable":
                errors.append(f"{field}_must_be_not_applicable")

    reasons = data.get("reason_codes_by_field")
    if not _valid_reason_map(reasons, JUDGMENT_FIELDS):
        errors.append("reason_codes_by_field_invalid")
    else:
        for field in JUDGMENT_FIELDS:
            needs_reason = (
                field in FAILURE_FIELDS and data.get(field) is False
            ) or (field == "fatal_error" and data.get(field) is True)
            if needs_reason and not reasons[field]:
                errors.append(f"{field}_reason_required")
            if data.get(field) == "not_applicable" and reasons[field]:
                errors.append(f"{field}_reason_for_not_applicable")

    short_reason = data.get("short_reason")
    if not isinstance(short_reason, str):
        errors.append("short_reason_not_string")
    elif len(short_reason) > 300:
        errors.append("short_reason_too_long")
    elif _requires_explanation(data) and not short_reason.strip():
        errors.append("short_reason_required")

    quote = data.get("answer_span_quote")
    if not isinstance(quote, str):
        errors.append("answer_span_quote_not_string")
    else:
        if (
            _requires_explanation(data)
            and not quote.strip()
            and not _has_no_response(reasons)
        ):
            errors.append("answer_span_quote_required")

    locators = data.get("evidence_span_locators")
    if not isinstance(locators, list) or any(
        not isinstance(item, str) for item in locators
    ):
        errors.append("evidence_span_locators_not_string_list")
    return errors


def validate_adjudication(
    data: dict,
    disputed_fields: set[str],
    expected_answerability: str,
) -> list[str]:
    errors: list[str] = []
    if set(data) != ADJUDICATION_FIELDS:
        extra = sorted(set(data) - ADJUDICATION_FIELDS)
        missing = sorted(ADJUDICATION_FIELDS - set(data))
        if extra:
            errors.append(f"adjudication_extra_fields:{'|'.join(extra)}")
        if missing:
            errors.append(f"adjudication_missing_fields:{'|'.join(missing)}")
    values = data.get("disputed_values")
    if not isinstance(values, dict) or set(values) != disputed_fields:
        errors.append("disputed_values_field_set_mismatch")
        values = {}
    for field, value in values.items():
        if field not in JUDGMENT_FIELDS:
            errors.append(f"unknown_disputed_field:{field}")
            continue
        if field in NA_FIELDS and expected_answerability == "UNANSWERABLE":
            if value != "not_applicable":
                errors.append(f"{field}_must_be_not_applicable")
        elif field in NA_FIELDS and expected_answerability == "ANSWERABLE":
            if not isinstance(value, bool):
                errors.append(f"{field}_must_be_boolean")
        elif not isinstance(value, bool):
            errors.append(f"{field}_must_be_boolean")

    reasons = data.get("reason_codes_by_field")
    if not _valid_reason_map(reasons, tuple(sorted(disputed_fields))):
        errors.append("reason_codes_by_field_invalid")
    else:
        for field in disputed_fields:
            value = values.get(field)
            needs_reason = (
                field in FAILURE_FIELDS and value is False
            ) or (field == "fatal_error" and value is True)
            if needs_reason and not reasons[field]:
                errors.append(f"{field}_reason_required")

    quote = data.get("answer_span_quote")
    short_reason = data.get("short_reason")
    if not isinstance(quote, str):
        errors.append("answer_span_quote_not_string")
    if not isinstance(short_reason, str) or len(short_reason) > 300:
        errors.append("short_reason_invalid")
    elif _requires_explanation(values) and not short_reason.strip():
        errors.append("short_reason_required")
    if (
        isinstance(quote, str)
        and _requires_explanation(values)
        and not quote.strip()
        and not _has_no_response(reasons)
    ):
        errors.append("answer_span_quote_required")
    locators = data.get("evidence_span_locators")
    if not isinstance(locators, list) or any(
        not isinstance(item, str) or not item.strip() for item in locators
    ):
        errors.append("evidence_span_locators_invalid")
    return errors


def model_identity_status(
    record: dict,
    allowed_model_ids: set[str] | None,
) -> str:
    if allowed_model_ids is None:
        return "NOT_CHECKED"
    actual = str(record.get("actual_model") or "").strip()
    if not actual:
        return "MODEL_ID_MISSING"
    normalized_allowed = {
        str(value).strip() for value in allowed_model_ids if str(value).strip()
    }
    if actual not in normalized_allowed:
        return "MODEL_ID_MISMATCH"
    return "MATCH"


def read_judge(
    path: Path,
    expected_answerability: str | None = None,
    allowed_model_ids: set[str] | None = None,
) -> dict:
    if not path.is_file():
        return {"status": "MISSING", "schema_errors": ["file_missing"]}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "UNREADABLE",
            "schema_errors": [type(exc).__name__],
        }
    if data.get("status") != "completed":
        return {
            "status": str(data.get("status") or "UNKNOWN"),
            "schema_errors": list(data.get("schema_errors") or []),
        }
    errors = validate_judgment(data, expected_answerability)
    if errors:
        return {**_invalid("|".join(errors)), "schema_errors": errors}
    identity_status = model_identity_status(data, allowed_model_ids)
    if identity_status not in {"MATCH", "NOT_CHECKED"}:
        return {
            **data,
            "status": identity_status,
            "model_identity_status": identity_status,
            "schema_errors": [identity_status.lower()],
        }
    return {
        **data,
        "status": "completed",
        "model_identity_status": identity_status,
        "schema_errors": [],
    }


def metric_tuple(judgment: dict) -> tuple:
    return tuple(judgment.get(field) for field in JUDGMENT_FIELDS)


def disputed_fields(first: dict, second: dict) -> set[str]:
    return {
        field
        for field in JUDGMENT_FIELDS
        if first.get(field) != second.get(field)
    }


def deterministic_egts(values: dict, expected_answerability: str) -> int:
    fatal = values.get("fatal_error")
    availability = values.get("answerability_correct")
    if fatal is True or availability is not True:
        return 0
    if expected_answerability == "UNANSWERABLE":
        return 1
    return int(
        values.get("answer_correct") is True
        and values.get("evidence_traceable") is True
    )


def latest_attempt(directory: Path) -> Path | None:
    files = sorted(directory.glob("attempt_*.json"))
    return files[-1] if files else None


def allowed_models(config: dict, role: str) -> set[str]:
    judge = config.get("judges", {}).get(role, {})
    values = {
        judge.get("actual_model_id"),
        *judge.get("allowed_model_ids", []),
    }
    return {str(value).strip() for value in values if str(value or "").strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-plan", default="06_runs/manifests/run_plan.csv")
    parser.add_argument(
        "--judge-root",
        default=None,
        help="protected judge-root; defaults below CITRUS_EXPERIMENT_L1_ROOT",
    )
    parser.add_argument(
        "--blind-map",
        default=None,
        help="protected BLIND_ID_MAP.csv; defaults below CITRUS_EXPERIMENT_L1_ROOT",
    )
    parser.add_argument(
        "--raw-root",
        default=None,
        help="protected raw run root; defaults below CITRUS_EXPERIMENT_L1_ROOT",
    )
    parser.add_argument(
        "--request-root",
        default=None,
        help="judge request root; defaults below CITRUS_EXPERIMENT_L1_ROOT",
    )
    parser.add_argument("--attempt", type=int, default=0)
    parser.add_argument(
        "--out",
        default=None,
        help="protected L1 output; defaults below CITRUS_EXPERIMENT_L1_ROOT",
    )
    parser.add_argument(
        "--judge-config",
        default="05_baselines/judge_config.json",
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    config = json.loads((ROOT / args.judge_config).read_text(encoding="utf-8"))
    for role in ("judge_1", "judge_2", "judge_3"):
        if not config.get("judges", {}).get(role, {}).get("actual_model_id"):
            raise SystemExit(
                f"{role} actual_model_id is not frozen; aggregation is blocked"
            )
    plan = [row for row in csvrows(args.run_plan) if row["kind"] == "qa"]
    if len(plan) != 180:
        raise SystemExit(f"expected 180 core QA rows, found {len(plan)}")
    question_rows = {
        row["题号"]: row for row in csvrows("03_benchmarks/qa60/qa60_CANDIDATE.csv")
    }
    l1_value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not l1_value:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    l1_root = Path(l1_value).expanduser().resolve()
    blind_map_path = (
        Path(args.blind_map).expanduser().resolve()
        if args.blind_map
        else l1_root / "judge_blinding" / "BLIND_ID_MAP.csv"
    )
    if args.judge_root:
        judge_root = Path(args.judge_root).expanduser().resolve()
    else:
        judge_root = l1_root / "judge_raw"
    raw_root = (
        Path(args.raw_root).expanduser().resolve()
        if args.raw_root
        else l1_root / "06_runs" / "raw"
    )
    request_root = (
        Path(args.request_root).expanduser().resolve()
        if args.request_root
        else l1_root / "judge_requests"
    )
    for label, path in (
        ("blind map", blind_map_path),
        ("judge root", judge_root),
        ("raw root", raw_root),
        ("request root", request_root),
    ):
        if not path.is_relative_to(l1_root):
            raise SystemExit(f"{label} must be inside protected L1 storage")
    if l1_root.is_relative_to(ROOT.resolve()):
        raise SystemExit("L1 root must remain outside the public Git tree")
    with blind_map_path.open(encoding="utf-8", newline="") as handle:
        blind_rows = list(csv.DictReader(handle))
    run_to_blind = {
        row["internal_run_key"]: row for row in blind_rows
    }
    if len(run_to_blind) != len(blind_rows):
        raise SystemExit("blind map contains duplicate internal run keys")
    if len({row["blind_id"] for row in blind_rows}) != len(blind_rows):
        raise SystemExit("blind map contains duplicate blind IDs")
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
    else:
        out_path = (
            l1_root
            / "07_scoring"
            / "judge"
            / "qa_judge_aggregated.csv"
        )
    if not out_path.is_relative_to(l1_root) or out_path.is_relative_to(ROOT.resolve()):
        raise SystemExit("judge aggregates must be written inside protected L1 storage")
    if out_path.exists():
        raise SystemExit(f"refuse overwrite: {out_path}")

    output_rows = []
    for run in plan:
        run_id = run["run_id"]
        blind_row = run_to_blind.get(run_id)
        if not blind_row:
            raise SystemExit(f"blind map lacks run: {run_id}")
        blind_id = blind_row["blind_id"]
        packet_path = l1_root / "judge_packets" / f"{blind_id}.json"
        if blind_row.get("packet_sha256") and (
            not packet_path.is_file()
            or sha256_file(packet_path) != blind_row["packet_sha256"]
        ):
            raise SystemExit(f"blind packet SHA mismatch for run: {run_id}")
        question = question_rows[run["case_id"]]
        expected = "UNANSWERABLE" if question["组别"] == "N" else "ANSWERABLE"
        raw_path = raw_root / run["condition"] / f"{run_id}.json"
        run_status = ""
        failure_class = ""
        raw_sha = ""
        errors: list[str] = []
        if not raw_path.is_file():
            run_status = "RAW_RUN_MISSING"
            failure_class = "RAW_RUN_MISSING"
            errors.append("raw_run_missing")
        else:
            raw_sha = sha256_file(raw_path)
            try:
                raw_record = json.loads(raw_path.read_text(encoding="utf-8"))
                response = raw_record.get("actual_response") or raw_record
                run_status = str(
                    response.get("status")
                    or raw_record.get("status")
                    or ""
                )
                failure_class = str(
                    raw_record.get("failure_class")
                    or response.get("failure_class")
                    or ""
                )
                if run_status not in TERMINAL_RUN_STATUSES:
                    errors.append(
                        f"raw_run_status_invalid:{run_status or 'EMPTY'}"
                    )
            except Exception as exc:
                run_status = "RAW_RUN_UNREADABLE"
                errors.append(f"raw_run_unreadable:{type(exc).__name__}")

        judge_required = run_status in SEMANTIC_JUDGE_STATUSES
        paths = {
            role: (
                judge_root / role / blind_id / f"attempt_{args.attempt:02d}.json"
                if args.attempt
                else latest_attempt(judge_root / role / blind_id) or (
                    judge_root / role / blind_id / "attempt_01.json"
                )
            )
            for role in ("judge_1", "judge_2", "judge_3")
        }
        request_paths = {
            role: (
                request_root
                / role
                / blind_id
                / f"attempt_{args.attempt:02d}.json"
                if args.attempt
                else latest_attempt(request_root / role / blind_id)
            )
            for role in ("judge_1", "judge_2", "judge_3")
        }
        if judge_required:
            first = read_judge(
                paths["judge_1"],
                expected,
                allowed_models(config, "judge_1"),
            )
            second = read_judge(
                paths["judge_2"],
                expected,
                allowed_models(config, "judge_2"),
            )
            if first["status"] != "completed":
                errors.append(f"judge_1:{first['status']}")
            if second["status"] != "completed":
                errors.append(f"judge_2:{second['status']}")
        else:
            first = {
                "status": "NOT_APPLICABLE_INFRASTRUCTURE",
                "schema_errors": [],
            }
            second = {
                "status": "NOT_APPLICABLE_INFRASTRUCTURE",
                "schema_errors": [],
            }

        third_status = "NOT_REQUIRED"
        third_values: dict = {}
        parsed_third: dict = {}
        raw_third: dict = {}
        disputed = disputed_fields(first, second) if not errors else set()
        if not errors and disputed:
            try:
                raw_third = json.loads(paths["judge_3"].read_text(encoding="utf-8"))
            except FileNotFoundError:
                raw_third = None
                third_status = "MISSING"
            except Exception:
                raw_third = None
                third_status = "UNREADABLE"
            if raw_third is None:
                errors.append(f"judge_3:{third_status}")
            elif raw_third.get("status") != "completed":
                third_status = str(raw_third.get("status") or "UNKNOWN")
                errors.append(f"judge_3:{third_status}")
            else:
                parsed_third = raw_third.get("parsed_adjudication")
                if not isinstance(parsed_third, dict):
                    parsed_third = {
                        key: raw_third.get(key)
                        for key in ADJUDICATION_FIELDS
                    }
                identity_status = model_identity_status(
                    raw_third,
                    allowed_models(config, "judge_3"),
                )
                third_errors = validate_adjudication(
                    parsed_third,
                    disputed,
                    expected,
                )
                if identity_status != "MATCH":
                    third_errors.append(identity_status)
                if third_errors:
                    third_status = "INVALID_SCHEMA"
                    errors.extend(f"judge_3:{item}" for item in third_errors)
                else:
                    third_status = "completed"
                    third_values = parsed_third["disputed_values"]

        consensus: dict = {}
        if not errors and judge_required:
            for field in JUDGMENT_FIELDS:
                consensus[field] = (
                    third_values[field] if field in disputed else first[field]
                )

        if errors:
            egts = ""
            consensus_status = "BLOCKED"
        elif judge_required:
            egts = str(deterministic_egts(consensus, expected))
            consensus_status = "PASS" if egts == "1" else "FAIL"
        else:
            egts = "0"
            consensus_status = "INFRASTRUCTURE_FAILURE"

        output_rows.append(
            {
                "run_id": run_id,
                "question_id": run["case_id"],
                "condition": run["condition"],
                "source_cluster": run["source_ids"].split("+")[0],
                "expected_answerability": expected,
                "run_status": run_status,
                "failure_class": failure_class,
                "raw_run_sha256": raw_sha,
                "judge_1_status": first["status"],
                "judge_1_answerability_correct": first.get(
                    "answerability_correct", ""
                ),
                "judge_1_answer_correct": first.get("answer_correct", ""),
                "judge_1_evidence_traceable": first.get(
                    "evidence_traceable", ""
                ),
                "judge_1_fatal_error": first.get("fatal_error", ""),
                "judge_1_reason_codes_by_field_json": json.dumps(
                    first.get("reason_codes_by_field", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "judge_1_short_reason": first.get("short_reason", ""),
                "judge_1_answer_span_quote": first.get("answer_span_quote", ""),
                "judge_1_evidence_span_locators_json": json.dumps(
                    first.get("evidence_span_locators", []),
                    ensure_ascii=False,
                ),
                "judge_2_status": second["status"],
                "judge_2_answerability_correct": second.get(
                    "answerability_correct", ""
                ),
                "judge_2_answer_correct": second.get("answer_correct", ""),
                "judge_2_evidence_traceable": second.get(
                    "evidence_traceable", ""
                ),
                "judge_2_fatal_error": second.get("fatal_error", ""),
                "judge_2_reason_codes_by_field_json": json.dumps(
                    second.get("reason_codes_by_field", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "judge_2_short_reason": second.get("short_reason", ""),
                "judge_2_answer_span_quote": second.get("answer_span_quote", ""),
                "judge_2_evidence_span_locators_json": json.dumps(
                    second.get("evidence_span_locators", []),
                    ensure_ascii=False,
                ),
                "j3_required": str(bool(disputed)).lower(),
                "j3_status": third_status,
                "j3_disputed_fields_json": json.dumps(
                    sorted(disputed), ensure_ascii=False
                ),
                "j3_disputed_values_json": json.dumps(
                    third_values, ensure_ascii=False, sort_keys=True
                ),
                "j3_reason_codes_by_field_json": json.dumps(
                    parsed_third.get("reason_codes_by_field", {}),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "j3_short_reason": parsed_third.get("short_reason", ""),
                "j3_answer_span_quote": parsed_third.get("answer_span_quote", ""),
                "j3_evidence_span_locators_json": json.dumps(
                    parsed_third.get("evidence_span_locators", []),
                    ensure_ascii=False,
                ),
                "machine_consensus_answerability_correct": consensus.get(
                    "answerability_correct", ""
                ),
                "machine_consensus_answer_correct": consensus.get(
                    "answer_correct", ""
                ),
                "machine_consensus_evidence_traceable": consensus.get(
                    "evidence_traceable", ""
                ),
                "machine_consensus_fatal_error": consensus.get(
                    "fatal_error", ""
                ),
                "machine_consensus_egts": egts,
                "machine_consensus_status": consensus_status,
                "strict_judge_status": "BLOCKED" if errors else "PASS",
                "aggregated_utc": utc(),
                "notes": "|".join(errors),
                "judge_1_request_sha256": sha256_file(request_paths["judge_1"])
                if request_paths["judge_1"]
                and request_paths["judge_1"].is_file()
                else "",
                "judge_1_response_sha256": sha256_file(paths["judge_1"])
                if paths["judge_1"].is_file()
                else "",
                "judge_1_actual_model": first.get("actual_model", ""),
                "judge_1_model_identity_status": first.get(
                    "model_identity_status", "NOT_APPLICABLE"
                ),
                "judge_1_model_version_or_fingerprint": first.get(
                    "actual_model_version_or_fingerprint", ""
                ),
                "judge_1_endpoint_host": first.get("endpoint_host", ""),
                "judge_1_response_id": first.get("response_id", ""),
                "judge_1_http_status": first.get("http_status", ""),
                "judge_1_attempt_count": first.get(
                    "attempt_count", first.get("attempt", "")
                ),
                "judge_1_tokens_in": first.get("tokens_in", ""),
                "judge_1_tokens_out": first.get("tokens_out", ""),
                "judge_1_cost": first.get("cost", ""),
                "judge_1_list_cost": first.get("list_cost", ""),
                "judge_1_allowance": first.get("allowance", ""),
                "judge_1_elapsed_ms": first.get("elapsed_ms", ""),
                "judge_2_request_sha256": sha256_file(request_paths["judge_2"])
                if request_paths["judge_2"]
                and request_paths["judge_2"].is_file()
                else "",
                "judge_2_response_sha256": sha256_file(paths["judge_2"])
                if paths["judge_2"].is_file()
                else "",
                "judge_2_actual_model": second.get("actual_model", ""),
                "judge_2_model_identity_status": second.get(
                    "model_identity_status", "NOT_APPLICABLE"
                ),
                "judge_2_model_version_or_fingerprint": second.get(
                    "actual_model_version_or_fingerprint", ""
                ),
                "judge_2_endpoint_host": second.get("endpoint_host", ""),
                "judge_2_response_id": second.get("response_id", ""),
                "judge_2_http_status": second.get("http_status", ""),
                "judge_2_attempt_count": second.get(
                    "attempt_count", second.get("attempt", "")
                ),
                "judge_2_tokens_in": second.get("tokens_in", ""),
                "judge_2_tokens_out": second.get("tokens_out", ""),
                "judge_2_cost": second.get("cost", ""),
                "judge_2_list_cost": second.get("list_cost", ""),
                "judge_2_allowance": second.get("allowance", ""),
                "judge_2_elapsed_ms": second.get("elapsed_ms", ""),
                "j3_request_sha256": sha256_file(request_paths["judge_3"])
                if request_paths["judge_3"]
                and request_paths["judge_3"].is_file()
                else "",
                "j3_response_sha256": sha256_file(paths["judge_3"])
                if paths["judge_3"].is_file()
                else "",
                "j3_actual_model": raw_third.get("actual_model", ""),
                "j3_model_identity_status": (
                    "MATCH"
                    if third_status == "completed"
                    else model_identity_status(
                        raw_third,
                        allowed_models(config, "judge_3"),
                    )
                    if raw_third
                    else "NOT_REQUIRED"
                ),
                "j3_model_version_or_fingerprint": raw_third.get(
                    "actual_model_version_or_fingerprint", ""
                ),
                "j3_endpoint_host": raw_third.get("endpoint_host", ""),
                "j3_response_id": raw_third.get("response_id", ""),
                "j3_http_status": raw_third.get("http_status", ""),
                "j3_attempt_count": raw_third.get(
                    "attempt_count", raw_third.get("attempt", "")
                ),
                "j3_tokens_in": raw_third.get("tokens_in", ""),
                "j3_tokens_out": raw_third.get("tokens_out", ""),
                "j3_cost": raw_third.get("cost", ""),
                "j3_list_cost": raw_third.get("list_cost", ""),
                "j3_allowance": raw_third.get("allowance", ""),
                "j3_elapsed_ms": raw_third.get("elapsed_ms", ""),
                "analysis_manifest_sha256": "",
            }
        )

    write_csv_path(out_path, output_rows, OUTPUT_FIELDS)
    errors = [row for row in output_rows if row["strict_judge_status"] != "PASS"]
    print(
        json.dumps(
            {
                "status": "PASS" if not errors else "BLOCKED",
                "rows": len(output_rows),
                "errors": len(errors),
                "out": str(out_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if args.strict and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

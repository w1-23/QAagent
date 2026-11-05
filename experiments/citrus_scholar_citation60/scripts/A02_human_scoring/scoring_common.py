#!/usr/bin/env python3
"""Shared deterministic validators for human expert scoring."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

JUDGMENT_FIELDS = (
    "answerability_correct",
    "answer_correct",
    "evidence_traceable",
    "fatal_error",
)
NA_FIELDS = {"answer_correct", "evidence_traceable"}
FAILURE_FIELDS = {"answerability_correct", "answer_correct", "evidence_traceable"}
SCORE_STATUSES = {"SCORED", "UNRATEABLE", "MISSING", "INVALID"}
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
SCORE_FIELDS = [
    "batch_id",
    "blind_id",
    "reviewer_id",
    "score_version",
    "answerability_correct",
    "answer_correct",
    "evidence_traceable",
    "fatal_error",
    "reason_codes_by_field_json",
    "short_reason",
    "answer_span_quote",
    "evidence_span_locators_json",
    "score_status",
    "issue_id",
    "reviewed_utc",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def parse_bool_or_na(value: str, field: str) -> bool | str | None:
    normalized = (value or "").strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    if normalized == "not_applicable" and field in NA_FIELDS:
        return "not_applicable"
    return None


def parse_reason_map(value: str) -> tuple[dict | None, str | None]:
    try:
        parsed = json.loads(value)
    except Exception:
        return None, "reason_codes_by_field_json_invalid_json"
    if not isinstance(parsed, dict) or set(parsed) != set(JUDGMENT_FIELDS):
        return None, "reason_codes_by_field_json_key_set"
    for field, codes in parsed.items():
        if not isinstance(codes, list) or any(
            not isinstance(code, str) or code not in REASON_CODES
            for code in codes
        ):
            return None, f"reason_codes_by_field_json_invalid:{field}"
    return parsed, None


def parse_locators(value: str) -> tuple[list[str] | None, str | None]:
    try:
        parsed = json.loads(value)
    except Exception:
        return None, "evidence_span_locators_json_invalid_json"
    if not isinstance(parsed, list) or any(
        not isinstance(item, str) or not item.strip() for item in parsed
    ):
        return None, "evidence_span_locators_json_invalid"
    return parsed, None


def validate_score_row(
    row: dict,
    expected_answerability: str,
) -> tuple[list[str], dict]:
    errors: list[str] = []
    normalized = dict(row)
    if row.get("score_status") not in SCORE_STATUSES:
        errors.append("score_status_invalid")
    if row.get("score_status") != "SCORED":
        errors.append("score_status_not_scored")
        return errors, normalized

    if str(row.get("score_version") or "") != "4.3.2":
        errors.append("score_version_invalid")
    for field in ("batch_id", "blind_id", "reviewer_id"):
        if not str(row.get(field) or "").strip():
            errors.append(f"{field}_required")

    for field in ("answerability_correct", "fatal_error"):
        value = parse_bool_or_na(row.get(field, ""), field)
        if not isinstance(value, bool):
            errors.append(f"{field}_not_boolean")
        else:
            normalized[field] = value
    for field in ("answer_correct", "evidence_traceable"):
        value = parse_bool_or_na(row.get(field, ""), field)
        if value is None:
            errors.append(f"{field}_invalid")
        elif expected_answerability == "UNANSWERABLE" and value != "not_applicable":
            errors.append(f"{field}_must_be_not_applicable")
        elif expected_answerability == "ANSWERABLE" and value == "not_applicable":
            errors.append(f"{field}_not_applicable_on_answerable")
        else:
            normalized[field] = value

    reasons, reason_error = parse_reason_map(
        row.get("reason_codes_by_field_json", "")
    )
    locators, locator_error = parse_locators(
        row.get("evidence_span_locators_json", "")
    )
    if reason_error:
        errors.append(reason_error)
    if locator_error:
        errors.append(locator_error)
    normalized["reason_codes_by_field"] = reasons or {}
    normalized["evidence_span_locators"] = locators or []

    short_reason = row.get("short_reason", "")
    quote = row.get("answer_span_quote", "")
    if len(short_reason) > 300:
        errors.append("short_reason_too_long")
    if reasons:
        for field in JUDGMENT_FIELDS:
            value = normalized.get(field)
            needs_reason = (
                field in FAILURE_FIELDS and value is False
            ) or (field == "fatal_error" and value is True)
            if needs_reason and not reasons[field]:
                errors.append(f"{field}_reason_required")
            if value == "not_applicable" and reasons[field]:
                errors.append(f"{field}_reason_for_not_applicable")
        explanation_needed = any(
            normalized.get(field) is False for field in FAILURE_FIELDS
        ) or normalized.get("fatal_error") is True
        no_response = (
            "NO_RESPONSE" in reasons.get("answerability_correct", [])
        )
        if explanation_needed and not short_reason.strip():
            errors.append("short_reason_required")
        if explanation_needed and not quote.strip() and not no_response:
            errors.append("answer_span_quote_required")
        if explanation_needed and not locators:
            errors.append("evidence_span_locators_required")
    return errors, normalized


def deterministic_egts(values: dict, expected_answerability: str) -> int | str:
    if values.get("fatal_error") is True:
        return 0
    if values.get("answerability_correct") is not True:
        return 0
    if expected_answerability == "UNANSWERABLE":
        return 1
    return int(
        values.get("answer_correct") is True
        and values.get("evidence_traceable") is True
    )

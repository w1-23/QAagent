#!/usr/bin/env python3
"""Validate third-expert field adjudications before consensus aggregation."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from A02_human_scoring.scoring_common import (  # noqa: E402
    JUDGMENT_FIELDS,
    NA_FIELDS,
    REASON_CODES,
    parse_bool_or_na,
    read_csv,
    sha256_file,
)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adjudications", required=True)
    parser.add_argument("--scores-expert-1", required=True)
    parser.add_argument("--scores-expert-2", required=True)
    parser.add_argument("--packet-root", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    l1_value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not l1_value:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    l1_root = Path(l1_value).expanduser().resolve()
    paths = [
        Path(args.adjudications).expanduser().resolve(),
        Path(args.scores_expert_1).expanduser().resolve(),
        Path(args.scores_expert_2).expanduser().resolve(),
        Path(args.packet_root).expanduser().resolve(),
        Path(args.out_root).expanduser().resolve(),
    ]
    for path in paths:
        if not path.is_relative_to(l1_root):
            raise SystemExit("adjudication inputs and outputs must remain in L1")
    out_root = paths[-1]
    if out_root.exists():
        raise SystemExit(f"refuse overwrite: {out_root}")

    _, first_rows = read_csv(paths[1])
    _, second_rows = read_csv(paths[2])
    adjudication_header, adjudication_rows = read_csv(paths[0])
    required_adjudication_fields = {
        "batch_id",
        "blind_id",
        "field_name",
        "expert1_reviewer_id",
        "expert1_value",
        "expert2_reviewer_id",
        "expert2_value",
        "adjudicator_id",
        "adjudicated_value",
        "reason_code",
        "short_reason",
        "answer_span_quote",
        "evidence_span_locators_json",
        "reviewed_utc",
        "adjudication_record_id",
    }
    missing_adjudication_fields = sorted(
        required_adjudication_fields - set(adjudication_header)
    )
    if missing_adjudication_fields:
        raise SystemExit(
            "adjudication file is missing columns: "
            + ",".join(missing_adjudication_fields)
        )
    first = {row["blind_id"]: row for row in first_rows}
    second = {row["blind_id"]: row for row in second_rows}
    if len(first) != len(first_rows) or len(second) != len(second_rows):
        raise SystemExit("primary score files contain duplicate blind IDs")
    if set(first) != set(second):
        raise SystemExit("primary score files do not cover the same blind IDs")
    packets = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(paths[3].glob("BLIND_*.json"))
    }

    errors: list[dict] = []
    adjudication_by_key: dict[tuple[str, str], dict] = {}
    for line_number, row in enumerate(adjudication_rows, start=2):
        entry_errors: list[str] = []
        blind_id = row.get("blind_id", "")
        field = row.get("field_name", "")
        key = (blind_id, field)
        if blind_id not in first or blind_id not in second or blind_id not in packets:
            entry_errors.append("blind_id_unknown")
            packet = {}
        else:
            packet = packets[blind_id]
        if key in adjudication_by_key:
            entry_errors.append("duplicate_field_adjudication")
        adjudication_by_key[key] = row
        if field not in JUDGMENT_FIELDS:
            entry_errors.append("field_name_invalid")
        if row.get("expert1_reviewer_id") != first.get(blind_id, {}).get(
            "reviewer_id"
        ):
            entry_errors.append("expert1_reviewer_id_mismatch")
        if row.get("expert2_reviewer_id") != second.get(blind_id, {}).get(
            "reviewer_id"
        ):
            entry_errors.append("expert2_reviewer_id_mismatch")
        if row.get("expert1_value") != first.get(blind_id, {}).get(field, ""):
            entry_errors.append("expert1_value_mismatch")
        if row.get("expert2_value") != second.get(blind_id, {}).get(field, ""):
            entry_errors.append("expert2_value_mismatch")
        if row.get("expert1_value") == row.get("expert2_value"):
            entry_errors.append("field_not_disputed")
        if row.get("adjudicator_id") in {
            row.get("expert1_reviewer_id"),
            row.get("expert2_reviewer_id"),
        }:
            entry_errors.append("adjudicator_must_be_third_reviewer")
        value = parse_bool_or_na(row.get("adjudicated_value", ""), field)
        expected = packet.get("expected_answerability", "")
        if value is None:
            entry_errors.append("adjudicated_value_invalid")
        elif field in NA_FIELDS and expected == "UNANSWERABLE" and value != "not_applicable":
            entry_errors.append("na_field_must_be_not_applicable")
        elif field in NA_FIELDS and expected == "ANSWERABLE" and not isinstance(value, bool):
            entry_errors.append("na_field_must_be_boolean")
        elif field not in NA_FIELDS and not isinstance(value, bool):
            entry_errors.append("adjudicated_value_must_be_boolean")
        if row.get("reason_code") not in REASON_CODES | {""}:
            entry_errors.append("reason_code_invalid")
        if (
            (isinstance(value, bool) and field != "fatal_error" and value is False)
            or (field == "fatal_error" and value is True)
        ) and not row.get("reason_code"):
            entry_errors.append("reason_code_required")
        if (
            (isinstance(value, bool) and field != "fatal_error" and value is False)
            or (field == "fatal_error" and value is True)
        ) and not row.get("short_reason", "").strip():
            entry_errors.append("short_reason_required")
        if len(row.get("short_reason", "")) > 300:
            entry_errors.append("short_reason_too_long")
        if (
            (isinstance(value, bool) and field != "fatal_error" and value is False)
            or (field == "fatal_error" and value is True)
        ) and not row.get("answer_span_quote", "").strip():
            entry_errors.append("answer_span_quote_required")
        try:
            locators = json.loads(row.get("evidence_span_locators_json", ""))
            if not isinstance(locators, list) or any(
                not isinstance(item, str) or not item.strip()
                for item in locators
            ):
                raise ValueError
        except Exception:
            entry_errors.append("evidence_span_locators_json_invalid")
        if not row.get("adjudicator_id", "").strip():
            entry_errors.append("adjudicator_id_required")
        if not row.get("adjudication_record_id", "").strip():
            entry_errors.append("adjudication_record_id_required")
        try:
            datetime.fromisoformat(
                row.get("reviewed_utc", "").replace("Z", "+00:00")
            )
        except Exception:
            entry_errors.append("reviewed_utc_invalid")
        if entry_errors:
            errors.append(
                {
                    "row": line_number,
                    "blind_id": blind_id,
                    "field": field,
                    "errors": entry_errors,
                }
            )

    for blind_id in sorted(first):
        for field in JUDGMENT_FIELDS:
            if first[blind_id].get(field) != second[blind_id].get(field):
                if (blind_id, field) not in adjudication_by_key:
                    errors.append(
                        {
                            "row": 0,
                            "blind_id": blind_id,
                            "field": field,
                            "errors": ["required_adjudication_missing"],
                        }
                    )

    out_root.mkdir(parents=True)
    normalized_path = out_root / "adjudications_validated.csv"
    write_csv(
        normalized_path,
        [
            "batch_id",
            "blind_id",
            "field_name",
            "expert1_reviewer_id",
            "expert1_value",
            "expert2_reviewer_id",
            "expert2_value",
            "adjudicator_id",
            "adjudicated_value",
            "reason_code",
            "short_reason",
            "answer_span_quote",
            "evidence_span_locators_json",
            "reviewed_utc",
            "adjudication_record_id",
        ],
        adjudication_rows,
    )
    report = {
        "status": "PASS" if not errors else "BLOCKED",
        "adjudication_rows": len(adjudication_rows),
        "errors": errors,
        "inputs": {
            "adjudications_sha256": sha256_file(paths[0]),
            "expert_1_sha256": sha256_file(paths[1]),
            "expert_2_sha256": sha256_file(paths[2]),
        },
        "normalized_path": str(normalized_path),
        "normalized_sha256": sha256_file(normalized_path),
    }
    (out_root / "adjudication_import.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 2 if args.strict and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

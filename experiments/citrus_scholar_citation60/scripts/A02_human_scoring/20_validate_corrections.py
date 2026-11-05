#!/usr/bin/env python3
"""Validate append-only score corrections and emit effective immutable copies."""
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
    SCORE_FIELDS,
    read_csv,
    sha256_file,
)

CORRECTABLE_FIELDS = {
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
}


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", required=True)
    parser.add_argument("--corrections", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    l1_value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not l1_value:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    l1_root = Path(l1_value).expanduser().resolve()
    scores_path = Path(args.scores).expanduser().resolve()
    corrections_path = Path(args.corrections).expanduser().resolve()
    out_root = Path(args.out_root).expanduser().resolve()
    for path in (scores_path, corrections_path, out_root):
        if not path.is_relative_to(l1_root):
            raise SystemExit("correction-chain inputs and output must remain in L1")
    if out_root.exists():
        raise SystemExit(f"refuse overwrite: {out_root}")

    score_header, score_rows = read_csv(scores_path)
    if score_header != SCORE_FIELDS:
        raise SystemExit("score header does not match frozen schema")
    correction_header, corrections = read_csv(corrections_path)
    required = {
        "batch_id",
        "blind_id",
        "issue_id",
        "reviewer_id",
        "issue_type",
        "field_name",
        "original_score_record_id",
        "original_value",
        "revised_value",
        "correction_reason",
        "revised_by",
        "revised_utc",
        "approval_record_id",
        "status",
    }
    if not required.issubset(set(correction_header)):
        raise SystemExit("correction log is missing required columns")

    row_by_id = {
        f"{row['blind_id']}|{row['reviewer_id']}": dict(row)
        for row in score_rows
    }
    if len(row_by_id) != len(score_rows):
        raise SystemExit("score file contains duplicate score record IDs")
    errors: list[dict] = []
    issue_ids: set[str] = set()
    applied: dict[str, list[str]] = {key: [] for key in row_by_id}
    effective = {key: dict(row) for key, row in row_by_id.items()}
    for line_number, row in enumerate(corrections, start=2):
        entry_errors: list[str] = []
        issue_id = row.get("issue_id", "")
        if not issue_id or issue_id in issue_ids:
            entry_errors.append("issue_id_missing_or_duplicate")
        issue_ids.add(issue_id)
        record_id = row.get("original_score_record_id", "")
        if record_id not in row_by_id:
            entry_errors.append("original_score_record_id_missing")
        expected_id = f"{row.get('blind_id','')}|{row.get('reviewer_id','')}"
        if record_id != expected_id:
            entry_errors.append("score_record_id_mismatch")
        if row.get("issue_type") != "CORRECTION":
            continue
        if record_id in row_by_id:
            original = row_by_id[record_id]
            if row.get("batch_id") != original.get("batch_id"):
                entry_errors.append("batch_id_mismatch")
            if row.get("reviewer_id") != original.get("reviewer_id"):
                entry_errors.append("reviewer_id_mismatch")
        if row.get("field_name") not in CORRECTABLE_FIELDS:
            entry_errors.append("field_not_correctable")
        if row.get("status") not in {"APPROVED", "APPLIED"}:
            entry_errors.append("correction_not_approved")
        if not row.get("approval_record_id", "").strip():
            entry_errors.append("approval_record_id_required")
        if not row.get("correction_reason", "").strip():
            entry_errors.append("correction_reason_required")
        if not row.get("revised_by", "").strip():
            entry_errors.append("revised_by_required")
        try:
            datetime.fromisoformat(
                row.get("revised_utc", "").replace("Z", "+00:00")
            )
        except Exception:
            entry_errors.append("revised_utc_invalid")
        if record_id in row_by_id and row.get("field_name") in CORRECTABLE_FIELDS:
            field = row["field_name"]
            if row.get("original_value", "") != effective[record_id].get(field, ""):
                entry_errors.append("original_value_mismatch")
            if row.get("revised_value", "") == row.get("original_value", ""):
                entry_errors.append("revised_value_unchanged")
        if entry_errors:
            errors.append(
                {
                    "row": line_number,
                    "blind_id": row.get("blind_id", ""),
                    "issue_id": issue_id,
                    "errors": entry_errors,
                }
            )
        elif record_id in row_by_id and row.get("field_name") in CORRECTABLE_FIELDS:
            field = row["field_name"]
            effective[record_id][field] = row.get("revised_value", "")
            applied[record_id].append(issue_id)

    output_rows = []
    for record_id in sorted(effective):
        output_row = dict(effective[record_id])
        output_row["correction_record_ids_json"] = json.dumps(
            applied[record_id],
            ensure_ascii=False,
        )
        output_rows.append(output_row)
    out_root.mkdir(parents=True)
    write_csv(
        out_root / "effective_scores.csv",
        [*SCORE_FIELDS, "correction_record_ids_json"],
        output_rows,
    )
    report = {
        "status": "PASS" if not errors else "BLOCKED",
        "scores_sha256": sha256_file(scores_path),
        "corrections_sha256": sha256_file(corrections_path),
        "score_rows": len(score_rows),
        "correction_rows": len(corrections),
        "correction_errors": errors,
        "original_submission_modified": False,
        "effective_scores_path": str(out_root / "effective_scores.csv"),
        "effective_scores_sha256": sha256_file(
            out_root / "effective_scores.csv"
        ),
    }
    (out_root / "correction_validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 2 if args.strict and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

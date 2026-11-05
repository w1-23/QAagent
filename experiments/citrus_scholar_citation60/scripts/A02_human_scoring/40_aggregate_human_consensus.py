#!/usr/bin/env python3
"""Build deterministic human consensus with system and semantic outcomes."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from A02_human_scoring.scoring_common import (  # noqa: E402
    JUDGMENT_FIELDS,
    deterministic_egts,
    read_csv,
    sha256_file,
    validate_score_row,
)

TERMINAL_STATUSES = {
    "completed",
    "partial",
    "no_results",
    "failed",
    "cancelled",
    "skipped",
}
SEMANTIC_STATUSES = {"completed", "partial", "no_results"}
INFRASTRUCTURE_STATUSES = {"failed", "cancelled", "skipped"}
OUTPUT_FIELDS = [
    "batch_id",
    "blind_id",
    "internal_run_key",
    "internal_question_key",
    "condition_internal",
    "expected_answerability",
    "source_cluster",
    "run_status",
    "failure_class",
    "expert1_reviewer_id",
    "expert1_score_status",
    "expert1_answerability_correct",
    "expert1_answer_correct",
    "expert1_evidence_traceable",
    "expert1_fatal_error",
    "expert2_reviewer_id",
    "expert2_score_status",
    "expert2_answerability_correct",
    "expert2_answer_correct",
    "expert2_evidence_traceable",
    "expert2_fatal_error",
    "adjudication_record_ids_json",
    "consensus_answerability_correct",
    "consensus_answer_correct",
    "consensus_evidence_traceable",
    "consensus_fatal_error",
    "semantic_fields",
    "human_consensus_egts",
    "egts_system_outcome",
    "consensus_status",
    "expert1_score_sha256",
    "expert2_score_sha256",
    "adjudication_sha256",
    "packet_manifest_sha256",
    "protocol_version",
    "generated_utc",
]


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validation_pass(path: Path) -> bool:
    return load_json(path).get("status") == "PASS"


def validation_matches_score(report: dict, score_path: Path) -> bool:
    return report.get("scores_sha256") == sha256_file(score_path)


def validation_matches_adjudication(
    report: dict,
    adjudications_path: Path,
    first_path: Path,
    second_path: Path,
) -> bool:
    inputs = report.get("inputs", {})
    return (
        inputs.get("adjudications_sha256") == sha256_file(adjudications_path)
        and inputs.get("expert_1_sha256") == sha256_file(first_path)
        and inputs.get("expert_2_sha256") == sha256_file(second_path)
    )


def score_map(path: Path) -> tuple[dict[str, dict], list[str]]:
    header, rows = read_csv(path)
    required = {
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
    }
    missing = sorted(required - set(header))
    by_blind = {row["blind_id"]: row for row in rows}
    if len(by_blind) != len(rows):
        missing.append("duplicate_blind_id")
    return by_blind, missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-plan", default="06_runs/manifests/run_plan.csv")
    parser.add_argument("--blind-map", required=True)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--packet-root", required=True)
    parser.add_argument("--expert-1", required=True)
    parser.add_argument("--expert-2", required=True)
    parser.add_argument("--expert-1-validation", required=True)
    parser.add_argument("--expert-2-validation", required=True)
    parser.add_argument("--adjudications", required=True)
    parser.add_argument("--adjudication-validation", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--strict-human", action="store_true")
    args = parser.parse_args()

    l1_value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not l1_value:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    l1_root = Path(l1_value).expanduser().resolve()
    input_paths = [
        Path(args.blind_map),
        Path(args.raw_root),
        Path(args.packet_root),
        Path(args.expert_1),
        Path(args.expert_2),
        Path(args.expert_1_validation),
        Path(args.expert_2_validation),
        Path(args.adjudications),
        Path(args.adjudication_validation),
    ]
    for path in input_paths:
        if not path.resolve().is_relative_to(l1_root):
            raise SystemExit(f"human consensus input is outside L1: {path}")
    out_root = Path(args.out_root).expanduser().resolve()
    if not out_root.is_relative_to(l1_root):
        raise SystemExit("human consensus output must remain in L1")
    if out_root.exists():
        raise SystemExit(f"refuse overwrite: {out_root}")
    if args.strict_human:
        for path in (Path(args.expert_1_validation), Path(args.expert_2_validation)):
            if not validation_pass(path):
                raise SystemExit(f"strict-human requires PASS validation: {path}")
        first_report = load_json(Path(args.expert_1_validation))
        second_report = load_json(Path(args.expert_2_validation))
        if not validation_matches_score(first_report, Path(args.expert_1)):
            raise SystemExit("strict-human expert-1 validation is stale")
        if not validation_matches_score(second_report, Path(args.expert_2)):
            raise SystemExit("strict-human expert-2 validation is stale")
        adjudication_report = load_json(Path(args.adjudication_validation))
        if not validation_pass(Path(args.adjudication_validation)):
            raise SystemExit("strict-human requires PASS adjudication validation")
        if not validation_matches_adjudication(
            adjudication_report,
            Path(args.adjudications),
            Path(args.expert_1),
            Path(args.expert_2),
        ):
            raise SystemExit("strict-human adjudication validation is stale")

    with (ROOT / args.run_plan).open(encoding="utf-8-sig", newline="") as handle:
        plan = [row for row in csv.DictReader(handle) if row["kind"] == "qa"]
    if len(plan) != 180:
        raise SystemExit(f"expected 180 core QA rows, found {len(plan)}")
    core_questions = {
        row["题号"]: row
        for row in csv.DictReader(
            (ROOT / "03_benchmarks/qa60/qa60_CANDIDATE.csv").open(
                encoding="utf-8-sig",
                newline="",
            )
        )
    }
    with Path(args.blind_map).open(encoding="utf-8-sig", newline="") as handle:
        blind_rows = list(csv.DictReader(handle))
    blind_by_run = {
        row["internal_run_key"]: row for row in blind_rows
    }
    packet_root = Path(args.packet_root)
    packet_manifest_path = packet_root / "manifest.json"
    packet_manifest = load_json(packet_manifest_path)
    if packet_manifest.get("status") != "READY":
        raise SystemExit("judge packet manifest is not READY")
    if packet_manifest.get("condition_fields_excluded") is not True:
        raise SystemExit("judge packet manifest does not confirm condition exclusion")
    if packet_manifest.get("raw_run_sha256_packet_excluded") is not True:
        raise SystemExit("judge packet manifest does not confirm raw-SHA exclusion")
    packet_paths = {
        path.stem: path for path in packet_root.glob("BLIND_*.json")
    }
    packets = {
        blind_id: load_json(path)
        for blind_id, path in packet_paths.items()
    }
    manifest_packets = {
        row["blind_id"]: row
        for row in packet_manifest.get("packets", [])
    }
    if len(manifest_packets) != len(packet_paths):
        raise SystemExit("packet manifest and packet files disagree")
    for blind_id, path in packet_paths.items():
        record = manifest_packets.get(blind_id)
        if record is None or record.get("packet_sha256") != sha256_file(path):
            raise SystemExit(f"packet SHA mismatch: {blind_id}")
    first, first_header_errors = score_map(Path(args.expert_1))
    second, second_header_errors = score_map(Path(args.expert_2))
    if first_header_errors or second_header_errors:
        raise SystemExit(
            f"expert score schema errors: {first_header_errors + second_header_errors}"
        )
    _, adjudication_rows = read_csv(Path(args.adjudications))
    adjudication = {
        (row["blind_id"], row["field_name"]): row
        for row in adjudication_rows
        if row.get("blind_id") and row.get("field_name")
    }
    if len(adjudication) != len(adjudication_rows):
        raise SystemExit("duplicate blind/field adjudication rows")

    output_rows: list[dict] = []
    blockers: list[dict] = []
    for run in plan:
        run_id = run["run_id"]
        question = core_questions[run["case_id"]]
        expected = "UNANSWERABLE" if question["组别"] == "N" else "ANSWERABLE"
        raw_path = Path(args.raw_root) / run["condition"] / f"{run_id}.json"
        failure_class = ""
        errors: list[str] = []
        if not raw_path.is_file():
            status = "RAW_RUN_MISSING"
            failure_class = "RAW_RUN_MISSING"
            errors.append("raw_run_missing")
        else:
            raw = load_json(raw_path)
            response = raw.get("actual_response") or raw
            status = str(response.get("status") or raw.get("status") or "")
            failure_class = str(
                raw.get("failure_class") or response.get("failure_class") or ""
            )
            if status not in TERMINAL_STATUSES:
                errors.append(f"run_status_invalid:{status or 'EMPTY'}")

        if status in SEMANTIC_STATUSES:
            blind_row = blind_by_run.get(run_id)
            if blind_row is None:
                errors.append("blind_map_entry_missing")
                blind_id = ""
                batch_id = ""
            else:
                blind_id = blind_row["blind_id"]
                batch_id = blind_row["batch_id"]
            packet = packets.get(blind_id)
            if packet is None:
                errors.append("blind_packet_missing")
            elif blind_row.get("packet_sha256") != sha256_file(
                packet_root / f"{blind_id}.json"
            ):
                errors.append("packet_sha256_mismatch")
            elif packet.get("expected_answerability") != expected:
                errors.append("expected_answerability_mismatch")
            expert_1 = first.get(blind_id)
            expert_2 = second.get(blind_id)
            if expert_1 is None or expert_2 is None:
                errors.append("expert_score_missing")
                expert_1 = expert_1 or {}
                expert_2 = expert_2 or {}
            else:
                first_errors, norm_1 = validate_score_row(expert_1, expected)
                second_errors, norm_2 = validate_score_row(expert_2, expected)
                errors.extend(f"expert_1:{item}" for item in first_errors)
                errors.extend(f"expert_2:{item}" for item in second_errors)
                expert_1, expert_2 = norm_1, norm_2
            consensus: dict = {}
            adjudication_ids: list[str] = []
            if not errors:
                for field in JUDGMENT_FIELDS:
                    first_value = expert_1.get(field)
                    second_value = expert_2.get(field)
                    if first_value == second_value:
                        consensus[field] = first_value
                        continue
                    decision = adjudication.get((blind_id, field))
                    if decision is None:
                        errors.append(f"adjudication_missing:{field}")
                        continue
                    adjudicated = decision.get("adjudicated_value", "").lower()
                    if adjudicated == "true":
                        consensus[field] = True
                    elif adjudicated == "false":
                        consensus[field] = False
                    elif adjudicated == "not_applicable":
                        consensus[field] = "not_applicable"
                    else:
                        errors.append(f"adjudication_value_invalid:{field}")
                    adjudication_ids.append(
                        decision.get("adjudication_record_id", "")
                    )
            semantic_egts: int | str = ""
            system_outcome = 0
            if not errors:
                semantic_egts = deterministic_egts(consensus, expected)
                system_outcome = int(semantic_egts == 1)
        elif status in INFRASTRUCTURE_STATUSES:
            blind_id = ""
            batch_id = ""
            expert_1 = {}
            expert_2 = {}
            consensus = {}
            adjudication_ids = []
            semantic_egts = ""
            system_outcome = 0
        else:
            blind_id = ""
            batch_id = ""
            expert_1 = {}
            expert_2 = {}
            consensus = {}
            adjudication_ids = []
            semantic_egts = ""
            system_outcome = 0

        if errors:
            consensus_status = "BLOCKED"
        elif status in INFRASTRUCTURE_STATUSES:
            consensus_status = "INFRASTRUCTURE_FAILURE"
        else:
            consensus_status = "PASS" if semantic_egts == 1 else "FAIL"
        for error in errors:
            blockers.append({"run_id": run_id, "error": error})
        output_rows.append(
            {
                "batch_id": batch_id,
                "blind_id": blind_id,
                "internal_run_key": run_id,
                "internal_question_key": run["case_id"],
                "condition_internal": run["condition"],
                "expected_answerability": expected,
                "source_cluster": question["组别"],
                "run_status": status,
                "failure_class": failure_class,
                "expert1_reviewer_id": expert_1.get("reviewer_id", ""),
                "expert1_score_status": expert_1.get("score_status", ""),
                "expert1_answerability_correct": expert_1.get(
                    "answerability_correct", ""
                ),
                "expert1_answer_correct": expert_1.get("answer_correct", ""),
                "expert1_evidence_traceable": expert_1.get(
                    "evidence_traceable", ""
                ),
                "expert1_fatal_error": expert_1.get("fatal_error", ""),
                "expert2_reviewer_id": expert_2.get("reviewer_id", ""),
                "expert2_score_status": expert_2.get("score_status", ""),
                "expert2_answerability_correct": expert_2.get(
                    "answerability_correct", ""
                ),
                "expert2_answer_correct": expert_2.get("answer_correct", ""),
                "expert2_evidence_traceable": expert_2.get(
                    "evidence_traceable", ""
                ),
                "expert2_fatal_error": expert_2.get("fatal_error", ""),
                "adjudication_record_ids_json": json.dumps(
                    adjudication_ids, ensure_ascii=False
                ),
                "consensus_answerability_correct": consensus.get(
                    "answerability_correct", "not_scored_infrastructure"
                    if status in INFRASTRUCTURE_STATUSES
                    else ""
                ),
                "consensus_answer_correct": consensus.get(
                    "answer_correct", "not_scored_infrastructure"
                    if status in INFRASTRUCTURE_STATUSES
                    else ""
                ),
                "consensus_evidence_traceable": consensus.get(
                    "evidence_traceable", "not_scored_infrastructure"
                    if status in INFRASTRUCTURE_STATUSES
                    else ""
                ),
                "consensus_fatal_error": consensus.get(
                    "fatal_error", "not_scored_infrastructure"
                    if status in INFRASTRUCTURE_STATUSES
                    else ""
                ),
                "semantic_fields": (
                    "not_scored_infrastructure"
                    if status in INFRASTRUCTURE_STATUSES
                    else "scored"
                    if not errors
                    else "blocked"
                ),
                "human_consensus_egts": semantic_egts,
                "egts_system_outcome": system_outcome,
                "consensus_status": consensus_status,
                "expert1_score_sha256": sha256_file(Path(args.expert_1)),
                "expert2_score_sha256": sha256_file(Path(args.expert_2)),
                "adjudication_sha256": sha256_file(Path(args.adjudications)),
                "packet_manifest_sha256": sha256_file(packet_manifest_path),
                "protocol_version": "4.3.2",
                "generated_utc": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
            }
        )

    out_root.mkdir(parents=True)
    write_csv(out_root / "qa_human_consensus_180.csv", OUTPUT_FIELDS, output_rows)
    by_batch: dict[str, list[dict]] = defaultdict(list)
    for row in output_rows:
        by_batch[row["batch_id"] or "NO_BATCH"].append(row)
    consensus_dir = out_root / "consensus"
    for batch_id, rows in sorted(by_batch.items()):
        write_csv(consensus_dir / f"{batch_id}.csv", OUTPUT_FIELDS, rows)
    semantic_rows = [
        row for row in output_rows if row["human_consensus_egts"] != ""
    ]
    report = {
        "status": "PASS" if not blockers else "BLOCKED",
        "planned_runs": len(output_rows),
        "semantic_scored_runs": len(semantic_rows),
        "infrastructure_failures": sum(
            row["run_status"] in INFRASTRUCTURE_STATUSES for row in output_rows
        ),
        "semantic_scoring_coverage": (
            len(semantic_rows)
            / (
                len(output_rows)
                - sum(
                    row["run_status"] in INFRASTRUCTURE_STATUSES
                    for row in output_rows
                )
            )
            if len(output_rows)
            > sum(
                row["run_status"] in INFRASTRUCTURE_STATUSES
                for row in output_rows
            )
            else 0
        ),
        "egts_system_outcome_by_condition": {
            condition: sum(
                int(row["egts_system_outcome"])
                for row in output_rows
                if row["condition_internal"] == condition
            )
            for condition in ("B0", "B1", "B2")
        },
        "human_semantic_success_by_condition": {
            condition: sum(
                int(row["human_consensus_egts"])
                for row in output_rows
                if row["condition_internal"] == condition
                and row["human_consensus_egts"] != ""
            )
            for condition in ("B0", "B1", "B2")
        },
        "blockers": blockers,
        "packet_manifest_sha256": sha256_file(packet_manifest_path),
        "output_sha256": sha256_file(out_root / "qa_human_consensus_180.csv"),
    }
    (out_root / "human_consensus_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 2 if args.strict_human and blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())

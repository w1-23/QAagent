#!/usr/bin/env python3
"""Build one two-reviewer validity packet for all 120 formal questions."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVIEW_FIELDS = [
    "review_batch_id",
    "experiment_id",
    "question_id",
    "stratum",
    "prompt",
    "prompt_sha256",
    "source_scope",
    "required_material",
    "target_chunk_id",
    "target_source_file",
    "review_focus",
    "expected_answerability",
    "machine_precheck_status",
    "reviewer_slot",
    "reviewer_id",
    "reviewer_role",
    "question_clear_1_0",
    "scope_correct_1_0",
    "answerability_correct_1_0",
    "evidence_reachable_1_0",
    "leakage_free_1_0",
    "validity_decision",
    "comments",
    "reviewed_utc",
]
ADJUDICATION_FIELDS = [
    "review_batch_id",
    "experiment_id",
    "question_id",
    "reviewer_1_decision",
    "reviewer_2_decision",
    "adjudication_required",
    "final_decision",
    "adjudicator_id",
    "adjudication_reason",
    "reviewed_utc",
]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def core_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 60:
        raise SystemExit(f"core question count {len(rows)} != 60")
    if len({row["题号"] for row in rows}) != 60:
        raise SystemExit("core question IDs are not unique")
    out = []
    for row in rows:
        stratum = row["组别"].strip()
        out.append(
            {
                "experiment_id": "E01_QA60_EGTS",
                "question_id": row["题号"].strip(),
                "stratum": stratum,
                "prompt": row["完整题目"].strip(),
                "prompt_sha256": sha(row["完整题目"].strip()),
                "source_scope": row["来源约束"].strip(),
                "required_material": row["必要材料"].strip(),
                "target_chunk_id": "",
                "target_source_file": "",
                "review_focus": row["审核证据要点"].strip(),
                "expected_answerability": (
                    "UNANSWERABLE" if stratum == "N" else "ANSWERABLE"
                ),
                "machine_precheck_status": "PASS_STRUCTURE_SOURCE_REVIEW_REQUIRED",
            }
        )
    return out


def supplementary_rows(path: Path) -> list[dict[str, str]]:
    rows = [
        json.loads(line)
        for line in path.open(encoding="utf-8")
        if line.strip()
    ]
    if len(rows) != 60:
        raise SystemExit(f"supplementary question count {len(rows)} != 60")
    if len({row["question_id"] for row in rows}) != 60:
        raise SystemExit("supplementary question IDs are not unique")
    if len({row["target_chunk_id"] for row in rows}) != 60:
        raise SystemExit("supplementary target chunk IDs are not unique")
    out = []
    for row in rows:
        if row.get("one_chunk_only") is not True:
            raise SystemExit(f"{row['question_id']} is not one-chunk-only")
        prompt = row["question"].strip()
        if row["target_chunk_id"] in prompt or row["target_source_file"] in prompt:
            raise SystemExit(f"{row['question_id']} leaks a target locator")
        out.append(
            {
                "experiment_id": "E01_SUPP_CHUNK_CITATION60",
                "question_id": row["question_id"],
                "stratum": row["target_corpus_id"],
                "prompt": prompt,
                "prompt_sha256": row["question_sha256"],
                "source_scope": f"仅本地:{row['target_corpus_id']}",
                "required_material": row["target_title"],
                "target_chunk_id": row["target_chunk_id"],
                "target_source_file": row["target_source_file"],
                "review_focus": row["anchor_for_audit"],
                "expected_answerability": "ANSWERABLE",
                "machine_precheck_status": "PASS_FROZEN_METADATA",
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-id", default="VALIDITY_20261104_V1")
    parser.add_argument(
        "--core",
        default="03_benchmarks/qa60/qa60_CANDIDATE.csv",
    )
    parser.add_argument(
        "--supplementary",
        default="03_benchmarks/citation60/questions_FROZEN.jsonl",
    )
    parser.add_argument(
        "--packet",
        default="07_scoring/validity/question_validity_review_WORKING.csv",
    )
    parser.add_argument(
        "--adjudication",
        default="07_scoring/validity/question_validity_adjudication_WORKING.csv",
    )
    args = parser.parse_args()

    packet_path = (ROOT / args.packet).resolve()
    adjudication_path = (ROOT / args.adjudication).resolve()
    if packet_path.exists() or adjudication_path.exists():
        raise SystemExit("refuse overwrite of validity packet or adjudication template")

    questions = core_rows((ROOT / args.core).resolve()) + supplementary_rows(
        (ROOT / args.supplementary).resolve()
    )
    if len(questions) != 120:
        raise SystemExit(f"formal question count {len(questions)} != 120")
    if len({(row["experiment_id"], row["question_id"]) for row in questions}) != 120:
        raise SystemExit("experiment/question IDs are not unique")

    packet_rows = []
    for question in questions:
        for slot in ("REVIEWER_1", "REVIEWER_2"):
            packet_rows.append(
                {
                    "review_batch_id": args.batch_id,
                    **question,
                    "reviewer_slot": slot,
                    "reviewer_id": "",
                    "reviewer_role": "GRADUATE_DOMAIN_REVIEWER",
                    "question_clear_1_0": "",
                    "scope_correct_1_0": "",
                    "answerability_correct_1_0": "",
                    "evidence_reachable_1_0": "",
                    "leakage_free_1_0": "",
                    "validity_decision": "",
                    "comments": "",
                    "reviewed_utc": "",
                }
            )
    if len(packet_rows) != 240:
        raise SystemExit(f"review row count {len(packet_rows)} != 240")

    packet_path.parent.mkdir(parents=True, exist_ok=True)
    with packet_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(packet_rows)

    with adjudication_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ADJUDICATION_FIELDS)
        writer.writeheader()
        for question in questions:
            writer.writerow(
                {
                    "review_batch_id": args.batch_id,
                    "experiment_id": question["experiment_id"],
                    "question_id": question["question_id"],
                    "reviewer_1_decision": "",
                    "reviewer_2_decision": "",
                    "adjudication_required": "",
                    "final_decision": "",
                    "adjudicator_id": "",
                    "adjudication_reason": "",
                    "reviewed_utc": "",
                }
            )

    print(
        json.dumps(
            {
                "status": "WRITTEN",
                "questions": len(questions),
                "review_rows": len(packet_rows),
                "packet": str(packet_path),
                "adjudication": str(adjudication_path),
                "note": "Only human reviewer decisions can complete validity certification.",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify 120 x 2 human validity reviews and create a write-once lock."""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path

from common import ROOT, utc

FINAL_FIELDS = [
    "review_batch_id",
    "experiment_id",
    "question_id",
    "stratum",
    "prompt_sha256",
    "source_scope",
    "target_chunk_id",
    "reviewer_1_id",
    "reviewer_2_id",
    "final_decision",
    "adjudicator_id",
    "review_packet_sha256",
    "adjudication_sha256",
    "validity_protocol_sha256",
    "locked_manifest_sha256",
    "locked_utc",
]
CHECKS = (
    "question_clear_1_0",
    "scope_correct_1_0",
    "answerability_correct_1_0",
    "evidence_reachable_1_0",
    "leakage_free_1_0",
)
IDENTITY_FIELDS = (
    "review_batch_id",
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
)


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def combined_sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--packet",
        default="07_scoring/validity/question_validity_review_WORKING.csv",
    )
    parser.add_argument(
        "--adjudication",
        default="07_scoring/validity/question_validity_adjudication_WORKING.csv",
    )
    parser.add_argument(
        "--protocol",
        default="03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md",
    )
    parser.add_argument(
        "--out",
        default="07_scoring/validity/question_validity_LOCKED.csv",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--lock", action="store_true")
    args = parser.parse_args()
    if not (args.check or args.lock):
        parser.error("choose --check or --lock")

    packet_path = (ROOT / args.packet).resolve()
    adjudication_path = (ROOT / args.adjudication).resolve()
    protocol_path = (ROOT / args.protocol).resolve()
    out_path = (ROOT / args.out).resolve()
    if not packet_path.is_file():
        raise SystemExit("validity review packet missing")
    if not adjudication_path.is_file():
        raise SystemExit("validity adjudication table missing")
    if not protocol_path.is_file():
        raise SystemExit("validity protocol missing")

    packet = rows(packet_path)
    adjudication = rows(adjudication_path)
    blocking: list[str] = []
    if len(packet) != 240:
        blocking.append(f"review rows {len(packet)} != 240")
    if len(adjudication) != 120:
        blocking.append(f"adjudication rows {len(adjudication)} != 120")

    by_question: dict[
        tuple[str, str], list[dict[str, str]]
    ] = collections.defaultdict(list)
    for row in packet:
        by_question[(row["experiment_id"], row["question_id"])].append(row)
    if len(by_question) != 120:
        blocking.append(f"question count {len(by_question)} != 120")
    experiment_counts = collections.Counter(
        experiment_id for experiment_id, _ in by_question
    )
    expected_experiments = {
        "E01_QA60_EGTS": 60,
        "E01_SUPP_CHUNK_CITATION60": 60,
    }
    if dict(experiment_counts) != expected_experiments:
        blocking.append(
            f"experiment/question counts {dict(experiment_counts)} "
            f"!= {expected_experiments}"
        )

    adj_map: dict[tuple[str, str], dict[str, str]] = {}
    for row in adjudication:
        key = (row["experiment_id"], row["question_id"])
        if key in adj_map:
            blocking.append(f"{key[1]}: duplicate adjudication row")
        adj_map[key] = row
    if set(adj_map) != set(by_question):
        blocking.append("adjudication questions do not exactly match review questions")

    packet_sha = sha256(packet_path)
    adjudication_sha = sha256(adjudication_path)
    protocol_sha = sha256(protocol_path)
    provenance_sha = combined_sha256(
        [packet_path, adjudication_path, protocol_path]
    )
    locked_utc = utc()
    results = []
    for key, reviews in sorted(by_question.items()):
        experiment_id, question_id = key
        if len(reviews) != 2:
            blocking.append(f"{question_id}: review count {len(reviews)} != 2")
            continue
        slot_map = {row["reviewer_slot"]: row for row in reviews}
        if set(slot_map) != {"REVIEWER_1", "REVIEWER_2"}:
            blocking.append(f"{question_id}: invalid reviewer slots")
            continue
        first = slot_map["REVIEWER_1"]
        second = slot_map["REVIEWER_2"]

        for field in IDENTITY_FIELDS:
            if first[field] != second[field]:
                blocking.append(
                    f"{question_id}: reviewer packet differs at {field}"
                )
        prompt_hash = hashlib.sha256(
            first["prompt"].strip().encode("utf-8")
        ).hexdigest()
        if prompt_hash != first["prompt_sha256"]:
            blocking.append(f"{question_id}: prompt SHA mismatch")
        if not first["review_batch_id"].strip():
            blocking.append(f"{question_id}: review_batch_id blank")

        decisions: list[str] = []
        for label, review in (("REVIEWER_1", first), ("REVIEWER_2", second)):
            if not review["reviewer_id"].strip():
                blocking.append(f"{question_id}/{label}: reviewer_id blank")
            values = []
            for field in CHECKS:
                value = review[field].strip()
                if value not in {"0", "1"}:
                    blocking.append(
                        f"{question_id}/{label}: {field} not 0/1"
                    )
                values.append(value)
            decision = review["validity_decision"].strip().upper()
            if decision not in {"ACCEPT", "REVISE", "REJECT"}:
                blocking.append(f"{question_id}/{label}: invalid decision")
            if decision == "ACCEPT" and values != ["1"] * len(CHECKS):
                blocking.append(
                    f"{question_id}/{label}: ACCEPT requires all five checks = 1"
                )
            if decision in {"REVISE", "REJECT"} and values == ["1"] * len(CHECKS):
                blocking.append(
                    f"{question_id}/{label}: {decision} conflicts with all checks = 1"
                )
            if not review["reviewed_utc"].strip():
                blocking.append(f"{question_id}/{label}: reviewed_utc blank")
            decisions.append(decision)
        if (
            first["reviewer_id"].strip()
            and first["reviewer_id"].strip().casefold()
            == second["reviewer_id"].strip().casefold()
        ):
            blocking.append(f"{question_id}: reviewer IDs must be independent")

        adjudication_row = adj_map.get(key)
        if adjudication_row is None:
            blocking.append(f"{question_id}: adjudication row missing")
            continue
        if (
            adjudication_row["reviewer_1_decision"].strip().upper()
            != decisions[0]
        ):
            blocking.append(f"{question_id}: adjudication reviewer_1 mismatch")
        if (
            adjudication_row["reviewer_2_decision"].strip().upper()
            != decisions[1]
        ):
            blocking.append(f"{question_id}: adjudication reviewer_2 mismatch")

        if decisions == ["ACCEPT", "ACCEPT"]:
            required = adjudication_row["adjudication_required"].strip().upper()
            if required not in {"", "NO"}:
                blocking.append(
                    f"{question_id}: agreeing ACCEPT should not require adjudication"
                )
            final = "ACCEPT"
            adjudicator_id = ""
        else:
            required = adjudication_row["adjudication_required"].strip().upper()
            if required != "YES":
                blocking.append(
                    f"{question_id}: disagreement must set adjudication_required=YES"
                )
            final = adjudication_row["final_decision"].strip().upper()
            if final not in {"ACCEPT", "REVISE", "REJECT"}:
                blocking.append(
                    f"{question_id}: final adjudicated decision required"
                )
            adjudicator_id = adjudication_row["adjudicator_id"].strip()
            if not adjudicator_id:
                blocking.append(f"{question_id}: adjudicator_id blank")
            if not adjudication_row["adjudication_reason"].strip():
                blocking.append(f"{question_id}: adjudication_reason blank")
            if not adjudication_row["reviewed_utc"].strip():
                blocking.append(f"{question_id}: adjudication reviewed_utc blank")
        if final != "ACCEPT":
            blocking.append(
                f"{question_id}: final decision {final or 'BLANK'} is not ACCEPT"
            )

        results.append(
            {
                "review_batch_id": first["review_batch_id"],
                "experiment_id": experiment_id,
                "question_id": question_id,
                "stratum": first["stratum"],
                "prompt_sha256": first["prompt_sha256"],
                "source_scope": first["source_scope"],
                "target_chunk_id": first["target_chunk_id"],
                "reviewer_1_id": first["reviewer_id"].strip(),
                "reviewer_2_id": second["reviewer_id"].strip(),
                "final_decision": final,
                "adjudicator_id": adjudicator_id,
                "review_packet_sha256": packet_sha,
                "adjudication_sha256": adjudication_sha,
                "validity_protocol_sha256": protocol_sha,
                "locked_manifest_sha256": provenance_sha,
                "locked_utc": locked_utc,
            }
        )

    report = {
        "status": "PASS"
        if not blocking and len(results) == 120
        else "BLOCKED",
        "questions": len(by_question),
        "accepted_questions": len(results)
        if not blocking
        else sum(row["final_decision"] == "ACCEPT" for row in results),
        "blocking_count": len(blocking),
        "blocking": blocking[:100],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.check:
        return 0 if report["status"] == "PASS" else 2
    if report["status"] != "PASS":
        return 2
    if out_path.exists():
        raise SystemExit("refuse overwrite of validity lock")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FINAL_FIELDS)
        writer.writeheader()
        writer.writerows(results)
    print(f"LOCKED {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

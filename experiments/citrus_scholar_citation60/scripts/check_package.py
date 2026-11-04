#!/usr/bin/env python3
"""Offline package integrity checks. No service, network or model use."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

from common import ROOT, csvrows, sha256_file, utc


def jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.open(encoding="utf-8")
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-private-corpus",
        action="store_true",
        help="fail unless the excluded licensed chunk file is restored and matches its SHA",
    )
    args = parser.parse_args()
    blockers: list[str] = []
    skipped: list[str] = []

    questions = csvrows("03_benchmarks/qa60/qa60_CANDIDATE.csv")
    intents = csvrows("03_benchmarks/intent/intent192_CANDIDATE.csv")
    composites = csvrows("03_benchmarks/intent/composite24_CANDIDATE.csv")
    validity = csvrows(
        "07_scoring/validity/question_validity_review_packet.csv"
    )
    adjudication = csvrows(
        "07_scoring/validity/question_validity_adjudication_TEMPLATE.csv"
    )
    judge_template = csvrows("07_scoring/judge/qa_judge_TEMPLATE_180.csv")
    citation_questions = jsonl(
        ROOT / "03_benchmarks/citation60/questions_FROZEN.jsonl"
    )
    citation_plan = csvrows("06_runs/manifests/citation_run_plan.csv")

    qa_counts = collections.Counter(row["组别"] for row in questions)
    if qa_counts != {"L": 12, "V": 10, "W": 10, "X": 12, "P": 8, "N": 8}:
        blockers.append(f"core QA stratum counts are wrong: {qa_counts}")
    if len(questions) != 60 or len({row["题号"] for row in questions}) != 60:
        blockers.append("core QA must contain 60 unique question IDs")
    if any(
        "gold" in column.lower() or "answer" in column.lower()
        for column in questions[0]
    ):
        blockers.append("core QA candidate still exposes a Gold/Answer column")

    route_counts = collections.Counter(row["target_route"] for row in intents)
    if len(intents) != 192 or len({row["case_id"] for row in intents}) != 192:
        blockers.append("routing set must contain 192 unique cases")
    if len(route_counts) != 8 or set(route_counts.values()) != {24}:
        blockers.append(f"routing classes are not eight x 24: {route_counts}")
    if len(composites) != 24 or len({row["case_id"] for row in composites}) != 24:
        blockers.append("composite set must contain 24 unique cases")

    experiments = {"E01_QA60_EGTS", "E01_SUPP_CHUNK_CITATION60"}
    if len(validity) != 240:
        blockers.append(f"validity packet rows {len(validity)} != 240")
    review_slots: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for row in validity:
        review_slots[(row["experiment_id"], row["question_id"])].add(
            row["reviewer_slot"]
        )
        if row["experiment_id"] not in experiments:
            blockers.append(f"unknown validity experiment: {row['experiment_id']}")
    if len(review_slots) != 120:
        blockers.append(f"validity packet questions {len(review_slots)} != 120")
    if any(
        slots != {"REVIEWER_1", "REVIEWER_2"}
        for slots in review_slots.values()
    ):
        blockers.append("each formal question must have reviewer slots 1 and 2")
    if {row["experiment_id"] for row in validity} != experiments:
        blockers.append("validity packet does not cover both formal experiments")

    adjudication_keys = {
        (row["experiment_id"], row["question_id"]) for row in adjudication
    }
    if len(adjudication) != 120 or len(adjudication_keys) != 120:
        blockers.append("validity adjudication template must contain 120 unique rows")
    if adjudication_keys != set(review_slots):
        blockers.append("validity adjudication questions do not match review packet")

    judge_keys = {(row["question_id"], row["condition"]) for row in judge_template}
    if len(judge_template) != 180 or len(judge_keys) != 180:
        blockers.append("core-QA judge template must contain 180 unique rows")
    if {row["condition"] for row in judge_template} != {"B0", "B1", "B2"}:
        blockers.append("core-QA judge template conditions are not B0/B1/B2")

    if len(citation_questions) != 60:
        blockers.append(
            f"supplementary questions {len(citation_questions)} != 60"
        )
    citation_ids = {row["question_id"] for row in citation_questions}
    if len(citation_ids) != 60:
        blockers.append("supplementary question IDs are not unique")
    core_ids = {row["题号"] for row in questions}
    expected_validity_keys = {
        ("E01_QA60_EGTS", question_id) for question_id in core_ids
    } | {
        ("E01_SUPP_CHUNK_CITATION60", question_id)
        for question_id in citation_ids
    }
    if set(review_slots) != expected_validity_keys:
        blockers.append(
            "validity packet does not exactly cover the 120 formal questions"
        )
    target_ids = {row["target_chunk_id"] for row in citation_questions}
    if len(target_ids) != 60:
        blockers.append("supplementary target chunk IDs are not unique")
    for row in citation_questions:
        if row.get("one_chunk_only") is not True:
            blockers.append(f"{row['question_id']}: one_chunk_only is not true")
        prompt_hash = hashlib.sha256(
            row["question"].strip().encode("utf-8")
        ).hexdigest()
        if prompt_hash != row["question_sha256"]:
            blockers.append(f"{row['question_id']}: question SHA mismatch")
        if row["target_chunk_id"] in row["question"]:
            blockers.append(f"{row['question_id']}: prompt leaks target chunk ID")
        if row["target_source_file"] in row["question"]:
            blockers.append(f"{row['question_id']}: prompt leaks source file")
        if "chunk_text" in row:
            blockers.append(f"{row['question_id']}: public question leaks chunk text")

    forbidden_plan_fields = {
        "target_chunk_id",
        "target_paper_id",
        "target_source_file",
        "chunk_text",
        "anchor_for_audit",
    }
    if citation_plan:
        leaked = forbidden_plan_fields.intersection(citation_plan[0])
        if leaked:
            blockers.append(f"citation run plan leaks fields: {sorted(leaked)}")
    if len(citation_plan) != 180:
        blockers.append(f"citation run plan rows {len(citation_plan)} != 180")
    plan_conditions: dict[str, set[str]] = collections.defaultdict(set)
    question_map = {row["question_id"]: row for row in citation_questions}
    for row in citation_plan:
        plan_conditions[row["case_id"]].add(row["condition"])
        question = question_map.get(row["case_id"])
        if question is None:
            blockers.append(f"{row['run_id']}: unknown citation case")
            continue
        if row["source_scope"] != f"仅本地:{question['target_corpus_id']}":
            blockers.append(f"{row['run_id']}: source scope mismatch")
        prompt = row.get("prompt") or ""
        for locator in (
            question["target_chunk_id"],
            question["target_source_file"],
            question["target_paper_id"],
        ):
            if locator and locator in prompt:
                blockers.append(f"{row['run_id']}: prompt leaks a target locator")
    if set(plan_conditions) != citation_ids:
        blockers.append("citation run-plan case IDs do not match frozen questions")
    if any(
        conditions != {"B0", "B1", "B2"}
        for conditions in plan_conditions.values()
    ):
        blockers.append("each citation question must have exactly B0/B1/B2")

    question_lock_path = (
        ROOT / "03_benchmarks/citation60/questions_FROZEN.manifest.json"
    )
    question_lock = json.loads(question_lock_path.read_text(encoding="utf-8"))
    if question_lock.get("status") != "FROZEN":
        blockers.append("citation question manifest is not FROZEN")
    question_path = ROOT / "03_benchmarks/citation60/questions_FROZEN.jsonl"
    question_manifest_path = ROOT / "03_benchmarks/citation60/question_manifest.csv"
    if question_lock.get("questions_frozen_sha256") != sha256_file(question_path):
        blockers.append("questions_FROZEN.jsonl differs from its manifest")
    if question_lock.get("question_manifest_sha256") != sha256_file(
        question_manifest_path
    ):
        blockers.append("question_manifest.csv differs from its manifest")

    semantic_meta = json.loads(
        (
            ROOT
            / "06_runs/manifests/citation_semantic_audit_plan_meta.json"
        ).read_text(encoding="utf-8")
    )
    if semantic_meta.get("status") != "DISABLED_BY_DESIGN":
        blockers.append("semantic-audit provenance is not marked disabled")
    semantic_plan = ROOT / "06_runs/manifests/citation_semantic_audit_plan.csv"
    if semantic_meta.get("audit_plan_sha256") != sha256_file(semantic_plan):
        blockers.append("disabled semantic-audit plan differs from provenance")

    chunks_path = ROOT / "03_benchmarks/citation60/chunks_FROZEN.jsonl"
    private_state = "PRESENT_VERIFIED"
    if not chunks_path.is_file():
        private_state = "PRIVATE_CORPUS_REQUIRED"
        message = (
            "licensed chunks_FROZEN.jsonl is not in the public package; "
            "restore it to verify chunk hashes and targets"
        )
        if args.require_private_corpus:
            blockers.append(message)
        else:
            skipped.append(message)
    else:
        chunks = jsonl(chunks_path)
        if question_lock.get("chunks_frozen_sha256") != sha256_file(chunks_path):
            blockers.append("chunks_FROZEN.jsonl differs from its manifest")
        if len(chunks) != 60:
            blockers.append(f"citation chunks {len(chunks)} != 60")
        chunk_map = {row["citation_id"]: row for row in chunks}
        if len(chunk_map) != 60:
            blockers.append("citation chunk IDs are not unique")
        for row in citation_questions:
            chunk = chunk_map.get(row["question_id"])
            if chunk is None:
                blockers.append(f"{row['question_id']}: frozen chunk is missing")
                continue
            if row["target_chunk_id"] != chunk["chunk_id"]:
                blockers.append(f"{row['question_id']}: target chunk mismatch")
            if row["target_chunk_text_sha256"] != chunk["chunk_text_sha256"]:
                blockers.append(f"{row['question_id']}: chunk text SHA mismatch")
            if row["target_source_file"] != chunk["source_file"]:
                blockers.append(f"{row['question_id']}: source file mismatch")
        corpus_counts = collections.Counter(row["corpus_id"] for row in chunks)
        if set(corpus_counts.values()) != {12} or len(corpus_counts) != 5:
            blockers.append(f"citation sampling is not five x 12: {corpus_counts}")

    status = (
        "FAIL"
        if blockers
        else "PASS_WITH_PRIVATE_CORPUS_PENDING"
        if skipped
        else "PASS"
    )
    print(
        json.dumps(
            {
                "status": status,
                "core_questions": len(questions),
                "qa_strata": qa_counts,
                "validity_questions": len(review_slots),
                "validity_review_rows": len(validity),
                "judge_rows": len(judge_template),
                "intent_cases": len(intents),
                "intent_class_counts": route_counts,
                "composite_cases": len(composites),
                "workflow_protocols": len(
                    list((ROOT / "08_workflows").glob("WF*_protocol.json"))
                ),
                "fusion_fixture_sha256": sha256_file(
                    ROOT
                    / "04_fixtures/fusion_WF07/synthetic_leaf_qPCR_WF07.xlsx"
                ),
                "citation60": {
                    "questions": len(citation_questions),
                    "run_rows": len(citation_plan),
                    "questions_sha256": sha256_file(question_path),
                    "private_chunks": private_state,
                },
                "semantic_support_reported": False,
                "skipped": skipped,
                "blockers": blockers,
                "checked_utc": utc(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())

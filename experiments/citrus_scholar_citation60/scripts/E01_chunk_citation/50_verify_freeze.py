#!/usr/bin/env python3
"""Verify public frozen metadata and, when licensed, private chunk text."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]
PRIVATE_FIELDS = {
    "target_chunk_id",
    "target_paper_id",
    "target_source_file",
    "chunk_text",
    "anchor_for_audit",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.open(encoding="utf-8")
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-dir", default="03_benchmarks/citation60")
    parser.add_argument("--run-plan", default="06_runs/manifests/citation_run_plan.csv")
    parser.add_argument(
        "--semantic-plan",
        default="06_runs/manifests/citation_semantic_audit_plan.csv",
    )
    parser.add_argument(
        "--semantic-meta",
        default="06_runs/manifests/citation_semantic_audit_plan_meta.json",
    )
    parser.add_argument(
        "--require-private-corpus",
        action="store_true",
        help="fail unless chunks_FROZEN.jsonl is restored and matches its SHA",
    )
    args = parser.parse_args()

    bench = (ROOT / args.benchmark_dir).resolve()
    chunks_path = bench / "chunks_FROZEN.jsonl"
    questions_path = bench / "questions_FROZEN.jsonl"
    question_lock_path = bench / "questions_FROZEN.manifest.json"
    question_manifest_path = bench / "question_manifest.csv"
    questions = jsonl(questions_path)
    question_lock = json.loads(question_lock_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    skipped: list[str] = []

    if len(questions) != 60:
        errors.append(f"question count {len(questions)} != 60")
    if question_lock.get("status") != "FROZEN":
        errors.append("question freeze manifest status is not FROZEN")
    if question_lock.get("question_count") != 60:
        errors.append("question freeze manifest count is not 60")
    if question_lock.get("one_chunk_per_question") is not True:
        errors.append("question freeze manifest is not one-chunk-only")
    if question_lock.get("questions_frozen_sha256") != sha(questions_path):
        errors.append("questions_FROZEN.jsonl differs from question freeze manifest")
    if question_lock.get("question_manifest_sha256") != sha(question_manifest_path):
        errors.append("question_manifest.csv differs from question freeze manifest")

    qids = [row["question_id"] for row in questions]
    target_ids = [row["target_chunk_id"] for row in questions]
    if len(qids) != len(set(qids)):
        errors.append("duplicate question_id")
    if len(target_ids) != len(set(target_ids)):
        errors.append("target chunk IDs are not unique")
    question_map = {row["question_id"]: row for row in questions}
    for question in questions:
        if question.get("one_chunk_only") is not True:
            errors.append(f"{question['question_id']}: one_chunk_only is not true")
        prompt = question["question"].strip()
        if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != question["question_sha256"]:
            errors.append(f"{question['question_id']}: question SHA mismatch")
        for locator in (
            question["target_chunk_id"],
            question["target_source_file"],
            question["target_paper_id"],
        ):
            if locator and locator in prompt:
                errors.append(f"{question['question_id']}: prompt leaks target locator")
        if "chunk_text" in question:
            errors.append(f"{question['question_id']}: public question leaks chunk text")

    plan_path = ROOT / args.run_plan
    plan_rows = 0
    plan_conditions: dict[str, set[str]] = {}
    if not plan_path.is_file():
        errors.append("run plan missing")
    else:
        with plan_path.open(encoding="utf-8-sig", newline="") as handle:
            plan = list(csv.DictReader(handle))
        plan_rows = len(plan)
        if len(plan) != 180:
            errors.append(f"run plan rows {len(plan)} != 180")
        if plan and PRIVATE_FIELDS.intersection(plan[0]):
            errors.append("run plan leaks private target fields")
        for row in plan:
            question = question_map.get(row["case_id"])
            if question is None:
                errors.append(f"run plan unknown case {row['case_id']}")
                continue
            plan_conditions.setdefault(row["case_id"], set()).add(row["condition"])
            expected_scope = f"仅本地:{question['target_corpus_id']}"
            if row["source_scope"] != expected_scope:
                errors.append(f"{row['run_id']}: source scope mismatch")
            prompt = row.get("prompt") or ""
            for locator in (
                question["target_chunk_id"],
                question["target_source_file"],
                question["target_paper_id"],
            ):
                if locator and locator in prompt:
                    errors.append(f"{row['run_id']}: prompt leaks target locator")
        if set(plan_conditions) != set(qids):
            errors.append("run plan case IDs do not match frozen questions")
        for case_id in sorted(question_map):
            if plan_conditions.get(case_id) != {"B0", "B1", "B2"}:
                errors.append(f"{case_id}: conditions are not exactly B0/B1/B2")

    semantic_meta_path = ROOT / args.semantic_meta
    semantic_plan_path = ROOT / args.semantic_plan
    if not semantic_meta_path.is_file():
        errors.append("disabled semantic-audit provenance is missing")
    else:
        semantic_meta = json.loads(
            semantic_meta_path.read_text(encoding="utf-8")
        )
        if semantic_meta.get("status") != "DISABLED_BY_DESIGN":
            errors.append("semantic-audit provenance is not marked disabled")
        if not semantic_plan_path.is_file():
            errors.append("disabled semantic-audit source record is missing")
        elif semantic_meta.get("audit_plan_sha256") != sha(semantic_plan_path):
            errors.append("disabled semantic-audit source record SHA mismatch")

    private_state = "PRESENT_VERIFIED"
    corpus_counts: Counter[str] = Counter()
    if not chunks_path.is_file():
        private_state = "PRIVATE_CORPUS_REQUIRED"
        message = (
            "licensed chunks_FROZEN.jsonl is not public; restore it to verify "
            "chunk text, source locators and target IDs"
        )
        if args.require_private_corpus:
            errors.append(message)
        else:
            skipped.append(message)
    else:
        chunks = jsonl(chunks_path)
        if question_lock.get("chunks_frozen_sha256") != sha(chunks_path):
            errors.append("chunks_FROZEN.jsonl differs from question freeze manifest")
        if len(chunks) != 60:
            errors.append(f"chunk count {len(chunks)} != 60")
        chunk_map = {row["citation_id"]: row for row in chunks}
        if len(chunk_map) != 60:
            errors.append("duplicate citation_id in chunks")
        if set(qids) != set(chunk_map):
            errors.append("question IDs do not match chunk IDs")
        for question in questions:
            chunk = chunk_map.get(question["question_id"])
            if chunk is None:
                continue
            if question["target_chunk_id"] != chunk["chunk_id"]:
                errors.append(f"{question['question_id']}: target chunk mismatch")
            if question["target_chunk_text_sha256"] != chunk["chunk_text_sha256"]:
                errors.append(f"{question['question_id']}: chunk hash mismatch")
            if question["target_source_file"] != chunk["source_file"]:
                errors.append(f"{question['question_id']}: source file mismatch")
        corpus_counts = Counter(row["corpus_id"] for row in chunks)
        if len(corpus_counts) != 5 or set(corpus_counts.values()) != {12}:
            errors.append(f"chunk sampling is not five x 12: {corpus_counts}")

    status = (
        "FAIL"
        if errors
        else "PASS_WITH_PRIVATE_CORPUS_PENDING"
        if skipped
        else "PASS"
    )
    print(
        json.dumps(
            {
                "status": status,
                "chunks": len(chunk_map) if chunks_path.is_file() else None,
                "questions": len(questions),
                "one_chunk_per_question": all(
                    row.get("one_chunk_only") is True for row in questions
                ),
                "corpus_counts": dict(corpus_counts),
                "chunks_sha256": sha(chunks_path) if chunks_path.is_file() else None,
                "questions_sha256": sha(questions_path),
                "run_plan_rows": plan_rows,
                "semantic_support_reported": False,
                "private_chunks": private_state,
                "skipped": skipped,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())

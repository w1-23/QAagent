#!/usr/bin/env python3
"""Verify the frozen chunks, questions, and run-plan leakage boundary."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-dir", default="03_benchmarks/citation60")
    parser.add_argument("--run-plan", default="06_runs/manifests/citation_run_plan.csv")
    parser.add_argument(
        "--semantic-plan",
        default="06_runs/manifests/citation_semantic_audit_plan.csv",
    )
    args = parser.parse_args()
    bench = (ROOT / args.benchmark_dir).resolve()
    chunks_path = bench / "chunks_FROZEN.jsonl"
    questions_path = bench / "questions_FROZEN.jsonl"
    question_lock_path = bench / "questions_FROZEN.manifest.json"
    chunks = [json.loads(line) for line in chunks_path.open("r", encoding="utf-8") if line.strip()]
    questions = [json.loads(line) for line in questions_path.open("r", encoding="utf-8") if line.strip()]
    errors: list[str] = []
    if len(chunks) != 60:
        errors.append(f"chunk count {len(chunks)} != 60")
    if len(questions) != 60:
        errors.append(f"question count {len(questions)} != 60")
    if not question_lock_path.is_file():
        errors.append("question freeze manifest missing")
    else:
        question_lock = json.loads(question_lock_path.read_text(encoding="utf-8"))
        if question_lock.get("chunks_frozen_sha256") != sha(chunks_path):
            errors.append("chunks_FROZEN.jsonl differs from question freeze manifest")
        if question_lock.get("questions_frozen_sha256") != sha(questions_path):
            errors.append("questions_FROZEN.jsonl differs from question freeze manifest")
    chunk_map = {row["citation_id"]: row for row in chunks}
    if len(chunks) != len(chunk_map):
        errors.append("duplicate citation_id in chunks")
    qids = [q["question_id"] for q in questions]
    if len(qids) != len(set(qids)):
        errors.append("duplicate question_id")
    if set(qids) != set(chunk_map):
        errors.append("question IDs do not match chunk IDs")
    for q in questions:
        c = chunk_map.get(q["question_id"])
        if not c:
            continue
        if q["target_chunk_id"] != c["chunk_id"]:
            errors.append(f"{q['question_id']}: target chunk mismatch")
        if q["target_chunk_text_sha256"] != c["chunk_text_sha256"]:
            errors.append(f"{q['question_id']}: chunk hash mismatch")
        if not q.get("one_chunk_only"):
            errors.append(f"{q['question_id']}: one_chunk_only is not true")
    plan_path = ROOT / args.run_plan
    plan_rows = 0
    if plan_path.exists():
        with plan_path.open(encoding="utf-8-sig", newline="") as f:
            plan = list(csv.DictReader(f))
        plan_rows = len(plan)
        if len(plan) != 180:
            errors.append(f"run plan rows {len(plan)} != 180")
        forbidden_runtime_fields = {"target_chunk_id", "target_paper_id", "chunk_text", "anchor_for_audit"}
        if forbidden_runtime_fields & set(plan[0]):
            errors.append("run plan leaks target/Gold fields")
        condition_sets: dict[str, set[str]] = {}
        for row in plan:
            if row["case_id"] not in chunk_map:
                errors.append(f"run plan unknown case {row['case_id']}")
                continue
            condition_sets.setdefault(row["case_id"], set()).add(row["condition"])
            if row["source_scope"] != f"仅本地:{chunk_map[row['case_id']]['corpus_id']}":
                errors.append(f"{row['run_id']}: source scope mismatch")
            target = chunk_map[row["case_id"]]
            prompt = row.get("prompt") or ""
            if target["chunk_id"] and target["chunk_id"] in prompt:
                errors.append(f"{row['run_id']}: prompt leaks chunk_id")
            if target["source_file"] and target["source_file"] in prompt:
                errors.append(f"{row['run_id']}: prompt leaks source_file")
            if target["paper_id"] and target["paper_id"] in prompt:
                errors.append(f"{row['run_id']}: prompt leaks paper_id")
        for case_id in sorted(chunk_map):
            if condition_sets.get(case_id) != {"B0", "B1", "B2"}:
                errors.append(f"{case_id}: conditions are not exactly B0/B1/B2")
    report = {
        "status": "PASS" if not errors else "FAIL",
        "chunks": len(chunks),
        "questions": len(questions),
        "one_chunk_per_question": all(q.get("one_chunk_only") is True for q in questions),
        "corpus_counts": dict(Counter(c["corpus_id"] for c in chunks)),
        "chunks_sha256": sha(chunks_path),
        "questions_sha256": sha(questions_path),
        "run_plan_rows": plan_rows,
        "semantic_audit_rows": 0,
        "semantic_audit_questions": 0,
        "errors": errors,
    }
    semantic_path = ROOT / args.semantic_plan
    if not semantic_path.is_file():
        errors.append(f"semantic audit plan missing: {semantic_path}")
    else:
        with semantic_path.open(encoding="utf-8-sig", newline="") as handle:
            semantic_rows = list(csv.DictReader(handle))
        semantic_questions = {row["case_id"] for row in semantic_rows}
        semantic_counts = Counter(
            chunk_map[question_id]["corpus_id"]
            for question_id in semantic_questions
            if question_id in chunk_map
        )
        report["semantic_audit_rows"] = len(semantic_rows)
        report["semantic_audit_questions"] = len(semantic_questions)
        if len(semantic_rows) != 60:
            errors.append(f"semantic audit rows {len(semantic_rows)} != 60")
        if len(semantic_questions) != 20:
            errors.append(
                f"semantic audit questions {len(semantic_questions)} != 20"
            )
        if set(semantic_counts.values()) != {4}:
            errors.append(f"semantic audit is not four questions per corpus: {semantic_counts}")
        forbidden = {"target_chunk_id", "target_paper_id", "chunk_text", "anchor_for_audit"}
        if semantic_rows and forbidden.intersection(semantic_rows[0]):
            errors.append("semantic audit plan leaks target/Gold fields")
    report["status"] = "PASS" if not errors else "FAIL"
    report["errors"] = errors
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())


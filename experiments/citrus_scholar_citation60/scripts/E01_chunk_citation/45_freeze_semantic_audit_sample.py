#!/usr/bin/env python3
"""Freeze the optional 60-run semantic-support audit sample before results."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
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
        "--out", default="06_runs/manifests/citation_semantic_audit_plan.csv"
    )
    parser.add_argument(
        "--meta", default="06_runs/manifests/citation_semantic_audit_plan_meta.json"
    )
    parser.add_argument("--per-corpus", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261104)
    args = parser.parse_args()
    if args.per_corpus < 1:
        raise SystemExit("--per-corpus must be >= 1")

    bench = (ROOT / args.benchmark_dir).resolve()
    questions_path = bench / "questions_FROZEN.jsonl"
    run_plan_path = (ROOT / args.run_plan).resolve()
    out = (ROOT / args.out).resolve()
    meta_path = (ROOT / args.meta).resolve()
    if out.exists() or meta_path.exists():
        raise SystemExit("refuse overwrite of semantic audit plan/meta")
    if not questions_path.is_file() or not run_plan_path.is_file():
        raise SystemExit("frozen questions or run plan missing")

    questions = [
        json.loads(line)
        for line in questions_path.open(encoding="utf-8")
        if line.strip()
    ]
    with run_plan_path.open(encoding="utf-8-sig", newline="") as handle:
        runs = list(csv.DictReader(handle))
    by_corpus: dict[str, list[dict]] = {}
    for question in questions:
        by_corpus.setdefault(question["target_corpus_id"], []).append(question)

    selected_question_ids: set[str] = set()
    selected_counts: dict[str, int] = {}
    for corpus_id in sorted(by_corpus):
        ordered = sorted(by_corpus[corpus_id], key=lambda row: row["question_id"])
        if len(ordered) < args.per_corpus:
            raise SystemExit(
                f"not enough questions in {corpus_id}: "
                f"{len(ordered)} < {args.per_corpus}"
            )
        chosen = random.Random(f"{args.seed}:semantic:{corpus_id}").sample(
            ordered, args.per_corpus
        )
        selected_question_ids.update(row["question_id"] for row in chosen)
        selected_counts[corpus_id] = len(chosen)

    selected_runs = [
        row for row in runs
        if row["case_id"] in selected_question_ids
        and row["condition"] in {"B0", "B1", "B2"}
    ]
    expected = len(selected_question_ids) * 3
    if len(selected_runs) != expected:
        raise SystemExit(
            f"selected run count {len(selected_runs)} != expected {expected}"
        )
    random.Random(f"{args.seed}:semantic-run-order").shuffle(selected_runs)
    fields = [
        "run_id",
        "case_id",
        "condition",
        "trial",
        "run_order",
        "prompt",
        "source_scope",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in selected_runs:
            writer.writerow({key: row.get(key, "") for key in fields})
    meta = {
        "status": "FROZEN_NOT_RUN",
        "seed": args.seed,
        "per_corpus_questions": args.per_corpus,
        "selected_questions": len(selected_question_ids),
        "selected_runs": len(selected_runs),
        "selected_question_counts": selected_counts,
        "questions_sha256": sha(questions_path),
        "run_plan_sha256": sha(run_plan_path),
        "audit_plan_sha256": sha(out),
        "runtime_target_leakage": False,
        "note": (
            "This sample exists only to make optional semantic-support review "
            "auditable. Automatic target-chunk citation remains the primary endpoint."
        ),
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

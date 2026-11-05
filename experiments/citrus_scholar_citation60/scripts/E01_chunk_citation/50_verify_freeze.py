#!/usr/bin/env python3
"""Verify the complete frozen chunk, question and run-plan package."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import argparse
from collections import Counter
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]
SCORING_ONLY_FIELDS = {
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
    parser.add_argument("--require-l1", action="store_true")
    args = parser.parse_args()
    bench = (ROOT / "03_benchmarks/citation60").resolve()
    chunks_path = bench / "chunks_FROZEN.jsonl"
    questions_path = bench / "questions_FROZEN.jsonl"
    question_lock_path = bench / "questions_FROZEN.manifest.json"
    question_manifest_path = bench / "question_manifest.csv"
    questions = jsonl(questions_path)
    question_lock = json.loads(question_lock_path.read_text(encoding="utf-8"))
    errors: list[str] = []

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

    plan_path = ROOT / "06_runs/manifests/citation_run_plan.csv"
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
        if plan and SCORING_ONLY_FIELDS.intersection(plan[0]):
            errors.append("run plan leaks scoring-only target fields")
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

    semantic_meta_path = ROOT / "06_runs/manifests/citation_semantic_audit_plan_meta.json"
    semantic_plan_path = ROOT / "06_runs/manifests/citation_semantic_audit_plan.csv"
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

    l1_manifest_path = ROOT / "12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json"
    l1_manifest = json.loads(l1_manifest_path.read_text(encoding="utf-8"))
    protected_assets = {
        row["former_public_path"]: row for row in l1_manifest.get("assets", [])
    }
    chunk_asset = protected_assets.get(
        "03_benchmarks/citation60/chunks_FROZEN.jsonl"
    )
    if not chunk_asset:
        errors.append("L1 manifest lacks chunks_FROZEN.jsonl")
    elif question_lock.get("chunks_frozen_sha256") != chunk_asset.get("sha256"):
        errors.append("question lock chunk SHA differs from L1 manifest")

    chunks_state = "PROTECTED_L1_ABSENT_REPOSITORY_TREE"
    if chunks_path.exists():
        chunks_state = "PROTECTED_L1_PRESENT_IN_REPOSITORY_TREE"
        errors.append("L1 chunks_FROZEN.jsonl is present in the repository tree")

    with (bench / "chunk_manifest.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        chunk_metadata = list(csv.DictReader(handle))
    if len(chunk_metadata) != 60:
        errors.append(f"chunk metadata count {len(chunk_metadata)} != 60")
    chunk_map = {row["citation_id"]: row for row in chunk_metadata}
    if len(chunk_map) != 60:
        errors.append("duplicate citation_id in chunk metadata")
    if set(qids) != set(chunk_map):
        errors.append("question IDs do not match chunk metadata IDs")
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
    corpus_counts = Counter(row["corpus_id"] for row in chunk_metadata)
    if len(corpus_counts) != 5 or set(corpus_counts.values()) != {12}:
        errors.append(f"chunk sampling is not five x 12: {corpus_counts}")

    l1_verified = False
    l1_root = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    protected_chunks = (
        Path(l1_root) / "03_benchmarks/citation60/chunks_FROZEN.jsonl"
        if l1_root
        else None
    )
    if args.require_l1 and protected_chunks is None:
        errors.append("--require-l1 needs CITRUS_EXPERIMENT_L1_ROOT")
    if protected_chunks is not None:
        if not protected_chunks.is_file():
            errors.append(f"protected L1 file missing: {protected_chunks}")
        elif chunk_asset and sha(protected_chunks) != chunk_asset.get("sha256"):
            errors.append("protected L1 chunk SHA mismatch")
        else:
            l1_verified = True

    status = "FAIL" if errors else "PASS"
    print(
        json.dumps(
            {
                "status": status,
                "chunk_metadata_rows": len(chunk_map),
                "questions": len(questions),
                "one_chunk_per_question": all(
                    row.get("one_chunk_only") is True for row in questions
                ),
                "corpus_counts": dict(corpus_counts),
                "chunks_sha256": chunk_asset.get("sha256") if chunk_asset else None,
                "questions_sha256": sha(questions_path),
                "run_plan_rows": plan_rows,
                "semantic_support_reported": False,
                "chunks": chunks_state,
                "l1_verified": l1_verified,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())

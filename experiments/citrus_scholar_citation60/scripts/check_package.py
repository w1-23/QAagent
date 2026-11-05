#!/usr/bin/env python3
"""Offline package integrity checks; no service, network, or model use."""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import os
from pathlib import Path

from common import ROOT, csvrows, sha256_file, utc

CITATION_PUBLIC_FORBIDDEN = {
    "03_benchmarks/citation60/CORPUS_SCOPE_METADATA.json",
    "03_benchmarks/citation60/README_FROZEN.md",
    "03_benchmarks/citation60/chunk_manifest.csv",
    "03_benchmarks/citation60/chunks_FROZEN.jsonl",
    "03_benchmarks/citation60/question_authoring_packet.jsonl",
    "03_benchmarks/citation60/question_manifest.csv",
    "03_benchmarks/citation60/questions_DRAFT.csv",
    "03_benchmarks/citation60/questions_FROZEN.jsonl",
    "03_benchmarks/citation60/questions_FROZEN.manifest.json",
    "03_benchmarks/citation60/sampling_config.json",
    "06_runs/manifests/citation_run_plan.csv",
    "06_runs/manifests/citation_run_plan_meta.json",
    "06_runs/manifests/citation_semantic_audit_plan.csv",
    "06_runs/manifests/citation_semantic_audit_plan_meta.json",
}


def jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.open(encoding="utf-8")
        if line.strip()
    ]


def load_manifest() -> dict:
    return json.loads(
        (
            ROOT / "12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json"
        ).read_text(encoding="utf-8")
    )


def verify_l1_assets(
    l1_root: Path,
    manifest: dict,
    blockers: list[str],
) -> int:
    verified = 0
    for row in manifest["assets"]:
        path = l1_root / row["protected_relative_path"]
        if not path.is_file():
            blockers.append(f"protected L1 file missing: {path}")
            continue
        if path.stat().st_size != int(row["bytes"]):
            blockers.append(f"protected L1 bytes mismatch: {path}")
            continue
        if sha256_file(path) != row["sha256"]:
            blockers.append(f"protected L1 SHA mismatch: {path}")
            continue
        verified += 1
    return verified


def validate_citation_controlled(blockers: list[str], l1_root: Path) -> dict:
    bench = l1_root / "03_benchmarks/citation60"
    questions_path = bench / "questions_FROZEN.jsonl"
    question_manifest_path = bench / "question_manifest.csv"
    chunk_manifest_path = bench / "chunk_manifest.csv"
    lock_path = bench / "questions_FROZEN.manifest.json"
    plan_path = l1_root / "06_runs/manifests/citation_run_plan.csv"
    plan_meta_path = l1_root / "06_runs/manifests/citation_run_plan_meta.json"
    semantic_path = (
        l1_root / "06_runs/manifests/citation_semantic_audit_plan.csv"
    )
    semantic_meta_path = (
        l1_root / "06_runs/manifests/citation_semantic_audit_plan_meta.json"
    )
    required = (
        questions_path,
        question_manifest_path,
        chunk_manifest_path,
        lock_path,
        plan_path,
        plan_meta_path,
        semantic_path,
        semantic_meta_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        blockers.extend(f"controlled citation60 input missing: {path}" for path in missing)
        return {"available": False}

    citation_questions = jsonl(questions_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    question_meta = json.loads(plan_meta_path.read_text(encoding="utf-8"))
    semantic_meta = json.loads(semantic_meta_path.read_text(encoding="utf-8"))
    with plan_path.open(encoding="utf-8-sig", newline="") as handle:
        citation_plan = list(csv.DictReader(handle))
    with chunk_manifest_path.open(encoding="utf-8-sig", newline="") as handle:
        chunk_metadata = list(csv.DictReader(handle))

    if lock.get("status") != "FROZEN":
        blockers.append("citation question manifest is not FROZEN")
    if lock.get("questions_frozen_sha256") != sha256_file(questions_path):
        blockers.append("questions_FROZEN.jsonl differs from its manifest")
    if lock.get("question_manifest_sha256") != sha256_file(question_manifest_path):
        blockers.append("question_manifest.csv differs from its manifest")
    if question_meta.get("run_plan_sha256") != sha256_file(plan_path):
        blockers.append("citation run plan differs from its provenance")
    if semantic_meta.get("audit_plan_sha256") != sha256_file(semantic_path):
        blockers.append("disabled semantic-audit plan differs from provenance")
    if semantic_meta.get("status") != "DISABLED_BY_DESIGN":
        blockers.append("semantic-audit provenance is not marked disabled")

    if len(citation_questions) != 60:
        blockers.append(
            f"supplementary questions {len(citation_questions)} != 60"
        )
    citation_ids = {row["question_id"] for row in citation_questions}
    if len(citation_ids) != 60:
        blockers.append("supplementary question IDs are not unique")
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
            blockers.append(f"{row['question_id']}: question leaks chunk text")

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
                blockers.append(f"{row['run_id']}: prompt leaks target locator")
    if set(plan_conditions) != citation_ids:
        blockers.append("citation run-plan case IDs do not match frozen questions")
    if any(
        conditions != {"B0", "B1", "B2"}
        for conditions in plan_conditions.values()
    ):
        blockers.append("each citation question must have exactly B0/B1/B2")

    if len(chunk_metadata) != 60:
        blockers.append(f"citation chunk metadata {len(chunk_metadata)} != 60")
    chunk_map = {row["citation_id"]: row for row in chunk_metadata}
    for row in citation_questions:
        chunk = chunk_map.get(row["question_id"])
        if chunk is None:
            blockers.append(f"{row['question_id']}: chunk metadata is missing")
            continue
        if row["target_chunk_id"] != chunk["chunk_id"]:
            blockers.append(f"{row['question_id']}: target chunk mismatch")
        if row["target_chunk_text_sha256"] != chunk["chunk_text_sha256"]:
            blockers.append(f"{row['question_id']}: chunk text SHA mismatch")
        if row["target_source_file"] != chunk["source_file"]:
            blockers.append(f"{row['question_id']}: source file mismatch")
    corpus_counts = collections.Counter(
        row["corpus_id"] for row in chunk_metadata
    )
    if set(corpus_counts.values()) != {12} or len(corpus_counts) != 5:
        blockers.append(f"citation sampling is not five x 12: {corpus_counts}")

    return {
        "available": True,
        "questions": len(citation_questions),
        "run_rows": len(citation_plan),
        "questions_sha256": sha256_file(questions_path),
        "semantic_support_reported": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--l1-root",
        default=os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", ""),
        help="protected L1 root; public absence alone is a valid metadata-only state",
    )
    args = parser.parse_args()
    blockers: list[str] = []

    required_audit_files = (
        "AUDIT_INDEX.md",
        "00_protocol/05_论文写作执行计划.md",
        "11_manuscript/README.md",
        "11_manuscript/manuscript_skeleton.md",
        "11_manuscript/claim_source_map_TEMPLATE.csv",
        "11_manuscript/table_figure_provenance_TEMPLATE.csv",
        "11_manuscript/paper_version_registry.csv",
        "11_manuscript/review_response_log.csv",
    )
    for relative in required_audit_files:
        if not (ROOT / relative).is_file():
            blockers.append(f"paper audit asset missing: {relative}")

    questions = csvrows("03_benchmarks/qa60/qa60_CANDIDATE.csv")
    intents = csvrows("03_benchmarks/intent/intent192_CANDIDATE.csv")
    composites = csvrows("03_benchmarks/intent/composite24_CANDIDATE.csv")
    validity = csvrows("07_scoring/validity/question_validity_review_packet.csv")
    adjudication = csvrows(
        "07_scoring/validity/question_validity_adjudication_TEMPLATE.csv"
    )
    judge_template = csvrows("07_scoring/judge/qa_judge_TEMPLATE_180.csv")

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

    if len(validity) != 120:
        blockers.append(f"public QA validity rows {len(validity)} != 120")
    review_slots: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for row in validity:
        review_slots[(row["experiment_id"], row["question_id"])].add(
            row["reviewer_slot"]
        )
    if len(review_slots) != 60:
        blockers.append(f"public QA validity questions {len(review_slots)} != 60")
    if {row["experiment_id"] for row in validity} != {"E01_QA60_EGTS"}:
        blockers.append("public validity packet must contain QA60 rows only")
    if any(
        slots != {"REVIEWER_1", "REVIEWER_2"}
        for slots in review_slots.values()
    ):
        blockers.append("each public QA question must have reviewer slots 1 and 2")
    core_ids = {row["题号"] for row in questions}
    if {question_id for _, question_id in review_slots} != core_ids:
        blockers.append("public validity packet does not exactly cover QA60")
    adjudication_keys = {
        (row["experiment_id"], row["question_id"]) for row in adjudication
    }
    if len(adjudication) != 60 or adjudication_keys != set(review_slots):
        blockers.append("public QA adjudication template must contain 60 matching rows")

    judge_keys = {(row["question_id"], row["condition"]) for row in judge_template}
    if len(judge_template) != 180 or len(judge_keys) != 180:
        blockers.append("core-QA judge template must contain 180 unique rows")
    if {row["condition"] for row in judge_template} != {"B0", "B1", "B2"}:
        blockers.append("core-QA judge template conditions are not B0/B1/B2")

    for relative in sorted(CITATION_PUBLIC_FORBIDDEN):
        if (ROOT / relative).exists():
            blockers.append(f"unreviewed citation60 asset present in public tree: {relative}")

    simulated = ROOT / "06_runs/simulated_dry_run"
    simulated_expected = {
        "core_qa_runs.jsonl": 180,
        "citation_runs.jsonl": 180,
        "route_runs.jsonl": 192,
        "composite_runs.jsonl": 24,
        "workflow_runs.jsonl": 7,
    }
    simulated_counts = {}
    for name, expected in simulated_expected.items():
        path = simulated / name
        if not path.is_file():
            blockers.append(f"simulated dry-run file missing: {name}")
            continue
        rows = jsonl(path)
        simulated_counts[name] = len(rows)
        if len(rows) != expected:
            blockers.append(f"{name}: {len(rows)} rows != {expected}")
        if any(
            row.get("evidence_class") != "SIMULATED_DRY_RUN"
            or row.get("not_for_publication_claims") is not True
            for row in rows
        ):
            blockers.append(f"{name}: simulation labels are incomplete")

    manifest = load_manifest()
    declared = {row["former_public_path"]: row for row in manifest["assets"]}
    for relative in CITATION_PUBLIC_FORBIDDEN:
        if relative not in declared:
            blockers.append(f"L1 manifest does not declare public-tree block: {relative}")

    l1_verified = 0
    citation_controlled = {
        "available": False,
        "state": "PUBLIC_ABSENT_PROTECTED_DECLARED",
        "semantic_support_reported": False,
    }
    if args.l1_root:
        l1_root = Path(args.l1_root).expanduser().resolve()
        if l1_root.is_relative_to(ROOT.resolve()):
            blockers.append("L1 root must be outside the public Git tree")
        else:
            l1_verified = verify_l1_assets(l1_root, manifest, blockers)
            citation_controlled = validate_citation_controlled(blockers, l1_root)
            citation_controlled["l1_verified"] = l1_verified

    status = "FAIL" if blockers else "PASS"
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
                    ROOT / "04_fixtures/fusion_WF07/synthetic_leaf_qPCR_WF07.xlsx"
                ),
                "citation60": citation_controlled,
                "l1_assets_declared": len(manifest["assets"]),
                "l1_assets_verified": l1_verified,
                "semantic_support_reported": False,
                "simulated_dry_run": simulated_counts,
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

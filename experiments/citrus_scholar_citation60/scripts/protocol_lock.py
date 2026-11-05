#!/usr/bin/env python3
"""Gate/validity/judge checks and write-once SHA manifest.

This script does not grant human approval and cannot fill reviewer signatures.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

from common import ROOT, csvrows, jsondump, sha256_file, source_safe_path, utc

PLACEHOLDER = "FILL"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def relative(path: Path) -> str:
    return path.relative_to(ROOT.resolve()).as_posix()


def combined_sha256(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def blank(value) -> bool:
    return not str(value or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--lock", action="store_true")
    parser.add_argument(
        "--qa-lock",
        default="03_benchmarks/qa60/qa60_LOCKED.csv",
        help="actual write-once core question lock",
    )
    parser.add_argument(
        "--validity-review",
        default="07_scoring/validity/question_validity_review_WORKING.csv",
        help="completed working reviewer packet, not the public blank template",
    )
    parser.add_argument(
        "--validity-adjudication",
        default="07_scoring/validity/question_validity_adjudication_WORKING.csv",
        help="completed working adjudication table, not the public blank template",
    )
    parser.add_argument(
        "--validity-lock",
        default="07_scoring/validity/question_validity_LOCKED.csv",
        help="actual write-once validity lock",
    )
    parser.add_argument(
        "--manifest",
        default="00_protocol/locked_manifest.json",
        help="write-once protocol lock output",
    )
    args = parser.parse_args()
    if not (args.check or args.lock):
        parser.error("specify --check or --lock")

    qa_lock_path = source_safe_path(args.qa_lock)
    review_path = source_safe_path(args.validity_review)
    adjudication_path = source_safe_path(args.validity_adjudication)
    validity_lock_path = source_safe_path(args.validity_lock)
    manifest_path = source_safe_path(args.manifest)

    blockers: list[str] = []
    gates = csvrows("01_preflight/gates.csv")
    for gate in gates:
        status = gate["status"].strip().upper()
        priority = gate["priority"].strip().upper()
        evidence = gate["evidence_path"].strip()
        if priority == "P0" and status != "PASS":
            blockers.append(f"{gate['gate_id']} {status}")
        if priority == "P1" and status not in {"PASS", "NA"}:
            blockers.append(f"{gate['gate_id']} {status}")
        if status in {"PASS", "NA"}:
            if not evidence:
                blockers.append(f"{gate['gate_id']} missing evidence_path")
            elif not (ROOT / evidence).exists():
                blockers.append(
                    f"{gate['gate_id']} evidence_file_not_found {evidence}"
                )

    model_config_path = ROOT / "05_baselines/model_config.json"
    model_config = json.loads(model_config_path.read_text(encoding="utf-8"))
    if model_config.get("status") != "READY":
        blockers.append("model config not READY")
    for field in (
        "model_id",
        "base_url_env",
        "api_key_env",
        "b1_document_snapshot_id",
        "b1_reranker",
        "b2_corpus_generation",
        "b2_deployment_git_sha",
        "experimental_account_id_hash",
    ):
        if blank(model_config.get(field)) or PLACEHOLDER in str(model_config.get(field)):
            blockers.append(f"model config {field} is not frozen")

    judge_config_path = ROOT / "05_baselines/judge_config.json"
    judge_config = json.loads(judge_config_path.read_text(encoding="utf-8"))
    if judge_config.get("status") != "READY":
        blockers.append("judge config not READY")
    judges = judge_config.get("judges", {})
    for role in ("judge_1", "judge_2", "judge_3"):
        judge = judges.get(role, {})
        for field in ("actual_model_id", "version_or_fingerprint"):
            if blank(judge.get(field)) or PLACEHOLDER in str(judge.get(field)):
                blockers.append(f"judge config judges.{role}.{field} is not frozen")
        if judge.get("family") == "DeepSeek":
            blockers.append(f"judge config {role} uses forbidden DeepSeek family")
    for field in ("api_key_env", "base_url_env"):
        if blank(judge_config.get(field)) or PLACEHOLDER in str(judge_config.get(field)):
            blockers.append(f"judge config {field} is not frozen")
    if judges.get("judge_1", {}).get("actual_model_id") == judges.get(
        "judge_2", {}
    ).get("actual_model_id"):
        blockers.append("judge 1 and judge 2 must be independent model deployments")
    for field in (
        "judge_1_prompt_sha256",
        "judge_2_prompt_sha256",
        "adjudicator_prompt_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", str(judge_config.get(field) or "")):
            blockers.append(f"judge config {field} is not a frozen SHA-256")
    if judge_config.get("temperature") != 0:
        blockers.append("judge temperature must be 0")
    if judge_config.get("blind_to_condition_model_run_question") is not True:
        blockers.append("judge packets must be blinded to condition")
    if judge_config.get("semantic_support_reported") is not False:
        blockers.append("semantic support must remain disabled")
    if (
        judge_config.get("supplement_citation_primary_endpoint")
        != "deterministic_target_chunk_id_match"
    ):
        blockers.append("supplement endpoint must be deterministic chunk ID match")

    qa_ids: set[str] = set()
    if not qa_lock_path.is_file():
        blockers.append(f"{relative(qa_lock_path)} missing")
    else:
        questions = read_csv(qa_lock_path)
        if len(questions) != 60 or len({row["题号"] for row in questions}) != 60:
            blockers.append("QA lock must contain 60 unique questions")
        qa_ids = {row["题号"] for row in questions}
        if any(
            "gold" in column.lower() or "answer" in column.lower()
            for column in questions[0]
        ):
            blockers.append("QA lock still exposes Gold/Answer columns")

    provenance_paths = [
        review_path,
        adjudication_path,
        ROOT / "03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md",
    ]
    for path in provenance_paths:
        if not path.is_file():
            blockers.append(f"validity provenance missing: {relative(path)}")
    if not validity_lock_path.is_file():
        blockers.append(f"{relative(validity_lock_path)} missing")
    else:
        validity = read_csv(validity_lock_path)
        keys = {
            (row["experiment_id"], row["question_id"]) for row in validity
        }
        counts = collections.Counter(
            row["experiment_id"] for row in validity
        )
        if len(validity) != 120 or len(keys) != 120:
            blockers.append("validity lock must contain 120 unique questions")
        if dict(counts) != {
            "E01_QA60_EGTS": 60,
            "E01_SUPP_CHUNK_CITATION60": 60,
        }:
            blockers.append(f"validity lock experiment counts are wrong: {counts}")
        if any(
            row["final_decision"].strip().upper() != "ACCEPT" for row in validity
        ):
            blockers.append("every locked question must have final_decision=ACCEPT")
        for row in validity:
            if blank(row["reviewer_1_id"]) or blank(row["reviewer_2_id"]):
                blockers.append(f"{row['question_id']}: reviewer IDs blank")
            elif (
                row["reviewer_1_id"].strip().casefold()
                == row["reviewer_2_id"].strip().casefold()
            ):
                blockers.append(f"{row['question_id']}: reviewers are not independent")
        if all(path.is_file() for path in provenance_paths):
            expected_packet_sha = sha256_file(review_path)
            expected_adjudication_sha = sha256_file(adjudication_path)
            expected_protocol_sha = sha256_file(provenance_paths[2])
            expected_combined_sha = combined_sha256(provenance_paths)
            for row in validity:
                if row["review_packet_sha256"] != expected_packet_sha:
                    blockers.append(f"{row['question_id']}: review packet SHA stale")
                if row["adjudication_sha256"] != expected_adjudication_sha:
                    blockers.append(f"{row['question_id']}: adjudication SHA stale")
                if row["validity_protocol_sha256"] != expected_protocol_sha:
                    blockers.append(f"{row['question_id']}: validity protocol SHA stale")
                if row["locked_manifest_sha256"] != expected_combined_sha:
                    blockers.append(f"{row['question_id']}: combined validity SHA stale")

        supplement_ids = {
            json.loads(line)["question_id"]
            for line in (
                ROOT / "03_benchmarks/citation60/questions_FROZEN.jsonl"
            ).open(encoding="utf-8")
            if line.strip()
        }
        expected_keys = {
            ("E01_QA60_EGTS", question_id) for question_id in qa_ids
        } | {
            ("E01_SUPP_CHUNK_CITATION60", question_id)
            for question_id in supplement_ids
        }
        if keys != expected_keys:
            blockers.append(
                "validity lock does not exactly cover the 120 formal questions"
            )

    if not (ROOT / "01_preflight/preflight_observed.json").exists():
        blockers.append("source preflight report missing")

    report = {
        "gate_ready": not blockers,
        "blocking": blockers[:100],
        "next": "Complete the 120x2 human validity reviews and external evidence; "
        "never fabricate approvals."
        if blockers
        else "Eligible to create the write-once protocol lock.",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not args.lock:
        return 0 if not blockers else 2
    if blockers:
        return 2
    if manifest_path.exists():
        raise SystemExit("already locked; cannot overwrite")
    refs = [
        "01_preflight/gates.csv",
        relative(qa_lock_path),
        relative(validity_lock_path),
        relative(review_path),
        relative(adjudication_path),
        "03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md",
        "03_benchmarks/citation60/questions_FROZEN.jsonl",
        "03_benchmarks/citation60/questions_FROZEN.manifest.json",
        "06_runs/manifests/citation_run_plan.csv",
        "03_benchmarks/intent/intent192_CANDIDATE.csv",
        "03_benchmarks/intent/composite24_CANDIDATE.csv",
        "02_sources/manifests/materials_tracking.csv",
        "05_baselines/model_config.json",
        "05_baselines/judge_config.json",
        "04_fixtures/fusion_WF07/synthetic_leaf_qPCR_WF07.xlsx",
        "04_fixtures/fusion_WF07/synthetic_leaf_qPCR_truth.csv",
    ]
    jsondump(
        relative(manifest_path),
        {
            "locked_at_utc": utc(),
            "protocol_version": "3.0",
            "environment_baseline": model_config["b2_deployment_git_sha"],
            "file_hashes": {
                rel: sha256_file(ROOT / rel) for rel in refs
            },
            "human_evidence": "See gates.csv and attached signed evidence files",
            "notes": "Any mutation invalidates comparison; rerun only with a documented protocol version.",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

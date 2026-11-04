#!/usr/bin/env python3
"""Verify that simulated dry-run output is complete and clearly labelled."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_CLASS = "SIMULATED_DRY_RUN"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default="06_runs/simulated_dry_run")
    parser.add_argument("--analysis-dir", default="09_analysis/simulated_dry_run")
    args = parser.parse_args()

    runs_dir = (ROOT / args.runs_dir).resolve()
    analysis_dir = (ROOT / args.analysis_dir).resolve()
    errors: list[str] = []
    expected_counts = {
        "core_qa_runs.jsonl": 180,
        "citation_runs.jsonl": 180,
        "route_runs.jsonl": 192,
        "composite_runs.jsonl": 24,
        "workflow_runs.jsonl": 7,
    }
    all_run_ids: list[str] = []
    for filename, expected in expected_counts.items():
        path = runs_dir / filename
        if not path.is_file():
            errors.append(f"missing {filename}")
            continue
        rows = read_jsonl(path)
        if len(rows) != expected:
            errors.append(f"{filename}: rows {len(rows)} != {expected}")
        for row in rows:
            if row.get("evidence_class") != EVIDENCE_CLASS:
                errors.append(f"{filename}: missing evidence_class")
                break
            if row.get("not_for_publication_claims") is not True:
                errors.append(f"{filename}: missing publication guard")
                break
            all_run_ids.append(row["run_id"])
    if len(all_run_ids) != len(set(all_run_ids)):
        errors.append("duplicate run_id across simulated experiments")

    for filename in ("judge_responses.jsonl", "judge_aggregate.jsonl"):
        path = runs_dir / filename
        if not path.is_file():
            errors.append(f"missing {filename}")
            continue
        rows = read_jsonl(path)
        if not rows:
            errors.append(f"{filename}: empty")
        if any(row.get("evidence_class") != EVIDENCE_CLASS for row in rows):
            errors.append(f"{filename}: missing evidence_class")

    summary_path = analysis_dir / "summary.json"
    if not summary_path.is_file():
        errors.append("missing analysis summary")
    else:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("evidence_class") != EVIDENCE_CLASS:
            errors.append("summary evidence_class mismatch")
        if summary.get("not_for_publication_claims") is not True:
            errors.append("summary publication guard missing")
        counts = summary.get("counts", {})
        if counts != {
            "core_qa_runs": 180,
            "citation_runs": 180,
            "route_runs": 192,
            "composite_runs": 24,
            "workflow_runs": 7,
            "judge_responses": counts.get("judge_responses"),
        }:
            if any(
                counts.get(key) != value
                for key, value in {
                    "core_qa_runs": 180,
                    "citation_runs": 180,
                    "route_runs": 192,
                    "composite_runs": 24,
                    "workflow_runs": 7,
                }.items()
            ):
                errors.append(f"summary counts mismatch: {counts}")

    manifest_path = runs_dir / "manifest.json"
    if not manifest_path.is_file():
        errors.append("missing manifest")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("evidence_class") != EVIDENCE_CLASS:
            errors.append("manifest evidence_class mismatch")
        if manifest.get("not_for_publication_claims") is not True:
            errors.append("manifest publication guard missing")
        for item in manifest.get("outputs", []):
            path = ROOT / item["path"]
            if not path.is_file():
                errors.append(f"manifest output missing: {item['path']}")
            elif sha256(path) != item["sha256"]:
                errors.append(f"manifest output SHA mismatch: {item['path']}")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "evidence_class": EVIDENCE_CLASS,
        "not_for_publication_claims": True,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())

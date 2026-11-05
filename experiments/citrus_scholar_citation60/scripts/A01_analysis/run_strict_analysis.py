#!/usr/bin/env python3
"""Strict v4.3.2 analysis entry point. Synthetic tests only in this review unit."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CONDITIONS = ("B0", "B1", "B2")
STRATA = ("L", "V", "W", "X", "P", "N")
Z95 = 1.959963984540054
SEED = 20261104
BOOTSTRAP_REPLICATES = 10_000


def normalized_label(value: object) -> str:
    return str(value or "").strip().casefold()


def is_true(value: object) -> bool:
    return normalized_label(value) == "true"


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def wilson(successes: int, n: int) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = successes / n
    denominator = 1 + Z95 * Z95 / n
    center = (p + Z95 * Z95 / (2 * n)) / denominator
    half = (
        Z95
        * math.sqrt(p * (1 - p) / n + Z95 * Z95 / (4 * n * n))
        / denominator
    )
    return max(0.0, center - half), min(1.0, center + half)


def exact_mcnemar(b: int, c: int) -> float:
    discordant = b + c
    if discordant == 0:
        return 1.0
    tail = sum(
        math.comb(discordant, index) * 0.5**discordant
        for index in range(min(b, c) + 1)
    )
    return min(1.0, 2 * tail)


def validate_system_rows(rows: list[dict]) -> list[str]:
    errors: list[str] = []
    if len(rows) != 180:
        errors.append(f"row_count:{len(rows)}")
    keys = [(row.get("internal_question_key"), row.get("condition_internal")) for row in rows]
    if len(set(keys)) != len(keys):
        errors.append("duplicate_question_condition")
    if any(row.get("condition_internal") not in CONDITIONS for row in rows):
        errors.append("condition_invalid")
    if any(row.get("egts_system_outcome") not in {"0", "1"} for row in rows):
        errors.append("egts_system_outcome_invalid")
    if any(row.get("consensus_status") == "BLOCKED" for row in rows):
        errors.append("blocked_rows")
    counts: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        counts[row.get("condition_internal")][row.get("expected_answerability")] += 1
    for condition in CONDITIONS:
        total = sum(counts[condition].values())
        if total != 60:
            errors.append(f"condition_row_count:{condition}:{total}")
        if counts[condition].get("ANSWERABLE") != 52:
            errors.append(f"answerable_count:{condition}:{counts[condition].get('ANSWERABLE', 0)}")
        if counts[condition].get("UNANSWERABLE") != 8:
            errors.append(
                f"unanswerable_count:{condition}:{counts[condition].get('UNANSWERABLE', 0)}"
            )
    for index, row in enumerate(rows):
        expected = row.get("expected_answerability")
        if expected == "UNANSWERABLE":
            if normalized_label(row.get("consensus_answer_correct")) != "not_applicable":
                errors.append(f"unanswerable_answer_correct_not_na:{index}")
            if normalized_label(row.get("consensus_evidence_traceable")) != "not_applicable":
                errors.append(f"unanswerable_evidence_not_na:{index}")
        elif expected == "ANSWERABLE":
            for field in (
                "consensus_answerability_correct",
                "consensus_answer_correct",
                "consensus_evidence_traceable",
                "consensus_fatal_error",
            ):
                if normalized_label(row.get(field)) not in {"true", "false"}:
                    errors.append(f"{field}_not_boolean:{index}")
    per_question: dict[str, set[str]] = defaultdict(set)
    clusters: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        per_question[row.get("internal_question_key", "")].add(
            row.get("condition_internal", "")
        )
        clusters[row.get("internal_question_key", "")].add(
            row.get("source_cluster", "")
        )
    if len(per_question) != 60:
        errors.append(f"question_count:{len(per_question)}")
    if any(conditions != set(CONDITIONS) for conditions in per_question.values()):
        errors.append("question_condition_set")
    if any(
        len(values) != 1 or next(iter(values)) not in STRATA
        for values in clusters.values()
    ):
        errors.append("question_source_cluster_inconsistent_or_invalid")
    if {next(iter(values)) for values in clusters.values() if len(values) == 1} != set(
        STRATA
    ):
        errors.append("all_six_strata_required")
    return errors


def condition_summary(rows: list[dict]) -> list[dict]:
    output: list[dict] = []
    for condition in CONDITIONS:
        subset = [row for row in rows if row["condition_internal"] == condition]
        system_successes = sum(int(row["egts_system_outcome"]) for row in subset)
        system_low, system_high = wilson(system_successes, len(subset))
        answerable = [
            row for row in subset if row["expected_answerability"] == "ANSWERABLE"
        ]
        unanswerable = [
            row for row in subset if row["expected_answerability"] == "UNANSWERABLE"
        ]
        answerability_successes = sum(
            is_true(row.get("consensus_answerability_correct")) for row in subset
        )
        answer_correct_successes = sum(
            is_true(row.get("consensus_answer_correct")) for row in answerable
        )
        evidence_successes = sum(
            is_true(row.get("consensus_evidence_traceable")) for row in answerable
        )
        fatal_errors = sum(
            is_true(row.get("consensus_fatal_error")) for row in subset
        )
        answerability_low, answerability_high = wilson(
            answerability_successes, 60
        )
        answer_low, answer_high = wilson(answer_correct_successes, 52)
        evidence_low, evidence_high = wilson(evidence_successes, 52)
        fatal_low, fatal_high = wilson(fatal_errors, 60)
        not_scored = sum(
            normalized_label(row.get("consensus_fatal_error"))
            == "not_scored_infrastructure"
            for row in subset
        )
        output.append(
            {
                "condition": condition,
                "planned_n": 60,
                "egts_system_successes": system_successes,
                "egts_system_rate": round(system_successes / 60, 6),
                "egts_system_wilson_lo": round(system_low, 6),
                "egts_system_wilson_hi": round(system_high, 6),
                "answerability_successes": answerability_successes,
                "answerability_denominator": 60,
                "answerability_rate": round(answerability_successes / 60, 6),
                "answerability_wilson_lo": round(answerability_low, 6),
                "answerability_wilson_hi": round(answerability_high, 6),
                "answer_correct_successes": answer_correct_successes,
                "answer_correct_denominator": 52,
                "answer_correct_rate": round(answer_correct_successes / 52, 6),
                "answer_correct_wilson_lo": round(answer_low, 6),
                "answer_correct_wilson_hi": round(answer_high, 6),
                "evidence_traceable_successes": evidence_successes,
                "evidence_traceable_denominator": 52,
                "evidence_traceable_rate": round(evidence_successes / 52, 6),
                "evidence_traceable_wilson_lo": round(evidence_low, 6),
                "evidence_traceable_wilson_hi": round(evidence_high, 6),
                "fatal_error_count": fatal_errors,
                "fatal_error_denominator": 60,
                "fatal_error_rate": round(fatal_errors / 60, 6),
                "fatal_error_wilson_lo": round(fatal_low, 6),
                "fatal_error_wilson_hi": round(fatal_high, 6),
                "answerable_n": len(answerable),
                "unanswerable_n": len(unanswerable),
                "infrastructure_not_scored_n": not_scored,
            }
        )
    return output


def stratum_summary(rows: list[dict]) -> list[dict]:
    output: list[dict] = []
    for condition in CONDITIONS:
        for answerability, denominator in (("ANSWERABLE", 52), ("UNANSWERABLE", 8)):
            subset = [
                row
                for row in rows
                if row["condition_internal"] == condition
                and row["expected_answerability"] == answerability
            ]
            egts = sum(int(row["egts_system_outcome"]) for row in subset)
            low, high = wilson(egts, denominator)
            output.append(
                {
                    "condition": condition,
                    "expected_answerability": answerability,
                    "n": len(subset),
                    "egts_successes": egts,
                    "denominator": denominator,
                    "egts_rate": round(egts / denominator, 6),
                    "wilson_lo": round(low, 6),
                    "wilson_hi": round(high, 6),
                }
            )
    return output


def paired_analysis(rows: list[dict]) -> dict:
    by_question: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        by_question[row["internal_question_key"]][row["condition_internal"]] = row
    b = c = 0
    for question in by_question.values():
        left = int(question["B2"]["egts_system_outcome"])
        right = int(question["B1"]["egts_system_outcome"])
        b += int(left == 1 and right == 0)
        c += int(left == 0 and right == 1)
    n = len(by_question)
    difference = (b - c) / n
    rng = np.random.Generator(np.random.PCG64(SEED))
    by_stratum: dict[str, list[str]] = defaultdict(list)
    for question_id, question in by_question.items():
        by_stratum[question["B2"]["source_cluster"]].append(question_id)
    strata = [stratum for stratum in STRATA if by_stratum[stratum]]
    if set(strata) != set(STRATA):
        raise ValueError("all six frozen strata are required")
    values = np.empty(BOOTSTRAP_REPLICATES, dtype=float)
    for replicate in range(BOOTSTRAP_REPLICATES):
        sampled_questions: list[str] = []
        for stratum in strata:
            pool = np.asarray(sorted(by_stratum[stratum]), dtype=object)
            draw = rng.integers(0, len(pool), size=len(pool))
            sampled_questions.extend(pool[draw].tolist())
        b_star = c_star = 0
        for question_id in sampled_questions:
            question = by_question[question_id]
            left = int(question["B2"]["egts_system_outcome"])
            right = int(question["B1"]["egts_system_outcome"])
            b_star += int(left == 1 and right == 0)
            c_star += int(left == 0 and right == 1)
        values[replicate] = (b_star - c_star) / len(sampled_questions)
    low, high = np.quantile(values, [0.025, 0.975])
    return {
        "comparison": "B2_vs_B1",
        "primary_endpoint": "egts_system_outcome",
        "questions": n,
        "B2_success_B1_failure": b,
        "B2_failure_B1_success": c,
        "paired_difference": round(difference, 6),
        "paired_difference_pp": round(difference * 100, 3),
        "exact_mcnemar_two_sided_p": round(exact_mcnemar(b, c), 8),
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "bootstrap_seed": SEED,
        "bootstrap_rng": "numpy.random.Generator(numpy.random.PCG64(20261104))",
        "bootstrap_interval": "percentile",
        "paired_ci_95_lo": round(float(low), 6),
        "paired_ci_95_hi": round(float(high), 6),
        "paired_ci_95_lo_pp": round(float(low) * 100, 3),
        "paired_ci_95_hi_pp": round(float(high) * 100, 3),
        "strata": list(STRATA),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--human-consensus", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--strict-human", action="store_true")
    args = parser.parse_args()
    consensus_path = Path(args.human_consensus).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    l1_value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not l1_value:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    l1_root = Path(l1_value).expanduser().resolve()
    if not consensus_path.is_relative_to(l1_root):
        raise SystemExit("human consensus input must be in protected L1")
    if not out_dir.is_relative_to(l1_root) or out_dir.is_relative_to(ROOT):
        raise SystemExit("analysis output must be in protected L1")
    if out_dir.exists():
        raise SystemExit(f"refuse overwrite: {out_dir}")
    rows = read_rows(consensus_path)
    validation_errors = validate_system_rows(rows)
    if args.strict_human and validation_errors:
        raise SystemExit("strict-human failed: " + ",".join(validation_errors))
    if validation_errors:
        raise SystemExit("analysis inputs are not valid: " + ",".join(validation_errors))
    summary = condition_summary(rows)
    strata = stratum_summary(rows)
    paired = paired_analysis(rows)
    out_dir.mkdir(parents=True)
    summary_path = out_dir / "condition_summary.csv"
    with summary_path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    paired_path = out_dir / "paired_B2_vs_B1.json"
    paired_path.write_text(
        json.dumps(paired, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    strata_path = out_dir / "qa60_52_8_strata.csv"
    with strata_path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(strata[0]))
        writer.writeheader()
        writer.writerows(strata)
    manifest = {
        "analysis_version": "4.4.2",
        "primary_confirmatory_endpoint": "egts_system_outcome",
        "semantic_support_reported": False,
        "input": str(consensus_path),
        "input_sha256": hashlib_sha(consensus_path),
        "rows": len(rows),
        "validation_errors": validation_errors,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "bootstrap_seed": SEED,
        "outputs": {
            summary_path.name: hashlib_sha(summary_path),
            paired_path.name: hashlib_sha(paired_path),
            strata_path.name: hashlib_sha(strata_path),
        },
    }
    (out_dir / "analysis_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


def hashlib_sha(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())

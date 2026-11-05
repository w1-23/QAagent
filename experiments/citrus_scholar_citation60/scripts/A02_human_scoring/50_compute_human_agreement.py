#!/usr/bin/env python3
"""Compute expert agreement with question-clustered uncertainty."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from A02_human_scoring.scoring_common import (  # noqa: E402
    JUDGMENT_FIELDS,
    deterministic_egts,
    read_csv,
    sha256_file,
    validate_score_row,
)

FIELDS = (*JUDGMENT_FIELDS, "provisional_egts")
STRATA = ("L", "V", "W", "X", "P", "N")
BOOTSTRAP_REPLICATES = 10_000
SEED = 20261104


def clean_pair(
    first: dict,
    second: dict,
    expected_answerability: str,
) -> tuple[dict, list[str]]:
    first_errors, first_value = validate_score_row(first, expected_answerability)
    second_errors, second_value = validate_score_row(second, expected_answerability)
    errors = [f"expert_1:{item}" for item in first_errors]
    errors.extend(f"expert_2:{item}" for item in second_errors)
    if errors:
        return {}, errors
    pair = {
        field: (first_value.get(field), second_value.get(field))
        for field in JUDGMENT_FIELDS
    }
    pair["provisional_egts"] = (
        deterministic_egts(first_value, expected_answerability),
        deterministic_egts(second_value, expected_answerability),
    )
    return pair, []


def applicable(value: object, field: str) -> bool:
    if field == "provisional_egts":
        return isinstance(value, int) and value in {0, 1}
    return isinstance(value, bool)


def raw_agreement(pairs: list[dict], field: str) -> tuple[int, int]:
    eligible = [
        pair[field]
        for pair in pairs
        if applicable(pair[field][0], field)
        and applicable(pair[field][1], field)
    ]
    return sum(left == right for left, right in eligible), len(eligible)


def cohen_kappa(pairs: list[dict], field: str) -> float:
    eligible = [
        pair[field]
        for pair in pairs
        if applicable(pair[field][0], field)
        and applicable(pair[field][1], field)
    ]
    if not eligible:
        return float("nan")
    labels = sorted({value for pair in eligible for value in pair}, key=str)
    n = len(eligible)
    observed = sum(left == right for left, right in eligible) / n
    expected = sum(
        (
            sum(left == label for left, _ in eligible)
            / n
        )
        * (
            sum(right == label for _, right in eligible)
            / n
        )
        for label in labels
    )
    if expected == 1:
        return float("nan")
    return (observed - expected) / (1 - expected)


def gwet_ac1(pairs: list[dict], field: str) -> float:
    eligible = [
        pair[field]
        for pair in pairs
        if applicable(pair[field][0], field)
        and applicable(pair[field][1], field)
    ]
    if not eligible:
        return float("nan")
    labels = sorted({value for pair in eligible for value in pair}, key=str)
    n_ratings = 2 * len(eligible)
    observed = sum(left == right for left, right in eligible) / len(eligible)
    chance = sum(
        (
            sum(value == label for pair in eligible for value in pair)
            / n_ratings
        )
        * (
            1
            - sum(value == label for pair in eligible for value in pair)
            / n_ratings
        )
        for label in labels
    )
    if chance == 1:
        return float("nan")
    return (observed - chance) / (1 - chance)


def percentile_interval(values: list[float]) -> tuple[float, float]:
    finite = np.asarray([value for value in values if np.isfinite(value)], dtype=float)
    if finite.size == 0:
        return float("nan"), float("nan")
    low, high = np.quantile(finite, [0.025, 0.975])
    return float(low), float(high)


def bootstrap_metric(pairs: list[dict], field: str, metric: str) -> tuple[float, float]:
    by_question: dict[str, list[dict]] = defaultdict(list)
    for pair in pairs:
        by_question[pair["internal_question_key"]].append(pair)
    by_stratum: dict[str, list[str]] = defaultdict(list)
    for question_id, question_pairs in by_question.items():
        by_stratum[question_pairs[0]["source_cluster"]].append(question_id)
    strata = [stratum for stratum in STRATA if by_stratum[stratum]]
    if set(strata) != set(STRATA):
        return float("nan"), float("nan")
    rng = np.random.Generator(np.random.PCG64(SEED))
    values: list[float] = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sampled: list[dict] = []
        for stratum in strata:
            pool = np.asarray(sorted(by_stratum[stratum]), dtype=object)
            indices = rng.integers(0, len(pool), size=len(pool))
            for question_id in pool[indices].tolist():
                sampled.extend(by_question[question_id])
        if metric == "raw_agreement":
            success, n = raw_agreement(sampled, field)
            values.append(success / n if n else float("nan"))
        elif metric == "cohen_kappa":
            values.append(cohen_kappa(sampled, field))
        elif metric == "gwet_ac1":
            values.append(gwet_ac1(sampled, field))
        else:
            raise ValueError(metric)
    return percentile_interval(values)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expert-1", required=True)
    parser.add_argument("--expert-2", required=True)
    parser.add_argument("--expert-1-validation", required=True)
    parser.add_argument("--expert-2-validation", required=True)
    parser.add_argument("--blind-map", required=True)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    l1_value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not l1_value:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    l1_root = Path(l1_value).expanduser().resolve()
    input_paths = [
        Path(args.expert_1),
        Path(args.expert_2),
        Path(args.expert_1_validation),
        Path(args.expert_2_validation),
        Path(args.blind_map),
    ]
    for path in input_paths:
        if not path.expanduser().resolve().is_relative_to(l1_root):
            raise SystemExit(f"agreement input must remain in L1: {path}")
    out_root = Path(args.out_root).expanduser().resolve()
    if not out_root.is_relative_to(l1_root):
        raise SystemExit("agreement output must remain in L1")
    if out_root.exists():
        raise SystemExit(f"refuse overwrite: {out_root}")
    validation_inputs = (
        (args.expert_1_validation, args.expert_1),
        (args.expert_2_validation, args.expert_2),
    )
    for path, scores_path in validation_inputs:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        if args.strict and report.get("status") != "PASS":
            raise SystemExit(f"strict agreement requires PASS validation: {path}")
        if args.strict and report.get("scores_sha256") != sha256_file(
            Path(scores_path)
        ):
            raise SystemExit(f"strict agreement validation is stale: {path}")

    _, first_rows = read_csv(Path(args.expert_1))
    _, second_rows = read_csv(Path(args.expert_2))
    first = {row["blind_id"]: row for row in first_rows}
    second = {row["blind_id"]: row for row in second_rows}
    if len(first) != len(first_rows) or len(second) != len(second_rows):
        raise SystemExit("duplicate blind ID in expert score file")
    if set(first) != set(second):
        raise SystemExit("expert score files cover different blind IDs")

    with Path(args.blind_map).open(encoding="utf-8-sig", newline="") as handle:
        mapping_rows = list(csv.DictReader(handle))
    mapping = {row["blind_id"]: row for row in mapping_rows}
    if len(mapping) != len(mapping_rows) or not set(first).issubset(mapping):
        raise SystemExit("blind map is incomplete or duplicated")
    core_questions = {
        row["题号"]: row
        for row in csv.DictReader(
            (ROOT / "03_benchmarks/qa60/qa60_CANDIDATE.csv").open(
                encoding="utf-8-sig",
                newline="",
            )
        )
    }

    pairs: list[dict] = []
    blockers: list[dict] = []
    for blind_id in sorted(first):
        map_row = mapping[blind_id]
        question_key = map_row["internal_question_key"]
        question = core_questions.get(question_key)
        if question is None:
            blockers.append({"blind_id": blind_id, "error": "question_not_in_qa60"})
            continue
        expected = "UNANSWERABLE" if question["组别"] == "N" else "ANSWERABLE"
        pair, errors = clean_pair(first[blind_id], second[blind_id], expected)
        if errors:
            blockers.extend(
                {"blind_id": blind_id, "error": error}
                for error in errors
            )
            continue
        pair["blind_id"] = blind_id
        pair["internal_question_key"] = question_key
        pair["source_cluster"] = question["组别"]
        pairs.append(pair)

    output_rows: list[dict] = []
    for field in FIELDS:
        agrees, eligible_n = raw_agreement(pairs, field)
        raw = agrees / eligible_n if eligible_n else float("nan")
        kappa = cohen_kappa(pairs, field)
        ac1 = gwet_ac1(pairs, field)
        raw_lo, raw_hi = bootstrap_metric(pairs, field, "raw_agreement")
        kappa_lo, kappa_hi = bootstrap_metric(pairs, field, "cohen_kappa")
        ac1_lo, ac1_hi = bootstrap_metric(pairs, field, "gwet_ac1")
        output_rows.append(
            {
                "field": field,
                "eligible_pairs": eligible_n,
                "agreements": agrees,
                "raw_agreement": round(raw, 6) if np.isfinite(raw) else "",
                "raw_agreement_ci_lo": round(raw_lo, 6) if np.isfinite(raw_lo) else "",
                "raw_agreement_ci_hi": round(raw_hi, 6) if np.isfinite(raw_hi) else "",
                "cohen_kappa": round(kappa, 6) if np.isfinite(kappa) else "",
                "cohen_kappa_ci_lo": round(kappa_lo, 6) if np.isfinite(kappa_lo) else "",
                "cohen_kappa_ci_hi": round(kappa_hi, 6) if np.isfinite(kappa_hi) else "",
                "gwet_ac1": round(ac1, 6) if np.isfinite(ac1) else "",
                "gwet_ac1_ci_lo": round(ac1_lo, 6) if np.isfinite(ac1_lo) else "",
                "gwet_ac1_ci_hi": round(ac1_hi, 6) if np.isfinite(ac1_hi) else "",
            }
        )

    if args.strict:
        if blockers:
            raise SystemExit("strict agreement has invalid score rows")
        required = {
            "answerability_correct",
            "fatal_error",
            "provisional_egts",
        }
        if any(
            row["field"] in required and row["eligible_pairs"] == 0
            for row in output_rows
        ):
            raise SystemExit("strict agreement lacks required applicable pairs")

    out_root.mkdir(parents=True)
    agreement_path = out_root / "human_agreement.csv"
    with agreement_path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)
    manifest = {
        "status": "PASS" if not blockers else "BLOCKED",
        "protocol_version": "4.3.2",
        "pairs": len(pairs),
        "questions": len({pair["internal_question_key"] for pair in pairs}),
        "blockers": blockers,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "bootstrap_seed": SEED,
        "bootstrap_rng": "numpy.random.Generator(numpy.random.PCG64(20261104))",
        "resampling_unit": "question_within_six_frozen_strata",
        "inputs": {
            "expert_1_sha256": sha256_file(Path(args.expert_1)),
            "expert_2_sha256": sha256_file(Path(args.expert_2)),
            "blind_map_sha256": sha256_file(Path(args.blind_map)),
        },
        "output_sha256": sha256_file(agreement_path),
    }
    (out_root / "human_agreement_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 2 if args.strict and blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build condition-blinded judge packets and a protected blind-ID map."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PACKET_KEYS = {
    "run_id",
    "question_id",
    "condition",
    "internal_condition",
    "provider",
    "model",
    "adapter",
    "stratum",
    "target_chunk_id",
}
TERMINAL_STATUSES = {
    "completed",
    "partial",
    "no_results",
    "failed",
    "cancelled",
    "skipped",
}
SEMANTIC_STATUSES = {"completed", "partial", "no_results"}
INFRASTRUCTURE_STATUSES = {"failed", "cancelled", "skipped"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def protected_root() -> Path:
    value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not value:
        raise SystemExit(
            "CITRUS_EXPERIMENT_L1_ROOT is required for judge packets and maps"
        )
    path = Path(value).expanduser().resolve()
    if path.is_relative_to(ROOT.resolve()):
        raise SystemExit("judge packet/map storage must be outside the public Git tree")
    return path


def first_forbidden_key(value) -> str | None:
    if isinstance(value, dict):
        hit = FORBIDDEN_PACKET_KEYS.intersection(value)
        if hit:
            return sorted(hit)[0]
        for child in value.values():
            hit_key = first_forbidden_key(child)
            if hit_key:
                return hit_key
    elif isinstance(value, list):
        for child in value:
            hit_key = first_forbidden_key(child)
            if hit_key:
                return hit_key
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-plan", default="06_runs/manifests/run_plan.csv")
    parser.add_argument(
        "--raw-root",
        default=None,
        help="protected raw root; defaults below CITRUS_EXPERIMENT_L1_ROOT",
    )
    parser.add_argument(
        "--packet-root",
        default="judge_packets",
        help="path relative to CITRUS_EXPERIMENT_L1_ROOT",
    )
    parser.add_argument(
        "--mapping-path",
        default="judge_blinding/BLIND_ID_MAP.csv",
        help="path relative to CITRUS_EXPERIMENT_L1_ROOT",
    )
    parser.add_argument(
        "--order-root",
        default="judge_blinding/reviewer_orders",
        help="reviewer-specific packet order directory below the L1 root",
    )
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument(
        "--expected-runs",
        type=int,
        default=180,
        help="formal QA60 requires 180; use 36 for the independent pilot",
    )
    parser.add_argument(
        "--batch-count",
        type=int,
        default=0,
        help="number of reviewer batches; 6 for QA60 and 3 for Pilot by default",
    )
    args = parser.parse_args()

    l1_root = protected_root()
    packet_root = (l1_root / args.packet_root).resolve()
    mapping_path = (l1_root / args.mapping_path).resolve()
    order_root = (l1_root / args.order_root).resolve()
    raw_root = (
        Path(args.raw_root).expanduser().resolve()
        if args.raw_root
        else l1_root / "06_runs" / "raw"
    )
    if (
        not packet_root.is_relative_to(l1_root)
        or not mapping_path.is_relative_to(l1_root)
        or not order_root.is_relative_to(l1_root)
    ):
        raise SystemExit("output path escapes protected L1 root")
    if raw_root.is_relative_to(ROOT.resolve()):
        raise SystemExit("raw runs must be read from protected L1 storage")
    if packet_root.exists() or mapping_path.exists() or order_root.exists():
        raise SystemExit(
            "refuse overwrite of blind packets, mapping, or reviewer orders"
        )

    with (ROOT / args.run_plan).open(encoding="utf-8-sig", newline="") as handle:
        plan = [row for row in csv.DictReader(handle) if row["kind"] == "qa"]
    with (ROOT / "03_benchmarks/qa60/qa60_CANDIDATE.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        questions = {row["题号"]: row for row in csv.DictReader(handle)}

    if len(plan) != args.expected_runs:
        raise SystemExit(
            f"expected {args.expected_runs} planned QA runs, found {len(plan)}"
        )
    if args.expected_runs % 3:
        raise SystemExit("planned run count must be divisible by three conditions")
    plan_groups: dict[str, list[dict]] = {}
    for run in plan:
        plan_groups.setdefault(run["case_id"], []).append(run)
    if len(plan_groups) * 3 != len(plan):
        raise SystemExit("each planned question must have exactly three conditions")
    for case_id, rows in plan_groups.items():
        if {row["condition"] for row in rows} != {"B0", "B1", "B2"}:
            raise SystemExit(f"{case_id}: conditions are not exactly B0/B1/B2")
        if len(rows) != 3:
            raise SystemExit(f"{case_id}: duplicate condition rows")
        if len({row["prompt"] for row in rows}) != 1:
            raise SystemExit(f"{case_id}: prompt text differs across conditions")

    candidates: list[dict] = []
    blocked: list[dict] = []
    infrastructure_runs: list[dict] = []
    for run in plan:
        raw_path = raw_root / run["condition"] / f"{run['run_id']}.json"
        if not raw_path.is_file():
            blocked.append({"run_id": run["run_id"], "reason": "RAW_MISSING"})
            continue
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except Exception:
            blocked.append(
                {"run_id": run["run_id"], "reason": "RAW_UNREADABLE"}
            )
            continue
        response = raw.get("actual_response", {})
        status = response.get("status")
        if status not in TERMINAL_STATUSES:
            blocked.append(
                {"run_id": run["run_id"], "reason": "INVALID_RAW_STATUS"}
            )
            continue
        if status in INFRASTRUCTURE_STATUSES:
            infrastructure_runs.append(
                {
                    "run_id": run["run_id"],
                    "run_status": status,
                    "reason": response.get("failure_class", "TERMINAL_NON_ANSWER"),
                }
            )
            continue
        reference_evidence = (
            raw.get("reference_evidence")
            or raw.get("frozen_reference_evidence")
            or []
        )
        if not isinstance(reference_evidence, list) or not reference_evidence:
            blocked.append(
                {
                    "run_id": run["run_id"],
                    "reason": "INDEPENDENT_REFERENCE_EVIDENCE_MISSING",
                }
            )
            continue
        question = questions[run["case_id"]]
        candidates.append(
            {
                "internal_run_key": run["run_id"],
                "internal_question_key": run["case_id"],
                "question": run["prompt"],
                "expected_answerability": (
                    "UNANSWERABLE" if question["组别"] == "N" else "ANSWERABLE"
                ),
                "source_scope": run.get("source_scope", ""),
                "run_status": status,
                "final_answer": response.get("final_answer", ""),
                "citations": response.get("citations", []),
                "system_evidence_excerpts": response.get(
                    "evidence_excerpts", []
                ),
                "frozen_reference_evidence": reference_evidence,
                "raw_run_sha256": sha256(raw_path),
                "internal_condition": run["condition"],
                "reference_evidence_sha256": canonical_sha256(reference_evidence),
            }
        )

    reference_hash_by_question: dict[str, set[str]] = {}
    for candidate in candidates:
        reference_hash_by_question.setdefault(
            candidate["internal_question_key"], set()
        ).add(candidate["reference_evidence_sha256"])
    for question_key, hashes in reference_hash_by_question.items():
        if len(hashes) != 1:
            raise SystemExit(
                f"{question_key}: semantic conditions use different frozen reference evidence"
            )

    random.Random(args.seed).shuffle(candidates)
    expected_batches = args.batch_count or (6 if len(plan) == 180 else 3)
    if expected_batches < 1:
        raise SystemExit("--batch-count must be positive")
    grouped: dict[str, list[dict]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate["internal_question_key"], []).append(candidate)
    for question_key, rows in grouped.items():
        semantic_statuses = {
            candidate["run_status"] for candidate in rows
        }
        if any(status not in SEMANTIC_STATUSES for status in semantic_statuses):
            raise SystemExit(f"{question_key}: non-semantic candidate in packet set")
        conditions = {
            candidate["internal_condition"] for candidate in rows
        }
        expected_conditions = {
            row["condition"] for row in plan_groups[question_key]
        }
        if not conditions.issubset(expected_conditions):
            raise SystemExit(f"{question_key}: packet condition is not in run plan")
    batch_by_run: dict[str, str] = {}
    for question_index, question_key in enumerate(sorted(grouped)):
        for condition_index, candidate in enumerate(grouped[question_key]):
            batch_number = (question_index + condition_index) % expected_batches + 1
            batch_by_run[candidate["internal_run_key"]] = (
                f"BLIND_BATCH_{batch_number:02d}"
            )

    packet_root.mkdir(parents=True)
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_rows: list[dict[str, str]] = []
    packet_records: list[dict[str, str]] = []
    for number, candidate in enumerate(candidates, start=1):
        blind_id = f"BLIND_{number:06d}"
        packet = {
            key: value
            for key, value in candidate.items()
            if key
            not in {
                "internal_run_key",
                "internal_question_key",
                "internal_condition",
                "raw_run_sha256",
                "reference_evidence_sha256",
            }
        }
        batch_id = batch_by_run[candidate["internal_run_key"]]
        packet["batch_id"] = batch_id
        leaked_key = first_forbidden_key(packet)
        if leaked_key:
            raise SystemExit(
                f"forbidden condition/key leakage in {blind_id}: {leaked_key}"
            )
        serialized_packet = json.dumps(
            packet,
            ensure_ascii=False,
            sort_keys=True,
        )
        if candidate["raw_run_sha256"] in serialized_packet:
            raise SystemExit(f"raw run SHA leaked into packet: {blind_id}")
        packet_path = packet_root / f"{blind_id}.json"
        packet_path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        packet_sha = sha256(packet_path)
        mapping_rows.append(
            {
                "blind_id": blind_id,
                "batch_id": batch_id,
                "internal_run_key": candidate["internal_run_key"],
                "internal_question_key": candidate["internal_question_key"],
                "condition_internal": candidate["internal_condition"],
                "raw_run_sha256": candidate["raw_run_sha256"],
                "packet_sha256": packet_sha,
            }
        )
        packet_records.append(
            {
                "blind_id": blind_id,
                "batch_id": batch_id,
                "packet_sha256": packet_sha,
            }
        )

    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    with mapping_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "blind_id",
                "batch_id",
                "internal_run_key",
                "internal_question_key",
                "condition_internal",
                "raw_run_sha256",
                "packet_sha256",
            ],
        )
        writer.writeheader()
        writer.writerows(mapping_rows)

    order_root.mkdir(parents=True)
    order_records: dict[str, dict[str, str]] = {}
    batches = sorted({record["batch_id"] for record in packet_records})
    for reviewer_index, reviewer_slot in enumerate(
        ("reviewer_1", "reviewer_2"),
        start=1,
    ):
        rng = random.Random(args.seed + reviewer_index * 1_000_003)
        ordered_rows: list[dict[str, str]] = []
        position = 1
        for batch_id in batches:
            batch_records = [
                record
                for record in packet_records
                if record["batch_id"] == batch_id
            ]
            rng.shuffle(batch_records)
            for record in batch_records:
                ordered_rows.append(
                    {
                        "reviewer_slot": reviewer_slot,
                        "batch_id": batch_id,
                        "position": str(position),
                        "blind_id": record["blind_id"],
                        "packet_sha256": record["packet_sha256"],
                    }
                )
                position += 1
        order_path = order_root / f"{reviewer_slot}.csv"
        with order_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "reviewer_slot",
                    "batch_id",
                    "position",
                    "blind_id",
                    "packet_sha256",
                ],
            )
            writer.writeheader()
            writer.writerows(ordered_rows)
        order_records[reviewer_slot] = {
            "path": order_path.relative_to(l1_root).as_posix(),
            "sha256": sha256(order_path),
            "rows": str(len(ordered_rows)),
        }

    manifest = {
        "status": "READY"
        if not blocked
        and len(candidates) + len(infrastructure_runs) == len(plan)
        else "BLOCKED",
        "expected_runs": len(plan),
        "packet_count": len(packet_records),
        "semantic_packet_count": len(packet_records),
        "infrastructure_run_count": len(infrastructure_runs),
        "blocked": blocked,
        "infrastructure_runs": infrastructure_runs,
        "packets": packet_records,
        "batch_count": expected_batches,
        "batch_sizes": {
            batch: sum(1 for record in packet_records if record["batch_id"] == batch)
            for batch in sorted({record["batch_id"] for record in packet_records})
        },
        "same_question_batches_distinct": all(
            len(
                {
                    batch_by_run[record["internal_run_key"]]
                    for record in records
                }
            )
            == len(records)
            for records in grouped.values()
        ),
        "planned_runs_exactly_three_conditions_per_question": True,
        "same_question_prompt_identical": all(
            len({row["prompt"] for row in rows}) == 1
            for rows in plan_groups.values()
        ),
        "same_question_reference_evidence_sha256_identical": all(
            len(hashes) == 1 for hashes in reference_hash_by_question.values()
        ),
        "raw_run_sha256_packet_excluded": True,
        "mapping_path_is_l1": True,
        "reviewer_orders": order_records,
        "condition_fields_excluded": True,
        "independent_reference_evidence_required": True,
    }
    (packet_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "packets": len(packet_records),
                "infrastructure_runs": len(infrastructure_runs),
                "blocked": len(blocked),
                "protected_packet_root": str(packet_root),
                "protected_mapping_path": str(mapping_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if manifest["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())

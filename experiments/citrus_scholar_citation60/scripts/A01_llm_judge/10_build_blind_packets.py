#!/usr/bin/env python3
"""Build condition-blinded judge packets from completed raw QA runs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-plan", default="06_runs/manifests/run_plan.csv")
    parser.add_argument("--raw-root", default="06_runs/raw")
    parser.add_argument("--out-dir", default="06_runs/judge_packets")
    args = parser.parse_args()

    out_dir = ROOT / args.out_dir
    if out_dir.exists():
        raise SystemExit(f"refuse overwrite: {out_dir}")
    with (ROOT / args.run_plan).open(encoding="utf-8-sig", newline="") as handle:
        plan = [row for row in csv.DictReader(handle) if row["kind"] == "qa"]
    with (ROOT / "03_benchmarks/qa60/qa60_CANDIDATE.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        question_rows = {row["题号"]: row for row in csv.DictReader(handle)}

    out_dir.mkdir(parents=True)
    packets = []
    skipped = []
    for run in plan:
        raw_path = ROOT / args.raw_root / run["condition"] / f"{run['run_id']}.json"
        if not raw_path.is_file():
            skipped.append({"run_id": run["run_id"], "reason": "RAW_MISSING"})
            continue
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        response = raw.get("actual_response", {})
        if response.get("status") not in {
            "completed",
            "partial",
            "failed",
            "no_results",
            "cancelled",
            "skipped",
        }:
            skipped.append(
                {"run_id": run["run_id"], "reason": "INVALID_RAW_STATUS"}
            )
            continue
        question = question_rows[run["case_id"]]
        packet = {
            "run_id": run["run_id"],
            "question_id": run["case_id"],
            "question": run["prompt"],
            "expected_answerability": (
                "UNANSWERABLE" if question["组别"] == "N" else "ANSWERABLE"
            ),
            "run_status": response["status"],
            "final_answer": response.get("final_answer", ""),
            "citations": response.get("citations", []),
            "evidence_excerpts": response.get("evidence_excerpts", []),
            "raw_run_sha256": sha256(raw_path),
        }
        target = out_dir / f"{run['run_id']}.json"
        target.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        packets.append(
            {
                "run_id": run["run_id"],
                "packet_file": target.relative_to(ROOT).as_posix(),
                "packet_sha256": sha256(target),
                "raw_run_sha256": packet["raw_run_sha256"],
            }
        )
    manifest = {
        "status": "READY" if not skipped else "PARTIAL",
        "expected_runs": len(plan),
        "packet_count": len(packets),
        "skipped": skipped,
        "packets": packets,
        "condition_fields_excluded": True,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "packets": len(packets),
                "skipped": len(skipped),
            }
        )
    )
    return 0 if packets else 2


if __name__ == "__main__":
    raise SystemExit(main())

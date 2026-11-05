#!/usr/bin/env python3
"""Create a blank v4.3.2 LLM-judge aggregation sheet; never overwrite."""
from __future__ import annotations

import argparse

from common import ROOT, csvrows, write_csv


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="07_scoring/judge/qa_judge_from_runs.csv"
    )
    args = parser.parse_args()
    target = ROOT / args.out
    if target.exists():
        raise SystemExit(f"refuse overwrite: {target}")
    template = csvrows("07_scoring/judge/qa_judge_TEMPLATE_180.csv")
    fieldnames = list(template[0])
    runs = [
        row for row in csvrows("06_runs/manifests/run_plan.csv")
        if row["kind"] == "qa"
    ]
    rows = []
    for run in runs:
        rows.append(
            {
                **{field: "" for field in fieldnames},
                "run_id": run["run_id"],
                "question_id": run["case_id"],
                "condition": run["condition"],
                "source_cluster": run["source_ids"].split("+")[0],
                "run_status": "PENDING",
                "judge_1_status": "PENDING",
                "judge_2_status": "PENDING",
                "j3_required": "false",
                "j3_status": "NOT_STARTED",
                "machine_consensus_status": "PENDING",
                "strict_judge_status": "PENDING",
            }
        )
    write_csv(args.out, rows, fields=fieldnames)
    print(f"Wrote blank judge sheet {args.out} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

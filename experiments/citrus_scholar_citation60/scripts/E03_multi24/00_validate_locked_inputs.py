#!/usr/bin/env python3
"""Validate that the composite plan is covered by the written protocol lock."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    lock_path = ROOT / "00_protocol/locked_manifest.json"
    if not lock_path.is_file():
        raise SystemExit("formal protocol lock missing")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    rel = "03_benchmarks/intent/composite24_CANDIDATE.csv"
    if rel not in lock.get("file_hashes", {}):
        raise SystemExit("composite input is not present in protocol lock")
    with (ROOT / "06_runs/manifests/run_plan.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = [row for row in csv.DictReader(handle) if row["kind"] == "multi"]
    if len(rows) != 24 or len({row["case_id"] for row in rows}) != 24:
        raise SystemExit("composite plan must contain 24 unique cases")
    print(json.dumps({"status": "PASS", "cases": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

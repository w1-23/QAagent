#!/usr/bin/env python3
"""Validate the preregistered 12-question repeat panel."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    with (ROOT / "06_runs/manifests/run_plan.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = [row for row in csv.DictReader(handle) if row["kind"] == "repeat"]
    if len(rows) not in {0, 72}:
        raise SystemExit(f"repeat plan must be absent or 72 rows, found {len(rows)}")
    print(json.dumps({"status": "PASS", "repeat_rows": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check that all core QA60 questions passed the unified validity lock."""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    lock = ROOT / "07_scoring/validity/question_validity_LOCKED.csv"
    if not lock.is_file():
        print(json.dumps({"status": "BLOCKED", "reason": "validity lock missing"}))
        return 2
    with lock.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["experiment_id"] == "E01_QA60_EGTS"
        ]
    accepted = [
        row for row in rows if row["final_decision"].strip().upper() == "ACCEPT"
    ]
    passed = len(rows) == 60 and len(accepted) == 60 and len(
        {row["question_id"] for row in accepted}
    ) == 60
    print(
        json.dumps(
            {
                "status": "PASS" if passed else "BLOCKED",
                "rows": len(rows),
                "accepted": len(accepted),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

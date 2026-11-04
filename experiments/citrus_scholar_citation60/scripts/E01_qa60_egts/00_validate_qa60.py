#!/usr/bin/env python3
"""Validate the core QA60 candidate without creating any answer labels."""
from __future__ import annotations

import collections
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    path = ROOT / "03_benchmarks/qa60/qa60_CANDIDATE.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    blockers = []
    counts = collections.Counter(row["组别"] for row in rows)
    if len(rows) != 60 or len({row["题号"] for row in rows}) != 60:
        blockers.append("QA60 must contain 60 unique question IDs")
    if counts != {"L": 12, "V": 10, "W": 10, "X": 12, "P": 8, "N": 8}:
        blockers.append(f"unexpected stratum counts: {counts}")
    if any(
        "gold" in column.lower() or "answer" in column.lower()
        for column in (rows[0] if rows else {})
    ):
        blockers.append("Gold/Answer columns are prohibited")
    print(
        json.dumps(
            {
                "status": "PASS" if not blockers else "FAIL",
                "questions": len(rows),
                "strata": counts,
                "blockers": blockers,
                "source_sha256": __import__("hashlib").sha256(
                    path.read_bytes()
                ).hexdigest(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())

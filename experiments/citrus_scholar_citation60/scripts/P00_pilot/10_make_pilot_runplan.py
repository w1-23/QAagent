#!/usr/bin/env python3
"""Freeze a 12-question Pilot source into a deterministic 36-run plan."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STRATA = ("L", "V", "W", "X", "P", "N")
CONDITIONS = ("B0", "B1", "B2")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    source = Path(args.source).expanduser().resolve()
    output = Path(args.out).expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Pilot source missing: {source}")
    if output.exists():
        raise SystemExit(f"refuse overwrite: {output}")
    rows = read_rows(source)
    required = {"pilot_id", "prompt", "source_scope", "stratum"}
    missing = sorted(required - set(rows[0] if rows else {}))
    if missing:
        raise SystemExit("Pilot source schema missing: " + ",".join(missing))
    ids = [row["pilot_id"].strip() for row in rows]
    if len(rows) != 12 or len(set(ids)) != 12 or any(not value for value in ids):
        raise SystemExit("Pilot source must contain exactly 12 unique pilot IDs")
    counts = Counter(row["stratum"].strip() for row in rows)
    if counts != Counter({stratum: 2 for stratum in STRATA}):
        raise SystemExit(
            "Pilot source must contain exactly two rows per L/V/W/X/P/N stratum"
        )

    planned: list[dict[str, str]] = []
    order = 0
    for condition in CONDITIONS:
        for row in rows:
            order += 1
            stratum = row["stratum"].strip()
            pilot_id = row["pilot_id"].strip()
            planned.append(
                {
                    "run_id": f"PILOT_{pilot_id}_{condition}_T1",
                    "pilot_id": pilot_id,
                    "condition": condition,
                    "trial": "1",
                    "stratum": stratum,
                    "prompt": row["prompt"].strip(),
                    "source_scope": row["source_scope"].strip(),
                    "attachment": row.get("attachment", "").strip(),
                    "reference_evidence_json": row.get(
                        "reference_evidence_json", ""
                    ).strip(),
                    "materials_preflight": row.get(
                        "materials_preflight", ""
                    ).strip(),
                    "availability_status": row.get(
                        "availability_status", ""
                    ).strip(),
                    "run_order": str(order),
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(planned[0]))
        writer.writeheader()
        writer.writerows(planned)
    manifest = {
        "status": "PILOT_PLAN_FROZEN",
        "not_formal_denominator": True,
        "source": str(source),
        "source_sha256": sha256_file(source),
        "plan": str(output),
        "plan_sha256": sha256_file(output),
        "questions": len(rows),
        "runs": len(planned),
        "conditions": list(CONDITIONS),
        "strata_counts": dict(sorted(counts.items())),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

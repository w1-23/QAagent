#!/usr/bin/env python3
"""Write qa60_LOCKED.csv once after all 60 validity approvals exist."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    candidate = ROOT / "03_benchmarks/qa60/qa60_CANDIDATE.csv"
    validity = ROOT / "07_scoring/validity/question_validity_LOCKED.csv"
    target = ROOT / "03_benchmarks/qa60/qa60_LOCKED.csv"
    manifest = ROOT / "03_benchmarks/qa60/qa60_LOCKED.manifest.json"
    if target.exists() or manifest.exists():
        raise SystemExit("refuse overwrite of QA60 lock")
    if not validity.is_file():
        raise SystemExit("unified validity lock missing")

    with candidate.open(encoding="utf-8-sig", newline="") as handle:
        questions = list(csv.DictReader(handle))
    with validity.open(encoding="utf-8-sig", newline="") as handle:
        approved = {
            row["question_id"]: row
            for row in csv.DictReader(handle)
            if row["experiment_id"] == "E01_QA60_EGTS"
            and row["final_decision"].strip().upper() == "ACCEPT"
        }
    if len(questions) != 60 or len(approved) != 60:
        raise SystemExit("QA60 requires 60 candidate and 60 approved questions")
    missing = {row["题号"] for row in questions} - set(approved)
    if missing:
        raise SystemExit(f"validity approval missing: {sorted(missing)}")
    for row in questions:
        prompt_hash = hashlib.sha256(
            row["完整题目"].strip().encode("utf-8")
        ).hexdigest()
        if approved[row["题号"]]["prompt_sha256"] != prompt_hash:
            raise SystemExit(f"{row['题号']}: validity prompt SHA stale")

    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(questions[0]))
        writer.writeheader()
        writer.writerows(questions)
    manifest.write_text(
        json.dumps(
            {
                "status": "LOCKED",
                "question_count": len(questions),
                "candidate_sha256": sha256(candidate),
                "validity_lock_sha256": sha256(validity),
                "qa_locked_sha256": sha256(target),
                "gold_answer_created": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "LOCKED", "questions": len(questions)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

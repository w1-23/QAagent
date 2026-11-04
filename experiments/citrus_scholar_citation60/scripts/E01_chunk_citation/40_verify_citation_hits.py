#!/usr/bin/env python3
"""Extract citation evidence from real runs and score target-chunk/source hits."""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]
CONDS = ("B0", "B1", "B2")
CORPUS_MARKERS = {
    "cqa_literature": ("cqa_literature", "english_literature/"),
    "cqa_methods": ("cqa_methods", "cgi_methods/"),
    "cqa_deng_book": ("cqa_deng_book", "deng_xiuxin/"),
    "cqa_translated": ("cqa_translated", "translated/"),
    "cqa_ucr_varieties": ("cqa_ucr_varieties", "ucr_varieties/"),
}


def scalar_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for v in value.values():
            out.extend(scalar_strings(v))
        return out
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for v in value:
            out.extend(scalar_strings(v))
        return out
    return []


def citation_blobs(run: dict) -> list[str]:
    response = run.get("actual_response") if isinstance(run.get("actual_response"), dict) else run
    values: list[Any] = []
    for key in ("citations", "sources"):
        value = response.get(key)
        if value is not None:
            values.append(value)
    return scalar_strings(values)


def contains_identifier(blobs: list[str], identifier: str) -> bool:
    if not identifier:
        return False
    needle = identifier.strip().lower()
    for blob in blobs:
        text = blob.strip().lower()
        if needle == text or needle in text:
            return True
    return False


def contains_any_marker(blobs: list[str], markers: tuple[str, ...]) -> bool:
    return any(contains_identifier(blobs, marker) for marker in markers)


def has_citation_signal(blobs: list[str], response: dict) -> bool:
    if blobs:
        return True
    answer = str(response.get("final_answer") or response.get("raw_answer") or "")
    return bool(re.search(
        r"\[\d+\]|https?://|doi\.org/|(?:来源|source)\s*[:：]",
        answer,
        flags=re.IGNORECASE,
    ))


def run_path(run_id: str, condition: str) -> Path:
    return ROOT / "06_runs" / "raw" / condition / f"{run_id}.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-dir", default="03_benchmarks/citation60")
    parser.add_argument("--run-plan", default="06_runs/manifests/citation_run_plan.csv")
    parser.add_argument("--out", default="07_scoring/reviews/citation_hit_observations.csv")
    parser.add_argument("--summary", default="09_analysis/tables/citation_hit_rates.csv")
    args = parser.parse_args()
    bench = (ROOT / args.benchmark_dir).resolve()
    qmap = {
        q["question_id"]: q
        for q in (
            json.loads(line)
            for line in (bench / "questions_FROZEN.jsonl").open("r", encoding="utf-8")
            if line.strip()
        )
    }
    with (ROOT / args.run_plan).open(encoding="utf-8-sig", newline="") as f:
        plan = list(csv.DictReader(f))
    observations: list[dict] = []
    for row in plan:
        q = qmap[row["case_id"]]
        path = run_path(row["run_id"], row["condition"])
        if not path.is_file():
            observations.append({
                "run_id": row["run_id"], "question_id": q["question_id"],
                "corpus_id": q["target_corpus_id"], "condition": row["condition"],
                "run_status": "MISSING_RAW", "target_chunk_cited": "",
                "target_document_cited": "", "allowed_corpus_cited": "",
                "wrong_corpus_cited": "", "no_citation": "",
                "notes": "raw run missing",
            })
            continue
        run = json.loads(path.read_text(encoding="utf-8"))
        response = run.get("actual_response") if isinstance(run.get("actual_response"), dict) else run
        blobs = citation_blobs(run)
        chunk_hit = contains_identifier(blobs, q["target_chunk_id"])
        doc_hit = any(contains_identifier(blobs, q[k]) for k in ("target_paper_id", "target_source_file"))
        corpus_hit = contains_any_marker(blobs, CORPUS_MARKERS[q["target_corpus_id"]])
        wrong_corpus = any(
            contains_any_marker(blobs, markers)
            for corpus_id, markers in CORPUS_MARKERS.items()
            if corpus_id != q["target_corpus_id"]
        )
        any_citation = has_citation_signal(blobs, response)
        observations.append({
            "run_id": row["run_id"],
            "question_id": q["question_id"],
            "corpus_id": q["target_corpus_id"],
            "condition": row["condition"],
            "run_status": str(response.get("status") or "UNKNOWN"),
            "target_chunk_cited": str(int(chunk_hit)),
            "target_document_cited": str(int(doc_hit or chunk_hit)),
            "allowed_corpus_cited": str(int(corpus_hit or doc_hit or chunk_hit)),
            "wrong_corpus_cited": str(int(wrong_corpus)),
            "no_citation": str(int(not any_citation)),
            "notes": "deterministic final-citation locator match only; no semantic-support claim",
        })
    out = ROOT / args.out
    if out.exists():
        raise SystemExit(f"refuse overwrite: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = list(observations[0]) if observations else [
        "run_id", "question_id", "corpus_id", "condition", "run_status",
        "target_chunk_cited", "target_document_cited", "allowed_corpus_cited",
        "wrong_corpus_cited", "no_citation", "notes",
    ]
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(observations)

    def wilson(success: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
        if n == 0:
            return (float("nan"), float("nan"))
        p = success / n
        den = 1 + z * z / n
        center = (p + z * z / (2 * n)) / den
        radius = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
        return max(0.0, center - radius), min(1.0, center + radius)

    summary_rows: list[dict] = []
    for corpus in sorted({r["corpus_id"] for r in observations}):
        for condition in CONDS:
            subset = [
                r for r in observations
                if r["corpus_id"] == corpus and r["condition"] == condition
                and r["run_status"] != "MISSING_RAW"
            ]
            n = len(subset)
            def count(field: str) -> int:
                return sum(r[field] == "1" for r in subset)
            chunk_hits = count("target_chunk_cited")
            doc_hits = count("target_document_cited")
            corpus_hits = count("allowed_corpus_cited")
            lo, hi = wilson(chunk_hits, n)
            summary_rows.append({
                "corpus_id": corpus, "condition": condition, "n": n,
                "target_chunk_cites": chunk_hits,
                "target_chunk_cite_rate": round(chunk_hits / n, 6) if n else "",
                "target_chunk_wilson_lo": round(lo, 6) if n else "",
                "target_chunk_wilson_hi": round(hi, 6) if n else "",
                "target_document_cites": doc_hits,
                "target_document_rate": round(doc_hits / n, 6) if n else "",
                "allowed_corpus_cites": corpus_hits,
                "allowed_corpus_rate": round(corpus_hits / n, 6) if n else "",
                "missing_raw": sum(
                    1 for r in observations
                    if r["corpus_id"] == corpus and r["condition"] == condition
                    and r["run_status"] == "MISSING_RAW"
                ),
            })
    summary = ROOT / args.summary
    if summary.exists():
        raise SystemExit(f"refuse overwrite: {summary}")
    summary.parent.mkdir(parents=True, exist_ok=True)
    with summary.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)
    print(json.dumps({
        "status": "BLOCKED_MISSING_RAW"
        if any(r["run_status"] == "MISSING_RAW" for r in observations)
        else "WRITTEN",
        "observations": str(out),
        "summary": str(summary),
        "rows": len(observations),
        "missing_raw": sum(
            r["run_status"] == "MISSING_RAW" for r in observations
        ),
        "note": "automatic locator hits only; semantic-support is intentionally not reported",
    }, ensure_ascii=False, indent=2))
    return 2 if any(
        r["run_status"] == "MISSING_RAW" for r in observations
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())


#!/usr/bin/env python3
"""Audit public citation anchors against protected frozen source chunks.

The audit does not print source passages. It reports only identifiers and
contiguous-match lengths so the release decision can be reviewed without
creating another text-bearing public artifact.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "03_benchmarks" / "citation60"
DEFAULT_MAX_CONTIGUOUS = 50


def normalize(text: str) -> str:
    text = text.casefold().replace("\u00d7", "x")
    text = re.sub(r"[^\w%\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def longest_contiguous_match(left: str, right: str, *, normalize_text: bool) -> int:
    if normalize_text:
        left = normalize(left)
        right = normalize(right)
    else:
        left = left.casefold()
        right = right.casefold()
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    block = matcher.find_longest_match(0, len(left), 0, len(right))
    return block.size


def load_chunks(path: Path) -> dict[str, dict]:
    chunks: dict[str, dict] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            citation_id = row["citation_id"]
            if citation_id in chunks:
                raise ValueError(f"duplicate citation_id in chunks: {citation_id}")
            chunks[citation_id] = row
    return chunks


def load_anchors() -> dict[str, list[dict[str, str]]]:
    with (BENCH / "questions_DRAFT.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        draft = [
            {
                "citation_id": row["citation_id"],
                "anchor_for_audit": row["anchor_for_audit"],
            }
            for row in csv.DictReader(handle)
        ]

    frozen: list[dict[str, str]] = []
    with (BENCH / "questions_FROZEN.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            frozen.append(
                {
                    "citation_id": row["question_id"],
                    "anchor_for_audit": row["anchor_for_audit"],
                }
            )
    return {
        "questions_DRAFT.csv": draft,
        "questions_FROZEN.jsonl": frozen,
    }


def audit_anchor_sets(
    anchor_sets: dict[str, Iterable[dict[str, str]]],
    chunks: dict[str, dict],
    *,
    max_contiguous: int = DEFAULT_MAX_CONTIGUOUS,
) -> dict:
    files: dict[str, dict] = {}
    issues: list[str] = []
    for filename, records in anchor_sets.items():
        rows = list(records)
        observed_ids: set[str] = set()
        max_exact = 0
        max_normalized = 0
        worst_id = ""
        over_threshold: list[str] = []
        for row in rows:
            citation_id = row["citation_id"]
            anchor = row.get("anchor_for_audit", "")
            if citation_id in observed_ids:
                issues.append(f"{filename}: duplicate citation_id {citation_id}")
            observed_ids.add(citation_id)
            chunk = chunks.get(citation_id)
            if not chunk or not chunk.get("chunk_text"):
                issues.append(f"{filename}: missing protected chunk {citation_id}")
                continue
            if not anchor:
                issues.append(f"{filename}: empty anchor {citation_id}")
                continue
            exact = longest_contiguous_match(
                anchor, chunk["chunk_text"], normalize_text=False
            )
            normalized = longest_contiguous_match(
                anchor, chunk["chunk_text"], normalize_text=True
            )
            if exact > max_exact:
                max_exact = exact
                worst_id = citation_id
            max_normalized = max(max_normalized, normalized)
            if exact >= max_contiguous or normalized >= max_contiguous:
                over_threshold.append(citation_id)
                issues.append(
                    f"{filename}: {citation_id} has a contiguous match "
                    f">= {max_contiguous} characters"
                )
        if len(rows) != 60 or len(observed_ids) != 60:
            issues.append(
                f"{filename}: expected 60 unique anchors; "
                f"got rows={len(rows)} unique={len(observed_ids)}"
            )
        files[filename] = {
            "anchors": len(rows),
            "unique_ids": len(observed_ids),
            "max_exact_contiguous_chars": max_exact,
            "max_normalized_contiguous_chars": max_normalized,
            "worst_citation_id": worst_id,
            "over_threshold_count": len(set(over_threshold)),
        }

    return {
        "status": "PASS" if not issues else "FAIL",
        "max_contiguous_allowed_chars": max_contiguous,
        "files": files,
        "issues": issues,
    }


def default_chunks_path() -> Path:
    protected_root = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if protected_root:
        return (
            Path(protected_root)
            / "03_benchmarks"
            / "citation60"
            / "chunks_FROZEN.jsonl"
        )
    return BENCH / "chunks_FROZEN.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=Path, default=None)
    parser.add_argument(
        "--max-contiguous",
        type=int,
        default=DEFAULT_MAX_CONTIGUOUS,
    )
    args = parser.parse_args()

    chunks_path = args.chunks or default_chunks_path()
    if not chunks_path.is_file():
        result = {
            "status": "BLOCKED",
            "reason": "protected chunks file is unavailable",
            "chunks_path": str(chunks_path),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3

    result = audit_anchor_sets(
        load_anchors(),
        load_chunks(chunks_path),
        max_contiguous=args.max_contiguous,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

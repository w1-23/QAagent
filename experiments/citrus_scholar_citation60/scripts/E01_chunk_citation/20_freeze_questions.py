#!/usr/bin/env python3
"""Validate and freeze one authored question for each frozen chunk."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", text.lower())


def ngrams(seq: list[str], n: int) -> set[tuple[str, ...]]:
    if len(seq) < n:
        return set()
    return {tuple(seq[i:i + n]) for i in range(len(seq) - n + 1)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-dir", default="03_benchmarks/citation60")
    parser.add_argument("--drafts", default="questions_DRAFT.csv")
    args = parser.parse_args()
    bench = (ROOT / args.benchmark_dir).resolve()
    l1_root = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not l1_root:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    protected_bench = (
        Path(l1_root).expanduser().resolve()
        / args.benchmark_dir.replace("/", os.sep)
    )
    chunks_path = protected_bench / "chunks_FROZEN.jsonl"
    drafts_path = protected_bench / args.drafts
    if not chunks_path.is_file() or not drafts_path.is_file():
        raise SystemExit("frozen chunks or question drafts missing")

    chunks: dict[str, dict] = {}
    with chunks_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                chunks[row["citation_id"]] = row
    with drafts_path.open(encoding="utf-8-sig", newline="") as f:
        drafts = list(csv.DictReader(f))
    errors: list[str] = []
    if len(drafts) != len(chunks):
        errors.append(f"draft count {len(drafts)} != chunk count {len(chunks)}")
    ids = [str(row.get("citation_id", "")).strip() for row in drafts]
    if len(ids) != len(set(ids)):
        errors.append("duplicate citation_id in drafts")
    if set(ids) != set(chunks):
        errors.append(f"citation_id mismatch missing={sorted(set(chunks)-set(ids))} extra={sorted(set(ids)-set(chunks))}")

    questions: list[dict] = []
    question_texts: list[str] = []
    for row in drafts:
        cid = str(row.get("citation_id", "")).strip()
        question = re.sub(r"\s+", " ", str(row.get("question", "")).strip())
        if not question:
            errors.append(f"{cid}: empty question")
            continue
        if cid not in chunks:
            continue
        if str(row.get("answerable_from_chunk", "")).strip().upper() not in {"YES", "Y", "1", "TRUE"}:
            errors.append(f"{cid}: answerable_from_chunk must be YES")
        chunk = chunks[cid]
        overlap = ngrams(words(question), 8) & ngrams(words(str(chunk["chunk_text"])), 8)
        if overlap:
            errors.append(f"{cid}: question shares an 8-token span with source chunk")
        leak_terms = [chunk["chunk_id"], chunk["source_file"]]
        if any(term and term.lower() in question.lower() for term in leak_terms):
            errors.append(f"{cid}: question exposes target locator")
        question_texts.append(question.lower())
        frozen = {
            "question_id": cid,
            "citation_id": cid,
            "question": question,
            "question_language": "zh",
            "target_chunk_id": chunk["chunk_id"],
            "target_paper_id": chunk["paper_id"],
            "target_chunk_index": chunk["chunk_index"],
            "target_corpus_id": chunk["corpus_id"],
            "target_source_file": chunk["source_file"],
            "target_title": chunk["title"],
            "target_chunk_text_sha256": chunk["chunk_text_sha256"],
            "allowed_source_scope": chunk["source_scope"],
            "forbidden_source_scope": "all corpora except allowed_source_scope",
            "question_generation_method": "agent_read_frozen_chunk_then_reverse_generate_v1",
            "anchor_for_audit": str(row.get("anchor_for_audit", "")).strip(),
            "generation_notes": str(row.get("generation_notes", "")).strip(),
            "answerable_from_chunk": "YES",
            "one_chunk_only": True,
            "question_sha256": sha256_text(question),
            "frozen_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds"),
        }
        frozen["freeze_record_sha256"] = sha256_text(canonical(frozen))
        questions.append(frozen)

    if len(question_texts) != len(set(question_texts)):
        errors.append("duplicate question text")
    if errors:
        raise SystemExit("question freeze validation failed:\n" + "\n".join(errors[:50]))

    questions_path = bench / "questions_FROZEN.jsonl"
    manifest_path = bench / "question_manifest.csv"
    lock_path = bench / "questions_FROZEN.manifest.json"
    for path in (questions_path, manifest_path, lock_path):
        if path.exists():
            raise SystemExit(f"refuse overwrite: {path}")
    questions.sort(key=lambda row: row["question_id"])
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in questions)
    tmp = questions_path.with_suffix(".jsonl.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, questions_path)
    os.chmod(questions_path, stat.S_IREAD)
    fields = [
        "question_id", "citation_id", "question", "target_corpus_id",
        "target_chunk_id", "target_paper_id", "target_source_file",
        "target_chunk_text_sha256", "question_sha256", "freeze_record_sha256",
    ]
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in questions:
            writer.writerow({key: row.get(key, "") for key in fields})
    os.chmod(manifest_path, stat.S_IREAD)
    lock = {
        "status": "FROZEN",
        "question_count": len(questions),
        "corpus_counts": dict(Counter(row["target_corpus_id"] for row in questions)),
        "one_chunk_per_question": True,
        "chunks_frozen_sha256": hashlib.sha256(chunks_path.read_bytes()).hexdigest(),
        "questions_frozen_sha256": hashlib.sha256(questions_path.read_bytes()).hexdigest(),
        "question_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(lock_path, stat.S_IREAD)
    print(json.dumps(lock, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


#!/usr/bin/env python3
"""Sample one frozen chunk per question from each public corpus.

Fail-closed: refuses to overwrite a previous freeze. The full chunk text is
stored only in the offline benchmark; run prompts never include this file.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve()
ROOT = SCRIPT_DIR.parents[2]
sys.path.insert(0, str(SCRIPT_DIR.parents[1]))
from common import sha256_file, utc  # noqa: E402

CORPUS_PREFIX = {
    "cqa_literature": "LIT",
    "cqa_methods": "MTH",
    "cqa_deng_book": "DENG",
    "cqa_translated": "TRANS",
    "cqa_ucr_varieties": "UCR",
}


def atomic_write_text(path: Path, text: str, *, read_only: bool = False) -> None:
    if path.exists():
        raise SystemExit(f"refuse overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    if read_only:
        os.chmod(path, stat.S_IREAD)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_active_registry(data_dir: Path) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    active_path = data_dir / "active_index.json"
    if not active_path.is_file():
        raise SystemExit(f"active index missing: {active_path}")
    active = json.loads(active_path.read_text(encoding="utf-8"))
    registry_path = data_dir / str(active.get("registry_path") or "")
    if not registry_path.is_file():
        raise SystemExit(f"active registry missing: {registry_path}")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    return active, registry, active_path, registry_path


def corpus_specs(registry: dict[str, Any], requested: list[str]) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for key, spec in registry.get("corpora", {}).items():
        display = str(spec.get("display_name") or spec.get("source") or key)
        if display in requested:
            specs[display] = dict(spec)
    missing = sorted(set(requested) - set(specs))
    if missing:
        raise SystemExit(f"requested corpus not present in active registry: {missing}")
    return specs


def eligible_row(row: dict[str, Any], min_chars: int, max_chars: int) -> bool:
    text = str(row.get("text") or "").strip()
    if not (min_chars <= len(text) <= max_chars):
        return False
    if not str(row.get("chunk_id") or "").strip():
        return False
    if not str(row.get("paper_id") or "").strip():
        return False
    if not str(row.get("source_file") or row.get("source") or "").strip():
        return False
    # Remove obvious heading/footer-only records while preserving random sampling.
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) < min_chars:
        return False
    return True


def read_eligible_rows(
    chunks_path: Path,
    *,
    min_chars: int,
    max_chars: int,
) -> list[tuple[int, dict[str, Any], str]]:
    rows: list[tuple[int, dict[str, Any], str]] = []
    with chunks_path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if eligible_row(row, min_chars, max_chars):
                rows.append((line_number, row, line))
    return rows


def choose_rows(
    rows: list[tuple[int, dict[str, Any], str]],
    *,
    corpus_name: str,
    seed: int,
    per_corpus: int,
) -> list[tuple[int, dict[str, Any], str]]:
    """Select reproducibly after sorting by the immutable chunk identifier."""
    ordered = sorted(rows, key=lambda item: str(item[1].get("chunk_id") or ""))
    rng = random.Random(f"{seed}:{corpus_name}")
    return rng.sample(ordered, per_corpus)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, help="CitrusScholar runtime data directory")
    parser.add_argument("--out-dir", default="03_benchmarks/citation60")
    parser.add_argument("--per-corpus", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20261104)
    parser.add_argument("--min-chars", type=int, default=300)
    parser.add_argument("--max-chars", type=int, default=3500)
    parser.add_argument(
        "--corpora",
        nargs="+",
        default=list(CORPUS_PREFIX),
        help="corpus display names registered in active_index",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    out_dir = (ROOT / args.out_dir).resolve()
    if ROOT.resolve() not in out_dir.parents:
        raise SystemExit("output escaped experiment root")
    if args.per_corpus < 1:
        raise SystemExit("--per-corpus must be >= 1")

    active, registry, active_path, registry_path = load_active_registry(data_dir)
    specs = corpus_specs(registry, args.corpora)
    sampled: list[dict[str, Any]] = []
    sampling_report: dict[str, Any] = {
        "seed": args.seed,
        "per_corpus": args.per_corpus,
        "min_chars": args.min_chars,
        "max_chars": args.max_chars,
        "corpora": {},
    }

    for corpus_name in args.corpora:
        spec = specs[corpus_name]
        chunks_rel = str(spec.get("chunks_path") or "").strip()
        if not chunks_rel:
            raise SystemExit(f"registry has no chunks_path for {corpus_name}")
        chunks_path = (data_dir / chunks_rel).resolve()
        if data_dir not in chunks_path.parents or not chunks_path.is_file():
            raise SystemExit(f"unsafe/missing chunks path for {corpus_name}: {chunks_path}")
        rows = read_eligible_rows(chunks_path, min_chars=args.min_chars, max_chars=args.max_chars)
        if len(rows) < args.per_corpus:
            raise SystemExit(
                f"not enough eligible chunks for {corpus_name}: "
                f"{len(rows)} < {args.per_corpus}"
            )
        chosen = choose_rows(
            rows,
            corpus_name=corpus_name,
            seed=args.seed,
            per_corpus=args.per_corpus,
        )
        prefix = CORPUS_PREFIX[corpus_name]
        for ordinal, (line_number, row, raw_line) in enumerate(chosen, 1):
            text = str(row.get("text") or "").strip()
            record = {
                "citation_id": f"CC60_{prefix}_{ordinal:02d}",
                "corpus_id": corpus_name,
                "corpus_family_id": str(spec.get("family_id") or ""),
                "corpus_table_name": str(spec.get("table_name") or ""),
                "source_scope": corpus_name,
                "source_public": True,
                "source_registry_path": chunks_rel,
                "source_registry_sha256": sha256_file(chunks_path),
                "active_index_path": str(active_path.relative_to(data_dir)).replace("\\", "/"),
                "active_index_sha256": sha256_file(active_path),
                "registry_path": str(registry_path.relative_to(data_dir)).replace("\\", "/"),
                "registry_sha256": sha256_file(registry_path),
                "active_index_generation": str(active.get("generation") or ""),
                "chunk_line_number": line_number,
                "chunk_id": str(row.get("chunk_id") or ""),
                "paper_id": str(row.get("paper_id") or ""),
                "chunk_index": int(row.get("chunk_index") or 0),
                "title": str(row.get("title") or ""),
                "section_name": str(row.get("section_name") or ""),
                "source_file": str(row.get("source_file") or row.get("source") or ""),
                "source_type": str(row.get("source_type") or ""),
                "authority_tier": str(row.get("authority_tier") or ""),
                "chunk_kind": str(row.get("chunk_kind") or ""),
                "doi": str(row.get("doi") or ""),
                "year": str(row.get("year") or ""),
                "registry_id": str(row.get("registry_id") or ""),
                "species": str(row.get("species") or ""),
                "lang": str(row.get("lang") or ""),
                "chunk_text": text,
                "char_len": len(text),
                "chunk_text_sha256": sha256_text(text),
                "chunk_record_sha256": sha256_text(raw_line.rstrip("\r\n")),
                "sample_seed": args.seed,
                "sampled_at_utc": utc(),
            }
            record["freeze_record_sha256"] = sha256_text(canonical_json(record))
            sampled.append(record)
        sampling_report["corpora"][corpus_name] = {
            "eligible_chunks": len(rows),
            "sampled": len(chosen),
            "chunks_path": chunks_rel,
            "chunks_sha256": sha256_file(chunks_path),
        }

    random.Random(args.seed).shuffle(sampled)
    sampling_report.update(
        {
            "total_questions": len(sampled),
            "one_chunk_per_question": True,
            "created_utc": utc(),
            "active_index_generation": str(active.get("generation") or ""),
            "registry_version": str(registry.get("registry_version") or ""),
        }
    )
    sampling_report["sampling_config_sha256_input"] = sha256_text(canonical_json({
        "seed": args.seed,
        "per_corpus": args.per_corpus,
        "min_chars": args.min_chars,
        "max_chars": args.max_chars,
        "corpora": args.corpora,
    }))

    chunks_jsonl = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in sampled
    )
    atomic_write_text(out_dir / "chunks_FROZEN.jsonl", chunks_jsonl, read_only=True)
    atomic_write_text(
        out_dir / "sampling_config.json",
        json.dumps(sampling_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        read_only=True,
    )

    manifest_fields = [
        "citation_id", "corpus_id", "chunk_id", "paper_id", "chunk_index",
        "title", "section_name", "source_file", "source_type", "authority_tier",
        "doi", "registry_id", "chunk_text_sha256", "source_registry_sha256",
        "freeze_record_sha256",
    ]
    csv_path = out_dir / "chunk_manifest.csv"
    if csv_path.exists():
        raise SystemExit(f"refuse overwrite: {csv_path}")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=manifest_fields)
        writer.writeheader()
        for row in sampled:
            writer.writerow({key: row.get(key, "") for key in manifest_fields})
    os.chmod(csv_path, stat.S_IREAD)
    print(json.dumps({
        "status": "FROZEN",
        "questions": len(sampled),
        "one_chunk_per_question": True,
        "chunks_file": str(out_dir / "chunks_FROZEN.jsonl"),
        "manifest": str(csv_path),
        "seed": args.seed,
        "corpora": sampling_report["corpora"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


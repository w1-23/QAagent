#!/usr/bin/env python3
"""Build an offline question-authoring packet from frozen chunks.

The packet is deliberately separate from runtime prompts. Only the author/agent
reads it while writing one question per frozen chunk.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-dir", default="03_benchmarks/citation60")
    args = parser.parse_args()
    bench = (ROOT / args.benchmark_dir).resolve()
    chunks_path = bench / "chunks_FROZEN.jsonl"
    if not chunks_path.is_file():
        raise SystemExit(f"missing frozen chunks: {chunks_path}")
    output_jsonl = bench / "question_authoring_packet.jsonl"
    output_csv = bench / "questions_DRAFT.csv"
    if output_jsonl.exists() or output_csv.exists():
        raise SystemExit("refuse overwrite of authoring packet/drafts")

    packet: list[dict] = []
    with chunks_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            packet.append({
                key: row.get(key)
                for key in (
                    "citation_id", "corpus_id", "chunk_id", "paper_id", "title",
                    "section_name", "source_file", "source_type", "authority_tier",
                    "registry_id", "doi", "year", "chunk_kind", "chunk_text",
                    "chunk_text_sha256", "char_len",
                )
            })

    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in packet)
    tmp = output_jsonl.with_suffix(".jsonl.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, output_jsonl)

    fields = [
        "citation_id", "question", "answerable_from_chunk", "anchor_for_audit",
        "generation_notes", "status",
    ]
    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in packet:
            writer.writerow({
                "citation_id": row["citation_id"],
                "question": "",
                "answerable_from_chunk": "",
                "anchor_for_audit": "",
                "generation_notes": "",
                "status": "DRAFT",
            })
    print(json.dumps({
        "status": "PACKET_READY",
        "rows": len(packet),
        "packet": str(output_jsonl),
        "draft": str(output_csv),
        "warning": "Do not pass question_authoring_packet.jsonl or chunks_FROZEN.jsonl to the runtime agent",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


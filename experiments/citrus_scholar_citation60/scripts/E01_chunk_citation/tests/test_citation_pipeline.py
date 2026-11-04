from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "scripts" / "E01_chunk_citation"
BENCH = ROOT / "03_benchmarks" / "citation60"
FROZEN_CHUNKS = BENCH / "chunks_FROZEN.jsonl"
requires_frozen_chunks = pytest.mark.skipif(
    not FROZEN_CHUNKS.is_file(),
    reason="source-corpus chunk text is intentionally excluded from the public repository",
)


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EXPERIMENT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.open(encoding="utf-8")
        if line.strip()
    ]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_sampling_selection_is_reproducible():
    sampling = load_script("citation_sampling", "00_sample_freeze_chunks.py")
    rows = [
        (i, {"chunk_id": f"chunk-{i:03d}", "text": f"source {i}"}, f"line-{i}")
        for i in range(40)
    ]
    first = sampling.choose_rows(rows, corpus_name="cqa_test", seed=20261104, per_corpus=12)
    second = sampling.choose_rows(rows, corpus_name="cqa_test", seed=20261104, per_corpus=12)
    changed = sampling.choose_rows(rows, corpus_name="cqa_test", seed=20261105, per_corpus=12)
    assert [row[1]["chunk_id"] for row in first] == [
        row[1]["chunk_id"] for row in second
    ]
    assert [row[1]["chunk_id"] for row in first] != [
        row[1]["chunk_id"] for row in changed
    ]


def test_freeze_writer_refuses_overwrite(tmp_path):
    sampling = load_script("citation_sampling_overwrite", "00_sample_freeze_chunks.py")
    target = tmp_path / "frozen.jsonl"
    sampling.atomic_write_text(target, "first\n")
    with pytest.raises(SystemExit, match="refuse overwrite"):
        sampling.atomic_write_text(target, "second\n")
    assert target.read_text(encoding="utf-8") == "first\n"


@requires_frozen_chunks
def test_each_question_binds_exactly_one_frozen_chunk():
    chunks = jsonl(FROZEN_CHUNKS)
    questions = jsonl(BENCH / "questions_FROZEN.jsonl")
    chunk_map = {row["citation_id"]: row for row in chunks}
    assert len(chunks) == 60
    assert len(questions) == 60
    assert len(chunk_map) == 60
    assert len({row["question_id"] for row in questions}) == 60
    for question in questions:
        chunk = chunk_map[question["question_id"]]
        assert question["one_chunk_only"] is True
        assert question["target_chunk_id"] == chunk["chunk_id"]
        assert question["target_chunk_text_sha256"] == chunk["chunk_text_sha256"]
        assert question["target_source_file"] == chunk["source_file"]


@requires_frozen_chunks
def test_runtime_plan_does_not_expose_chunk_or_target_locator():
    chunks = {
        row["citation_id"]: row
        for row in jsonl(FROZEN_CHUNKS)
    }
    with (ROOT / "06_runs/manifests/citation_run_plan.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        plan = list(csv.DictReader(handle))
    assert len(plan) == 180
    forbidden_fields = {
        "target_chunk_id",
        "target_paper_id",
        "target_source_file",
        "chunk_text",
        "anchor_for_audit",
    }
    assert not forbidden_fields.intersection(plan[0])
    for row in plan:
        chunk = chunks[row["case_id"]]
        prompt = row["prompt"]
        assert chunk["chunk_id"] not in prompt
        assert chunk["source_file"] not in prompt
        assert chunk["chunk_text"][:80] not in prompt
        assert row["source_scope"] == f"仅本地:{chunk['corpus_id']}"


@requires_frozen_chunks
def test_frozen_question_and_chunk_hashes_match_lock():
    lock = json.loads(
        (BENCH / "questions_FROZEN.manifest.json").read_text(encoding="utf-8")
    )
    assert lock["status"] == "FROZEN"
    assert lock["question_count"] == 60
    assert lock["one_chunk_per_question"] is True
    assert sha256(FROZEN_CHUNKS) == lock["chunks_frozen_sha256"]
    assert sha256(BENCH / "questions_FROZEN.jsonl") == lock["questions_frozen_sha256"]
    assert sha256(BENCH / "question_manifest.csv") == lock["question_manifest_sha256"]


def test_citation_extraction_uses_citations_not_retrieval_trace_only():
    verify = load_script("citation_verify", "40_verify_citation_hits.py")
    response = {
        "status": "completed",
        "final_answer": "结论见[1]。",
        "citations": [
            {
                "chunk_id": "chunk-001",
                "corpus_id": "cqa_literature",
                "source_file": "english_literature/article.md",
            }
        ],
        "tool_calls": [
            {"name": "local_search", "source_ids": ["unquoted-chunk"]}
        ],
    }
    run = {"actual_response": response}
    blobs = verify.citation_blobs(run)
    assert verify.contains_identifier(blobs, "chunk-001")
    assert verify.contains_identifier(blobs, "english_literature/article.md")
    assert verify.contains_any_marker(
        blobs, verify.CORPUS_MARKERS["cqa_literature"]
    )
    assert verify.has_citation_signal(blobs, response) is True
    assert verify.contains_identifier(blobs, "unquoted-chunk") is False


def test_missing_citation_is_not_inferred_from_tool_retrieval():
    verify = load_script("citation_verify_missing", "40_verify_citation_hits.py")
    response = {
        "status": "completed",
        "final_answer": "未给出可核验引用。",
        "citations": [],
        "tool_calls": [
            {"name": "local_search", "source_ids": ["retrieved-but-not-cited"]}
        ],
    }
    blobs = verify.citation_blobs({"actual_response": response})
    assert blobs == []
    assert verify.has_citation_signal(blobs, response) is False


def test_optional_semantic_audit_sample_is_stratified_and_target_free():
    questions = {
        row["question_id"]: row
        for row in jsonl(BENCH / "questions_FROZEN.jsonl")
    }
    with (ROOT / "06_runs/manifests/citation_semantic_audit_plan.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        plan = list(csv.DictReader(handle))
    meta = json.loads(
        (ROOT / "06_runs/manifests/citation_semantic_audit_plan_meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(plan) == 60
    assert meta["selected_questions"] == 20
    assert meta["selected_runs"] == 60
    assert meta["status"] == "FROZEN_NOT_RUN"
    assert set(meta["selected_question_counts"].values()) == {4}
    assert {
        row["condition"] for row in plan
    } == {"B0", "B1", "B2"}
    assert "target_chunk_id" not in plan[0]
    question_ids = {row["case_id"] for row in plan}
    assert len(question_ids) == 20
    assert {
        questions[question_id]["target_corpus_id"]
        for question_id in question_ids
    } == {
        "cqa_deng_book",
        "cqa_literature",
        "cqa_methods",
        "cqa_translated",
        "cqa_ucr_varieties",
    }


def test_core_qa60_remains_active_and_separate_from_supplement():
    status = (ROOT / "03_benchmarks/qa60/STATUS.md").read_text(encoding="utf-8")
    assert "ACTIVE_CORE_CANDIDATE" in status
    assert "E01_QA60_EGTS" in status
    assert "E01_SUPP_CHUNK_CITATION60" in status
    assert "不得" in status and "合并" in status

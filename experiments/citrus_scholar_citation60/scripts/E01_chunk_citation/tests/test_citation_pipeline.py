from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT = ROOT / "scripts" / "E01_chunk_citation"
SCRIPTS = ROOT / "scripts"
BENCH = ROOT / "03_benchmarks" / "citation60"
FROZEN_CHUNKS = BENCH / "chunks_FROZEN.jsonl"
L1_MANIFEST = ROOT / "12_reproducibility" / "L1_CONTROLLED_ASSET_MANIFEST.json"
L1_ROOT = (
    Path(os.environ["CITRUS_EXPERIMENT_L1_ROOT"]).expanduser().resolve()
    if os.environ.get("CITRUS_EXPERIMENT_L1_ROOT")
    else None
)
L1_BENCH = L1_ROOT / "03_benchmarks/citation60" if L1_ROOT else None
L1_CHUNKS = L1_BENCH / "chunks_FROZEN.jsonl" if L1_BENCH else None
requires_frozen_chunks = pytest.mark.skipif(
    not L1_CHUNKS or not L1_CHUNKS.is_file(),
    reason="protected frozen citation60 assets are unavailable",
)


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, EXPERIMENT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_top_level_script(name: str, filename: str):
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot import {filename}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.open(encoding="utf-8")
        if line.strip()
    ]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected(relative: str) -> Path:
    if L1_ROOT is None:
        pytest.skip("CITRUS_EXPERIMENT_L1_ROOT is not configured")
    return L1_ROOT / relative


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
    chunks = jsonl(protected("03_benchmarks/citation60/chunks_FROZEN.jsonl"))
    questions = jsonl(
        protected("03_benchmarks/citation60/questions_FROZEN.jsonl")
    )
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
        for row in jsonl(protected("03_benchmarks/citation60/chunks_FROZEN.jsonl"))
    }
    with protected("06_runs/manifests/citation_run_plan.csv").open(
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
        protected(
            "03_benchmarks/citation60/questions_FROZEN.manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert lock["status"] == "FROZEN"
    assert lock["question_count"] == 60
    assert lock["one_chunk_per_question"] is True
    assert sha256(protected("03_benchmarks/citation60/chunks_FROZEN.jsonl")) == lock[
        "chunks_frozen_sha256"
    ]
    assert sha256(
        protected("03_benchmarks/citation60/questions_FROZEN.jsonl")
    ) == lock["questions_frozen_sha256"]
    assert sha256(
        protected("03_benchmarks/citation60/question_manifest.csv")
    ) == lock["question_manifest_sha256"]


def test_public_tree_excludes_complete_source_text():
    assert not FROZEN_CHUNKS.exists()
    assert not (BENCH / "question_authoring_packet.jsonl").exists()
    manifest = json.loads(L1_MANIFEST.read_text(encoding="utf-8"))
    assets = {
        row["former_public_path"]: row for row in manifest["assets"]
    }
    chunks = assets["03_benchmarks/citation60/chunks_FROZEN.jsonl"]
    authoring = assets[
        "03_benchmarks/citation60/question_authoring_packet.jsonl"
    ]
    assert chunks["data_class"] == "L1"
    assert authoring["data_class"] == "L1"
    assert chunks["remove_from_public_tree"] is True
    assert authoring["remove_from_public_tree"] is True


def test_public_derived_question_files_are_fail_closed():
    for name in (
        "chunk_manifest.csv",
        "CORPUS_SCOPE_METADATA.json",
        "questions_DRAFT.csv",
        "questions_FROZEN.jsonl",
        "questions_FROZEN.manifest.json",
        "question_manifest.csv",
        "sampling_config.json",
    ):
        assert not (BENCH / name).exists()


def test_public_anchor_release_audit_fails_closed_on_long_excerpt():
    anchor_audit = load_script(
        "citation_anchor_audit", "60_audit_release_anchors.py"
    )
    short = anchor_audit.audit_anchor_sets(
        {
            "fixture": [
                {
                    "citation_id": f"CC60_TEST_{index:02d}",
                    "anchor_for_audit": (
                        f"fact-{index}; 2026; allowed source category"
                    ),
                }
                for index in range(60)
            ]
        },
        {
            f"CC60_TEST_{index:02d}": {
                "citation_id": f"CC60_TEST_{index:02d}",
                "chunk_text": "unrelated protected source passage",
            }
            for index in range(60)
        },
    )
    assert short["status"] == "PASS"

    long_text = "A" * 50
    long = anchor_audit.audit_anchor_sets(
        {
            "fixture": [
                {
                    "citation_id": f"CC60_TEST_{index:02d}",
                    "anchor_for_audit": (
                        long_text if index == 0 else "short audit fact"
                    ),
                }
                for index in range(60)
            ]
        },
        {
            f"CC60_TEST_{index:02d}": {
                "citation_id": f"CC60_TEST_{index:02d}",
                "chunk_text": (
                    f"protected prefix {long_text} protected suffix"
                    if index == 0
                    else "unrelated protected source passage"
                ),
            }
            for index in range(60)
        },
    )
    assert long["status"] == "FAIL"
    assert long["files"]["fixture"]["over_threshold_count"] == 1


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


def test_semantic_audit_sample_is_disabled_by_design():
    questions = {
        row["question_id"]: row
        for row in jsonl(
            protected("03_benchmarks/citation60/questions_FROZEN.jsonl")
        )
    }
    with protected("06_runs/manifests/citation_semantic_audit_plan.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        plan = list(csv.DictReader(handle))
    meta = json.loads(
        protected(
            "06_runs/manifests/citation_semantic_audit_plan_meta.json"
        ).read_text(
            encoding="utf-8"
        )
    )
    assert len(plan) == 60
    assert meta["selected_questions"] == 20
    assert meta["selected_runs"] == 60
    assert meta["status"] == "DISABLED_BY_DESIGN"
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


def test_core_and_supplement_remain_active_and_separate():
    status = (ROOT / "03_benchmarks/qa60/STATUS.md").read_text(encoding="utf-8")
    assert "ACTIVE_CORE_CANDIDATE" in status
    assert "E01_QA60_EGTS" in status
    assert "E01_SUPP_CHUNK_CITATION60" in status
    assert "不得" in status and "池化" in status


def test_public_validity_packet_is_qa60_only():
    with (ROOT / "07_scoring/validity/question_validity_review_packet.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        packet = list(csv.DictReader(handle))
    assert len(packet) == 120
    assert len({(row["experiment_id"], row["question_id"]) for row in packet}) == 60
    by_question = {}
    for row in packet:
        by_question.setdefault(
            (row["experiment_id"], row["question_id"]), set()
        ).add(row["reviewer_slot"])
    assert all(
        slots == {"REVIEWER_1", "REVIEWER_2"}
        for slots in by_question.values()
    )
    assert {row["experiment_id"] for row in packet} == {"E01_QA60_EGTS"}


def test_semantic_support_is_not_an_active_metric():
    rubric = (ROOT / "07_scoring/rubric.md").read_text(encoding="utf-8")
    contract = (ROOT / "09_analysis/analysis_contract.md").read_text(encoding="utf-8")
    assert "not reported" in rubric
    assert "Do not generate" in contract
    assert "citation_semantic_support.csv" in contract


def test_no_gold_answer_or_human_scoring_artifacts_remain_active():
    with (ROOT / "03_benchmarks/qa60/qa60_CANDIDATE.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        core_headers = next(csv.reader(handle))
    assert not any(
        "gold" in header.lower() or "answer" in header.lower()
        for header in core_headers
    )
    assert not (
        ROOT / "07_scoring/reviews/qa_rater_independent_TEMPLATE.csv"
    ).exists()
    assert not (ROOT / "07_scoring/reviews/qa_score_template_180.csv").exists()
    assert not (ROOT / "07_scoring/reviews/qa_scores_adjudicated.csv").exists()
    gold_dir = ROOT / "07_scoring/gold"
    assert not gold_dir.exists() or not any(
        path.is_file() for path in gold_dir.rglob("*")
    )
    judge_config = json.loads(
        (ROOT / "05_baselines/judge_config.json").read_text(encoding="utf-8")
    )
    assert judge_config["semantic_support_reported"] is False
    judge_prompt = ROOT / "05_baselines/prompts/qa_judge_prompt.md"
    adjudicator_prompt = ROOT / "05_baselines/prompts/qa_adjudicator_prompt.md"
    assert judge_config["judge_prompt_path"] == "05_baselines/prompts/qa_judge_prompt.md"
    assert (
        judge_config["adjudicator_prompt_path"]
        == "05_baselines/prompts/qa_adjudicator_prompt.md"
    )
    assert judge_prompt.is_file()
    assert adjudicator_prompt.is_file()
    prompt_text = judge_prompt.read_text(encoding="utf-8")
    assert "reason_codes_by_field" in prompt_text
    assert "answer_span_quote" in prompt_text


def test_asset_register_has_release_classification_and_no_l1_text():
    register = ROOT / "12_reproducibility/ASSET_REGISTER_CURRENT.csv"
    with register.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert {"data_class", "release_status"}.issubset(reader.fieldnames or [])
        rows = list(reader)
    paths = {row["relative_path"] for row in rows}
    assert "03_benchmarks/citation60/chunks_FROZEN.jsonl" not in paths
    assert "03_benchmarks/citation60/question_authoring_packet.jsonl" not in paths
    assert {row["data_class"] for row in rows} <= {"L1", "L2"}


def test_gate_ledger_stays_blocked_or_pending():
    with (ROOT / "01_preflight/gates.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        gates = {row["gate_id"]: row["status"] for row in csv.DictReader(handle)}
    assert gates == {
        "G01": "BLOCKED",
        **{f"G{number:02d}": "PENDING" for number in range(2, 16)},
    }


def test_fatal_challenge_fixtures_cover_positives_and_negatives():
    with (
        ROOT / "07_scoring/expert/challenge_cases_v4.3.1.csv"
    ).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 13
    assert any(row["expected_fatal_error"] == "true" for row in rows)
    assert any(row["expected_fatal_error"] == "false" for row in rows)
    assert any(
        row["expected_fatal_error"] == "not_scored_infrastructure"
        for row in rows
    )
    classes = {row["challenge_class"] for row in rows}
    assert {
        "PRIVACY_DISCLOSURE",
        "PRIVATE_SOP_DISCLOSURE",
        "ORDINARY_FACTUAL_ERROR",
        "CORRECT_REFUSAL",
        "INCORRECT_REFUSAL",
        "FABRICATED_DOI",
        "FABRICATED_CT",
        "SEVERE_OVEREXTRAPOLATION",
    }.issubset(classes)


def test_llm_judge_reader_fails_closed_on_incomplete_schema(tmp_path):
    aggregate = load_top_level_script(
        "judge_aggregation_schema", "aggregate_llm_judges.py"
    )
    judge_file = tmp_path / "judge.json"
    judge_file.write_text(
        json.dumps(
            {
                "status": "completed",
                "decision": "PASS",
                "answer_correct": True,
                "evidence_span_locators": [],
                "reason_code": "complete",
            }
        ),
        encoding="utf-8",
    )
    result = aggregate.read_judge(judge_file)
    assert result["status"] == "INVALID_SCHEMA"
    assert "decision" not in result
    assert "model_written_decision_forbidden" in result["schema_errors"]


@requires_frozen_chunks
def test_controlled_question_files_do_not_embed_chunk_text():
    for question in jsonl(
        protected("03_benchmarks/citation60/questions_FROZEN.jsonl")
    ):
        assert "chunk_text" not in question
        assert question["target_chunk_id"] not in question["question"]
        assert question["target_source_file"] not in question["question"]


def test_simulated_dry_run_is_complete_and_separately_labelled():
    run_dir = ROOT / "06_runs/simulated_dry_run"
    expected = {
        "core_qa_runs.jsonl": 180,
        "citation_runs.jsonl": 180,
        "route_runs.jsonl": 192,
        "composite_runs.jsonl": 24,
        "workflow_runs.jsonl": 7,
    }
    for filename, count in expected.items():
        rows = jsonl(run_dir / filename)
        assert len(rows) == count
        assert all(
            row["evidence_class"] == "SIMULATED_DRY_RUN"
            and row["not_for_publication_claims"] is True
            for row in rows
        )
    for row in jsonl(run_dir / "core_qa_runs.jsonl"):
        assert "evidence_used" not in row["synthetic_metrics"]
        assert "evidence_traceable" in row["synthetic_metrics"]
    for row in jsonl(run_dir / "judge_responses.jsonl"):
        assert "decision" not in row
        assert "evidence_used" not in row
        assert "reason_codes_by_field" in row
        assert "answer_span_quote" in row
    summary = json.loads(
        (ROOT / "09_analysis/simulated_dry_run/summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["evidence_class"] == "SIMULATED_DRY_RUN"
    assert summary["not_for_publication_claims"] is True


def test_paper_audit_package_is_present():
    required = (
        "AUDIT_INDEX.md",
        "00_protocol/05_论文写作执行计划.md",
        "11_manuscript/README.md",
        "11_manuscript/manuscript_skeleton.md",
        "11_manuscript/claim_source_map_TEMPLATE.csv",
        "11_manuscript/table_figure_provenance_TEMPLATE.csv",
        "11_manuscript/paper_version_registry.csv",
        "11_manuscript/review_response_log.csv",
    )
    assert all((ROOT / relative).is_file() for relative in required)

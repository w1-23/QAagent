from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
HUMAN = SCRIPTS / "A02_human_scoring"
sys.path.insert(0, str(SCRIPTS))

from A02_human_scoring.scoring_common import (  # noqa: E402
    SCORE_FIELDS,
    deterministic_egts,
    validate_score_row,
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def score_row(
    blind_id: str,
    reviewer_id: str,
    expected: str,
    *,
    batch_id: str = "BLIND_BATCH_01",
) -> dict:
    na = expected == "UNANSWERABLE"
    return {
        "batch_id": batch_id,
        "blind_id": blind_id,
        "reviewer_id": reviewer_id,
        "score_version": "4.3.2",
        "answerability_correct": "true",
        "answer_correct": "not_applicable" if na else "true",
        "evidence_traceable": "not_applicable" if na else "true",
        "fatal_error": "false",
        "reason_codes_by_field_json": json.dumps(
            {
                "answerability_correct": [],
                "answer_correct": [],
                "evidence_traceable": [],
                "fatal_error": [],
            }
        ),
        "short_reason": "",
        "answer_span_quote": "",
        "evidence_span_locators_json": "[]",
        "score_status": "SCORED",
        "issue_id": "",
        "reviewed_utc": "2026-11-05T01:00:00Z",
    }


def run_script(
    path: Path,
    *args: str,
    l1_root: Path,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CITRUS_EXPERIMENT_L1_ROOT"] = str(l1_root)
    return subprocess.run(
        [sys.executable, str(path), *args],
        cwd=ROOT.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_score_rules_fail_closed_on_na_and_non_scored():
    valid = score_row("BLIND_000001", "EXPERT_A", "UNANSWERABLE")
    errors, normalized = validate_score_row(valid, "UNANSWERABLE")
    assert errors == []
    assert normalized["answer_correct"] == "not_applicable"
    assert deterministic_egts(normalized, "UNANSWERABLE") == 1

    invalid = dict(valid, answer_correct="true")
    errors, _ = validate_score_row(invalid, "UNANSWERABLE")
    assert "answer_correct_must_be_not_applicable" in errors

    unscored = dict(valid, score_status="MISSING")
    errors, _ = validate_score_row(unscored, "UNANSWERABLE")
    assert "score_status_not_scored" in errors


def test_expert_material_builder_is_blind_and_complete(tmp_path):
    l1_root = tmp_path / "l1"
    packet_root = l1_root / "judge_packets"
    order_root = l1_root / "judge_blinding" / "reviewer_orders"
    packet_root.mkdir(parents=True)
    order_root.mkdir(parents=True)
    packet_records = []
    for index in (1, 2):
        blind_id = f"BLIND_{index:06d}"
        packet = {
            "batch_id": "BLIND_BATCH_01",
            "question": f"Question {index}",
            "expected_answerability": "ANSWERABLE",
            "source_scope": "local corpus",
            "final_answer": f"Answer {index}",
            "citations": [],
            "system_evidence_excerpts": [],
            "frozen_reference_evidence": [
                {
                    "source_id": f"S{index}",
                    "sha256": "a" * 64,
                    "locator": f"p.{index}",
                    "excerpt": f"Evidence {index}",
                }
            ],
        }
        packet_path = packet_root / f"{blind_id}.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        packet_records.append(
            {
                "blind_id": blind_id,
                "batch_id": "BLIND_BATCH_01",
                "packet_sha256": sha256(packet_path),
            }
        )
    (packet_root / "manifest.json").write_text(
        json.dumps({"packets": packet_records}),
        encoding="utf-8",
    )
    for reviewer in ("reviewer_1", "reviewer_2"):
        write_csv(
            order_root / f"{reviewer}.csv",
            ["reviewer_slot", "batch_id", "position", "blind_id", "packet_sha256"],
            [
                {
                    "reviewer_slot": reviewer,
                    "batch_id": record["batch_id"],
                    "position": str(index),
                    "blind_id": record["blind_id"],
                    "packet_sha256": record["packet_sha256"],
                }
                for index, record in enumerate(packet_records, start=1)
            ],
        )
    result = run_script(
        HUMAN / "00_build_expert_materials.py",
        "--expected-packets",
        "2",
        l1_root=l1_root,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    output = l1_root / "expert_packets_v4_3_2"
    manifest = json.loads((output / "packet_manifest.json").read_text(encoding="utf-8"))
    assert manifest["packet_count"] == 2
    assert manifest["machine_scores_included"] is False
    for reviewer in ("reviewer_1", "reviewer_2"):
        assert (output / reviewer / "03_专家评分表.xlsx").is_file()
        with (output / reviewer / "03_专家评分表.csv").open(
            encoding="utf-8-sig", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 2
        assert all(not row["blind_id"].startswith("BLIND_B") for row in rows)


def test_primary_score_validation_requires_attestation_in_strict_mode(tmp_path):
    l1_root = tmp_path / "l1"
    packet_root = l1_root / "judge_packets"
    packet_root.mkdir(parents=True)
    packet = packet_root / "BLIND_000001.json"
    packet.write_text(
        json.dumps(
            {
                "batch_id": "BLIND_BATCH_01",
                "expected_answerability": "ANSWERABLE",
            }
        ),
        encoding="utf-8",
    )
    (packet_root / "manifest.json").write_text(
        json.dumps(
            {
                "packets": [
                    {
                        "blind_id": blind_id,
                        "packet_sha256": sha256(packet),
                    }
                    for blind_id in ["BLIND_000001"]
                ]
            }
        ),
        encoding="utf-8",
    )
    scores = l1_root / "scores.csv"
    write_csv(
        scores,
        SCORE_FIELDS,
        [score_row("BLIND_000001", "EXPERT_A", "ANSWERABLE")],
    )
    materials = l1_root / "expert_packets_v4_3_2" / "packet_manifest.json"
    materials.parent.mkdir()
    materials.write_text(
        json.dumps(
            {
                "manifest_version": "4.3.2",
                "packet_count": 1,
                "score_version": "4.3.2",
                "reviewers": {
                    "reviewer_1": {"reviewer_id": "EXPERT_A"},
                    "reviewer_2": {"reviewer_id": "EXPERT_B"},
                },
            }
        ),
        encoding="utf-8",
    )
    result = run_script(
        HUMAN / "10_validate_expert_scores.py",
        "--scores",
        str(scores),
        "--packet-root",
        str(packet_root),
        "--materials-manifest",
        str(materials),
        "--out",
        str(l1_root / "validation" / "expert_1.json"),
        "--strict",
        l1_root=l1_root,
    )
    assert result.returncode == 2
    report = json.loads(
        (l1_root / "validation" / "expert_1.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "BLOCKED"
    assert "attestation:attestation_required" in [
        error for blocker in report["blockers"] for error in blocker["errors"]
    ]


def test_append_only_correction_chain_uses_effective_previous_value(tmp_path):
    l1_root = tmp_path / "l1"
    l1_root.mkdir()
    scores = l1_root / "scores.csv"
    write_csv(
        scores,
        SCORE_FIELDS,
        [score_row("BLIND_000001", "EXPERT_A", "ANSWERABLE")],
    )
    correction_fields = [
        "batch_id",
        "blind_id",
        "issue_id",
        "reviewer_id",
        "issue_type",
        "field_name",
        "original_score_record_id",
        "original_value",
        "revised_value",
        "correction_reason",
        "revised_by",
        "revised_utc",
        "approval_record_id",
        "status",
    ]
    corrections = l1_root / "corrections.csv"
    write_csv(
        corrections,
        correction_fields,
        [
            {
                "batch_id": "BLIND_BATCH_01",
                "blind_id": "BLIND_000001",
                "issue_id": "ISSUE_1",
                "reviewer_id": "EXPERT_A",
                "issue_type": "CORRECTION",
                "field_name": "short_reason",
                "original_score_record_id": "BLIND_000001|EXPERT_A",
                "original_value": "",
                "revised_value": "first correction",
                "correction_reason": "Clarify the rationale.",
                "revised_by": "EXPERT_A",
                "revised_utc": "2026-11-05T01:10:00Z",
                "approval_record_id": "APPROVAL_1",
                "status": "APPLIED",
            },
            {
                "batch_id": "BLIND_BATCH_01",
                "blind_id": "BLIND_000001",
                "issue_id": "ISSUE_2",
                "reviewer_id": "EXPERT_A",
                "issue_type": "CORRECTION",
                "field_name": "short_reason",
                "original_score_record_id": "BLIND_000001|EXPERT_A",
                "original_value": "first correction",
                "revised_value": "second correction",
                "correction_reason": "Append a final clarification.",
                "revised_by": "EXPERT_A",
                "revised_utc": "2026-11-05T01:20:00Z",
                "approval_record_id": "APPROVAL_2",
                "status": "APPLIED",
            },
        ],
    )
    out_root = l1_root / "correction_validation"
    result = run_script(
        HUMAN / "20_validate_corrections.py",
        "--scores",
        str(scores),
        "--corrections",
        str(corrections),
        "--out-root",
        str(out_root),
        "--strict",
        l1_root=l1_root,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    with (out_root / "effective_scores.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["short_reason"] == "second correction"
    assert json.loads(rows[0]["correction_record_ids_json"]) == ["ISSUE_1", "ISSUE_2"]


def test_third_expert_adjudication_must_be_independent(tmp_path):
    l1_root = tmp_path / "l1"
    packet_root = l1_root / "judge_packets"
    packet_root.mkdir(parents=True)
    (packet_root / "BLIND_000001.json").write_text(
        json.dumps({"expected_answerability": "ANSWERABLE"}),
        encoding="utf-8",
    )
    first = l1_root / "expert_1.csv"
    second = l1_root / "expert_2.csv"
    write_csv(
        first,
        ["blind_id", "reviewer_id", "answer_correct"],
        [{"blind_id": "BLIND_000001", "reviewer_id": "EXPERT_A", "answer_correct": "true"}],
    )
    write_csv(
        second,
        ["blind_id", "reviewer_id", "answer_correct"],
        [{"blind_id": "BLIND_000001", "reviewer_id": "EXPERT_B", "answer_correct": "false"}],
    )
    adjudication_path = l1_root / "adjudications.csv"
    write_csv(
        adjudication_path,
        [
            "batch_id",
            "blind_id",
            "field_name",
            "expert1_reviewer_id",
            "expert1_value",
            "expert2_reviewer_id",
            "expert2_value",
            "adjudicator_id",
            "adjudicated_value",
            "reason_code",
            "short_reason",
            "answer_span_quote",
            "evidence_span_locators_json",
            "reviewed_utc",
            "adjudication_record_id",
        ],
        [
            {
                "batch_id": "BLIND_BATCH_01",
                "blind_id": "BLIND_000001",
                "field_name": "answer_correct",
                "expert1_reviewer_id": "EXPERT_A",
                "expert1_value": "true",
                "expert2_reviewer_id": "EXPERT_B",
                "expert2_value": "false",
                "adjudicator_id": "EXPERT_A",
                "adjudicated_value": "false",
                "reason_code": "FACTUAL_ERROR",
                "short_reason": "The reported value conflicts with the source.",
                "answer_span_quote": "wrong value",
                "evidence_span_locators_json": '["S1:p1"]',
                "reviewed_utc": "2026-11-05T01:30:00Z",
                "adjudication_record_id": "ADJ_1",
            }
        ],
    )
    out_root = l1_root / "adjudicated"
    result = run_script(
        HUMAN / "30_import_expert_adjudication.py",
        "--adjudications",
        str(adjudication_path),
        "--scores-expert-1",
        str(first),
        "--scores-expert-2",
        str(second),
        "--packet-root",
        str(packet_root),
        "--out-root",
        str(out_root),
        "--strict",
        l1_root=l1_root,
    )
    assert result.returncode == 2
    report = json.loads(
        (out_root / "adjudication_import.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "BLOCKED"
    assert any(
        "adjudicator_must_be_third_reviewer" in error["errors"]
        for error in report["errors"]
    )


def test_end_to_end_consensus_and_strict_analysis_are_deterministic(tmp_path):
    l1_root = tmp_path / "l1"
    packet_root = l1_root / "judge_packets"
    packet_root.mkdir(parents=True)
    raw_root = l1_root / "06_runs" / "raw"
    with (ROOT / "06_runs/manifests/run_plan.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        plan = [row for row in csv.DictReader(handle) if row["kind"] == "qa"]
    with (ROOT / "03_benchmarks/qa60/qa60_CANDIDATE.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        questions = {row["题号"]: row for row in csv.DictReader(handle)}

    blind_rows = []
    first_rows = []
    second_rows = []
    packet_records = []
    for index, run in enumerate(plan, start=1):
        blind_id = f"BLIND_{index:06d}"
        batch_id = f"BLIND_BATCH_{(index % 6) + 1:02d}"
        expected = "UNANSWERABLE" if questions[run["case_id"]]["组别"] == "N" else "ANSWERABLE"
        raw_path = raw_root / run["condition"] / f"{run['run_id']}.json"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(
            json.dumps(
                {
                    "actual_response": {
                        "status": "completed",
                        "final_answer": "Synthetic test fixture",
                        "citations": [],
                    }
                }
            ),
            encoding="utf-8",
        )
        packet_path = packet_root / f"{blind_id}.json"
        packet_path.write_text(
            json.dumps(
                {
                    "batch_id": batch_id,
                    "expected_answerability": expected,
                }
            ),
            encoding="utf-8",
        )
        blind_rows.append(
            {
                "blind_id": blind_id,
                "batch_id": batch_id,
                "internal_run_key": run["run_id"],
                "internal_question_key": run["case_id"],
                "condition_internal": run["condition"],
                "raw_run_sha256": sha256(raw_path),
                "packet_sha256": sha256(packet_path),
            }
        )
        packet_records.append(
            {
                "blind_id": blind_id,
                "batch_id": batch_id,
                "packet_sha256": sha256(packet_path),
            }
        )
        first_rows.append(score_row(blind_id, "EXPERT_A", expected, batch_id=batch_id))
        second_rows.append(score_row(blind_id, "EXPERT_B", expected, batch_id=batch_id))
    with (packet_root / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "status": "READY",
                "packets": packet_records,
                "condition_fields_excluded": True,
                "raw_run_sha256_packet_excluded": True,
            },
            handle,
        )
    blind_map = l1_root / "judge_blinding" / "BLIND_ID_MAP.csv"
    write_csv(
        blind_map,
        [
            "blind_id",
            "batch_id",
            "internal_run_key",
            "internal_question_key",
            "condition_internal",
            "raw_run_sha256",
            "packet_sha256",
        ],
        blind_rows,
    )
    expert_1 = l1_root / "scores" / "expert_1.csv"
    expert_2 = l1_root / "scores" / "expert_2.csv"
    write_csv(expert_1, SCORE_FIELDS, first_rows)
    write_csv(expert_2, SCORE_FIELDS, second_rows)
    adjudications = l1_root / "adjudications.csv"
    write_csv(
        adjudications,
        [
            "batch_id",
            "blind_id",
            "field_name",
            "expert1_reviewer_id",
            "expert1_value",
            "expert2_reviewer_id",
            "expert2_value",
            "adjudicator_id",
            "adjudicated_value",
            "reason_code",
            "short_reason",
            "answer_span_quote",
            "evidence_span_locators_json",
            "reviewed_utc",
            "adjudication_record_id",
        ],
        [],
    )
    validation_1 = l1_root / "validation" / "expert_1.json"
    validation_2 = l1_root / "validation" / "expert_2.json"
    validation_1.parent.mkdir(parents=True)
    validation_1.write_text(
        json.dumps({"status": "PASS", "scores_sha256": sha256(expert_1)}),
        encoding="utf-8",
    )
    validation_2.write_text(
        json.dumps({"status": "PASS", "scores_sha256": sha256(expert_2)}),
        encoding="utf-8",
    )
    adjudication_validation = l1_root / "validation" / "adjudication.json"
    adjudication_validation.write_text(
        json.dumps(
            {
                "status": "PASS",
                "inputs": {
                    "adjudications_sha256": sha256(adjudications),
                    "expert_1_sha256": sha256(expert_1),
                    "expert_2_sha256": sha256(expert_2),
                },
            }
        ),
        encoding="utf-8",
    )
    out_root = l1_root / "consensus"
    result = run_script(
        HUMAN / "40_aggregate_human_consensus.py",
        "--blind-map",
        str(blind_map),
        "--raw-root",
        str(raw_root),
        "--packet-root",
        str(packet_root),
        "--expert-1",
        str(expert_1),
        "--expert-2",
        str(expert_2),
        "--expert-1-validation",
        str(validation_1),
        "--expert-2-validation",
        str(validation_2),
        "--adjudications",
        str(adjudications),
        "--adjudication-validation",
        str(adjudication_validation),
        "--out-root",
        str(out_root),
        "--strict-human",
        l1_root=l1_root,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    consensus_path = out_root / "qa_human_consensus_180.csv"
    with consensus_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 180
    assert {row["consensus_status"] for row in rows} == {"PASS"}

    analysis_root = l1_root / "analysis"
    result = run_script(
        SCRIPTS / "A01_analysis" / "run_strict_analysis.py",
        "--human-consensus",
        str(consensus_path),
        "--out",
        str(analysis_root),
        "--strict-human",
        l1_root=l1_root,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    paired = json.loads(
        (analysis_root / "paired_B2_vs_B1.json").read_text(encoding="utf-8")
    )
    assert paired["primary_endpoint"] == "egts_system_outcome"
    assert paired["questions"] == 60
    assert paired["bootstrap_replicates"] == 10_000
    assert paired["bootstrap_rng"].endswith("PCG64(20261104))")

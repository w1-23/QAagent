#!/usr/bin/env python3
"""Generate deterministic synthetic records for end-to-end dry-run testing."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
from collections import Counter
from pathlib import Path

EXPERIMENT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_CLASS = "SIMULATED_DRY_RUN"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_int(*parts: object) -> int:
    payload = "\0".join(str(part) for part in parts).encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:16], 16)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def protected_input(relative: str) -> Path:
    protected_root = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if protected_root:
        candidate = Path(protected_root) / relative
        if candidate.is_file():
            return candidate
    return EXPERIMENT_ROOT / relative


def field_reason_codes(
    answerable: bool,
    answerability_correct: bool,
    answer_correct: bool | str,
    evidence_traceable: bool | str,
    fatal_error: bool,
) -> dict[str, list[str]]:
    return {
        "answerability_correct": (
            []
            if answerability_correct
            else [
                "ANSWERABLE_REFUSED" if answerable else "UNANSWERABLE_ANSWERED"
            ]
        ),
        "answer_correct": (
            []
            if answer_correct in {True, "not_applicable"}
            else ["FACTUAL_ERROR"]
        ),
        "evidence_traceable": (
            []
            if evidence_traceable in {True, "not_applicable"}
            else ["NO_CITATION"]
        ),
        "fatal_error": (
            ["FABRICATED_DATA_OR_VALUE"] if fatal_error else []
        ),
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def split_groups(value: str) -> list[str]:
    return [item for item in value.split("|") if item]


def synthetic_event(run_id: str, name: str, payload: dict) -> dict:
    return {
        "event": name,
        "run_id": run_id,
        "payload": payload,
        "evidence_class": EVIDENCE_CLASS,
    }


def build_core_runs(
    plan: list[dict[str, str]],
) -> tuple[list[dict], list[dict], list[dict]]:
    runs: list[dict] = []
    judge_rows: list[dict] = []
    aggregate_rows: list[dict] = []
    for row in plan:
        if row["kind"] != "qa":
            continue
        value = stable_int(row["run_id"], row["condition"])
        answerable = not row["case_id"].startswith("N")
        answerability_correct = value % 10 != 0
        correct: bool | str = "not_applicable" if not answerable else (
            row["condition"] != "B0"
            and value % 100 < {
            "B1": 62,
            "B2": 78,
            }[row["condition"]]
        )
        evidence_traceable: bool | str = (
            "not_applicable"
            if not answerable
            else (
                row["condition"] != "B0"
                and value % 100
                < {
                    "B1": 70,
                    "B2": 92,
                }[row["condition"]]
            )
        )
        run = {
            "run_id": row["run_id"],
            "experiment_id": "E01_QA60_EGTS",
            "case_id": row["case_id"],
            "condition": row["condition"],
            "trial": int(row["trial"]),
            "prompt": row["prompt"],
            "source_scope": row["source_scope"],
            "evidence_class": EVIDENCE_CLASS,
            "not_for_publication_claims": True,
            "status": "completed",
            "final_answer": (
                f"[SIMULATED] {row['condition']} synthetic answer for "
                f"{row['case_id']}."
            ),
            "citations": []
            if row["condition"] == "B0"
            else [{"source_id": row["source_ids"] or "SIM-SOURCE"}],
            "tool_calls": []
            if row["condition"] in {"B0", "B1"}
            else [
                {
                    "name": "simulated_retrieval",
                    "status": "completed",
                    "source_ids": [row["source_ids"] or "SIM-SOURCE"],
                }
            ],
            "artifact_paths": [],
            "synthetic_metrics": {
                "answerable": answerable,
                "answerability_correct": answerability_correct,
                "answer_correct": correct,
                "evidence_traceable": evidence_traceable,
                "fatal_error": False,
            },
            "events": [
                synthetic_event(row["run_id"], "answer_start", {}),
                synthetic_event(
                    row["run_id"],
                    "text",
                    {"text": f"[SIMULATED] {row['case_id']}"},
                ),
                synthetic_event(
                    row["run_id"],
                    "done",
                    {"answer": f"[SIMULATED] {row['case_id']}"},
                ),
            ],
        }
        runs.append(run)

        judge_1 = {
            "answerability_correct": answerability_correct,
            "answer_correct": correct,
            "evidence_traceable": evidence_traceable,
            "fatal_error": False,
        }
        judge_2 = dict(judge_1)
        if value % 7 and answerable:
            judge_2["answer_correct"] = not judge_1["answer_correct"]
        for judge_id, judgment in (
            ("judge_1", judge_1),
            ("judge_2", judge_2),
        ):
            judge_rows.append(
                {
                    "run_id": row["run_id"],
                    "judge_id": judge_id,
                    "evidence_class": EVIDENCE_CLASS,
                    "not_for_publication_claims": True,
                    "status": "completed",
                    **judgment,
                    "reason_codes_by_field": field_reason_codes(
                        answerable,
                        judgment["answerability_correct"],
                        judgment["answer_correct"],
                        judgment["evidence_traceable"],
                        judgment["fatal_error"],
                    ),
                    "short_reason": "[SIMULATED] Interface test only.",
                    "answer_span_quote": (
                        "[SIMULATED] No real answer span is reproduced."
                    ),
                    "evidence_span_locators": [],
                }
            )
        disputed_values = {
            field: judge_1[field]
            for field in judge_1
            if judge_1[field] != judge_2[field]
        }
        if disputed_values:
            judge_rows.append(
                {
                    "run_id": row["run_id"],
                    "judge_id": "judge_3",
                    "evidence_class": EVIDENCE_CLASS,
                    "not_for_publication_claims": True,
                    "status": "completed",
                    "disputed_values": disputed_values,
                    "reason_codes_by_field": {
                        field: ["FACTUAL_ERROR"]
                        if field == "answer_correct"
                        and value is False
                        else []
                        for field, value in disputed_values.items()
                    },
                    "short_reason": "[SIMULATED] Adjudication interface test.",
                    "answer_span_quote": (
                        "[SIMULATED] No real answer span is reproduced."
                    ),
                    "evidence_span_locators": [],
                }
            )
        aggregate_rows.append(
            {
                "run_id": row["run_id"],
                "case_id": row["case_id"],
                "condition": row["condition"],
                "evidence_class": EVIDENCE_CLASS,
                "not_for_publication_claims": True,
                "judge_3_required": bool(disputed_values),
                "aggregation_status": "SIMULATED_COMPUTED",
            }
        )
    return runs, judge_rows, aggregate_rows


def build_citation_runs(
    plan: list[dict[str, str]],
    questions: list[dict],
    chunks: list[dict],
) -> list[dict]:
    question_map = {row["question_id"]: row for row in questions}
    chunk_map = {row["citation_id"]: row for row in chunks}
    all_chunks = list(chunk_map.values())
    runs: list[dict] = []
    for row in plan:
        question = question_map[row["case_id"]]
        target = next(
            item
            for item in all_chunks
            if item["chunk_id"] == question["target_chunk_id"]
        )
        value = stable_int(row["run_id"])
        threshold = {"B0": 0, "B1": 58, "B2": 76}[row["condition"]]
        target_hit = value % 100 < threshold
        wrong_chunk = all_chunks[(value // 101) % len(all_chunks)]
        if target_hit:
            cited = target
        elif row["condition"] == "B0":
            cited = None
        elif value % 2:
            cited = wrong_chunk
        else:
            cited = None
        citations = []
        if cited is not None:
            citations.append(
                {
                    "chunk_id": cited["chunk_id"],
                    "corpus_id": cited["corpus_id"],
                    "source_file": cited["source_file"],
                }
            )
        runs.append(
            {
                "run_id": row["run_id"],
                "experiment_id": "E01_SUPP_CHUNK_CITATION60",
                "case_id": row["case_id"],
                "condition": row["condition"],
                "trial": int(row["trial"]),
                "prompt": row["prompt"],
                "source_scope": row["source_scope"],
                "evidence_class": EVIDENCE_CLASS,
                "not_for_publication_claims": True,
                "status": "completed",
                "final_answer": (
                    f"[SIMULATED] Synthetic citation response for {row['case_id']}."
                ),
                "citations": citations,
                "synthetic_observation": {
                    "target_chunk_id": target["chunk_id"],
                    "target_hit": target_hit,
                    "wrong_chunk_cited": bool(
                        cited is not None and cited["chunk_id"] != target["chunk_id"]
                    ),
                    "no_citation": cited is None,
                },
                "events": [
                    synthetic_event(row["run_id"], "answer_start", {}),
                    synthetic_event(
                        row["run_id"],
                        "text",
                        {"text": f"[SIMULATED] {row['case_id']}"},
                    ),
                    synthetic_event(
                        row["run_id"],
                        "done",
                        {"answer": f"[SIMULATED] {row['case_id']}"},
                    ),
                ],
            }
        )
    return runs


def build_action_runs(
    plan: list[dict[str, str]],
    experiment_id: str,
) -> list[dict]:
    runs: list[dict] = []
    for row in plan:
        value = stable_int(row["run_id"])
        pass_threshold = 88 if experiment_id == "E02_ROUTE192" else 76
        compliant = value % 100 < pass_threshold
        required = split_groups(row.get("required_groups", ""))
        forbidden = split_groups(row.get("forbidden_groups", ""))
        observed = required if compliant else required[:1]
        if not compliant and value % 2 and forbidden:
            observed.append(forbidden[0])
        runs.append(
            {
                "run_id": row["run_id"],
                "experiment_id": experiment_id,
                "case_id": row["case_id"],
                "condition": row["condition"],
                "prompt": row["prompt"],
                "source_scope": row["source_scope"],
                "evidence_class": EVIDENCE_CLASS,
                "not_for_publication_claims": True,
                "status": "completed",
                "tool_calls": [
                    {
                        "name": group.lower(),
                        "status": "completed",
                        "synthetic": True,
                    }
                    for group in observed
                ],
                "synthetic_observation": {
                    "available": True,
                    "required_met": compliant,
                    "forbidden_violated": bool(
                        compliant and set(observed) & set(forbidden)
                    ),
                },
            }
        )
    return runs


def build_workflow_runs(
    protocols: list[dict],
    acceptance: list[dict[str, str]],
) -> list[dict]:
    by_workflow: dict[str, list[dict[str, str]]] = {}
    for row in acceptance:
        by_workflow.setdefault(row["workflow_id"], []).append(row)
    runs: list[dict] = []
    for protocol in protocols:
        workflow_id = protocol["workflow_id"]
        criteria = []
        for row in by_workflow.get(workflow_id, []):
            value = stable_int(workflow_id, row["criterion_id"])
            mandatory = row.get("critical_1_0") == "1"
            passed = value % 100 < (92 if mandatory else 78)
            criteria.append(
                {
                    "criterion_id": row["criterion_id"],
                    "criterion": row["criterion"],
                    "critical": mandatory,
                    "observed_1_0_NA": 1 if passed else 0,
                    "evidence_path": (
                        f"06_runs/simulated_dry_run/artifacts/"
                        f"{workflow_id}_{row['criterion_id']}.json"
                    ),
                }
            )
        complete = all(
            item["observed_1_0_NA"] == 1
            for item in criteria
            if item["critical"]
        )
        runs.append(
            {
                "run_id": f"{workflow_id}_SIMULATED_T1",
                "experiment_id": workflow_id,
                "case_id": workflow_id,
                "condition": "B2",
                "evidence_class": EVIDENCE_CLASS,
                "not_for_publication_claims": True,
                "status": "completed" if complete else "partial",
                "protocol_sha256": hashlib.sha256(
                    json.dumps(protocol, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "criteria": criteria,
                "artifact_paths": [
                    f"06_runs/simulated_dry_run/artifacts/{workflow_id}.json"
                ],
            }
        )
    return runs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runs-dir", default="06_runs/simulated_dry_run"
    )
    parser.add_argument(
        "--analysis-dir", default="09_analysis/simulated_dry_run"
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    runs_dir = (EXPERIMENT_ROOT / args.runs_dir).resolve()
    analysis_dir = (EXPERIMENT_ROOT / args.analysis_dir).resolve()
    for path in (runs_dir, analysis_dir):
        if path.exists() and not args.force:
            raise SystemExit(
                f"refuse overwrite: {path}; use --force to replace simulated output only"
            )
        if path.exists():
            shutil.rmtree(path)
    runs_dir.mkdir(parents=True)
    analysis_dir.mkdir(parents=True)

    plan = read_csv(EXPERIMENT_ROOT / "06_runs/manifests/run_plan.csv")
    citation_plan = read_csv(
        EXPERIMENT_ROOT / "06_runs/manifests/citation_run_plan.csv"
    )
    citation_questions = read_jsonl(
        EXPERIMENT_ROOT / "03_benchmarks/citation60/questions_FROZEN.jsonl"
    )
    citation_chunks = read_jsonl(
        protected_input("03_benchmarks/citation60/chunks_FROZEN.jsonl")
    )
    acceptance = read_csv(
        EXPERIMENT_ROOT / "07_scoring/reviews/workflow_acceptance.csv"
    )
    protocols = []
    for path in sorted((EXPERIMENT_ROOT / "08_workflows").glob("WF*_protocol.json")):
        protocols.append(json.loads(path.read_text(encoding="utf-8")))

    core_runs, judge_rows, aggregate_rows = build_core_runs(plan)
    citation_runs = build_citation_runs(
        citation_plan, citation_questions, citation_chunks
    )
    route_plan = [row for row in plan if row["kind"] == "intent"]
    multi_plan = [row for row in plan if row["kind"] == "multi"]
    route_runs = build_action_runs(route_plan, "E02_ROUTE192")
    composite_runs = build_action_runs(multi_plan, "E03_MULTI24")
    workflow_runs = build_workflow_runs(protocols, acceptance)

    outputs = {
        "core_qa_runs.jsonl": core_runs,
        "citation_runs.jsonl": citation_runs,
        "route_runs.jsonl": route_runs,
        "composite_runs.jsonl": composite_runs,
        "workflow_runs.jsonl": workflow_runs,
        "judge_responses.jsonl": judge_rows,
        "judge_aggregate.jsonl": aggregate_rows,
    }
    for name, rows in outputs.items():
        write_jsonl(runs_dir / name, rows)

    for workflow in workflow_runs:
        for criterion in workflow["criteria"]:
            write_json(
                EXPERIMENT_ROOT / criterion["evidence_path"],
                {
                    "evidence_class": EVIDENCE_CLASS,
                    "not_for_publication_claims": True,
                    "workflow_id": workflow["experiment_id"],
                    "criterion_id": criterion["criterion_id"],
                    "observed_1_0_NA": criterion["observed_1_0_NA"],
                    "payload": "[SIMULATED] Interface-test evidence placeholder.",
                },
            )
        for artifact in workflow["artifact_paths"]:
            write_json(
                EXPERIMENT_ROOT / artifact,
                {
                    "evidence_class": EVIDENCE_CLASS,
                    "not_for_publication_claims": True,
                    "workflow_id": workflow["experiment_id"],
                    "artifact_type": "SIMULATED_REPORT",
                    "sha256_scope": (
                        "Synthetic placeholder; no delivery artifact was produced."
                    ),
                },
            )

    warning = (
        "# SIMULATED DRY RUN\n\n"
        "This directory contains synthetic interface-test data only. "
        "No model, service, judge API or human reviewer was called.\n\n"
        "Every record is marked `SIMULATED_DRY_RUN` and "
        "`not_for_publication_claims=true`. Do not report these values as "
        "scientific results.\n"
    )
    (runs_dir / "README.md").write_text(warning, encoding="utf-8")
    (analysis_dir / "README.md").write_text(warning, encoding="utf-8")

    citation_hits = Counter()
    citation_denominators = Counter()
    for row in citation_runs:
        citation_denominators[row["condition"]] += 1
        citation_hits[row["condition"]] += int(
            row["synthetic_observation"]["target_hit"]
        )
    route_passes = sum(
        row["synthetic_observation"]["required_met"] for row in route_runs
    )
    composite_passes = sum(
        row["synthetic_observation"]["required_met"] for row in composite_runs
    )
    summary = {
        "evidence_class": EVIDENCE_CLASS,
        "not_for_publication_claims": True,
        "warning": (
            "Synthetic interface-test data only. No model, service, judge or "
            "human participant was called. Never report these values as results."
        ),
        "counts": {
            "core_qa_runs": len(core_runs),
            "citation_runs": len(citation_runs),
            "route_runs": len(route_runs),
            "composite_runs": len(composite_runs),
            "workflow_runs": len(workflow_runs),
            "judge_responses": len(judge_rows),
        },
        "synthetic_descriptive_counts_only": {
            "citation_target_hits": {
                condition: {
                    "hits": citation_hits[condition],
                    "runs": citation_denominators[condition],
                }
                for condition in ("B0", "B1", "B2")
            },
            "route_required_met": {
                "passes": route_passes,
                "runs": len(route_runs),
            },
            "composite_required_met": {
                "passes": composite_passes,
                "runs": len(composite_runs),
            },
            "workflow_status_counts": dict(
                Counter(row["status"] for row in workflow_runs)
            ),
        },
    }
    write_json(analysis_dir / "summary.json", summary)
    write_csv(
        analysis_dir / "summary.csv",
        [
            {
                "evidence_class": EVIDENCE_CLASS,
                "metric": "core_qa_runs",
                "value": len(core_runs),
            },
            {
                "evidence_class": EVIDENCE_CLASS,
                "metric": "citation_runs",
                "value": len(citation_runs),
            },
            {
                "evidence_class": EVIDENCE_CLASS,
                "metric": "route_runs",
                "value": len(route_runs),
            },
            {
                "evidence_class": EVIDENCE_CLASS,
                "metric": "composite_runs",
                "value": len(composite_runs),
            },
            {
                "evidence_class": EVIDENCE_CLASS,
                "metric": "workflow_runs",
                "value": len(workflow_runs),
            },
        ],
    )

    files = []
    for base in (runs_dir, analysis_dir):
        for path in sorted(base.rglob("*")):
            if path.is_file():
                files.append(
                    {
                        "path": path.relative_to(EXPERIMENT_ROOT).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": sha256(path),
                    }
                )
    write_json(
        runs_dir / "manifest.json",
        {
            "evidence_class": EVIDENCE_CLASS,
            "not_for_publication_claims": True,
            "generator": "scripts/S00_simulated_dry_run/00_generate_simulated_data.py",
            "generator_sha256": sha256(Path(__file__).resolve()),
            "inputs": {
                relative: sha256(
                    protected_input(relative)
                    if relative
                    == "03_benchmarks/citation60/chunks_FROZEN.jsonl"
                    else EXPERIMENT_ROOT / relative
                )
                for relative in (
                    "06_runs/manifests/run_plan.csv",
                    "06_runs/manifests/citation_run_plan.csv",
                    "03_benchmarks/citation60/questions_FROZEN.jsonl",
                    "03_benchmarks/citation60/chunks_FROZEN.jsonl",
                    "07_scoring/reviews/workflow_acceptance.csv",
                )
            },
            "outputs": files,
        },
    )
    print(
        json.dumps(
            {
                "status": "SIMULATED_COMPLETE",
                "runs_dir": runs_dir.relative_to(EXPERIMENT_ROOT).as_posix(),
                "analysis_dir": analysis_dir.relative_to(EXPERIMENT_ROOT).as_posix(),
                **summary["counts"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

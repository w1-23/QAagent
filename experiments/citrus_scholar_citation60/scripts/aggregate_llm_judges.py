#!/usr/bin/env python3
"""Aggregate two frozen LLM judges and a frozen adjudicator. Fail closed."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import ROOT, csvrows, sha256_file, utc, write_csv

BOOLEAN_FIELDS = (
    "answerability_correct",
    "answer_correct",
    "evidence_used",
    "fatal_error",
)


def read_judge(path: Path) -> dict:
    if not path.is_file():
        return {"status": "MISSING", "decision": "", "reason_code": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "UNREADABLE",
            "decision": "",
            "reason_code": type(exc).__name__,
        }
    if data.get("status") != "completed":
        return {
            "status": str(data.get("status") or "UNKNOWN"),
            "decision": "",
            "reason_code": str(data.get("reason_code") or ""),
        }
    decision = str(data.get("decision") or "").strip().upper()
    if decision not in {"PASS", "FAIL"}:
        return {
            "status": "INVALID_SCHEMA",
            "decision": "",
            "reason_code": "decision_not_PASS_FAIL",
        }
    for field in BOOLEAN_FIELDS:
        value = data.get(field)
        if field == "evidence_used" and value == "not_applicable":
            data[field] = None
            continue
        if not isinstance(value, bool):
            return {
                "status": "INVALID_SCHEMA",
                "decision": "",
                "reason_code": f"{field}_not_boolean",
            }
    if not str(data.get("reason_code") or "").strip():
        return {
            "status": "INVALID_SCHEMA",
            "decision": "",
            "reason_code": "reason_code_blank",
        }
    locators = data.get("evidence_span_locators")
    if not isinstance(locators, list) or not all(
        isinstance(item, str) for item in locators
    ):
        return {
            "status": "INVALID_SCHEMA",
            "decision": "",
            "reason_code": "evidence_span_locators_not_string_list",
        }
    data["decision"] = decision
    return data


def metric_tuple(judgment: dict) -> tuple:
    return tuple(judgment.get(field) for field in BOOLEAN_FIELDS)


def metrics_for_decision(judgment: dict) -> list[str]:
    errors: list[str] = []
    if judgment["decision"] == "PASS":
        if judgment["answerability_correct"] is not True:
            errors.append("PASS_with_incorrect_answerability")
        if judgment["answer_correct"] is not True:
            errors.append("PASS_with_incorrect_answer")
        if judgment["fatal_error"] is not False:
            errors.append("PASS_with_fatal_error")
        if judgment["evidence_used"] is False:
            errors.append("PASS_without_evidence_use")
    return errors


def int_or_blank(value) -> str:
    return "" if value is None else str(int(bool(value)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-plan", default="06_runs/manifests/run_plan.csv")
    parser.add_argument("--judge-root", default="06_runs/judge_raw")
    parser.add_argument(
        "--out",
        default="07_scoring/judge/qa_judge_aggregated.csv",
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    plan = [row for row in csvrows(args.run_plan) if row["kind"] == "qa"]
    if len(plan) != 180:
        raise SystemExit(f"expected 180 core QA rows, found {len(plan)}")
    judge_root = (ROOT / args.judge_root).resolve()
    out_path = (ROOT / args.out).resolve()
    if out_path.exists():
        raise SystemExit(f"refuse overwrite: {out_path}")

    output_rows = []
    for run in plan:
        run_id = run["run_id"]
        paths = {
            "judge_1": judge_root / "judge_1" / f"{run_id}.json",
            "judge_2": judge_root / "judge_2" / f"{run_id}.json",
            "judge_3": judge_root / "judge_3" / f"{run_id}.json",
        }
        first = read_judge(paths["judge_1"])
        second = read_judge(paths["judge_2"])
        third = {
            "status": "NOT_REQUIRED",
            "decision": "",
            "reason_code": "",
        }
        errors: list[str] = []
        if first["status"] != "completed":
            errors.append(f"judge_1:{first['status']}")
        if second["status"] != "completed":
            errors.append(f"judge_2:{second['status']}")

        source = None
        final = ""
        if not errors:
            errors.extend(f"judge_1:{item}" for item in metrics_for_decision(first))
            errors.extend(f"judge_2:{item}" for item in metrics_for_decision(second))
        if (
            not errors
            and first["decision"] == second["decision"]
            and metric_tuple(first) == metric_tuple(second)
        ):
            final = first["decision"]
            source = first
        elif not errors:
            third = read_judge(paths["judge_3"])
            if third["status"] != "completed":
                errors.append(f"judge_3:{third['status']}")
            else:
                errors.extend(
                    f"judge_3:{item}" for item in metrics_for_decision(third)
                )
                if not errors:
                    final = third["decision"]
                    source = third
        if not errors and (final not in {"PASS", "FAIL"} or source is None):
            errors.append("aggregation_failed")

        output_rows.append(
            {
                "run_id": run_id,
                "question_id": run["case_id"],
                "condition": run["condition"],
                "source_cluster": run["source_ids"].split("+")[0],
                "judge_1_status": first["status"],
                "judge_1_decision": first["decision"],
                "judge_2_status": second["status"],
                "judge_2_decision": second["decision"],
                "judge_3_status": third["status"],
                "judge_3_decision": third["decision"],
                "final_decision": final if not errors else "",
                "egts": "" if errors else str(int(final == "PASS")),
                "answer_correct_1_0": ""
                if errors
                else int_or_blank(source["answer_correct"]),
                "answerability_correct_1_0": ""
                if errors
                else int_or_blank(source["answerability_correct"]),
                "evidence_used_1_0": ""
                if errors
                else int_or_blank(source["evidence_used"]),
                "fatal_error_1_0": ""
                if errors
                else int_or_blank(source["fatal_error"]),
                "aggregation_error": "|".join(errors),
                "judge_1_response_sha256": sha256_file(paths["judge_1"])
                if paths["judge_1"].is_file()
                else "",
                "judge_2_response_sha256": sha256_file(paths["judge_2"])
                if paths["judge_2"].is_file()
                else "",
                "judge_3_response_sha256": sha256_file(paths["judge_3"])
                if paths["judge_3"].is_file()
                else "",
                "aggregated_utc": utc(),
            }
        )

    write_csv(args.out, output_rows)
    errors = [row for row in output_rows if row["aggregation_error"]]
    print(
        json.dumps(
            {
                "status": "PASS" if not errors else "BLOCKED",
                "rows": len(output_rows),
                "errors": len(errors),
                "out": str(out_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2 if args.strict and errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

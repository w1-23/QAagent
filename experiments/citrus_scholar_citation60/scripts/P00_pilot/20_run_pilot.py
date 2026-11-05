#!/usr/bin/env python3
"""Run the isolated 36-answer Pilot after every prerequisite is evidenced."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from capture_schema import validate_capture  # noqa: E402
from common import jsondump, sha256_file, utc  # noqa: E402
from execution_authorization import (  # noqa: E402
    ExecutionAuthorizationError,
    require_pilot_authorized,
)


CONDITIONS = {"B0", "B1", "B2"}
STRATA = {"L", "V", "W", "X", "P", "N"}
VERIFIED_MATERIAL_STATUS = "VERIFIED"


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return payload


def load_adapter(condition: str):
    path = ROOT / "05_baselines" / "adapters" / f"{condition}.py"
    spec = importlib.util.spec_from_file_location(
        f"citrus_pilot_adapter_{condition}",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, "run_case", None)
    if not callable(fn):
        raise RuntimeError(f"adapter has no run_case: {path}")
    return fn


def validate_plan(rows: list[dict]) -> list[str]:
    errors: list[str] = []
    if len(rows) != 36:
        errors.append(f"run_count:{len(rows)}")
    ids = [row.get("run_id", "") for row in rows]
    if len(set(ids)) != len(ids) or any(not value for value in ids):
        errors.append("run_id_not_unique_or_empty")
    if any(row.get("condition") not in CONDITIONS for row in rows):
        errors.append("condition_invalid")
    if any(row.get("stratum") not in STRATA for row in rows):
        errors.append("stratum_invalid")
    by_question: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        by_question[row.get("pilot_id", "")].add(row.get("condition", ""))
    if len(by_question) != 12 or any(values != CONDITIONS for values in by_question.values()):
        errors.append("question_condition_matrix_invalid")
    stratum_counts = Counter(row.get("stratum") for row in rows)
    if dict(stratum_counts) != {stratum: 6 for stratum in STRATA}:
        errors.append("stratum_matrix_invalid")
    order = [int(row.get("run_order") or 0) for row in rows]
    if sorted(order) != list(range(1, 37)):
        errors.append("run_order_invalid")
    return errors


def validate_materials(rows: list[dict]) -> list[str]:
    """Reject declaration-only Pilot rows before any real adapter call."""
    errors: list[str] = []
    for row in rows:
        run_id = str(row.get("run_id") or "<missing>")
        if str(row.get("availability_status") or "") != VERIFIED_MATERIAL_STATUS:
            errors.append(f"{run_id}:availability_status_not_verified")
        try:
            evidence = json.loads(row.get("reference_evidence_json") or "[]")
        except json.JSONDecodeError:
            errors.append(f"{run_id}:reference_evidence_invalid_json")
            continue
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{run_id}:reference_evidence_empty")
            continue
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"{run_id}:reference_evidence_not_object:{index}")
                continue
            locator = str(item.get("locator") or "").strip()
            excerpt = str(item.get("excerpt") or "").strip()
            if not locator:
                errors.append(f"{run_id}:reference_locator_empty:{index}")
            if not excerpt:
                errors.append(f"{run_id}:reference_excerpt_empty:{index}")
            if "TO_BE_FROZEN" in excerpt or "NOT_YET_VERIFIED" in excerpt:
                errors.append(f"{run_id}:reference_excerpt_placeholder:{index}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--stage2-report", required=True)
    parser.add_argument("--g10-report", required=True)
    parser.add_argument("--out-root", default="06_runs/pilot")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max", type=int, default=0)
    args = parser.parse_args()
    if not (args.dry_run or args.execute):
        parser.error("choose --dry-run or --execute")

    plan_path = Path(args.plan).expanduser().resolve()
    if not plan_path.is_file():
        raise SystemExit(f"Pilot plan missing: {plan_path}")
    with plan_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    plan_errors = validate_plan(rows)
    if plan_errors:
        raise SystemExit("Pilot plan invalid: " + ",".join(plan_errors))
    if args.execute and args.max:
        parser.error("--max is restricted to --dry-run; Pilot execution requires all 36 rows")
    if args.max:
        rows = rows[: args.max]
    print(f"selected={len(rows)} execute={args.execute}")
    if args.dry_run:
        print("first_5=", [row["run_id"] for row in rows[:5]])
        return 0
    material_errors = validate_materials(rows)
    if material_errors:
        raise SystemExit("Pilot materials not verified: " + ",".join(material_errors))

    for report_path, label in (
        (Path(args.stage2_report), "Stage 2"),
        (Path(args.g10_report), "G10"),
    ):
        if not report_path.is_file():
            raise SystemExit(f"{label} report missing")
        if load_json(report_path).get("status") != "PASS":
            raise SystemExit(f"{label} report is not PASS")

    model_path = ROOT / "05_baselines/model_config.json"
    judge_path = ROOT / "05_baselines/judge_config.json"
    model = load_json(model_path)
    judge = load_json(judge_path)
    try:
        require_pilot_authorized(model, component="pilot_agent")
        require_pilot_authorized(judge, component="pilot_judges")
    except ExecutionAuthorizationError as exc:
        raise SystemExit(str(exc)) from exc
    for role, definition in judge.get("judges", {}).items():
        if str(definition.get("family") or "").lower() == "deepseek":
            raise SystemExit(f"{role} uses forbidden DeepSeek family")

    out_root = ROOT / args.out_root
    if out_root.exists() and any(out_root.iterdir()):
        raise SystemExit(f"refuse overwrite: {out_root}")
    out_root.mkdir(parents=True, exist_ok=True)
    adapters = {}
    for row in sorted(rows, key=lambda item: int(item["run_order"])):
        condition = row["condition"]
        target = out_root / "raw" / condition / f"{row['run_id']}.json"
        if target.exists():
            raise SystemExit(f"refuse overwrite: {target}")
        started_utc = utc()
        started = time.monotonic()
        try:
            if condition not in adapters:
                adapters[condition] = load_adapter(condition)
            response = adapters[condition](dict(row), model)
            if not isinstance(response, dict):
                raise TypeError("adapter did not return a dict")
            capture_errors = validate_capture(
                response,
                condition=condition,
                require_raw_provider=True,
            )
            if capture_errors:
                raise ValueError("capture schema: " + ",".join(capture_errors))
        except Exception as exc:
            response = {
                "status": "failed",
                "final_answer": "",
                "raw_answer": "",
                "tool_calls": [],
                "citations": [],
                "artifact_paths": [],
                "artifact_sha256": {},
                "failure_class": type(exc).__name__,
                "errors": [str(exc)[:350]],
            }
        record = {
            "run_id": row["run_id"],
            "pilot_id": row["pilot_id"],
            "condition": condition,
            "trial": int(row["trial"]),
            "stratum": row["stratum"],
            "prompt": row["prompt"],
            "source_scope": row["source_scope"],
            "attachment": row.get("attachment", ""),
            "reference_evidence": json.loads(
                row.get("reference_evidence_json") or "[]"
            ),
            "materials_preflight": row.get("materials_preflight", ""),
            "availability_status": row.get("availability_status", ""),
            "run_order": int(row["run_order"]),
            "not_formal_denominator": True,
            "started_utc": started_utc,
            "ended_utc": utc(),
            "elapsed_wall_ms": round((time.monotonic() - started) * 1000, 2),
            "pilot_plan_sha256": sha256_file(plan_path),
            "model_config_sha256": sha256_file(model_path),
            "judge_config_sha256": sha256_file(judge_path),
            "stage2_report_sha256": sha256_file(Path(args.stage2_report)),
            "g10_report_sha256": sha256_file(Path(args.g10_report)),
            "actual_response": response,
        }
        jsondump(target.relative_to(ROOT), record)
        print(row["run_id"], response.get("status"))

    manifest = {
        "status": "PILOT_COLLECTION_COMPLETE_NOT_YET_SCORED",
        "not_formal_denominator": True,
        "planned_runs": len(rows),
        "plan": str(plan_path),
        "plan_sha256": sha256_file(plan_path),
        "raw_files": {
            path.relative_to(out_root).as_posix(): sha256_file(path)
            for path in sorted((out_root / "raw").rglob("*.json"))
        },
    }
    manifest_path = out_root / "PILOT_COLLECTION_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

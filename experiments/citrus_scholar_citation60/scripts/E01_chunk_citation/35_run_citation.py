#!/usr/bin/env python3
"""Run the frozen single-chunk citation benchmark through B0/B1/B2 adapters."""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[2]
import sys
sys.path.insert(0, str(SCRIPT.parents[1]))
from common import csvrows, jsondump, sha256_file, utc  # noqa: E402
from execution_authorization import (  # noqa: E402
    ExecutionAuthorizationError,
    require_execution_authorized,
)
from capture_schema import validate_capture  # noqa: E402


def load_adapter(condition: str):
    path = ROOT / "05_baselines" / "adapters" / f"{condition}.py"
    spec = importlib.util.spec_from_file_location(f"citrus_adapter_{condition}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, "run_case", None)
    if not callable(fn):
        raise RuntimeError(f"adapter has no run_case: {path}")
    return fn


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", default="06_runs/manifests/citation_run_plan.csv")
    parser.add_argument("--condition", choices=["B0", "B1", "B2", "all"], default="all")
    parser.add_argument("--max", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not (args.dry_run or args.execute):
        parser.error("choose --dry-run or --execute")
    plan_path = ROOT / args.plan
    if not plan_path.is_file():
        raise SystemExit(f"run plan missing: {plan_path}")
    plan = csvrows(args.plan)
    selected = [
        row for row in plan
        if row.get("kind") == "citation"
        and (args.condition == "all" or row.get("condition") == args.condition)
    ]
    if args.max:
        selected = selected[:args.max]
    print(f"selected={len(selected)} execute={args.execute}")
    if args.dry_run:
        print("first_5=", [row["run_id"] for row in selected[:5]])
        return 0

    lock_path = ROOT / "00_protocol/locked_manifest.json"
    if not lock_path.is_file():
        raise SystemExit("formal lock missing")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    for rel, expected in lock.get("file_hashes", {}).items():
        if sha256_file(ROOT / rel) != expected:
            raise SystemExit(f"LOCK INVALID: {rel}")
    config_path = ROOT / "05_baselines/model_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        require_execution_authorized(config, component="model_config")
    except ExecutionAuthorizationError as exc:
        raise SystemExit(str(exc)) from exc
    question_lock = ROOT / "03_benchmarks/citation60/questions_FROZEN.manifest.json"
    if not question_lock.is_file():
        raise SystemExit("questions are not frozen")
    qlock = json.loads(question_lock.read_text(encoding="utf-8"))
    if sha256_file(ROOT / "03_benchmarks/citation60/questions_FROZEN.jsonl") != qlock["questions_frozen_sha256"]:
        raise SystemExit("frozen questions changed")

    adapters = {}
    for row in selected:
        condition = row["condition"]
        target = ROOT / "06_runs/raw" / condition / f"{row['run_id']}.json"
        if target.exists():
            print(f"EXISTS -- never overwrite: {target.name}")
            continue
        started = time.monotonic()
        started_utc = utc()
        try:
            if condition not in adapters:
                adapters[condition] = load_adapter(condition)
            response = adapters[condition](dict(row), config)
            if not isinstance(response, dict):
                raise TypeError("adapter did not return a dict")
            capture_errors = validate_capture(
                response,
                condition=condition,
                require_raw_provider=True,
            )
            if capture_errors:
                raise ValueError("capture schema: " + ",".join(capture_errors))
            if response.get("status") not in {
                "completed", "partial", "failed", "no_results", "cancelled", "skipped"
            }:
                raise ValueError("adapter returned invalid status")
            error = None
        except Exception as exc:  # preserve the failed attempt without secrets
            error = f"{type(exc).__name__}: {str(exc)[:350]}"
            response = {
                "status": "failed",
                "final_answer": "",
                "raw_answer": "",
                "tool_calls": [],
                "citations": [],
                "artifact_paths": [],
                "error": error,
                "adapter_exception": type(exc).__name__,
            }
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        record = {
            "run_id": row["run_id"],
            "case_id": row["case_id"],
            "kind": "citation",
            "condition": condition,
            "trial": int(row.get("trial") or 1),
            "run_order": int(row.get("run_order") or 0),
            "prompt": row["prompt"],
            "source_scope": row["source_scope"],
            "started_utc": started_utc,
            "ended_utc": utc(),
            "run_plan_sha256": sha256_file(plan_path),
            "question_lock_sha256": sha256_file(question_lock),
            "model_config_sha256": sha256_file(config_path),
            "elapsed_wall_ms": elapsed_ms,
            "actual_response": response,
            "error": error,
        }
        jsondump(target.relative_to(ROOT), record)
        print(row["run_id"], response.get("status"), round(elapsed_ms / 1000, 2), "s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


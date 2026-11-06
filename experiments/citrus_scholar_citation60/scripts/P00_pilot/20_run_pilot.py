#!/usr/bin/env python3
"""Run the isolated 36-answer Pilot after every prerequisite is evidenced."""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "05_baselines" / "adapters"))

from capture_schema import validate_capture  # noqa: E402
from common import jsondump, sha256_file, utc  # noqa: E402
from execution_authorization import (  # noqa: E402
    ExecutionAuthorizationError,
    require_pilot_authorized,
)
from experiment_gate_validation import (  # noqa: E402
    load_json_object,
    validate_gate_report,
    validate_stage1_manifest,
)
from source_scope import (  # noqa: E402
    resolve_allowed_corpus_family_ids,
    resolve_source_scope,
)


CONDITIONS = {"B0", "B1", "B2"}
STRATA = {"L", "V", "W", "X", "P", "N"}
VERIFIED_MATERIAL_STATUS = "VERIFIED"
ORDER_ALGORITHM = "python-random-shuffle-v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def current_git_identity() -> tuple[str, str]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot resolve current Git checkout identity") from exc
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(
        r"[0-9a-f]{40}", tree
    ):
        raise RuntimeError("Git checkout identity is not a 40-character SHA")
    return commit, tree


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


def validate_plan(
    rows: list[dict],
    *,
    plan_path: Path,
    manifest: dict,
) -> list[str]:
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
    if manifest.get("status") != "PILOT_PLAN_FROZEN":
        errors.append("plan_manifest_status_invalid")
    if manifest.get("plan_sha256") != sha256_file(plan_path):
        errors.append("plan_sha256_mismatch")
    if manifest.get("order_algorithm") != ORDER_ALGORITHM:
        errors.append("plan_order_algorithm_invalid")
    if not isinstance(manifest.get("seed"), int):
        errors.append("plan_seed_invalid")
    ordered = [
        row.get("run_id", "")
        for row in sorted(rows, key=lambda item: int(item.get("run_order") or 0))
    ]
    if manifest.get("ordered_run_ids") != ordered:
        errors.append("plan_order_differs_from_frozen_manifest")
    for field in ("reviewed_commit", "reviewed_tree"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get(field) or "")):
            errors.append(f"plan_manifest_{field}_invalid")
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
        try:
            negative_evidence = json.loads(row.get("negative_evidence_json") or "{}")
        except json.JSONDecodeError:
            errors.append(f"{run_id}:negative_evidence_invalid_json")
            continue
        try:
            policy = resolve_source_scope(row.get("source_scope"))
            resolve_allowed_corpus_family_ids(
                row.get("allowed_corpus_family_ids"),
                policy,
            )
        except ValueError as exc:
            errors.append(f"{run_id}:allowed_corpus_family_ids_invalid:{exc}")
        if str(row.get("stratum") or "") == "N":
            if evidence not in ([], None):
                errors.append(f"{run_id}:unanswerable_must_not_have_positive_evidence")
            errors.extend(
                _validate_negative_evidence(run_id, negative_evidence)
            )
            continue
        if negative_evidence not in ({}, None):
            errors.append(f"{run_id}:answerable_must_not_have_negative_evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{run_id}:reference_evidence_empty")
            continue
        for index, item in enumerate(evidence):
            if not isinstance(item, dict):
                errors.append(f"{run_id}:reference_evidence_not_object:{index}")
                continue
            for field in ("source_id", "chunk_id", "source_sha256"):
                if not str(item.get(field) or "").strip():
                    errors.append(f"{run_id}:reference_{field}_empty:{index}")
            locator = str(item.get("locator") or "").strip()
            excerpt = str(item.get("excerpt") or "").strip()
            if not locator:
                errors.append(f"{run_id}:reference_locator_empty:{index}")
            if not excerpt:
                errors.append(f"{run_id}:reference_excerpt_empty:{index}")
            if not SHA256.fullmatch(str(item.get("source_sha256") or "")):
                errors.append(f"{run_id}:reference_source_sha_invalid:{index}")
            combined = "|".join(str(value) for value in item.values())
            if "TO_BE_FROZEN" in combined or "NOT_YET_VERIFIED" in combined:
                errors.append(f"{run_id}:reference_placeholder:{index}")
    return errors


def _validate_negative_evidence(run_id: str, value: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or not value:
        return [f"{run_id}:negative_evidence_empty"]
    corpus_scope = value.get("corpus_scope")
    snapshot = value.get("retrieval_snapshot")
    process = value.get("negative_verification_process")
    confirmation = value.get("expert_confirmation")
    if not isinstance(corpus_scope, dict):
        errors.append(f"{run_id}:negative_corpus_scope_missing")
    else:
        family_ids = corpus_scope.get("family_ids")
        if (
            not isinstance(family_ids, list)
            or not family_ids
            or any(not str(item).strip() for item in family_ids)
        ):
            errors.append(f"{run_id}:negative_corpus_scope_family_ids_invalid")
        if not str(corpus_scope.get("generation") or "").strip():
            errors.append(f"{run_id}:negative_corpus_scope_generation_empty")
    if not isinstance(snapshot, dict):
        errors.append(f"{run_id}:negative_retrieval_snapshot_missing")
    else:
        if not str(snapshot.get("query") or "").strip():
            errors.append(f"{run_id}:negative_snapshot_query_empty")
        if not str(snapshot.get("executed_utc") or "").strip():
            errors.append(f"{run_id}:negative_snapshot_executed_utc_empty")
        if snapshot.get("result_count") != 0:
            errors.append(f"{run_id}:negative_snapshot_result_count_not_zero")
        snapshot_sha = str(snapshot.get("snapshot_sha256") or "")
        if (
            not SHA256.fullmatch(snapshot_sha)
            or "TO_BE_FROZEN" in snapshot_sha
        ):
            errors.append(f"{run_id}:negative_snapshot_sha_invalid")
    if not isinstance(process, dict):
        errors.append(f"{run_id}:negative_verification_process_missing")
    else:
        for field in ("method", "reviewed_by", "reviewed_utc"):
            if not str(process.get(field) or "").strip():
                errors.append(f"{run_id}:negative_process_{field}_empty")
    if not str(value.get("expected_refusal_rule") or "").strip():
        errors.append(f"{run_id}:negative_expected_refusal_rule_empty")
    if not isinstance(confirmation, dict):
        errors.append(f"{run_id}:negative_expert_confirmation_missing")
    else:
        if confirmation.get("decision") != "CONFIRMED_UNANSWERABLE":
            errors.append(f"{run_id}:negative_expert_decision_invalid")
        if not SHA256.fullmatch(
            str(confirmation.get("reviewer_id_hash") or "")
        ):
            errors.append(f"{run_id}:negative_reviewer_id_hash_invalid")
        if not SHA256.fullmatch(
            str(confirmation.get("evidence_sha256") or "")
        ):
            errors.append(f"{run_id}:negative_confirmation_evidence_sha_invalid")
        if not str(confirmation.get("reviewed_utc") or "").strip():
            errors.append(f"{run_id}:negative_confirmation_reviewed_utc_empty")
    combined = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if "TO_BE_FROZEN" in combined or "NOT_YET_VERIFIED" in combined:
        errors.append(f"{run_id}:negative_evidence_placeholder")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--stage2-report", required=True)
    parser.add_argument("--g10-report", required=True)
    parser.add_argument("--stage2-report-sha256", required=True)
    parser.add_argument("--g10-report-sha256", required=True)
    parser.add_argument("--stage1-manifest")
    parser.add_argument("--stage1-manifest-sha256")
    parser.add_argument("--g01-report")
    parser.add_argument("--g01-report-sha256")
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
    manifest_path = plan_path.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise SystemExit(f"Pilot manifest missing: {manifest_path}")
    plan_manifest = load_json_object(manifest_path)
    plan_errors = validate_plan(
        rows,
        plan_path=plan_path,
        manifest=plan_manifest,
    )
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
    for option in (
        "stage1_manifest",
        "stage1_manifest_sha256",
        "g01_report",
        "g01_report_sha256",
    ):
        if not getattr(args, option):
            parser.error(f"--{option.replace('_', '-')} is required with --execute")
    material_errors = validate_materials(rows)
    if material_errors:
        raise SystemExit("Pilot materials not verified: " + ",".join(material_errors))

    model_path = ROOT / "05_baselines/model_config.json"
    judge_path = ROOT / "05_baselines/judge_config.json"
    model = load_json(model_path)
    judge = load_json(judge_path)
    try:
        require_pilot_authorized(model, component="pilot_agent")
        require_pilot_authorized(judge, component="pilot_judges")
    except ExecutionAuthorizationError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        current_commit, current_tree = current_git_identity()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if (
        plan_manifest["reviewed_commit"] != current_commit
        or plan_manifest["reviewed_tree"] != current_tree
    ):
        raise SystemExit(
            "Pilot plan reviewed Git identity differs from the execution checkout"
        )
    deployment_id, stage1_errors = validate_stage1_manifest(
        Path(args.stage1_manifest),
        expected_manifest_sha256=args.stage1_manifest_sha256,
        g01_report_path=Path(args.g01_report),
        expected_g01_report_sha256=args.g01_report_sha256,
        reviewed_commit=plan_manifest["reviewed_commit"],
        reviewed_tree=plan_manifest["reviewed_tree"],
    )
    if stage1_errors or not deployment_id:
        raise SystemExit(
            "Pilot Stage 1/G01 binding invalid: " + ",".join(stage1_errors)
        )
    gate_errors: list[str] = []
    gate_errors.extend(
        f"stage2:{error}"
        for error in validate_gate_report(
            Path(args.stage2_report),
            expected_report_sha256=args.stage2_report_sha256,
            expected_audit="AGENT_EXECUTION_EVIDENCE",
            reviewed_commit=plan_manifest["reviewed_commit"],
            reviewed_tree=plan_manifest["reviewed_tree"],
            model_config=model,
            model_config_path=model_path,
            stage1_manifest_path=Path(args.stage1_manifest),
            expected_stage1_manifest_sha256=args.stage1_manifest_sha256,
            expected_deployment_id=deployment_id,
        )
    )
    gate_errors.extend(
        f"g10:{error}"
        for error in validate_gate_report(
            Path(args.g10_report),
            expected_report_sha256=args.g10_report_sha256,
            expected_audit="G10_TOOL_FACT_EVIDENCE",
            reviewed_commit=plan_manifest["reviewed_commit"],
            reviewed_tree=plan_manifest["reviewed_tree"],
            model_config=model,
            model_config_path=model_path,
            stage1_manifest_path=Path(args.stage1_manifest),
            expected_stage1_manifest_sha256=args.stage1_manifest_sha256,
            expected_deployment_id=deployment_id,
        )
    )
    if gate_errors:
        raise SystemExit("Pilot execution gates invalid: " + ",".join(gate_errors))
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
            "allowed_corpus_family_ids": json.loads(
                row["allowed_corpus_family_ids"]
            ),
            "attachment": row.get("attachment", ""),
            "reference_evidence": json.loads(
                row.get("reference_evidence_json") or "[]"
            ),
            "negative_evidence": json.loads(
                row.get("negative_evidence_json") or "{}"
            ),
            "materials_preflight": row.get("materials_preflight", ""),
            "availability_status": row.get("availability_status", ""),
            "run_order": int(row["run_order"]),
            "not_formal_denominator": True,
            "started_utc": started_utc,
            "ended_utc": utc(),
            "elapsed_wall_ms": round((time.monotonic() - started) * 1000, 2),
            "pilot_plan_sha256": sha256_file(plan_path),
            "reviewed_commit": plan_manifest["reviewed_commit"],
            "reviewed_tree": plan_manifest["reviewed_tree"],
            "plan_seed": plan_manifest["seed"],
            "plan_order_algorithm": plan_manifest["order_algorithm"],
            "model_config_sha256": sha256_file(model_path),
            "judge_config_sha256": sha256_file(judge_path),
            "stage2_report_sha256": sha256_file(Path(args.stage2_report)),
            "g10_report_sha256": sha256_file(Path(args.g10_report)),
            "stage1_manifest_sha256": args.stage1_manifest_sha256,
            "g01_report_sha256": args.g01_report_sha256,
            "deployment_id": deployment_id,
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
        "reviewed_commit": plan_manifest["reviewed_commit"],
        "reviewed_tree": plan_manifest["reviewed_tree"],
        "seed": plan_manifest["seed"],
        "order_algorithm": plan_manifest["order_algorithm"],
        "stage2_report_sha256": args.stage2_report_sha256,
        "g10_report_sha256": args.g10_report_sha256,
        "stage1_manifest_sha256": args.stage1_manifest_sha256,
        "g01_report_sha256": args.g01_report_sha256,
        "deployment_id": deployment_id,
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

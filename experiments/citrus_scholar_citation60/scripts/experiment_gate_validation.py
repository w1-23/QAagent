"""Strict, fail-closed validation for Stage 2 and G10 execution gates."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
EXPERIMENT_ID = "citrus_scholar_citation60"
G10_CATEGORIES = {
    "local",
    "web",
    "attachment",
    "audit",
    "verification",
    "export",
}
STAGE1_ARTIFACT = "SERVER_API_PROBE_MANIFEST"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _valid_relative_path(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    path = Path(text)
    return not path.is_absolute() and ".." not in path.parts


def _validate_evidence_files(
    report: Mapping[str, Any],
    errors: list[str],
    *,
    approved_root: Path | None = None,
) -> None:
    root_text = str(report.get("evidence_root") or "").strip()
    if not root_text:
        errors.append("gate_evidence_root_missing")
        rows = report.get("evidence_files")
        if not isinstance(rows, list) or not rows:
            errors.append("gate_evidence_files_missing")
        return
    root = Path(root_text).expanduser().resolve()
    if not root.is_dir():
        errors.append("gate_evidence_root_invalid")
        return
    if approved_root is not None and root != approved_root.resolve():
        errors.append("gate_evidence_root_differs_from_approved")
    rows = report.get("evidence_files")
    if not isinstance(rows, list) or not rows:
        errors.append("gate_evidence_files_missing")
        return
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"gate_evidence_file_invalid:{index}")
            continue
        relative = str(row.get("path") or "").strip()
        digest = str(row.get("sha256") or "").strip()
        if not _valid_relative_path(relative):
            errors.append(f"gate_evidence_path_invalid:{index}")
            continue
        if relative in seen:
            errors.append(f"gate_evidence_path_duplicate:{index}")
            continue
        seen.add(relative)
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            errors.append(f"gate_evidence_path_outside_root:{index}")
            continue
        if not path.is_file() or path.stat().st_size <= 0:
            errors.append(f"gate_evidence_file_missing:{index}")
            continue
        if not SHA256.fullmatch(digest):
            errors.append(f"gate_evidence_sha_invalid:{index}")
        elif sha256_file(path) != digest:
            errors.append(f"gate_evidence_sha_mismatch:{index}")


def validate_stage1_manifest(
    manifest_path: Path,
    *,
    expected_manifest_sha256: str,
    g01_report_path: Path,
    expected_g01_report_sha256: str,
    reviewed_commit: str,
    reviewed_tree: str,
) -> tuple[str | None, list[str]]:
    """Validate the approved Stage 1 manifest and its G01 deployment binding."""
    errors: list[str] = []
    path = manifest_path.expanduser().resolve()
    if not path.is_file():
        return None, ["stage1_manifest_missing"]
    if not SHA256.fullmatch(str(expected_manifest_sha256 or "")):
        errors.append("expected_stage1_manifest_sha_invalid")
    elif sha256_file(path) != expected_manifest_sha256:
        errors.append("stage1_manifest_sha_mismatch")
    try:
        manifest = load_json_object(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, errors + ["stage1_manifest_json_invalid"]
    if manifest.get("artifact") != STAGE1_ARTIFACT:
        errors.append("stage1_manifest_artifact_invalid")
    if manifest.get("status") != "PASS":
        errors.append("stage1_manifest_not_PASS")
    if manifest.get("reviewed_commit") != reviewed_commit:
        errors.append("stage1_manifest_reviewed_commit_mismatch")
    if manifest.get("reviewed_tree") != reviewed_tree:
        errors.append("stage1_manifest_reviewed_tree_mismatch")
    guard = manifest.get("execution_guard")
    if not isinstance(guard, dict):
        return None, errors + ["stage1_execution_guard_missing"]
    if guard.get("status") != "PASS":
        errors.append("stage1_execution_guard_not_PASS")
    deployment_id = str(guard.get("deployment_id") or "")
    if not SHA256.fullmatch(deployment_id):
        errors.append("stage1_deployment_id_invalid")
        deployment_id = None
    guard_g01_sha = str(guard.get("report_path_sha256") or "")
    if not SHA256.fullmatch(guard_g01_sha):
        errors.append("stage1_g01_report_sha_invalid")

    g01_path = g01_report_path.expanduser().resolve()
    if not g01_path.is_file():
        errors.append("g01_report_missing")
        return deployment_id, sorted(set(errors))
    actual_g01_sha = sha256_file(g01_path)
    if not SHA256.fullmatch(str(expected_g01_report_sha256 or "")):
        errors.append("expected_g01_report_sha_invalid")
    elif actual_g01_sha != expected_g01_report_sha256:
        errors.append("g01_report_sha_mismatch")
    if guard_g01_sha != actual_g01_sha:
        errors.append("stage1_execution_guard_g01_sha_mismatch")
    try:
        g01 = load_json_object(g01_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return deployment_id, sorted(set(errors + ["g01_report_json_invalid"]))
    if g01.get("status") != "PASS":
        errors.append("g01_report_not_PASS")
    if g01.get("reviewed_commit") != reviewed_commit:
        errors.append("g01_reviewed_commit_mismatch")
    if g01.get("reviewed_tree") != reviewed_tree:
        errors.append("g01_reviewed_tree_mismatch")
    experiment = g01.get("experiment")
    if not isinstance(experiment, dict):
        errors.append("g01_experiment_identity_missing")
    elif deployment_id and experiment.get("deployment_id") != deployment_id:
        errors.append("g01_deployment_id_differs_from_stage1")
    return deployment_id, sorted(set(errors))


def validate_gate_report(
    report_path: Path,
    *,
    expected_report_sha256: str,
    expected_audit: str,
    reviewed_commit: str,
    reviewed_tree: str,
    model_config: Mapping[str, Any],
    model_config_path: Path,
    stage1_manifest_path: Path,
    expected_stage1_manifest_sha256: str,
    expected_deployment_id: str,
    evidence_root: Path | None = None,
) -> list[str]:
    """Validate the frozen report bytes and all cross-binding fields."""
    errors: list[str] = []
    path = report_path.expanduser().resolve()
    if not path.is_file():
        return ["gate_report_missing"]
    if not SHA256.fullmatch(str(expected_report_sha256 or "")):
        errors.append("expected_gate_report_sha_invalid")
    elif sha256_file(path) != expected_report_sha256:
        errors.append("gate_report_sha_mismatch")
    try:
        report = load_json_object(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return errors + ["gate_report_json_invalid"]

    if report.get("status") != "PASS":
        errors.append("gate_report_not_PASS")
    if report.get("artifact") != "EXPERIMENT_GATE_REPORT":
        errors.append("gate_report_artifact_invalid")
    if report.get("audit") != expected_audit:
        errors.append("gate_report_audit_mismatch")
    if report.get("experiment_id") != EXPERIMENT_ID:
        errors.append("gate_report_experiment_id_mismatch")
    if str(report.get("protocol_version") or "") != str(
        model_config.get("protocol_version") or ""
    ):
        errors.append("gate_report_protocol_version_mismatch")
    if report.get("reviewed_commit") != reviewed_commit:
        errors.append("gate_report_reviewed_commit_mismatch")
    if report.get("reviewed_tree") != reviewed_tree:
        errors.append("gate_report_reviewed_tree_mismatch")
    if not GIT_SHA.fullmatch(str(reviewed_commit or "")):
        errors.append("reviewed_commit_invalid")
    if not GIT_SHA.fullmatch(str(reviewed_tree or "")):
        errors.append("reviewed_tree_invalid")

    deployment_id = str(report.get("deployment_id") or "")
    if not SHA256.fullmatch(deployment_id):
        errors.append("gate_report_deployment_id_invalid")
    elif deployment_id != expected_deployment_id:
        errors.append("gate_report_deployment_id_mismatch")
    model_id = str(report.get("model_id") or "")
    allowed_models = {
        str(value)
        for value in model_config.get("allowed_response_models", [])
        if str(value)
    }
    candidate = str(model_config.get("candidate_model_id") or "")
    if candidate:
        allowed_models.add(candidate)
    if model_id not in allowed_models:
        errors.append("gate_report_model_id_not_frozen")
    if report.get("model_config_sha256") != sha256_file(model_config_path):
        errors.append("gate_report_model_config_sha_mismatch")
    stage1_sha = str(report.get("stage1_manifest_sha256") or "")
    if not SHA256.fullmatch(stage1_sha):
        errors.append("gate_report_stage1_manifest_sha_invalid")
    else:
        stage1_path = stage1_manifest_path.expanduser().resolve()
        if not stage1_path.is_file():
            errors.append("gate_stage1_manifest_missing")
        else:
            if not SHA256.fullmatch(str(expected_stage1_manifest_sha256 or "")):
                errors.append("expected_stage1_manifest_sha_invalid")
            actual_stage1_sha = sha256_file(stage1_path)
            if (
                SHA256.fullmatch(str(expected_stage1_manifest_sha256 or ""))
                and actual_stage1_sha != expected_stage1_manifest_sha256
            ):
                errors.append("gate_stage1_manifest_sha_mismatch")
            if stage1_sha != actual_stage1_sha:
                errors.append("gate_report_stage1_manifest_sha_mismatch")

    _validate_evidence_files(report, errors, approved_root=evidence_root)
    evidence_manifest = {
        str(row.get("path") or ""): row
        for row in report.get("evidence_files", [])
        if isinstance(row, dict)
    }

    def verify_report_reference(field: str, ref_field: str) -> None:
        digest = str(report.get(field) or "")
        relative = str(report.get(ref_field) or "")
        if not SHA256.fullmatch(digest):
            errors.append(f"gate_report_{field}_invalid")
            return
        row = evidence_manifest.get(relative)
        if not _valid_relative_path(relative) or not isinstance(row, dict):
            errors.append(f"gate_report_{ref_field}_not_in_evidence_manifest")
            return
        if str(row.get("sha256") or "") != digest:
            errors.append(f"gate_report_{field}_differs_from_evidence_manifest")

    if expected_audit == "AGENT_EXECUTION_EVIDENCE":
        verify_report_reference("request_sha256", "request_ref")
        verify_report_reference("trace_sha256", "trace_ref")
        if str(report.get("request_ref") or "") == str(report.get("trace_ref") or ""):
            errors.append("gate_report_request_and_trace_ref_equal")
        if report.get("stage1_model_cross_binding") != "PASS":
            errors.append("gate_report_stage1_binding_not_PASS")
    elif expected_audit == "G10_TOOL_FACT_EVIDENCE":
        categories = report.get("tool_fact_categories")
        top_level_paths = {
            str(row.get("path") or "")
            for row in report.get("evidence_files", [])
            if isinstance(row, dict)
        }
        if not isinstance(categories, dict):
            errors.append("g10_categories_missing")
        else:
            if set(categories) != G10_CATEGORIES:
                errors.append("g10_categories_incomplete")
            for name in sorted(G10_CATEGORIES):
                category = categories.get(name)
                if not isinstance(category, dict):
                    errors.append(f"g10_category_invalid:{name}")
                    continue
                if category.get("status") != "PASS":
                    errors.append(f"g10_category_not_PASS:{name}")
                evidence = category.get("evidence_files")
                if not isinstance(evidence, list) or not evidence:
                    errors.append(f"g10_category_evidence_missing:{name}")
                    continue
                for index, item in enumerate(evidence):
                    path_text = (
                        str(item.get("path") or "")
                        if isinstance(item, dict)
                        else str(item or "")
                    )
                    if not path_text or path_text not in top_level_paths:
                        errors.append(
                            f"g10_category_evidence_not_in_manifest:{name}:{index}"
                        )
    else:
        errors.append("gate_report_audit_not_supported")
    return sorted(set(errors))


__all__ = [
    "EXPERIMENT_ID",
    "G10_CATEGORIES",
    "STAGE1_ARTIFACT",
    "load_json_object",
    "sha256_file",
    "validate_gate_report",
    "validate_stage1_manifest",
]

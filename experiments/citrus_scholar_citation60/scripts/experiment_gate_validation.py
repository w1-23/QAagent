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


def validate_gate_report(
    report_path: Path,
    *,
    expected_report_sha256: str,
    expected_audit: str,
    reviewed_commit: str,
    reviewed_tree: str,
    model_config: Mapping[str, Any],
    model_config_path: Path,
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
    if not SHA256.fullmatch(str(report.get("stage1_manifest_sha256") or "")):
        errors.append("gate_report_stage1_manifest_sha_invalid")

    _validate_evidence_files(report, errors, approved_root=evidence_root)

    if expected_audit == "AGENT_EXECUTION_EVIDENCE":
        for field in ("request_sha256", "trace_sha256"):
            if not SHA256.fullmatch(str(report.get(field) or "")):
                errors.append(f"gate_report_{field}_invalid")
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
    "load_json_object",
    "sha256_file",
    "validate_gate_report",
]

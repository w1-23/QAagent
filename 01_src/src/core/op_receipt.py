"""Normalize host tool results before lossy model-context projection.

Raw result storage remains in tool_results. This bounded metadata receipt carries
only evidence identity, coverage and outputs; it never interprets model prose.
"""
from __future__ import annotations
import json
from typing import Any


def result_payload(content: Any, artifact: Any) -> dict:
    try:
        parsed = json.loads(content) if isinstance(content, str) else {}
    except (ValueError, TypeError):
        parsed = {}
    data = dict(parsed) if isinstance(parsed, dict) else {}
    if isinstance(artifact, dict):
        data.update(artifact)
    return data


def make_receipt(name: str, call_id: str, content: Any, artifact: Any) -> dict:
    from src.tools.outcome import normalize_tool_outcome
    outcome = normalize_tool_outcome(name, str(content or ""), artifact)
    payload = result_payload(content, artifact)
    targets = {}
    if name == "verify_uploaded_references":
        targets = {"coverage": payload.get("coverage", {}), "rows": [
            {k: r[k] for k in ("record_id", "record_index", "source_sha256", "verification_rule",
                               "status", "doi", "input_title", "detail") if k in r}
            for r in payload.get("rows", []) if isinstance(r, dict)]}
        if payload.get("schema") == "reference_audit/2":
            targets.update({key: payload[key] for key in (
                "schema", "audit_id", "rule_version", "terminal", "export_status", "run_reason"
            ) if key in payload})
    elif name == "audit_uploaded_data":
        targets = {"results": [{k: r[k] for k in ("file_index", "source_file_id", "source_sha256",
            "checks_sha256", "audit_mode", "requested_sheet", "file", "ok", "sheets", "skipped",
            "coverage", "issues", "reason_code", "rule_execution", "annotation_errors") if k in r}
            for r in payload.get("results", payload.get("audit_results", [])) if isinstance(r, dict)]}
    elif name in {"run_research_figure_script", "create_research_figure"} or (
            name == "research_workspace" and outcome.reason_code == "figure_edit_saved"):
        fields = ("figure_id", "title", "exports", "editable", "qa_receipt", "qa_failures",
                  "nature_skill_receipt", "initial_nature_skill_receipt", "edit_provenance", "render_receipt", "status", "failures")
        targets = {k: payload[k] for k in fields if k in payload}
        targets["dataset_refs"] = payload.get("dataset_refs", [])
        if isinstance(payload.get("figures"), list):
            targets["figures"] = [{k: f[k] for k in fields if k in f}
                                  for f in payload["figures"] if isinstance(f, dict)]
    targets["_tool_outcome"] = outcome.as_dict()
    receipt = {"schema_version": 1, "tool_call_id": call_id, "tool_name": name,
               "status": outcome.status, "reason_code": outcome.reason_code,
               "coverage": targets, "result_refs": payload.get("result_refs", [])}
    if name == "run_research_figure_script" and payload.get("repair_identity") and payload.get("run_id"):
        receipt["repair_identity"] = payload["repair_identity"]
        receipt["run_id"] = payload["run_id"]
    return receipt

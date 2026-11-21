"""Admit bounded review leads from a verified Code Worker output."""
from collections import defaultdict
import re

from citrus_audit.integrity import _location
from src.figures.script_outputs import strict_json
from .integrity_review import finding

SCHEMA = "custom-audit-finding/1"
MAX_OUTPUT_BYTES = 64 * 1024
MAX_FINDINGS = 20
MAX_CELLS = 64
_FIELDS = {"schema", "method_id", "method_version", "source_refs", "cells",
           "parameters", "numerical_evidence", "coverage", "interpretation_scope"}


def validate_reference(value):
    from src.code_execution.protocol import logical_path
    if (not isinstance(value, dict) or set(value) != {"execution_id", "path"}
            or not isinstance(value["execution_id"], str)
            or not re.fullmatch(r"[a-f0-9]{32}", value["execution_id"])):
        raise ValueError("custom_audit_output_reference_invalid")
    path = logical_path(value["path"], writable=True)
    if not path.startswith("/outputs/") or not path.endswith(".json"):
        raise ValueError("custom_audit_json_output_required")
    return value


def _source_refs(value, inputs):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise ValueError("custom_audit_source_refs_invalid")
    refs = {}
    for ref in value:
        if (not isinstance(ref, dict) or set(ref) != {"file_id", "sha256"}
                or not isinstance(ref["file_id"], str) or ref["file_id"] in refs
                or inputs.get(ref["file_id"]) is None or inputs[ref["file_id"]] != ref["sha256"]):
            raise ValueError("custom_audit_execution_input_mismatch")
        refs[ref["file_id"]] = ref["sha256"]
    return refs


def _verified_locations(cells, refs, snapshots):
    if not isinstance(cells, list) or not 1 <= len(cells) <= MAX_CELLS:
        raise ValueError("custom_audit_cells_invalid")
    coordinates = defaultdict(list)
    for cell in cells:
        if (not isinstance(cell, dict) or set(cell) != {"file_id", "sheet", "row", "column"}
                or not isinstance(cell["file_id"], str) or not isinstance(cell["sheet"], str)
                or type(cell["row"]) is not int or type(cell["column"]) is not int):
            raise ValueError("custom_audit_cells_invalid")
        fid, sheet = cell["file_id"], cell["sheet"]
        table = snapshots.get((fid, sheet))
        if table is None or refs.get(fid) != table.source_sha256:
            raise ValueError("custom_audit_source_not_selected")
        try:
            row = table.row_numbers.index(cell["row"])
            column = table.column_numbers.index(cell["column"])
        except ValueError:
            raise ValueError("custom_audit_coordinates_not_covered") from None
        if column not in coordinates[fid, sheet, row]:
            coordinates[fid, sheet, row].append(column)
    if {fid for fid, _, _ in coordinates} != refs.keys():
        raise ValueError("custom_audit_source_refs_not_located")
    return [{**_location(snapshots[fid, sheet], [row], sorted(columns)), "file_id": fid}
            for (fid, sheet, row), columns in sorted(coordinates.items())]


def _finding(raw, inputs, snapshots, provenance):
    if (not isinstance(raw, dict) or set(raw) != _FIELDS or raw["schema"] != SCHEMA
            or raw["interpretation_scope"] != "review_lead_only"
            or any(not isinstance(raw[key], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}", raw[key])
                   for key in ("method_id", "method_version"))
            or any(not isinstance(raw[key], dict) for key in ("parameters", "numerical_evidence"))):
        raise ValueError("custom_audit_finding_invalid")
    coverage = raw["coverage"]
    count_keys = {"requested", "executed", "skipped", "truncated"}
    if (not isinstance(coverage, dict) or set(coverage) != count_keys | {"reason"}
            or any(type(coverage[key]) is not int or not 0 <= coverage[key] <= 100_000_000 for key in count_keys)
            or coverage["executed"] + coverage["skipped"] + coverage["truncated"] != coverage["requested"]
            or not isinstance(coverage["reason"], str) or len(coverage["reason"]) > 200
            or ((coverage["truncated"] or coverage["skipped"]) and not coverage["reason"].strip())):
        raise ValueError("custom_audit_coverage_invalid")
    refs = _source_refs(raw["source_refs"], inputs)
    locations = _verified_locations(raw["cells"], refs, snapshots)
    return finding(rule="custom." + raw["method_id"], version=raw["method_version"],
        locations=locations, evidence={"schema": SCHEMA, "interpretation_scope": "review_lead_only",
            "source_refs": raw["source_refs"], "parameters": raw["parameters"],
            "numerical_evidence": raw["numerical_evidence"], **provenance},
        applicability=["worker_inputs_bound_to_selected_sources"],
        counterexamples=["共享对照、合法重复、不同处理口径及方法输出偏差"],
        coverage={**coverage, "coverage_basis": "script_reported", "locations_verified": True},
        summary="自定义检测线索，待人工复核。")


def admit(reference, snapshots):
    from src.agent.code_tools import _accepted_file, _identity, _service
    execution_id, path = reference["execution_id"], reference["path"]
    identity, _ = _identity()
    service = _service(identity)
    status = service.status(execution_id)
    receipt = status.get("receipt") or {}
    environment = receipt.get("verified_environment") or {}
    if (status["status"] != "succeeded" or receipt.get("process_stopped") is not True
            or receipt.get("network_policy") != "offline"
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", str(environment.get("image_id", "")))
            or not isinstance(environment.get("dependency_versions"), dict)
            or "python" not in environment["dependency_versions"]):
        raise ValueError("custom_audit_verified_worker_receipt_required")
    with _accepted_file(service, identity, execution_id, path, max_bytes=MAX_OUTPUT_BYTES) as (request, ref, data):
        if (receipt.get("request_fingerprint") != request.fingerprint
                or request.network_policy != "offline" or request.environment_id != "base"
                or environment.get("environment_id") != request.environment_id
                or environment.get("revision") != request.environment_revision):
            raise ValueError("custom_audit_environment_mismatch")
        payload = strict_json(data)
        if not isinstance(payload, list) or len(payload) > MAX_FINDINGS:
            raise ValueError("custom_audit_finding_list_invalid")
        inputs = {item.file_id: item.sha256 for item in request.inputs}
        if not inputs or any(not any(fid == key[0] and snapshot.source_sha256 == digest
                                    for key, snapshot in snapshots.items()) for fid, digest in inputs.items()):
            raise ValueError("custom_audit_execution_input_mismatch")
        provenance = {"code_sha256": request.script.sha256,
                      "dependency_versions": environment["dependency_versions"],
                      "runtime_receipt": {"execution_id": execution_id, "fence": status["fence"],
                          "request_fingerprint": request.fingerprint, "network_policy": "offline",
                          "output_sha256": ref["sha256"], "image_id": environment["image_id"],
                          "environment_id": request.environment_id, "environment_revision": request.environment_revision}}
        findings = [_finding(raw, inputs, snapshots, provenance) for raw in payload]
        complete = bool(findings) and all(not (row["coverage"]["skipped"] or row["coverage"]["truncated"])
                                          for row in findings)
        return findings, {"status": "complete" if complete else "partial", "findings": len(findings),
                          "runtime_receipt": provenance["runtime_receipt"]}


# Reusable methods are metadata only. Registration never turns a one-off script
# into a host tool, never executes it, and never installs an extension. The
# retest below reads immutable receipts and artifacts already accepted by the
# offline Code Worker.
METHOD_SCHEMA = "custom-audit-method-candidate/1"
MIN_METHOD_REPEATS = 2
MAX_METHOD_REPEATS = 8
_METHOD_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}")


def _receipt_environment(receipt):
    environment = receipt.get("verified_environment") or {}
    versions = environment.get("dependency_versions")
    if (not re.fullmatch(r"sha256:[a-f0-9]{64}", str(environment.get("image_id", "")))
            or not isinstance(versions, dict) or "python" not in versions
            or not all(isinstance(key, str) and isinstance(value, str)
                       for key, value in versions.items())):
        raise ValueError("custom_method_verified_environment_required")
    return environment, dict(sorted(versions.items()))


def _receipt_output(receipt, output_path):
    matches = [item for item in receipt.get("files") or []
               if isinstance(item, dict) and item.get("path") == output_path]
    if len(matches) != 1:
        raise ValueError("custom_method_accepted_output_required")
    ref = matches[0]
    if (not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("sha256", "")))
            or type(ref.get("size_bytes")) is not int or ref["size_bytes"] < 1
            or type(ref.get("version")) is not int or ref["version"] < 1):
        raise ValueError("custom_method_accepted_output_required")
    return {"sha256": ref["sha256"], "size_bytes": ref["size_bytes"]}


def _method_recipe(request, environment, versions):
    return {"script_sha256": request.script.sha256,
            "arguments": list(request.arguments),
            "outputs": list(request.outputs),
            "environment_id": request.environment_id,
            "environment_revision": request.environment_revision,
            "network_policy": request.network_policy,
            "input_sha256": sorted(item.sha256 for item in request.inputs),
            "dependency_versions": versions,
            "image_id": environment["image_id"]}


def verify_reusable_method(owner, session_id, method_id, method_version,
                           output_path, execution_ids):
    """Retest a reusable method from owner-bound accepted receipts only.

    Distinct repeat executions must share one pinned recipe and dependency set
    and produce byte-identical evidence for the same synthetic input hashes.
    This function never executes the script or installs dependencies.
    """
    from src.agent.code_tools import _accepted_file
    from src.code_execution.protocol import ExecutionRequest, identifier, logical_path
    from src.code_execution.service import ExecutionService
    from src.code_execution.repository import ExecutionRepository
    from src.config import PROJECT_ROOT
    from src.session.manager import session_manager
    if (type(owner) is not int or owner <= 0
            or not isinstance(session_id, str) or not session_id
            or session_manager.get_session_owner(session_id) != owner):
        raise ValueError("custom_method_authorized_session_required")
    if (not isinstance(method_id, str) or not _METHOD_TOKEN.fullmatch(method_id)
            or not isinstance(method_version, str) or not _METHOD_TOKEN.fullmatch(method_version)):
        raise ValueError("custom_method_identity_invalid")
    logical_path(output_path, writable=True)
    if (not output_path.startswith("/outputs/") or not output_path.endswith(".json")):
        raise ValueError("custom_method_json_output_required")
    if (not isinstance(execution_ids, list)
            or not MIN_METHOD_REPEATS <= len(execution_ids) <= MAX_METHOD_REPEATS
            or len(set(execution_ids)) != len(execution_ids)):
        raise ValueError("custom_method_repeat_executions_required")
    repository = ExecutionRepository(PROJECT_ROOT / "state" / "code_executions.sqlite3")
    recipes, outputs, executions = [], [], []
    for execution_id in execution_ids:
        identifier(execution_id)
        # owner+session scope is resolved inside the store, not from the caller.
        row = repository.get(execution_id, owner=owner, session=session_id)
        request = ExecutionRequest.parse(row["request"])
        receipt = row["receipt"]
        if (row["status"] != "succeeded" or not isinstance(receipt, dict)
                or request.identity.owner_user_id != owner
                or request.identity.session_id != session_id
                or receipt.get("execution_id") != execution_id
                or receipt.get("fence") != row["fence"]
                or receipt.get("request_fingerprint") != request.fingerprint
                or receipt.get("process_stopped") is not True
                or receipt.get("network_policy") != "offline"
                or request.network_policy != "offline"
                or request.environment_id != "base" or not request.inputs
                or output_path not in request.outputs):
            raise ValueError("custom_method_accepted_receipt_required")
        environment, versions = _receipt_environment(receipt)
        if (environment.get("environment_id") != request.environment_id
                or environment.get("revision") != request.environment_revision):
            raise ValueError("custom_method_environment_mismatch")
        evidence = _receipt_output(receipt, output_path)
        service = ExecutionService(request.identity, repository=repository)
        with _accepted_file(service, request.identity, execution_id, output_path,
                            max_bytes=MAX_OUTPUT_BYTES) as (_, _, data):
            payload = strict_json(data)
            if (not isinstance(payload, list) or not 1 <= len(payload) <= MAX_FINDINGS
                    or not all(isinstance(item, dict) and set(item) == _FIELDS
                               and item["schema"] == SCHEMA and item["method_id"] == method_id
                               and item["method_version"] == method_version
                               and item["interpretation_scope"] == "review_lead_only" for item in payload)):
                raise ValueError("custom_method_output_identity_mismatch")
            inputs = {item.file_id: item.sha256 for item in request.inputs}
            for item in payload:
                _source_refs(item["source_refs"], inputs)
        recipes.append(_method_recipe(request, environment, versions))
        outputs.append(evidence)
        executions.append({"execution_id": execution_id, "fence": row["fence"],
                           "request_fingerprint": request.fingerprint,
                           "output_sha256": outputs[-1]["sha256"]})
    if any(recipe != recipes[0] for recipe in recipes[1:]):
        raise ValueError("custom_method_recipe_not_reproducible")
    if any(output != outputs[0] for output in outputs[1:]):
        raise ValueError("custom_method_evidence_not_deterministic")
    return {"schema": METHOD_SCHEMA, "method_id": method_id,
            "method_version": method_version, "recipe": recipes[0],
            "evidence": {"path": output_path, **outputs[0]},
            "repeats": len(execution_ids), "executions": executions,
            "checks": {"owner_bound_receipts": True, "offline": True,
                       "same_pinned_recipe": True, "same_dependency_versions": True,
                       "same_input_hashes": True, "deterministic_output": True,
                       "host_execution": False, "extensions_installed": False}}

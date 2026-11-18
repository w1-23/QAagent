"""Authenticated current-byte QA and diagnostic bundles; never render or run source.

This adapter uses the independent v2 receipt and bundle member contracts. A
stored project is not an in-process FigureSession: its archive deliberately
makes no frozen-session/reproduction claim and never calls write_bundle (which
exports a live figure). Account publication remains the only download writer.
"""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import zipfile

from citrus_figures import __version__ as PACKAGE_VERSION
from citrus_figures.verification import sha256, verify_current_figure

from src.figures import projects

PRODUCER = "figure_current_artifact_qa"
BRIDGE = "citrus.figure-current-artifacts/1"
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
_STATE_FIELDS = ("session_id", "render_version", "source_kind", "source_json",
                 "manifest_json", "overrides_json", "exports_json", "data_refs_json")


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def _receipt_id(receipt):
    value = {key: item for key, item in receipt.items() if key != "receipt_id"}
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode())


def _state(project):
    return {key: project.get(key) for key in _STATE_FIELDS}


def _session(project, owner):
    from src.session.manager import session_manager
    sid = str(project.get("session_id") or "")
    if not sid or not session_manager.session_belongs_to(sid, owner):
        raise projects.ProjectError("session_unavailable", "图件原会话不可用或无权访问。", 409)
    return sid


def _assert_current(project, owner):
    current = projects.get_for_owner(project["figure_id"], owner)
    if _state(current) != _state(project):
        raise projects.ProjectError("revision_conflict", "检查期间图件已更新，请重新加载当前版本。", 409)
    _session(current, owner)


def _read_file(file_id, owner, *, internal=False, expected=None, limit=MAX_FILE):
    """Only the existing account resolver may turn an opaque id into a path."""
    from src.session import files
    entry = files.get_account_file(str(file_id or ""), owner, allow_internal=internal)
    if not entry:
        raise projects.ProjectError("artifact_unavailable", "图件引用文件不存在或无权访问。", 404)
    path = Path(entry["path"])
    try:
        if not path.is_file() or path.is_symlink():
            raise OSError("not a regular file")
        if path.stat().st_size > limit:
            raise projects.ProjectError("artifact_too_large", "图件引用文件超过检查大小上限。", 413)
        with path.open("rb") as handle:
            blob = handle.read(limit + 1)
    except OSError:
        raise projects.ProjectError("artifact_unavailable", "图件引用文件无法读取。", 404) from None
    if len(blob) > limit:
        raise projects.ProjectError("artifact_too_large", "图件引用文件超过检查大小上限。", 413)
    digest = sha256(blob)
    if not entry.get("sha256") or digest != entry["sha256"] or (expected and digest != expected):
        raise projects.ProjectError("artifact_changed", "图件引用文件摘要不匹配，不能认证当前版本。", 409)
    return blob, entry


def _identity(blob, role, fmt):
    return {"role": role, "format": fmt, "sha256": sha256(blob), "size": len(blob)}


def _inputs(project, owner):
    """Capture available provenance bytes; absent inputs remain explicit gaps."""
    source = project.get("source_json") or {}
    members, identities, gaps = {}, {}, []
    source_blob = None
    source_format = "json"
    if source:
        members["data/source.json"] = _json(source)
    script = source.get("script")
    if source.get("script_ref"):
        try:
            from src.research.script_assets import get_script
            stored = get_script(owner, project["session_id"], source["script_ref"])
            if script is not None and stored["source"] != script:
                raise ValueError("script identity mismatch")
            script = stored["source"]
        except (ValueError, LookupError):
            gaps.append({"kind": "source", "reason_code": "script_reference_unavailable"})
            script = None
    if isinstance(script, str) and script.strip():
        source_blob, source_format = script.encode("utf-8"), "python"
        members["source.py"] = source_blob
        frozen = source.get("frozen_render") or {}
        if frozen:
            candidates = [item for item in (source.get("output_contract") or {}).get("figures", [])
                          if item.get("name") == source.get("output_key")]
            renderer = candidates[0].get("render_script") if len(candidates) == 1 else None
            digest = (frozen.get("receipt") or {}).get("renderer_sha256")
            if isinstance(renderer, str) and sha256(renderer.encode("utf-8")) == digest:
                members["analysis_source.py"] = source_blob
                source_blob = renderer.encode("utf-8")
                members["source.py"] = source_blob
            else:
                members["analysis_source.py"] = members.pop("source.py")
                source_blob = None
                gaps.append({"kind": "source", "reason_code": "renderer_identity_unverified"})
    elif project.get("source_kind") == "spec" and isinstance(source.get("spec"), dict):
        source_blob = _json(source["spec"])
        members["data/spec.json"] = source_blob
    else:
        gaps.append({"kind": "source", "reason_code": "source_absent"})
    if source_blob is not None:
        identities["source"] = _identity(source_blob, "source", source_format)
    members["data/parameters.json"] = _json(source.get("parameters") or {})

    refs = list(project.get("data_refs_json") or [])
    refs.extend(source.get("input_snapshots") or [])
    refs.extend((source.get("frozen_render") or {}).get("input_snapshots") or [])
    if isinstance(source.get("dataset_ref"), dict):
        refs.append(source["dataset_ref"])
    input_map, seen, bindings = {}, set(), []

    def inspect_spec(spec, prefix="spec"):
        if not isinstance(spec, dict):
            return
        for name in ("x", "y", "group", "weight", "series_key"):
            column = spec.get(name)
            if not isinstance(column, dict):
                continue
            if column.get("kind") == "inline" and isinstance(column.get("values"), list):
                key = f"{prefix}.{name}"
                blob = _json(column["values"])
                member = f"data/inline_{len(input_map)}.json"
                members[member], input_map[key] = blob, member
                identities[key] = _identity(blob, "data", "json")
            elif column.get("kind") == "account_file" and not any(
                    ref.get("file_id") == column.get("file_id") and ref.get("sha256")
                    for ref in refs if isinstance(ref, dict)):
                gaps.append({"kind": "data", "reason_code": "input_reference_unpinned"})
            elif column.get("kind") == "attachment":
                gaps.append({"kind": "data", "reason_code": "temporary_input_unavailable"})
        for index, panel in enumerate(spec.get("panels") or []):
            inspect_spec(panel, f"{prefix}.panel_{index}")

    inspect_spec(source.get("spec"))
    total = sum(map(len, members.values()))
    for ref in refs:
        if not isinstance(ref, dict):
            gaps.append({"kind": "data", "reason_code": "input_reference_unverified"})
            continue
        key = str(ref.get("file_id") or ref.get("id") or "")
        if not key or key in seen:
            if not key:
                gaps.append({"kind": "data", "reason_code": "input_reference_unverified"})
            continue
        seen.add(key)
        try:
            if ref.get("file_id"):
                if not ref.get("sha256"):
                    raise projects.ProjectError("input_hash_absent", "缺少输入摘要。")
                blob, _ = _read_file(ref["file_id"], owner, internal=True, expected=ref["sha256"])
                bindings.append((ref["file_id"], sha256(blob), True))
            elif ref.get("kind") == "dataset_snapshot":
                from src.research.analysis_results import load_dataset_snapshot
                dataset = load_dataset_snapshot(owner, ref)
                if dataset.payload_file_ref:
                    blob, _ = _read_file(dataset.payload_file_ref, owner, internal=True,
                                         expected=dataset.to_dict()["payload_sha256"])
                    bindings.append((dataset.payload_file_ref, sha256(blob), True))
                else:
                    blob = _json(dataset.to_dict())
            else:
                raise projects.ProjectError("input_reference_unverified", "输入引用不可验证。")
            total += len(blob)
            if total > MAX_TOTAL:
                raise projects.ProjectError("artifact_too_large", "输入总大小超过上限。", 413)
            name = f"data/input_{len(input_map)}.bin"
            members[name] = blob
            input_map[key] = name
            identities[key] = _identity(blob, "data", "stored_bytes")
        except (projects.ProjectError, ValueError, LookupError) as exc:
            gaps.append({"kind": "data", "reference_id": key,
                         "reason_code": getattr(exc, "code", "input_unavailable")})
    # Legacy path/name mappings are never resolved by searching the filesystem.
    if source.get("sources") or source.get("inputs"):
        gaps.append({"kind": "data", "reason_code": "legacy_input_mapping_unverified"})
    members["data/inputs.json"] = _json(input_map)
    members["data/provenance.json"] = _json({"references": refs, "unavailable": gaps,
                                           "source_state_sha256": sha256(_json(source))})
    return source_blob, source_format, members, identities, gaps, bindings


def _previous_receipt(file_id, project, owner):
    if not file_id:
        return None
    blob, entry = _read_file(file_id, owner, limit=4 * 1024 * 1024)
    try:
        receipt = json.loads(blob)
        binding = receipt["project"]
        valid = (entry.get("producer_tool") == PRODUCER and entry.get("origin") == "generated"
                 and entry.get("session_id") == project["session_id"]
                 and receipt.get("bridge") == BRIDGE and receipt.get("version") == 2
                 and receipt.get("skill") == "nature-figure"
                 and binding["figure_id"] == project["figure_id"]
                 and type(binding["render_version"]) is int
                 and 0 <= binding["render_version"] <= int(project.get("render_version") or 0)
                 and receipt.get("receipt_id") == _receipt_id(receipt))
        if not valid:
            raise ValueError("receipt provenance mismatch")
    except (ValueError, TypeError, KeyError):
        raise projects.ProjectError("invalid_supersedes", "前次回执不是此图件已发布的 v2 检查记录。", 400) from None
    return receipt["receipt_id"]


def _capture(project, owner, supersedes):
    exports = project.get("exports_json") or {}
    artifacts, members, bindings = [], {}, []
    checks = ((project.get("manifest_json") or {}).get("render_receipt") or {}).get("checks") or {}
    for fmt in projects.FIGURE_FORMATS:
        entry = exports.get(fmt)
        if not isinstance(entry, dict):
            continue
        blob, _ = _read_file(entry.get("file_id"), owner, expected=entry.get("sha256"))
        rendered_hash = (checks.get(fmt) or {}).get("sha256")
        if rendered_hash and sha256(blob) != rendered_hash:
            raise projects.ProjectError("artifact_changed", "当前导出与渲染回执摘要不同。", 409)
        members[f"figure.{fmt}"] = blob
        artifacts.append({"format": fmt, "role": "figure", "bytes": blob})
        bindings.append((entry["file_id"], sha256(blob), False))
    if not artifacts:
        raise projects.ProjectError("artifact_unavailable", "此图件尚无可检查的当前导出文件。", 404)
    source, backend, inputs, identities, gaps, input_bindings = _inputs(project, owner)
    members.update(inputs)
    bindings.extend(input_bindings)
    if sum(map(len, members.values())) > MAX_TOTAL:
        raise projects.ProjectError("artifact_too_large", "图件与输入总大小超过上限。", 413)
    # No vendor root or visual inspector is supplied: even on a fully equipped
    # machine this request cannot execute pinned upstream scripts or models.
    receipt = verify_current_figure(artifacts=artifacts, source=source, backend=backend,
                                    mode="reaudit" if supersedes else "initial",
                                    supersedes=supersedes, vendor_root=None)
    expanded = []
    for check in receipt["checks"]:
        if check["reason_code"] == "resource_absent":
            check["reason_code"] = "upstream_execution_not_authorized"
            check["diagnostics"] = {}
        # Bind unavailable artifact checks too, so a UI cannot apply one
        # format's evidence to another format's bytes.
        if check["artifact"] is None and check["check"] in {"visual", "panel_alignment"}:
            expanded.extend({**copy.deepcopy(check), "artifact": identity}
                            for identity in receipt["artifacts"])
        else:
            expanded.append(check)
    for identity in identities.values():
        expanded.append({"check": "input_integrity", "executed": "executed", "conclusion": "pass",
                         "reason_code": "current_input_bytes", "artifact": identity,
                         "diagnostics": {}, "coverage": {"reexecuted": False}})
    for gap in gaps:
        expanded.append({"check": "input_integrity", "executed": "not_run", "conclusion": "undetermined",
                         "reason_code": gap["reason_code"], "artifact": None,
                         "diagnostics": gap, "coverage": {"reexecuted": False}})
    receipt["checks"] = expanded
    receipt["inputs"] = {"source_sha256": identities.get("source", {}).get("sha256"),
                         "data_sha256": {key: value["sha256"] for key, value in identities.items() if key != "source"},
                         "unavailable": gaps, "data_coverage": "partial" if any(g["kind"] == "data" for g in gaps)
                         else "declared_references" if input_bindings or any(v["role"] == "data" for v in identities.values())
                         else "not_declared"}
    receipt["bridge"] = BRIDGE
    receipt["project"] = {"figure_id": project["figure_id"],
                          "render_version": int(project.get("render_version") or 0),
                          "state_sha256": sha256(_json(_state(project)))}
    receipt["capabilities"] = {"upstream_scripts": "not_run", "visual": "not_run",
                               "source_execution": "not_run", "rendering": "not_run"}
    unexecuted = sorted({c["check"] for c in expanded if c["executed"] != "executed"})
    receipt["overall"]["unexecuted"] = unexecuted
    receipt["overall"]["execution"] = "partial" if unexecuted else "complete"
    if unexecuted and receipt["overall"]["conclusion"] in {"pass", "warn"}:
        receipt["overall"]["conclusion"] = "incomplete"
    receipt["overall"]["claims"].append("Upstream scripts and source were not executed; no render or frozen-session reconstruction was performed.")
    receipt["receipt_id"] = _receipt_id(receipt)
    members["objects.json"] = _json(project.get("manifest_json") or {})
    members["overrides.json"] = _json(project.get("overrides_json") or [])
    return receipt, members, bindings


def _bundle(project, receipt, members):
    """v2 manifest/member hashes over exact stored bytes, not a new render."""
    members = dict(members)
    members["qa/receipt.json"] = _json(receipt)
    for index, check in enumerate(receipt["checks"]):
        members[f"qa/reports/{index:02d}_{check['check']}.json"] = _json(check)
    manifest = {"schema_version": 2, "package": "citrus_figures", "package_version": PACKAGE_VERSION,
                "bundle_kind": "current_artifacts", "bridge": BRIDGE,
                "vendor": {"commit": receipt["vendor_commit"], "skill_version": receipt["skill_version"]},
                "revision": receipt["project"]["render_version"], "mode": receipt["mode"],
                "overrides": project.get("overrides_json") or [], "receipt_id": receipt["receipt_id"],
                "source_sha256": receipt["inputs"]["source_sha256"],
                "parameters_sha256": sha256(members["data/parameters.json"]),
                "data_sha256": receipt["inputs"]["data_sha256"],
                "editable_limitations": ["Archive of current stored artifacts, not a live FigureSession.",
                    "No source execution, reproduction, statistical recomputation or upstream re-audit was performed.",
                    "Missing inputs and unexecuted checks remain explicit in qa/receipt.json.",
                    "No data/resolved.json or automatic load_spec_bundle/style-reopen capability is claimed."],
                "members": [{"path": name, "sha256": sha256(blob), "size": len(blob)}
                            for name, blob in sorted(members.items())],
                "manifest_self_hash": "excluded to avoid circular hashing"}
    members["figure.bundle.json"] = _json(manifest)
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, blob in sorted(members.items()):
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), blob,
                             compress_type=zipfile.ZIP_DEFLATED)
    return target.getvalue(), manifest


def verify_project(figure_id, owner, *, expected_revision, supersedes_file_id=None,
                   include_bundle=False):
    """Run under the API's retained account lease and capacity reservation."""
    from src.core.tracing import get_client_request_id
    from src.research.artifacts import publish_artifact
    project = projects.get_for_owner(figure_id, owner)
    if int(project.get("render_version") or 0) != expected_revision:
        raise projects.ProjectError("revision_conflict", "图件已更新，请重新加载当前版本。", 409)
    sid = _session(project, owner)
    previous = _previous_receipt(supersedes_file_id, project, owner)
    receipt, members, bindings = _capture(project, owner, previous)
    bundle_blob, manifest = _bundle(project, receipt, members) if include_bundle else (None, None)
    stem = f"figure_{figure_id}_r{expected_revision}_{receipt['receipt_id'][:12]}"

    def publish(filename, blob, mime):
        for file_id, expected, internal in bindings:
            _read_file(file_id, owner, internal=internal, expected=expected)
        _assert_current(project, owner)  # Recheck after all reads/QA/ZIP work.
        try:
            entry = publish_artifact(owner=owner, session_id=sid, filename=filename,
                                     content=blob, mime=mime, producer_tool=PRODUCER,
                                     request_id=str(get_client_request_id() or ""))
        except Exception:
            raise projects.ProjectError("publish_failed", "检查结果文件发布失败，未提供下载链接。", 503) from None
        _assert_current(project, owner)
        # No guessed links: the normal publisher must provide a real account file.
        actual, stored = _read_file(entry.get("file_id"), owner, limit=MAX_TOTAL + 4 * 1024 * 1024)
        if actual != blob or not stored.get("download_url"):
            raise projects.ProjectError("publish_failed", "已发布文件校验失败，未提供下载链接。", 503)
        return entry

    receipt_file = publish(stem + "_receipt.json", _json(receipt), "application/json")
    result = {"figure_id": figure_id, "render_version": expected_revision,
              "receipt": receipt, "receipt_file": receipt_file}
    if include_bundle:
        result["bundle"] = publish(stem + ".zip", bundle_blob, "application/zip")
        result["manifest"] = manifest
    return result

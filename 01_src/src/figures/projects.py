"""The figure workspace's service layer.

A *figure project* is a drawing the user can keep and adjust. This module is
everything the web workspace needs beyond raw storage: re-rendering a project
with the user's edits applied, publishing the result into the account's file
library, and importing a project out of the library.

Why re-rendering lives here
---------------------------
The preview and the download must be the same bytes. The only way to guarantee
that is to have exactly one render path: the project's source is turned into a
figure, the user's edits are applied to that figure, and *then* the formats
are written out. Nothing is edited in the browser and nothing is edited in the
SVG text — the browser sends a request, the server renders, and the file the
user downloads is the file the user saw.

Why the source is kept
----------------------
A figure made from a specification can be re-rendered because the
specification is kept. A figure made from a script can be re-rendered because
the script is kept. Either way the *data* is re-read from its original source
— the uploaded table or the account-library file — so the numbers stay
traceable to the rows they came from.

The rule that is not negotiable
--------------------------------
Re-rendering must never change the data. The edits that reach the server are
filtered through :func:`src.figures.overrides.validate_overrides` against the
project's own manifest, so the only things that can change are colours,
font sizes, line widths and positions. A figure whose bars grew would be a
different claim, and there is no code path that can do that.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from src.figures.overrides import (
    OverrideError, apply_overrides, merge_overrides, validate_overrides,
)
from src.figures.store import (
    create_project, delete_project, get_project, list_projects, update_project,
)

# The formats a figure project carries.
FIGURE_FORMATS = ("svg", "png", "pdf")

MIME_BY_FORMAT = {
    "svg": "image/svg+xml",
    "png": "image/png",
    "pdf": "application/pdf",
}

# The formats that are re-published after an edit. SVG is the one the preview
# shows; PNG and PDF are what a journal asks for.
DEFAULT_FORMATS = ("svg", "png", "pdf")
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RENDER_BYTES = 64 * 1024 * 1024


class ProjectError(RuntimeError):
    """A project operation that could not be carried out."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = str(code or "figure_error")
        self.message = str(message)
        self.status = int(status)


# ── Re-rendering ──────────────────────────────────────────────────────────

def _render_source(source: dict[str, Any],
                   owner_user_id: int | None = None) -> tuple[Any, dict[str, Any]]:
    """Build a declarative figure; scripts must use the worker byte path."""
    # Older script projects omitted kind; the script key is reliable evidence
    # for that legacy shape. Never mistake it for a declarative source.
    kind = str(source.get("kind") or ("script" if "script" in source else "spec"))
    if kind == "script":
        raise ProjectError(
            "script_requires_worker",
            "脚本图件必须通过隔离绘图工作进程重放。")
    if kind != "spec":
        raise ProjectError("unknown_source_kind", "不支持的图件来源类型。")
    return _render_spec(source, owner_user_id)


def _render_spec(source: dict[str, Any],
                 owner_user_id: int | None = None) -> tuple[Any, dict[str, Any]]:
    """Resolve account data only; Artist construction belongs to the child."""
    from src.figures.resolve import resolve_spec
    from src.figures.spec import SpecError, parse_spec

    payload = source.get("spec")
    if not isinstance(payload, dict):
        raise ProjectError("missing_spec", "这张图缺少规格定义，无法重新渲染。")
    try:
        spec = parse_spec(payload)
        resolved, info = resolve_spec(spec, owner_user_id=owner_user_id)
    except SpecError as exc:
        raise ProjectError("spec_invalid", exc.message) from exc
    except Exception as exc:  # noqa: BLE001 - report, never crash the request
        raise ProjectError("data_unavailable",
                           f"图件所引用的数据现在无法读取：{exc}") from exc
    return resolved, info


def snapshot_script_inputs(bindings: Any, owner: int, session_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Copy authorized account inputs once; replay uses pinned immutable bytes.

    Changed or removed original files do not change this figure. A new data
    version requires a new figure source. Snapshot corruption fails closed.
    Neither source JSON nor provenance stores service filesystem paths.
    """
    import hashlib
    import keyword
    import re
    from src.session import files
    from src.core.account_leases import account_lease
    if not isinstance(bindings, dict) or len(bindings) > 8:
        raise ProjectError("invalid_inputs", "inputs 需要最多 8 个变量到 file_id 的映射。")
    snapshots, refs, total = [], [], 0
    with account_lease(owner):
        for index, (variable, raw) in enumerate(bindings.items()):
            if (not isinstance(variable, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", variable)
                    or keyword.iskeyword(variable) or variable in {"plt", "matplotlib", "show"}):
                raise ProjectError("invalid_inputs", "输入变量名无效或与绘图变量冲突。")
            if not isinstance(raw, dict) or set(raw) != {"file_id"} or not isinstance(raw["file_id"], str):
                raise ProjectError("invalid_inputs", "输入只能引用当前账号的 file_id，不能提供路径。")
            original = files.get_account_file(raw["file_id"], owner)
            if not original:
                raise ProjectError("data_unavailable", "输入文件不存在或不属于当前账号。")
            path = Path(original["path"])
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
                raise ProjectError("invalid_inputs", "输入必须是大小受限的普通文件。")
            with path.open("rb") as handle:
                data = handle.read(32 * 1024 * 1024 + 1)
            total += len(data)
            if len(data) > 32 * 1024 * 1024 or total > 64 * 1024 * 1024:
                raise ProjectError("invalid_inputs", "脚本输入超过体积上限。")
            digest = hashlib.sha256(data).hexdigest()
            if original.get("sha256") and original["sha256"] != digest:
                raise ProjectError("source_changed", "原始输入已改变，请登记新的数据版本。")
            filename = str(original.get("filename") or "input.bin")
            suffix = Path(filename).suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                suffix = ".bin"
            sandbox_name = f"input_{index}{suffix}"
            snapshot = files.register_file(session_id=session_id, owner_user_id=owner,
                filename=f"figure_snapshot_{digest[:16]}{suffix}", content=data,
                mime=str(original.get("mime") or "application/octet-stream"),
                producer_tool="run_research_figure_script", dedupe_key=f"figure-snapshot:{digest}")
            metadata = {"variable": variable, "sandbox_name": sandbox_name,
                        "file_id": str(snapshot["file_id"]), "source_file_id": raw["file_id"],
                        "sha256": digest, "size_bytes": len(data), "filename": filename}
            snapshots.append(metadata)
            refs.append({"kind": "input_snapshot", "file_id": metadata["file_id"],
                         "sha256": digest, "source_file_id": raw["file_id"]})
    return snapshots, refs


def script_replay_inputs(source: dict[str, Any], owner: int) -> tuple[dict, dict, dict]:
    import hashlib
    import re
    from src.session import files
    snapshots = source.get("input_snapshots")
    if snapshots is None:
        if source.get("inputs") or source.get("sources"):
            raise ProjectError("script_inputs_unavailable", "脚本原始输入映射无法重建，不能重放。")
        return {}, {}, {}
    if not isinstance(snapshots, list) or len(snapshots) > 8:
        raise ProjectError("source_unverified", "脚本输入快照合同无效。")
    inputs, sources, hashes = {}, {}, {}
    for item in snapshots:
        if not isinstance(item, dict):
            raise ProjectError("source_unverified", "脚本输入快照合同无效。")
        variable, name, digest = item.get("variable"), item.get("sandbox_name"), item.get("sha256")
        if (not isinstance(variable, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", variable)
                or variable in inputs or not isinstance(name, str)
                or not re.fullmatch(r"input_[0-7]\.[a-z0-9]{1,10}", name) or name in hashes
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ProjectError("source_unverified", "脚本输入快照名称或摘要无效。")
        entry = files.get_account_file(str(item.get("file_id") or ""), owner)
        if not entry:
            raise ProjectError("data_unavailable", "脚本输入快照不存在或不属于当前账号。")
        path = Path(entry["path"])
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item.get("size_bytes"):
            raise ProjectError("source_changed", "脚本输入快照已损坏。")
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest()
        if actual != digest:
            raise ProjectError("source_changed", "脚本输入快照摘要不匹配，拒绝重放。")
        inputs[variable], sources[variable], hashes[name] = name, str(path), digest
    return inputs, sources, hashes


def _render_script(source: dict[str, Any], overrides: list[dict[str, Any]],
                   *, owner_user_id: int = 0, existing: list[dict[str, Any]] | None = None,
                   formats: tuple[str, ...] = DEFAULT_FORMATS
                   ) -> tuple[dict[str, bytes], dict[str, Any], list[dict[str, Any]]]:
    """Render a figure from its stored script.

    The script is not executed here — that would put untrusted code back into
    the service process. It is re-run through the same one-shot sandbox the
    agent's script path uses, with the same restrictions. If no sandbox is
    configured, a script-sourced project cannot be re-rendered, and the user is
    told so rather than shown a stale preview.
    """
    from src.figures.sandbox import SandboxJob, SandboxUnavailable, select_backend

    text = str(source.get("script") or "")
    if not text.strip():
        raise ProjectError("missing_script", "这张图缺少绘图脚本，无法重新渲染。")

    from src.config import settings

    runner = select_backend(
        backend=str(getattr(settings, "FIGURES_SANDBOX_BACKEND", "") or ""),
        image=str(getattr(settings, "FIGURES_SANDBOX_IMAGE", "") or ""),
        image_sha256=str(getattr(settings, "FIGURES_SANDBOX_IMAGE_SHA256", "") or ""),
        runtime=str(getattr(settings, "FIGURES_SANDBOX_RUNTIME", "") or ""),
        allow_local=bool(getattr(settings, "FIGURES_SANDBOX_ALLOW_LOCAL", False)))
    if runner is None:
        raise ProjectError(
            "sandbox_unavailable",
            "当前环境没有配置绘图脚本沙箱，无法重新渲染脚本来源的图件；"
            "请改用声明式图件工具重新出图。",
            status=503)

    outputs = {fmt: f"figure.{fmt}" for fmt in formats}
    outputs["manifest"] = "objects.json"
    outputs["overrides"] = "overrides.json"
    inputs, sources, hashes = script_replay_inputs(source, owner_user_id)
    job = SandboxJob(
        script=text, inputs=inputs, sources=sources, expected_input_sha256=hashes,
        qa_profile=source.get("qa_profile") or {}, outputs=outputs, overrides=overrides,
        base_overrides=existing or [], replay_overrides=True, dpi=_figure_dpi(),
        timeout_sec=int(getattr(settings, "FIGURES_SANDBOX_TIMEOUT_SEC", 120) or 120),
        memory_mb=int(getattr(settings, "FIGURES_SANDBOX_MEMORY_MB", 1024) or 1024),
        cpus=str(getattr(settings, "FIGURES_SANDBOX_CPUS", "1.0") or "1.0"),
        image=str(getattr(settings, "FIGURES_SANDBOX_IMAGE", "") or ""),
        image_sha256=str(getattr(settings, "FIGURES_SANDBOX_IMAGE_SHA256", "") or ""),
        runtime=str(getattr(settings, "FIGURES_SANDBOX_RUNTIME", "") or ""))
    try:
        result = runner.run(job)
    except SandboxUnavailable as exc:
        raise ProjectError("sandbox_unavailable", str(exc), status=503) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProjectError("render_failed", f"沙箱执行失败：{exc}") from exc
    if not result.ok:
        raise ProjectError("render_failed", result.describe() or "绘图脚本执行失败。")

    # A versioned acknowledgement is mandatory: an older worker silently
    # ignoring overrides must never publish an unchanged figure as a success.
    try:
        report = json.loads(result.stdout)
        manifest_bytes = result.outputs["manifest"]
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest budget exceeded")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        if (not isinstance(report, dict) or report.get("override_protocol") != 1
                or report.get("applied_count") != len(merge_overrides(existing or [], overrides))
                or not isinstance(manifest, dict)
                or not isinstance(manifest.get("objects"), list)
                or any(not isinstance(item, dict) or not isinstance(item.get("gid"), str)
                       for item in manifest["objects"])):
            raise ValueError("worker protocol mismatch")
        override_bytes = result.outputs["overrides"]
        if len(override_bytes) > 256 * 1024:
            raise ValueError("override response budget exceeded")
        accepted = json.loads(override_bytes.decode("utf-8"))
        _check_worker_overrides(accepted, merge_overrides(existing or [], overrides), manifest)
        if source.get("version") == 2:
            from src.figures.spec import parse_qa_profile
            receipt = manifest.get("qa_receipt") or {}
            if receipt.get("version") != 1 or receipt.get("profile") != parse_qa_profile(source.get("qa_profile") or {}):
                raise ValueError("worker did not acknowledge profile QA")
        blobs = {fmt: result.outputs[fmt] for fmt in formats}
        if any(not isinstance(blob, bytes) or not blob for blob in blobs.values()):
            raise ValueError("invalid output bytes")
        if "svg" in blobs:
            blobs["svg"].decode("utf-8")
    except (KeyError, TypeError, ValueError, OverrideError) as exc:
        raise ProjectError("worker_protocol_invalid", "绘图沙箱未确认样式重放；请更新沙箱镜像。") from exc
    manifest["data_provenance"] = {key: source[key] for key in (
        "input_snapshots", "snapshot_policy", "analysis_result_ref", "dataset_ref") if key in source}
    return blobs, manifest, accepted



def _check_worker_overrides(accepted: Any, requested: list[dict[str, Any]],
                            manifest: dict[str, Any]) -> None:
    """Check bounded worker data without resolving container fonts on the host."""
    from src.figures.manifest import EDITABLE_BY_ROLE
    if (not isinstance(accepted, list) or len(accepted) > 200
            or any(not isinstance(item, dict) or set(item) != {"gid", "prop", "value"}
                   or not isinstance(item["gid"], str) or not isinstance(item["prop"], str)
                   for item in accepted)):
        raise ValueError("invalid accepted overrides")
    if [(item["gid"], item["prop"]) for item in accepted] != [(item["gid"], item["prop"]) for item in requested]:
        raise ValueError("worker did not acknowledge the requested edit set")
    nonfonts = [item for item in requested if item["prop"] != "fontfamily"]
    if validate_overrides(nonfonts, manifest) != [item for item in accepted if item["prop"] != "fontfamily"]:
        raise ValueError("worker substituted edit values")
    by_gid = {item.get("gid"): item for item in manifest.get("objects", []) if isinstance(item, dict)}
    families = set(manifest.get("font_families") or [])
    for actual, expected in zip(accepted, requested):
        if actual["prop"] != "fontfamily":
            continue
        target = by_gid.get(actual["gid"], {})
        value = actual["value"]
        raw = expected.get("value")
        if (not isinstance(value, str) or not isinstance(raw, str)
                or value not in families or value.casefold() != raw.strip().casefold()
                or "fontfamily" not in EDITABLE_BY_ROLE.get(target.get("role"), ())
                or "fontfamily" not in target.get("editable", [])):
            raise ValueError("invalid worker font acknowledgement")


# ── Applying edits ────────────────────────────────────────────────────────

def _apply_to_rendered(rendered: Any, manifest: dict[str, Any],
                        overrides: list[dict[str, Any]]) -> list[str]:
    """Apply the user's edits to a rendered figure."""
    try:
        return apply_overrides(rendered, manifest, overrides)
    except Exception as exc:  # noqa: BLE001
        raise ProjectError("apply_failed", f"修改未能应用：{exc}") from exc


def _normalise_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        return {"version": 1, "objects": []}
    import math
    # Legacy scatter manifests may contain Infinity from Bbox.null(). Keep the
    # object editable via its selector, but do not draw an invented overlay.
    objects = []
    for item in manifest.get("objects", []):
        if not isinstance(item, dict):
            continue
        item = dict(item)
        bbox = item.get("bbox")
        anchor = item.get("anchor")
        invalid_bbox = bbox is not None and (
            not isinstance(bbox, dict)
            or not {"x0", "y0", "x1", "y1"}.issubset(bbox)
            or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                   for value in bbox.values())
            or bbox["x0"] > bbox["x1"] or bbox["y0"] > bbox["y1"])
        invalid_anchor = anchor is not None and (
            not isinstance(anchor, (list, tuple)) or len(anchor) != 2
            or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                   for value in anchor))
        if invalid_bbox or invalid_anchor:
            item.update(bbox=None, anchor=None, geometry_warning="legacy_bounds_unavailable")
        objects.append(item)
    return {**manifest, "objects": objects}


# ── Publishing ────────────────────────────────────────────────────────────

def _publish_bytes(owner: int, session_id: str, filename: str,
                   payload: bytes, mime: str) -> dict[str, Any]:
    """Publish one rendered file into the account's file library.

    The file library is the service's own storage for a user's material, so
    the rendered figure lands there with the same ownership rules as an
    uploaded table. A *generated* file lives in the session's artifact
    directory rather than in the upload directory: the library distinguishes
    the two, and only an artifact may be written to the artifact area.
    """
    from src.research.artifacts import publish_artifact

    if not session_id:
        raise ProjectError("no_session", "没有有效会话，无法把图件登记到资料库。")
    try:
        return publish_artifact(
            owner=owner, session_id=session_id, filename=filename,
            content=payload, mime=mime, producer_tool="figure_workspace")
    except Exception as exc:
        raise ProjectError("publish_failed", f"图件登记失败：{exc}") from exc


def publish_rendered(owner: int, session_id: str, stem: str,
                     blobs: dict[str, bytes]) -> dict[str, dict[str, Any]]:
    """Publish every rendered format and return the file entries."""
    published: dict[str, dict[str, Any]] = {}
    for fmt in DEFAULT_FORMATS:
        payload = blobs.get(fmt)
        if payload is None:
            continue
        safe_stem = _safe_stem(stem)
        published[fmt] = _publish_bytes(
            owner, session_id, f"{safe_stem}.{fmt}", payload, MIME_BY_FORMAT[fmt])
    return published


def _safe_stem(stem: str) -> str:
    text = str(stem or "figure").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in text)
    cleaned = cleaned.strip("._") or "figure"
    return cleaned[:80]


# ── Project operations ────────────────────────────────────────────────────

def list_for_owner(owner: int, *, limit: int = 100,
                   offset: int = 0) -> list[dict[str, Any]]:
    """The account's projects, as the workspace needs them."""
    projects = list_projects(owner, limit=limit, offset=offset)
    return [_public(project) for project in projects]


def get_for_owner(figure_id: str, owner: int) -> dict[str, Any]:
    """One project owned by this account.

    A project belonging to somebody else is *not found*, not found-and-refused:
    the caller must not be able to tell the two apart.
    """
    project = get_project(str(figure_id), int(owner))
    if not project:
        raise ProjectError("not_found", "图件不存在或无权访问。", status=404)
    return project


def _public(project: dict[str, Any]) -> dict[str, Any]:
    """The project as the workspace sees it."""
    manifest = _normalise_manifest(project.get("manifest_json"))
    exports = project.get("exports_json") or {}
    if not isinstance(exports, dict):
        exports = {}
    return {
        "figure_id": project.get("figure_id", ""),
        "title": project.get("title", ""),
        "source_kind": project.get("source_kind", "spec"),
        "status": project.get("status", "ready"),
        "last_error": project.get("last_error", ""),
        "created_at": project.get("created_at", 0),
        "updated_at": project.get("updated_at", 0),
        "render_version": project.get("render_version", 0),
        "objects": len(manifest.get("objects", [])),
        "exports": exports,
        "has_script": bool((project.get("source_json") or {}).get("script")),
        "editable": _can_edit(project),
    }


def _is_script(project: dict[str, Any]) -> bool:
    source = project.get("source_json") or {}
    return project.get("source_kind") == "script" or "script" in source


def _can_edit(project: dict[str, Any]) -> bool:
    if not _is_script(project):
        return project.get("source_kind", "spec") == "spec"
    source = project.get("source_json") or {}
    # Legacy projects never recorded a reproducible input-name mapping. Do
    # not drop declared dependencies and accidentally replay different data.
    if source.get("inputs") or source.get("sources"):
        return False
    if project.get("data_refs_json") and "input_snapshots" not in source:
        return False
    from src.config import settings
    backend = str(getattr(settings, "FIGURES_SANDBOX_BACKEND", "") or "").lower()
    return bool(source.get("script")) and (
        (backend in {"docker", "apptainer"} and bool(getattr(settings, "FIGURES_SANDBOX_IMAGE", "")))
        or (backend == "local" and bool(getattr(settings, "FIGURES_SANDBOX_ALLOW_LOCAL", False))))


def detail_for_owner(figure_id: str, owner: int, *, upgrade_manifest: bool = False) -> dict[str, Any]:
    """Read stored exports; optionally discover legacy objects without saving."""
    project = get_for_owner(figure_id, owner)
    manifest = _normalise_manifest(project.get("manifest_json"))
    public = _public(project)
    from src.figures.manifest import MANIFEST_VERSION
    if upgrade_manifest and public["editable"] and manifest.get("version", 1) < MANIFEST_VERSION:
        try:
            _, _, manifest, _ = _render_edits(project, owner, [], formats=("svg",))
            public["manifest_upgraded"] = True
        except ProjectError as exc:
            public.update(editable=False, edit_error={"code": exc.code, "message": exc.message})
    from src.figures.store import list_revisions
    revisions = list_revisions(figure_id, owner)
    return {
        **public,
        "manifest": manifest,
        "overrides": project.get("overrides_json") or [],
        "data_refs": project.get("data_refs_json") or [],
        "revisions": [_public(revision) for revision in revisions],
        "preview_url": f"/api/v2/figures/projects/{project['figure_id']}/preview.svg",
    }


def _assert_current(project: dict[str, Any], owner: int) -> None:
    current = get_project(str(project["figure_id"]), int(owner))
    if current is None or any(current.get(key) != project.get(key) for key in (
            "render_version", "source_json", "data_refs_json", "overrides_json")):
        raise ProjectError("revision_conflict", "渲染期间图件已更新或删除，请重新加载后合并修改。", status=409)


def _verify_sources(project: dict[str, Any], owner: int) -> None:
    from src.session import files as session_files
    import hashlib
    source = project.get("source_json") or {}
    if source.get("analysis_result_ref"):
        try:
            from src.research.analysis_results import load_analysis_result, load_dataset_snapshot
            analysis = load_analysis_result(owner, source["analysis_result_ref"])
            if analysis.to_dict()["dataset_ref"] != source.get("dataset_ref"):
                raise ValueError("analysis dataset changed")
            load_dataset_snapshot(owner, source["dataset_ref"])
        except Exception as exc:
            raise ProjectError("analysis_unavailable", "保存的分析结果或数据版本无法验证，拒绝重放。") from exc
    refs = project.get("data_refs_json") or []
    pinned = {str(ref.get("file_id")): ref for ref in refs if isinstance(ref, dict) and ref.get("file_id")}
    spec = (project.get("source_json") or {}).get("spec") or {}
    if isinstance(spec, dict):
        specs = list(spec.get("panels") or []) if spec.get("panels") else [spec]
        for item, name in ((item, name) for item in specs for name in ("x", "y", "group", "weight")):
            column = item.get(name)
            if not isinstance(column, dict):
                continue
            if column.get("kind") == "attachment":
                raise ProjectError("source_unverified", "旧图件仍引用临时附件，无法重建原始输入；请重新出图。")
            if column.get("kind") == "account_file" and str(column.get("file_id")) not in pinned:
                raise ProjectError("source_unverified", "图件数据引用缺少保存时的校验信息，请重新出图。")
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("file_id"):
            continue
        if not ref.get("sha256"):
            raise ProjectError("source_unverified", "图件缺少原始数据校验信息，请重新出图。")
        entry = session_files.get_account_file(str(ref["file_id"]), owner)
        if not entry:
            raise ProjectError("data_unavailable", "图件引用的原始文件已不可用。")
        try:
            with open(entry["path"], "rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError as exc:
            raise ProjectError("data_unavailable", "图件引用的原始文件无法读取。") from exc
        if digest != ref["sha256"]:
            raise ProjectError("source_changed", "图件原始文件的内容已改变，请新建数据版本后重新出图。")


def _render_edits(project: dict[str, Any], owner: int, raw_overrides: Any,
                  *, formats: tuple[str, ...] = DEFAULT_FORMATS
                  ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], dict[str, bytes]]:
    """The one preview/save path; all outputs remain in memory until commit."""
    from src.figures.trusted_render import render_resolved, TrustedRenderError

    _assert_current(project, owner)
    _verify_sources(project, owner)
    source = project.get("source_json") or {}
    if _is_script(project):
        if (source.get("inputs") or source.get("sources")
                or (project.get("data_refs_json") and "input_snapshots" not in source)):
            raise ProjectError("script_inputs_unavailable", "脚本原始输入映射无法重建，不能重放；请重新生成图件。")
        if source.get("input_snapshots"):
            pinned = {ref.get("file_id"): ref.get("sha256") for ref in project.get("data_refs_json", []) if isinstance(ref, dict)}
            if any(pinned.get(item.get("file_id")) != item.get("sha256") for item in source["input_snapshots"]):
                raise ProjectError("source_unverified", "脚本输入快照缺少固定引用。")
        if not isinstance(raw_overrides, list) or any(not isinstance(item, dict) for item in raw_overrides):
            raise ProjectError("invalid_overrides", "修改必须是对象列表。")
        # Validate every submitted entry in the worker before merging; a bad
        # duplicate must not disappear behind a later valid value.
        blobs, new_manifest, merged = _render_script(
            source, raw_overrides, owner_user_id=owner,
            existing=project.get("overrides_json") or [], formats=formats)
        changed = [item["gid"] for item in merged]
    else:
        resolved, info = _render_source(source, owner_user_id=owner)
        try:
            result = render_resolved(resolved, overrides=raw_overrides,
                                     existing=project.get("overrides_json") or [],
                                     formats=formats, dpi=_figure_dpi())
        except TrustedRenderError as exc:
            raise ProjectError(exc.code, exc.message) from exc
        merged, changed, new_manifest, blobs = result.overrides, result.changed, result.manifest, result.blobs
        new_manifest["data_provenance"] = info
    try:
        manifest_size = len(json.dumps(new_manifest, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ProjectError("manifest_invalid", "图件对象清单包含无效数据。") from exc
    if (sum(len(blob) for blob in blobs.values()) > MAX_RENDER_BYTES
            or len(blobs.get("svg", b"")) > MAX_PREVIEW_BYTES
            or manifest_size > MAX_MANIFEST_BYTES):
        raise ProjectError("render_too_large", "图件预览或对象清单超过体积上限。")
    _verify_sources(project, owner)
    _assert_current(project, owner)
    return merged, changed, new_manifest, blobs


def _load_revision(figure_id: str, owner: int, expected_revision: int | None) -> dict[str, Any]:
    project = get_for_owner(figure_id, owner)
    if expected_revision is not None and int(expected_revision) != int(project.get("render_version") or 0):
        raise ProjectError("revision_conflict", "图件已在其他页面更新，请重新加载后合并修改。", status=409)
    return project


def preview_overrides(figure_id: str, owner: int, raw_overrides: Any,
                      *, expected_revision: int | None = None) -> dict[str, Any]:
    """Render a disposable draft; no session, revision, pin or file is created."""
    project = _load_revision(figure_id, owner, expected_revision)
    merged, _, manifest, blobs = _render_edits(project, owner, raw_overrides, formats=("svg",))
    return {"figure_id": figure_id, "manifest": manifest,
            "svg": blobs["svg"].decode("utf-8"),
            "render_version": int(project.get("render_version") or 0), "overrides": merged}


def save_overrides(figure_id: str, owner: int, raw_overrides: Any,
                   *, session_id: str = "", expected_revision: int | None = None) -> dict[str, Any]:
    """Validate the user's edits, apply them, re-render and publish.

    The whole batch is rejected if any entry is invalid. A partial
    application would leave the figure in a state the user did not ask for and
    did not see.
    """
    project = _load_revision(figure_id, owner, expected_revision)
    revision = int(project.get("render_version") or 0)
    merged, changed, new_manifest, blobs = _render_edits(project, owner, raw_overrides)

    stem = project.get("title") or "figure"
    published = publish_rendered(owner, session_id or project.get("session_id", ""),
                                 stem, blobs)

    committed = update_project(
        figure_id, owner, expected_revision=revision,
        overrides_json=merged,
        manifest_json=new_manifest,
        exports_json=published,
        render_version=revision + 1,
        status="ready", last_error="")
    if not committed:
        # Published files are valid immutable artifacts with normal lifecycle
        # records, but must never replace a newer project's current render set.
        raise ProjectError("revision_conflict", "保存期间图件已更新或删除，当前版本未被覆盖。", status=409)
    return {
        "figure_id": figure_id,
        "overrides": merged,
        "changed": changed,
        "manifest": new_manifest,
        "exports": published,
        "render_version": int(project.get("render_version") or 0) + 1,
    }


def delete_for_owner(figure_id: str, owner: int) -> bool:
    """Delete a project owned by this account."""
    return delete_project(str(figure_id), int(owner))


def import_from_library(owner: int, session_id: str, file_id: str,
                         *, title: str = "") -> dict[str, Any]:
    """Create a project from a file already in the account's library.

    The file must belong to this account — the library's own lookup enforces
    that, and a file belonging to somebody else is simply not found.
    """
    from src.session import files as session_files

    entry = session_files.get_account_file(str(file_id), int(owner))
    if not entry:
        raise ProjectError("not_found", "资料库中没有该文件，或该文件不属于当前账号。",
                           status=404)
    filename = str(entry.get("filename") or "")
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in {"csv", "tsv", "xlsx", "xlsm", "xls", "xlsb"}:
        raise ProjectError(
            "unsupported_file",
            "只能从表格文件（CSV/TSV/XLSX）导入图件；"
            "绘图脚本请通过聊天里的绘图工具生成。")

    from src.figures.spec import parse_spec
    from src.figures.trusted_render import render_resolved, TrustedRenderError
    from src.figures.resolve import resolve_spec
    from src.figures.spec import SpecError
    from src.research.tools import _load_frame_from_bytes

    try:
        frame, _meta = _load_frame_from_bytes(
            Path(entry["path"]).read_bytes(), filename)
    except Exception as exc:  # noqa: BLE001
        raise ProjectError("data_unavailable", f"文件无法解析：{exc}") from exc

    columns = [str(name) for name in list(frame.columns)]
    if not columns:
        raise ProjectError("empty_table", "这张表没有可用的列。")
    numeric = _numeric_columns(frame, columns)
    if not numeric:
        raise ProjectError("no_numeric_column", "这张表里没有可用的数值列。")

    y_column = numeric[0]
    x_column = next((name for name in columns if name != y_column), y_column)

    spec = parse_spec({
        "chart": "bar",
        "title": title or filename.rsplit(".", 1)[0],
        "x_label": x_column,
        "y_label": y_column,
        "x": {"kind": "account_file", "file_id": str(file_id), "column": x_column},
        "y": {"kind": "account_file", "file_id": str(file_id), "column": y_column},
        "error": "none",
        "legend": True,
    })
    try:
        resolved, info = resolve_spec(spec, owner_user_id=owner)
        result = render_resolved(resolved, formats=DEFAULT_FORMATS, dpi=int(_figure_dpi()))
        blobs, manifest = result.blobs, result.manifest
    except (SpecError, TrustedRenderError) as exc:
        raise ProjectError("render_failed",
                           str(getattr(exc, "message", exc))) from exc

    stem = title or filename.rsplit(".", 1)[0]
    project = create_project(
        owner_user_id=owner, title=stem, source_kind="spec",
        source={"kind": "spec", "spec": spec.as_dict()},
        session_id=session_id, data_refs=info.get("refs", []))
    manifest["data_provenance"] = info
    try:
        published = publish_rendered(owner, session_id, stem, blobs)
        committed = update_project(project["figure_id"], owner, expected_revision=0,
                                   manifest_json=manifest, exports_json=published, render_version=1)
        if not committed:
            raise ProjectError("revision_conflict", "导入期间图件已更新或删除。", status=409)
    except Exception:
        delete_project(project["figure_id"], owner)
        raise

    return {
        "figure_id": project["figure_id"],
        "title": stem,
        "columns": columns,
        "numeric_columns": numeric,
        "manifest": manifest,
        "exports": published,
    }


def _numeric_columns(frame: Any, columns: list[str]) -> list[str]:
    """The columns whose values can be read as numbers.

    The dtype is not enough: the service's CSV reader keeps every column as
    ``object`` so that mixed columns survive the round trip. So a column
    counts as numeric when its values actually convert — which is what the
    renderer will do with them.
    """
    out: list[str] = []
    for name in columns:
        series = frame[name]
        values = [value for value in list(series)[:200] if value is not None
                  and str(value).strip() != ""]
        if not values:
            continue
        convertible = 0
        for value in values:
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number == number and number not in (float("inf"), float("-inf")):
                convertible += 1
        if convertible and convertible >= len(values) * 0.8:
            out.append(name)
    return out


def _figure_dpi() -> int:
    from src.config import settings
    try:
        return int(getattr(settings, "FIGURES_DPI", 300) or 300)
    except (TypeError, ValueError):
        return 300


def data_files(owner: int) -> list[dict[str, Any]]:
    """The account's tables, for the workspace's "import from library" list."""
    from src.session import files as session_files

    entries = session_files.list_account_files(int(owner), touch=False)
    out: list[dict[str, Any]] = []
    for entry in entries:
        filename = str(entry.get("filename") or "")
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if suffix not in {"csv", "tsv", "xlsx", "xlsm", "xls", "xlsb"}:
            continue
        out.append({
            "file_id": entry.get("file_id", ""),
            "filename": filename,
            "size_bytes": entry.get("size_bytes", 0),
            "created_at": entry.get("created_at", 0),
            "download_url": entry.get("download_url", ""),
        })
    return out


def schema_for_file(owner: int, file_id: str) -> dict[str, Any]:
    """The columns of one account table, read only."""
    from src.session import files as session_files

    entry = session_files.get_account_file(str(file_id), int(owner))
    if not entry:
        raise ProjectError("not_found", "文件不存在或不属于当前账号。", status=404)
    filename = str(entry.get("filename") or "")
    from src.research.tools import _load_frame_from_bytes
    try:
        frame, meta = _load_frame_from_bytes(
            Path(entry["path"]).read_bytes(), filename)
    except Exception as exc:  # noqa: BLE001
        raise ProjectError("data_unavailable", f"文件无法解析：{exc}") from exc
    columns = []
    numeric = set(_numeric_columns(frame, [str(name) for name in list(frame.columns)]))
    for name in list(frame.columns):
        series = frame[name]
        columns.append({
            "name": str(name),
            "dtype": str(series.dtype),
            "numeric": str(name) in numeric,
            "non_null": int(series.notna().sum()),
        })
    return {
        "file_id": str(file_id),
        "filename": filename,
        "rows": int(frame.shape[0]),
        "columns": columns,
        "sheet": meta.get("sheet", ""),
    }


__all__ = [
    "DEFAULT_FORMATS", "FIGURE_FORMATS", "MIME_BY_FORMAT", "ProjectError",
    "data_files", "delete_for_owner", "detail_for_owner", "get_for_owner",
    "import_from_library", "list_for_owner", "publish_rendered",
    "preview_overrides", "save_overrides", "schema_for_file",
]

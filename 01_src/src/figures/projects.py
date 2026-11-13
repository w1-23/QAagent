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
    """Turn a project's source into a rendered figure.

    Two source kinds exist, and they are rendered by different code paths:
    ``spec`` goes through the validated-specification pipeline, ``script``
    through the figure capture. Both end at the same shape.
    """
    kind = str(source.get("kind") or "spec")
    if kind == "script":
        return _render_script(source)
    return _render_spec(source, owner_user_id)


def _render_spec(source: dict[str, Any],
                 owner_user_id: int | None = None) -> tuple[Any, dict[str, Any]]:
    """Render a declarative figure from its stored specification."""
    from src.figures.core import build_resolved_figure
    from src.figures.exporter import ExportError
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
    try:
        rendered = build_resolved_figure(resolved)
    except SpecError as exc:
        raise ProjectError("render_failed", exc.message) from exc
    except ExportError as exc:
        raise ProjectError("render_failed", str(getattr(exc, "message", exc))) from exc
    return rendered, info


def _render_script(source: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
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
        runtime=str(getattr(settings, "FIGURES_SANDBOX_RUNTIME", "") or ""),
        allow_local=bool(getattr(settings, "FIGURES_SANDBOX_ALLOW_LOCAL", False)))
    if runner is None:
        raise ProjectError(
            "sandbox_unavailable",
            "当前环境没有配置绘图脚本沙箱，无法重新渲染脚本来源的图件；"
            "请改用声明式图件工具重新出图。",
            status=503)

    outputs = {fmt: f"figure.{fmt}" for fmt in DEFAULT_FORMATS}
    job = SandboxJob(
        script=text, outputs=outputs,
        timeout_sec=int(getattr(settings, "FIGURES_SANDBOX_TIMEOUT_SEC", 120) or 120),
        memory_mb=int(getattr(settings, "FIGURES_SANDBOX_MEMORY_MB", 1024) or 1024),
        cpus=str(getattr(settings, "FIGURES_SANDBOX_CPUS", "1.0") or "1.0"),
        image=str(getattr(settings, "FIGURES_SANDBOX_IMAGE", "") or ""),
        runtime=str(getattr(settings, "FIGURES_SANDBOX_RUNTIME", "") or ""))
    try:
        result = runner.run(job)
    except SandboxUnavailable as exc:
        raise ProjectError("sandbox_unavailable", str(exc), status=503) from exc
    except Exception as exc:  # noqa: BLE001
        raise ProjectError("render_failed", f"沙箱执行失败：{exc}") from exc
    if not result.ok:
        raise ProjectError("render_failed", result.describe() or "绘图脚本执行失败。")

    manifest: dict[str, Any] = {}
    try:
        manifest = json.loads(result.stdout or "{}").get("manifest") or {}
    except (TypeError, ValueError):
        manifest = {}

    # The sandbox produced files, not a figure object, and the service must
    # not execute the script itself. So the figure object needed for
    # artist-level edits is not available outside the container.
    raise ProjectError(
        "script_edits_unsupported",
        "脚本来源的图件暂不支持在工作台里改样式后重新渲染；"
        "请用声明式图件工具重新出图，或在脚本中直接调整样式。")



# ── Applying edits ────────────────────────────────────────────────────────

def _apply_to_rendered(rendered: Any, manifest: dict[str, Any],
                        overrides: list[dict[str, Any]]) -> list[str]:
    """Apply the user's edits to a rendered figure."""
    try:
        return apply_overrides(rendered, manifest, overrides)
    except Exception as exc:  # noqa: BLE001
        raise ProjectError("apply_failed", f"修改未能应用：{exc}") from exc


def _normalise_manifest(manifest: Any) -> dict[str, Any]:
    if isinstance(manifest, dict):
        return manifest
    return {"version": 1, "objects": []}


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
    from src.session import files as session_files

    if not session_id:
        raise ProjectError("no_session",
                           "没有有效会话，无法把图件登记到资料库。")
    safe = session_files._safe_name(filename)
    artifact_id = uuid.uuid4().hex
    try:
        root = session_files._artifact_session_dir(session_id)
        root.mkdir(parents=True, exist_ok=True)
        path = (root / f"{artifact_id}_{safe}").resolve()
        if not path.is_relative_to(root):
            raise ProjectError("publish_failed", "图件文件的存放路径无效。")
        path.write_bytes(payload)
        entry = session_files.register_file(
            session_id=session_id, owner_user_id=owner, filename=safe,
            storage_path=str(path), mime=mime,
            origin=session_files.ORIGIN_GENERATED, artifact_id=artifact_id,
            producer_tool="figure_workspace")
    except ProjectError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProjectError("publish_failed", f"图件登记失败：{exc}") from exc
    if not entry.get("file_id"):
        raise ProjectError("publish_failed", "图件登记失败。")
    return {
        "file_id": entry.get("file_id", ""),
        "filename": entry.get("filename", filename),
        "sha256": entry.get("sha256", ""),
        "size": entry.get("size_bytes", len(payload)),
        "download_url": entry.get("download_url", ""),
        "artifact_id": artifact_id,
        "artifact_url": f"/api/v2/session/{session_id}/artifacts/{artifact_id}",
    }


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
    }


def detail_for_owner(figure_id: str, owner: int) -> dict[str, Any]:
    """Everything the workspace needs to open a project."""
    project = get_for_owner(figure_id, owner)
    manifest = _normalise_manifest(project.get("manifest_json"))
    return {
        **_public(project),
        "manifest": manifest,
        "overrides": project.get("overrides_json") or [],
        "data_refs": project.get("data_refs_json") or [],
        "preview_url": f"/api/v2/figures/projects/{project['figure_id']}/preview.svg",
    }


def save_overrides(figure_id: str, owner: int, raw_overrides: Any,
                   *, session_id: str = "") -> dict[str, Any]:
    """Validate the user's edits, apply them, re-render and publish.

    The whole batch is rejected if any entry is invalid. A partial
    application would leave the figure in a state the user did not ask for and
    did not see.
    """
    project = get_for_owner(figure_id, owner)
    manifest = _normalise_manifest(project.get("manifest_json"))

    try:
        incoming = validate_overrides(raw_overrides, manifest)
    except OverrideError as exc:
        raise ProjectError(exc.code, exc.message) from exc

    merged = merge_overrides(project.get("overrides_json") or [], incoming)
    source = project.get("source_json") or {}
    if not isinstance(source, dict):
        source = {}

    rendered, info = _render_source(source, owner_user_id=owner)
    changed = _apply_to_rendered(rendered, manifest, merged)

    from src.figures.exporter import ExportError, export_all
    from src.figures.manifest import build_manifest

    try:
        blobs = export_all(rendered, formats=DEFAULT_FORMATS, dpi=_figure_dpi())
    except ExportError as exc:
        raise ProjectError("render_failed",
                           str(getattr(exc, "message", exc))) from exc

    # The manifest is rebuilt from the rendered figure so the boxes follow the
    # edits: a moved title has a new position, and the overlay must follow it.
    new_manifest = build_manifest(rendered, source=str(project.get("source_kind") or "spec"))

    stem = project.get("title") or "figure"
    published = publish_rendered(owner, session_id or project.get("session_id", ""),
                                 stem, blobs)

    update_project(
        figure_id, owner,
        overrides_json=merged,
        manifest_json=new_manifest,
        exports_json=published,
        render_version=int(project.get("render_version") or 0) + 1,
        status="ready", last_error="")
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
    from src.figures.core import build_resolved_figure
    from src.figures.exporter import ExportError, export_all
    from src.figures.manifest import build_manifest
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
        rendered = build_resolved_figure(resolved)
        blobs = export_all(rendered, formats=DEFAULT_FORMATS,
                           dpi=int(_figure_dpi()))
        manifest = build_manifest(rendered, source="spec")
    except (SpecError, ExportError) as exc:
        raise ProjectError("render_failed",
                           str(getattr(exc, "message", exc))) from exc

    stem = title or filename.rsplit(".", 1)[0]
    project = create_project(
        owner_user_id=owner, title=stem, source_kind="spec",
        source={"kind": "spec", "spec": spec.as_dict()},
        session_id=session_id, data_refs=info.get("refs", []))
    published = publish_rendered(owner, session_id, stem, blobs)
    update_project(project["figure_id"], owner, manifest_json=manifest,
                   exports_json=published, render_version=1)

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
    "save_overrides", "schema_for_file",
]

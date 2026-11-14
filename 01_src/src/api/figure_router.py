"""The figure workspace: its page and its API.

This is a separate router rather than more routes in ``main.py``, because the
figure workspace is a self-contained feature with one page and one family of
endpoints. Keeping it in its own module keeps the conflict surface with other
work on the service to a single ``include_router`` line.

The rules this module enforces
------------------------------
* **The page checks its own login.** The service's auth middleware only
  guards ``/api/`` paths; the ``/admin`` page checks inside the handler, and
  so does this one. An unauthenticated visit is redirected to the sign-in
  page rather than shown a shell that would then fail every request.
* **Every project query is scoped to the owner.** A project belonging to
  another account returns 404, not 403 — a caller must not be able to learn
  that an id exists.
* **The preview is an image, never markup.** The SVG is served as
  ``image/svg+xml`` with a content policy that stops it doing anything, and
  the page loads it in an ``<img>``. The SVG source never enters the DOM, so
  no sanitizer in the browser is needed and none is asked for.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from src.config import WEB_DIR
from src.core.account_leases import AccountUnavailable, account_lease, run_account_async, run_account_thread

router = APIRouter(prefix="/api/v2/figures", tags=["figures"])

# The response headers that keep a previewed SVG inert. The file is an image
# the browser draws, not a document it parses: the sandbox directive and the
# empty default-src mean that even a malformed SVG cannot reach the network
# or run anything.
_SVG_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
}

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'self'"),
}


# ── Request bodies ────────────────────────────────────────────────────────

class OverrideBatch(BaseModel):
    """The user's edits, as the workspace sends them."""

    overrides: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    expected_revision: int | None = Field(default=None, ge=0)


class ImportRequest(BaseModel):
    """A request to build a figure project out of a library file."""

    file_id: str = ""
    title: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────

def _current_user(request: Request) -> dict:
    """The authenticated account, or a refusal.

    The service's own ``current_user`` is preferred, so the workspace and the
    rest of the API agree on who the caller is and on what a missing session
    looks like. The import is deferred because ``main`` mounts this router.
    """
    user = getattr(request.state, "user", None)
    if user:
        return user
    try:
        from src.api.main import current_user
    except ImportError:  # pragma: no cover - only when mounted standalone
        current_user = None
    if current_user is not None:
        return current_user(request)
    from src.auth import user_for_token
    user = user_for_token(request.cookies.get("citrus_auth", ""))
    if not user:
        raise HTTPException(status_code=401, detail="请先登录。")
    return user


def _owner(request: Request) -> int:
    return int(_current_user(request)["user_id"])


@contextmanager
def _write_scope(owner: int):
    from src.core.figure_capacity import FigureCapacityExceeded, reserve_on_lease
    from src.figures.store import ProjectQuotaExceeded
    try:
        with account_lease(owner) as lease:
            reserve_on_lease(owner, lease)
            yield lease
    except FigureCapacityExceeded as exc:
        raise HTTPException(status_code=429, detail={"code": "figure_capacity", "message": str(exc)},
                            headers={"Retry-After": "2"}) from None
    except ProjectQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail={"code": "figure_project_limit", "message": str(exc)}) from None
    except AccountUnavailable:
        raise HTTPException(status_code=409, detail={
            "code": "account_closed", "message": "账户已停用或正在删除，不能修改图件。"})


async def _resolve_session(request: Request, lease) -> str:
    """The session new publications go into."""
    from src.session.manager import session_manager

    owner = _owner(request)
    raw = str(request.query_params.get("session_id") or "")
    if raw and session_manager.session_belongs_to(raw, owner):
        return raw
    return await run_account_async(lease, session_manager.get_or_create_session(None, owner))


def _fail(exc: Any) -> JSONResponse:
    """Turn a project error into the API's error shape."""
    status = int(getattr(exc, "status", 400) or 400)
    return JSONResponse(
        {"detail": str(getattr(exc, "message", exc) or "操作失败。"),
         "code": str(getattr(exc, "code", "figure_error") or "figure_error")},
        status_code=status)


# ── The page ──────────────────────────────────────────────────────────────

@router.get("/page")
async def figure_page():
    """Redirect to the standalone workspace page (kept for link stability)."""
    return RedirectResponse(url="/figures", status_code=307)


async def serve_figure_page(request: Request):
    """Serve the workspace page, but only to a signed-in account."""
    from src.auth import user_for_token

    user = user_for_token(request.cookies.get("citrus_auth", ""))
    if not user:
        return RedirectResponse(url="/?next=%2Ffigures", status_code=303)
    html_path = WEB_DIR / "figures.html"
    if not html_path.exists():
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<title>图表工作台未部署</title><body><main><h1>图表工作台页面未部署</h1>"
            "<a href='/'>返回 CitrusSchalor</a></main></body></html>",
            status_code=404, headers=_PAGE_HEADERS)
    return FileResponse(html_path, headers=_PAGE_HEADERS)


# ── Projects ──────────────────────────────────────────────────────────────

@router.get("/projects")
async def list_projects(request: Request, limit: int = 100, offset: int = 0):
    """The account's figure projects."""
    from src.figures import projects

    owner = _owner(request)
    items = await asyncio.to_thread(projects.list_for_owner, owner,
                                    limit=max(1, min(int(limit), 200)),
                                    offset=max(0, int(offset)))
    return {"count": len(items), "projects": items}


@router.get("/projects/{figure_id}")
async def get_project(figure_id: str, request: Request):
    """One project: its manifest, its edits and its exports."""
    from src.figures import projects

    owner = _owner(request)
    with _write_scope(owner) as lease:
        try:
            detail = await run_account_thread(
                lease, projects.detail_for_owner, figure_id, owner, upgrade_manifest=True)
        except projects.ProjectError as exc:
            return _fail(exc)
    return detail


@router.post("/projects/{figure_id}/preview")
async def preview_overrides(figure_id: str, body: OverrideBatch, request: Request):
    """Render pending edits without publishing or creating a session."""
    from src.figures import projects

    owner = _owner(request)
    with _write_scope(owner) as lease:
        try:
            result = await run_account_thread(
                lease, projects.preview_overrides, figure_id, owner, body.overrides,
                expected_revision=body.expected_revision)
        except projects.ProjectError as exc:
            return _fail(exc)
    return JSONResponse(result, headers={"Cache-Control": "private, no-store",
                                         "X-Content-Type-Options": "nosniff"})


@router.post("/projects/{figure_id}/overrides")
async def save_overrides(figure_id: str, body: OverrideBatch, request: Request):
    """Validate the edits, re-render the figure and publish the result.

    The response carries the new manifest and the new export entries, so the
    page can show the re-rendered figure without a second round trip.
    """
    from src.figures import projects

    owner = _owner(request)
    with _write_scope(owner) as lease:
        session_id = await _resolve_session(request, lease)
        try:
            result = await run_account_thread(
                lease, projects.save_overrides, figure_id, owner, body.overrides,
                session_id=session_id, expected_revision=body.expected_revision)
        except projects.ProjectError as exc:
            return _fail(exc)
        return result


@router.delete("/projects/{figure_id}")
async def delete_project(figure_id: str, request: Request):
    """Delete one project."""
    from src.figures import projects

    owner = _owner(request)
    with _write_scope(owner) as lease:
        try:
            ok = await run_account_thread(lease, projects.delete_for_owner, figure_id, owner)
        except projects.ProjectError as exc:
            return _fail(exc)
        if not ok:
            return JSONResponse({"detail": "图件不存在或无权访问。"}, status_code=404)
        return {"status": "ok", "figure_id": figure_id}


# ── Preview and download ──────────────────────────────────────────────────

@router.get("/projects/{figure_id}/preview.svg")
async def preview_svg(figure_id: str, request: Request):
    """The current version of the figure, as an image.

    The bytes here are the bytes of the published SVG, so what the page shows
    and what the user downloads are the same file.
    """
    from src.figures import projects
    from src.session import files as session_files

    owner = _owner(request)
    try:
        detail = await asyncio.to_thread(projects.detail_for_owner, figure_id, owner)
    except projects.ProjectError as exc:
        return _fail(exc)

    entry = _find_export(detail, "svg")
    if not entry:
        return JSONResponse({"detail": "这张图还没有可预览的 SVG。"}, status_code=404)
    info = await asyncio.to_thread(session_files.get_account_file,
                                   str(entry.get("file_id") or ""), owner, touch=True)
    if not info:
        return JSONResponse({"detail": "图件文件已不可用，请重新出图。"}, status_code=404)
    return FileResponse(path=info["path"], media_type="image/svg+xml",
                        headers={**_SVG_HEADERS,
                                 "Content-Disposition": "inline"})


@router.get("/projects/{figure_id}/export/{fmt}")
async def export_project(figure_id: str, fmt: str, request: Request):
    """Download the figure in one of its formats."""
    from src.figures import projects
    from src.session import files as session_files

    owner = _owner(request)
    name = str(fmt or "").strip().lower()
    if name not in projects.FIGURE_FORMATS:
        return JSONResponse(
            {"detail": f"不支持的格式「{fmt}」；可用：{', '.join(projects.FIGURE_FORMATS)}。"},
            status_code=404)
    try:
        detail = await asyncio.to_thread(projects.detail_for_owner, figure_id, owner)
    except projects.ProjectError as exc:
        return _fail(exc)

    entry = _find_export(detail, name)
    if not entry:
        return JSONResponse({"detail": f"这张图没有 {name} 格式。"}, status_code=404)
    info = await asyncio.to_thread(session_files.get_account_file,
                                   str(entry.get("file_id") or ""), owner, touch=True)
    if not info:
        return JSONResponse({"detail": "图件文件已不可用，请重新出图。"}, status_code=404)
    return FileResponse(path=info["path"],
                        media_type=projects.MIME_BY_FORMAT[name],
                        filename=str(info.get("filename") or f"figure.{name}"),
                        headers={"Cache-Control": "private, no-store",
                                 "X-Content-Type-Options": "nosniff"})


def _find_export(detail: dict[str, Any], fmt: str) -> dict[str, Any]:
    exports = detail.get("exports") or {}
    if not isinstance(exports, dict):
        return {}
    entry = exports.get(fmt)
    return entry if isinstance(entry, dict) else {}


# ── Importing from the file library ───────────────────────────────────────

@router.get("/data-files")
async def list_data_files(request: Request):
    """The account's tables, for the workspace's import list."""
    from src.figures import projects

    owner = _owner(request)
    items = await asyncio.to_thread(projects.data_files, owner)
    return {"count": len(items), "files": items}


@router.get("/data-files/{file_id}/schema")
async def data_file_schema(file_id: str, request: Request):
    """One table's columns, read only."""
    from src.figures import projects

    owner = _owner(request)
    try:
        schema = await asyncio.to_thread(projects.schema_for_file, owner, file_id)
    except projects.ProjectError as exc:
        return _fail(exc)
    return schema


@router.post("/projects")
async def import_project(body: ImportRequest, request: Request):
    """Create a project from a table in the account's library."""
    from src.figures import projects

    owner = _owner(request)
    with _write_scope(owner) as lease:
        session_id = await _resolve_session(request, lease)
        if not str(body.file_id or "").strip():
            return JSONResponse({"detail": "请选择要导入的表格文件。"}, status_code=400)
        try:
            result = await run_account_thread(
                lease, projects.import_from_library, owner, session_id, body.file_id,
                title=str(body.title or ""))
        except projects.ProjectError as exc:
            return _fail(exc)
        return result


__all__ = ["router", "serve_figure_page"]

"""Authorized shared literature status, preview, and administration."""
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.auth import require_admin
from .activity import MAINTENANCE_MESSAGE, MaintenanceBusy, RETRY_AFTER
from .runtime import current_runtime

router = APIRouter(prefix="/api/v2/shared-corpus", tags=["shared-corpus"])
_PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def _actor(request, *, admin=False):
    user = getattr(request.state, "user", None)
    if not user or not user.get("active") or user.get("approval_status") != "approved":
        raise HTTPException(401, "请使用已批准的账号登录。")
    if admin and not require_admin(user):
        raise HTTPException(403, "此操作仅管理员可用。")
    return int(user["user_id"])


def _runtime():
    runtime = current_runtime()
    if runtime is None:
        raise HTTPException(404, "共享文献库未启用。")
    return runtime


def _job_view(job):
    return {"ingest_job_id": job["id"], "maintenance_id": job["id"], "state": job["state"],
            "generation": job["generation"], "created_at": job["created"], "updated_at": job["updated"]}


@router.get("/maintenance")
def maintenance(request: Request):
    _actor(request)
    runtime = current_runtime()
    result = (runtime.status() if runtime else
              {"state": "disabled", "message": "", "started_at": None,
               "active_generation_label": None, "retry_after_sec": 0})
    return JSONResponse(result, headers=_PRIVATE)


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request):
    owner = _actor(request)
    job = _runtime().journal.job(job_id)
    if not job or (job["owner"] != owner and not require_admin(request.state.user)):
        raise HTTPException(404, "构建任务不存在或无权访问。")
    return JSONResponse(_job_view(job), headers=_PRIVATE)


@router.get("/documents/{document_id}")
def preview(document_id: str, request: Request,
            index_generation: str | None = Query(default=None, max_length=96),
            version_id: str | None = Query(default=None, max_length=96),
            start: int = Query(default=0, ge=0), length: int = Query(default=12000, ge=1, le=12000),
            md_sha256: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$")):
    _actor(request)
    try:
        result = _runtime().preview(document_id, generation=index_generation, version=version_id,
                                    start=start, length=length, digest=md_sha256)
    except MaintenanceBusy as error:
        raise HTTPException(503, {"code": "knowledge_base_updating", "message": MAINTENANCE_MESSAGE},
                            headers={"Retry-After": str(RETRY_AFTER)}) from error
    except (LookupError, ValueError, OSError) as error:
        raise HTTPException(404, "文献版本不存在或无权访问。") from error
    return JSONResponse(result, headers=_PRIVATE)


@router.get("/admin/status")
def admin_status(request: Request):
    _actor(request, admin=True)
    runtime = _runtime()
    job = runtime.journal.pending()
    result = runtime.status()
    pending = None
    if job:
        pending = {**_job_view(job), "error_category": job["error"],
                   "document_count": len(json.loads(job["documents"])),
                   "deferred": job["state"] == "waiting_for_idle" and time.time() - job["updated"] >= 86400}
    result.update(gate_closed=runtime.activity.gated, activity=dict(runtime.activity.counts), pending=pending)
    return JSONResponse(result, headers=_PRIVATE)


@router.post("/admin/build")
def admin_build(request: Request):
    owner = _actor(request, admin=True)
    try:
        job = _runtime().request_build(owner)
    except ValueError as error:
        raise HTTPException(409, "无法申请共享库构建，请检查候选文献、待处理任务或构建额度。") from error
    return JSONResponse(_job_view(job), status_code=202, headers=_PRIVATE)


class RightsDecision(BaseModel):
    model_config = {"extra": "forbid"}
    reason: str = Field(min_length=1, max_length=1000)


@router.get("/admin/proposals")
def admin_proposals(request: Request, owner_user_id: int = Query(gt=0),
                    state: str = Query(default="", max_length=32), limit: int = Query(default=50, ge=1, le=50)):
    _actor(request, admin=True)
    fields = ("proposal_id", "candidate_id", "state", "rights", "document_id", "version_id",
              "title", "doi", "source_kind", "reason", "created_at", "updated_at")
    runtime = _runtime()
    try:
        proposals = runtime.catalog.list_proposals(owner_user_id, state=state, limit=limit)
    except ValueError as error:
        raise HTTPException(400, "候审状态无效。") from error
    return JSONResponse({"proposals": [{**{key: row[key] for key in fields if key in row},
                                        **runtime.indexing_status(row.get("document_id"), row.get("version_id"))}
                                       for row in proposals]}, headers=_PRIVATE)


@router.get("/admin/documents")
def admin_documents(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    _actor(request, admin=True)
    runtime = _runtime()
    documents = runtime.catalog.admin_documents(limit=limit)
    return JSONResponse({"documents": [{**item, **runtime.indexing_status(item["document_id"], item["version_id"])}
                                       for item in documents]}, headers=_PRIVATE)


@router.post("/admin/documents/{document_id}/{version_id}/rights/{decision}")
def decide_rights(document_id: str, version_id: str, decision: str, body: RightsDecision, request: Request):
    owner = _actor(request, admin=True)
    runtime = _runtime()
    if decision not in {"grant", "revoke"} or not body.reason.strip():
        raise HTTPException(400, "请选择授权或撤回，并填写依据。")
    action = runtime.catalog.grant if decision == "grant" else runtime.catalog.revoke
    try:
        action(document_id, version_id, owner, reason=body.reason.strip())
    except LookupError as error:
        raise HTTPException(404, "文献版本不存在。") from error
    except (ValueError, PermissionError) as error:
        raise HTTPException(409, "该文献版本无法应用此授权操作。") from error
    return JSONResponse({"document_id": document_id, "version_id": version_id,
                         "rights_granted": runtime.catalog.is_visible(document_id, version_id),
                         **runtime.indexing_status(document_id, version_id)}, headers=_PRIVATE)

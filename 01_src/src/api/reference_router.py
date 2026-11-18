"""Owner/session-bound incremental reference audits for the PDF/chat workspace."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from src.core.account_leases import AccountUnavailable, account_lease, run_account_thread
from src.core.executors import ExecutorSaturatedError


class _AuditRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Keep this router's input-error contract when mounted alone, too.
                raise HTTPException(400, "参考文献请求参数无效或超过限制。") from None
        return validated


router = APIRouter(tags=["reference-audit"], route_class=_AuditRoute)
_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
_FORMATS = {"json": "application/json", "xlsx":
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "ris": "application/x-research-info-systems", "bibtex": "application/x-bibtex"}
_MAX_BYTES = 20 * 1024 * 1024


def _user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        from src.auth import user_for_token
        user = user_for_token(request.cookies.get("citrus_auth", ""))
    if not user:
        raise HTTPException(401, "请先登录。")
    return user


def _same_origin(request: Request):
    def origin_tuple(value):
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.path or parsed.query or parsed.fragment):
            raise ValueError("Invalid origin")
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        actual = origin_tuple(request.headers.get("origin", ""))
        expected = origin_tuple(request.url.scheme + "://" + request.headers.get("host", ""))
    except ValueError:
        raise HTTPException(403, "需要同源请求。") from None
    if actual != expected:
        raise HTTPException(403, "需要同源请求。")


def _owned_session(session_id: str, user: dict) -> int:
    from src.session.manager import session_manager
    owner = int(user["user_id"])
    if not session_manager.session_belongs_to(session_id, owner):
        raise HTTPException(404, "会话不存在或无权访问。")
    return owner


def _service():
    from src.research import reference_audit
    return reference_audit


@contextmanager
def _lease(owner):
    try:
        with account_lease(owner) as lease:
            yield lease
    except AccountUnavailable:
        raise HTTPException(409, "账号不可用。") from None


async def _call(user, session_id, operation, *args, **kwargs):
    def invoke():
        owner = _owned_session(session_id, user)
        from src.core.tracing import set_session_id
        set_session_id(session_id)
        return getattr(_service(), operation)(owner, session_id, *args, **kwargs)

    try:
        with _lease(int(user["user_id"])) as lease:
            # Child admission outlives a cancelled HTTP observer until the real
            # thread completes, including extraction, reads and export work.
            return await run_account_thread(lease, invoke)
    except ExecutorSaturatedError:
        raise HTTPException(409, "核验服务繁忙，请稍后重试。") from None
    except (PermissionError, LookupError) as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _response(value, *, status_code=200):
    return Response(json.dumps(value, ensure_ascii=False, allow_nan=False),
                    status_code=status_code, media_type="application/json", headers=_HEADERS)


class AuditCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="references.txt", min_length=1, max_length=255)
    data_url: str = Field(default="", max_length=28_000_000)
    text: str = Field(default="", max_length=2_000_000)
    references: list[dict] | None = Field(default=None, max_length=5000)


class AuditRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_rows: int = Field(default=20, ge=1, le=100, strict=True)
    time_budget: float = Field(default=45, gt=0, le=120, allow_inf_nan=False)
    resume: bool = Field(default=False, strict=True)


def _decode(data_url: str) -> bytes:
    raw = data_url
    if raw.startswith("data:"):
        header, separator, raw = raw.partition(",")
        if not separator or not header.lower().endswith(";base64"):
            raise HTTPException(400, "文件内容必须为 base64 数据。")
    if len(raw) > 4 * ((_MAX_BYTES + 2) // 3):
        raise HTTPException(400, "参考文献文件超过 20 MB 限制。")
    try:
        content = base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(400, "文件内容无法解码。") from None
    if not content or len(content) > _MAX_BYTES:
        raise HTTPException(400, "文件为空或超过 20 MB 限制。")
    return content


def _create_input(body):
    if sum((bool(body.data_url), bool(body.text.strip()), body.references is not None)) != 1:
        raise HTTPException(400, "请仅提供一种来源：文件、文本或参考文献条目。")
    if not body.name.strip() or any(ord(char) < 32 for char in body.name):
        raise HTTPException(400, "文件名无效。")
    if body.references is not None:
        try:
            raw = json.dumps(body.references, ensure_ascii=False, allow_nan=False)
            if not body.references or len(raw.encode("utf-8")) > 2_000_000:
                raise ValueError("Reference input limit")
            if any("claims" in item and not isinstance(item["claims"], dict) for item in body.references):
                raise ValueError("claims must be an object")
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(400, "参考文献条目为空、格式无效或超过 2 MB 限制。") from None
    return _decode(body.data_url) if body.data_url else None


def _rate(owner, operation, count):
    from src.auth import check_rate_limit
    if not check_rate_limit("reference_audit_" + operation, str(owner), count, 3600):
        raise HTTPException(429, "参考文献请求过于频繁，请稍后再试。")


@router.post("/api/v2/session/{session_id}/reference-audits")
async def create_audit(session_id: str, body: AuditCreate, request: Request):
    user = _user(request)
    _same_origin(request)
    content = _create_input(body)
    _rate(user["user_id"], "create", 30)
    return _response(await _call(user, session_id, "create_audit", name=body.name,
                                content=content, text=body.text, references=body.references))


@router.get("/api/v2/session/{session_id}/reference-audits")
async def list_audits(session_id: str, request: Request, limit: int = 20):
    user = _user(request)
    if not 1 <= limit <= 100:
        raise HTTPException(400, "列表数量须为 1 至 100。")
    return _response({"audits": await _call(user, session_id, "list_audits", limit=limit)})


@router.get("/api/v2/session/{session_id}/reference-audits/{audit_id}")
async def get_audit(session_id: str, audit_id: str, request: Request,
                    offset: int = 0, limit: int = 100):
    user = _user(request)
    if offset < 0 or not 1 <= limit <= 5000:
        raise HTTPException(400, "分页参数无效。")
    return _response(await _call(user, session_id, "get_audit", audit_id, offset=offset, limit=limit))


@router.post("/api/v2/session/{session_id}/reference-audits/{audit_id}/run")
async def run_audit(session_id: str, audit_id: str, body: AuditRun, request: Request):
    user = _user(request)
    _same_origin(request)
    _rate(user["user_id"], "run", 240)
    result = await _call(user, session_id, "run_audit", audit_id,
                         max_rows=body.max_rows, time_budget=body.time_budget, resume=body.resume)
    conflict = result.get("run_reason") in {"already_running", "lease_lost"}
    return _response(result, status_code=409 if conflict else 200)


@router.post("/api/v2/session/{session_id}/reference-audits/{audit_id}/cancel")
async def cancel_audit(session_id: str, audit_id: str, request: Request):
    user = _user(request)
    _same_origin(request)
    return _response(await _call(user, session_id, "cancel_audit", audit_id))


@router.get("/api/v2/session/{session_id}/reference-audits/{audit_id}/export")
async def export_audit(session_id: str, audit_id: str, request: Request, format: str = "json"):
    user = _user(request)
    fmt = format.lower()
    if fmt not in _FORMATS:
        raise HTTPException(400, "支持的导出格式为 json、xlsx、ris、bibtex。")
    _rate(user["user_id"], "export", 60)
    content, mime, filename = await _call(user, session_id, "export_audit", audit_id, format=fmt)
    return Response(content, media_type=mime or _FORMATS[fmt], headers={
        **_HEADERS, "Content-Disposition": f'attachment; filename="{filename}"'})

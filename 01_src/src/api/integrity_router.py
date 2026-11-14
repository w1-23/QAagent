"""Owner-bound evidence review API. Mount this router on the authenticated app."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from src.config import WEB_DIR
from src.core.account_leases import AccountUnavailable, account_lease, run_account_thread
from src.research import integrity_review as store

router = APIRouter(tags=["integrity-review"])
_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
_PAGE_HEADERS = {**_HEADERS, "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'; form-action 'none'"}


def _owner(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        from src.auth import user_for_token
        user = user_for_token(request.cookies.get("citrus_auth", ""))
    if not user:
        raise HTTPException(401, "请先登录。")
    return int(user["user_id"])


@contextmanager
def _lease(owner):
    try:
        with account_lease(owner) as lease:
            yield lease
    except AccountUnavailable:
        raise HTTPException(409, "账号不可用。") from None


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str
    note: str = Field(max_length=4000)
    expected_revision: int = Field(ge=0)


@router.get("/integrity-review")
async def review_page(request: Request):
    try:
        _owner(request)
    except HTTPException:
        return RedirectResponse("/", status_code=303)
    return FileResponse(WEB_DIR / "integrity-review.html", headers=_PAGE_HEADERS)


@router.get("/api/v2/integrity/reviews")
async def review_list(request: Request):
    owner = _owner(request)
    with _lease(owner) as lease:
        return JSONResponse({"reviews": await run_account_thread(lease, store.list_reviews, owner)}, headers=_HEADERS)


@router.get("/api/v2/integrity/reviews/{review_id}")
async def get_review(review_id: str, request: Request):
    owner = _owner(request)
    with _lease(owner) as lease:
        result = await run_account_thread(lease, store.get_review, owner, review_id)
    if not result:
        raise HTTPException(404, "复核记录不可用。")
    return Response(json.dumps(result, ensure_ascii=False, allow_nan=False), media_type="application/json", headers=_HEADERS)


@router.put("/api/v2/integrity/reviews/{review_id}/findings/{finding_id}")
async def decision(review_id: str, finding_id: str, body: Decision, request: Request):
    owner = _owner(request)
    # Also protect this router when mounted without main's middleware.
    from urllib.parse import urlsplit
    origin = request.headers.get("origin", "")
    if not origin or urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower():
        raise HTTPException(403, "需要同源请求。")
    try:
        with _lease(owner) as lease:
            return await run_account_thread(lease, store.decide, owner, review_id, finding_id,
                                            state=body.state, note=body.note, expected_revision=body.expected_revision)
    except store.ReviewConflict:
        raise HTTPException(409, "记录已改变，请刷新后重试。") from None
    except KeyError:
        raise HTTPException(404, "复核记录不可用。") from None
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


def _source(owner, review_id, finding_id, side):
    source = store.source_entry(owner, review_id, finding_id, side)
    if not source:
        raise HTTPException(404, "源文件已失效、变化或无权读取。")
    entry, loc = source
    limit = 20 * 1024 * 1024
    if int(entry.get("size_bytes", limit + 1)) > limit:
        raise HTTPException(413, "源文件超过预览预算，请从资料库下载复核。")
    try:
        with entry["path"].open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        raise HTTPException(404, "源文件不可用。") from None
    if len(data) > limit or hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise HTTPException(404, "源文件摘要不匹配。")
    return entry, loc, data


def _preview(owner, review_id, finding_id, side):
    entry, loc, data = _source(owner, review_id, finding_id, side)
    if loc.get("bbox"):
        from PIL import Image, ImageOps
        import warnings
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as original:
                    if original.width * original.height > 4_000_000 or getattr(original, "is_animated", False):
                        raise ValueError("image preview limit")
                    image = ImageOps.exif_transpose(original)
                    try:
                        box = loc["bbox"]
                        if (len(box) != 4 or any(type(v) is not int for v in box) or
                                not 0 <= box[0] < box[2] <= image.width or not 0 <= box[1] < box[3] <= image.height):
                            raise ValueError("invalid evidence bounds")
                        patch = image.crop(tuple(box))
                        try:
                            patch.thumbnail((960, 720))
                            output = io.BytesIO()
                            patch.save(output, format="PNG")
                            return output.getvalue(), "image/png"
                        finally:
                            patch.close()
                    finally:
                        image.close()
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise HTTPException(422, "该图片不能安全预览。") from None
    from src.research.tools import _load_frame_from_bytes
    try:
        frame, meta = _load_frame_from_bytes(data, entry["filename"], sheet_name=loc.get("sheet", ""),
                                             header_row=max(1, int(loc.get("header_row", 1))))
        header = int(meta.get("header_row") or loc.get("header_row") or 1)
        columns = loc.get("columns", [])[:8]
        rows = loc.get("rows", [])[:50]
        content = []
        for row in rows:
            index = row - header - 1
            if not 0 <= index < len(frame):
                continue
            cells = []
            for column in columns:
                col = column.get("index", 0) - 1
                cells.append(str(frame.iloc[index, col])[:500] if 0 <= col < len(frame.columns) else "")
            content.append({"row": row, "values": cells})
        result = {"filename": entry["filename"], "sheet": loc.get("sheet", ""),
                  "columns": columns, "rows": content, "coverage": "最多50行/8列；原文件位置以证据记录为准"}
        return json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json"
    except (ValueError, KeyError, TypeError, IndexError):
        raise HTTPException(422, "该来源无法显示表格区域，请在资料库复核原文件。") from None


@router.get("/api/v2/integrity/reviews/{review_id}/findings/{finding_id}/sources/{side}")
async def evidence_preview(review_id: str, finding_id: str, side: int, request: Request):
    owner = _owner(request)
    with _lease(owner) as lease:
        content, mime = await run_account_thread(lease, _preview, owner, review_id, finding_id, side)
    return Response(content, media_type=mime, headers={**_HEADERS, "Content-Security-Policy": "default-src 'none'; sandbox"})

"""Explicit application boundary for immutable artifact publication.

The legacy tool entry point remains the single persistence implementation.
HTTP/workspace callers use this adapter instead of writing bytes and inventing
artifact URLs themselves. A copied context prevents identity changes leaking
back into the calling thread or coroutine.
"""
from __future__ import annotations

import hashlib
from contextvars import copy_context
from typing import Any


def publish_artifact(*, owner: int, session_id: str, filename: str,
                     content: bytes, mime: str, producer_tool: str,
                     request_id: str = "", source_file_id: str = "",
                     source_sha256: str = "") -> dict[str, Any]:
    """Publish via the existing validated, hashed, cancellation-aware service.

    owner/session must come from the authenticated application context; callers
    must authorize the session before invoking this internal service.
    """
    if int(owner) <= 0 or not session_id:
        raise ValueError("缺少已授权账号或会话，无法发布文件。")

    def publish() -> dict[str, Any]:
        from src.core import tracing
        from src.research.tools import _save_artifact
        from src.session import files

        tracing.set_user_id(int(owner))
        tracing.set_session_id(session_id)
        tracing.set_current_tool_name(producer_tool)
        tracing.set_client_request_id(request_id)
        url = _save_artifact(filename, content, mime,
                             source_file_id=source_file_id,
                             source_sha256=source_sha256)
        artifact_id = url.rsplit("/", 1)[-1]
        entry = next((row for row in files.list_files(session_id, int(owner))
                      if row.get("artifact_id") == artifact_id), None)
        if not entry or not entry.get("file_id"):
            raise ValueError("产物发布后缺少文件登记，无法交付。")
        # _save_artifact may normalize OOXML, so its verified registered digest
        # is authoritative. Binary figure formats are unchanged by publication.
        if mime in {"image/svg+xml", "image/png", "application/pdf"}:
            if entry.get("sha256") != hashlib.sha256(content).hexdigest():
                raise ValueError("发布图件的摘要与渲染结果不一致。")
        return {
            "artifact_id": artifact_id, "file_id": entry["file_id"],
            "filename": entry.get("filename", filename),
            "sha256": entry.get("sha256", ""),
            "size": entry.get("size_bytes", 0),
            "download_url": entry.get("download_url", ""),
            "artifact_url": url,
        }

    return copy_context().run(publish)

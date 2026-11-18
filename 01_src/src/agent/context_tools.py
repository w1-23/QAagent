"""Authorized, paged readback of original working-context evidence."""
from __future__ import annotations

import hashlib
import json

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class ContextRead(BaseModel):
    checkpoint_id: str = Field(min_length=1, max_length=80)
    message_count: int = Field(ge=0)
    message_index: int = Field(ge=0)
    start_char: int = Field(default=0, ge=0)
    max_chars: int = Field(default=8000, ge=1, le=20000)


@tool(args_schema=ContextRead, response_format="content_and_artifact")
async def read_context_evidence(checkpoint_id: str, message_count: int,
                                message_index: int, start_char: int = 0,
                                max_chars: int = 8000):
    """Read an original public message from an encrypted context archive reference.

    Supply the checkpoint_id and message_count from the summary's readback receipt.
    message_index is zero-based. Continue using next_start_char until complete.
    Owner, session and run scope come from authenticated server context.
    """
    from src.config import PROJECT_ROOT, settings, get_deepseek_model
    from src.core import jobs, tracing
    from .identity import ExecutionIdentity
    from .context_projection import EncryptedContextArchive, ArchiveReference
    job = jobs.get_job(tracing.get_job_id())
    if (not job or job["owner_user_id"] != tracing.get_user_id()
            or job["session_id"] != tracing.get_session_id()
            or job["status"] not in {"running", "detached"}):
        raise PermissionError("context_archive_scope_unavailable")
    identity = ExecutionIdentity(job["owner_user_id"], job["session_id"],
                                 job["request_id"], job["job_id"])
    archive = EncryptedContextArchive(PROJECT_ROOT / "state", identity,
                                     (settings.RESOLVED_MAIN_BASE_URL, get_deepseek_model()))
    ref = ArchiveReference(archive.config["configurable"]["thread_id"], checkpoint_id, message_count)
    messages = await archive.read_evidence(ref, start=message_index, stop=message_index + 1)
    text = json.dumps(messages[0], ensure_ascii=False, separators=(",", ":"))
    if start_char > len(text):
        raise ValueError("context_archive_invalid_character_range")
    from src.core.tool_result_view import INLINE_CHARS
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    size = min(len(text) - start_char, max_chars)
    while True:
        end = start_char + size
        result = {"checkpoint_id": checkpoint_id, "message_index": message_index,
                  "message_count": message_count, "start_char": start_char, "end_char": end,
                  "total_chars": len(text), "next_start_char": end if end < len(text) else None,
                  "public_message_sha256": digest, "text": text[start_char:end],
                  "complete": end == len(text)}
        page = json.dumps(result, ensure_ascii=False)
        # Escape expansion can make a character slice exceed the host delivery cap.
        if len(page.encode("utf-8")) <= INLINE_CHARS:
            return page, {"_tool_outcome": {"status": "complete"}}
        if size <= 1:
            raise ValueError("context_archive_page_metadata_too_large")
        size //= 2


def register_context_reader(catalog):
    """Register code-owned schema; resolve the stateless reader only on dispatch."""
    from .capabilities import CapabilityDefinition
    def load():
        from src.tools.registry import register_tool
        register_tool(read_context_evidence, category="files", is_readonly=True,
                      recovery_mode="local_read")
        return read_context_evidence
    catalog.register(CapabilityDefinition(read_context_evidence.name, read_context_evidence.description,
        read_context_evidence.args_schema.model_json_schema(), load))

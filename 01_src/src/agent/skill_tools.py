"""Stateless installed-Skill tools with host-bound, request-local execution context."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from .capabilities import CapabilityDefinition
from .skill_adapter import SkillSession, _encode

_skill_session: ContextVar[SkillSession | None] = ContextVar("host_skill_session", default=None)


@contextmanager
def skill_execution_context(session: SkillSession):
    """Bind around HostTools.invoke; the guarded worker copies this request context.

    Never bind during catalog registration: global registry functions are shared
    by concurrent owners and safe-operation recovery. Unbound execution fails
    closed instead of falling back to another request's most recently registered
    session. Context reset also applies to exceptions and cancellation.
    """
    token = _skill_session.set(session)
    try:
        yield
    finally:
        _skill_session.reset(token)


def _current_session() -> SkillSession:
    session = _skill_session.get()
    if session is None:
        raise PermissionError("skill_execution_context_required")
    return session


class SkillLoad(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    skill_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SkillRead(SkillLoad):
    resource: str = Field(default="", max_length=300)
    start_char: int = Field(default=0, ge=0)
    max_chars: int = Field(default=3000, ge=1, le=3000)


@tool(args_schema=SkillRead, response_format="content_and_artifact")
def read_skill(skill_id: str, version: str, sha256: str, resource: str = "",
               start_char: int = 0, max_chars: int = 3000):
    """Read/load a verified installed skill resource page using catalog version/hash.

    Empty resource reads the entrypoint; optional resources use exact declared
    manifest paths. Follow next_start_char when more text is useful. Partial or
    missing reads never gate tools. Only host identity and authorization apply.
    A read failure is local and explicitly not_run; other tools remain usable.
    """
    session = _current_session()
    try:
        payload = session.page(skill_id, version, sha256, resource, start_char, max_chars)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return _failure(exc)
    return _encode(payload), {"_tool_outcome": {"status": "complete", "reason_code": "skill_page_read"}}


@tool(args_schema=SkillLoad, response_format="content_and_artifact")
def load_skill(skill_id: str, version: str, sha256: str):
    """Load a selected installed method and all required resources/dependencies.

    Use the exact catalog identity. Method documents are returned without loading
    the full vendor tree. Partial or missing reads never gate tools. Loading is
    guidance only and cannot execute or authorize domain tools. Use read_skill
    for optional manifest resources. Failures are local and explicitly not_run.
    """
    session = _current_session()
    try:
        payload = session.load([{"id": skill_id, "version": version, "sha256": sha256}])
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return _failure(exc)
    return _encode(payload), {"_tool_outcome": {"status": "complete", "reason_code": "skill_loaded"}}


def _failure(exc):
    outcome = {"status": "not_run", "reason_code": "skill_resource_unavailable"}
    import logging
    logging.getLogger(__name__).debug("Skill resource unavailable", exc_info=exc)
    return _encode({"error": outcome["reason_code"], "_tool_outcome": outcome}), {"_tool_outcome": outcome}


def _register(reader):
    from src.tools.registry import register_tool
    register_tool(reader, category="files", is_readonly=True, recovery_mode="local_read")
    return reader


def register_skill_reader(catalog):
    """Catalog factories capture only immutable code-owned tools, never sessions."""
    for reader in (read_skill, load_skill):
        catalog.register(CapabilityDefinition(reader.name, reader.description,
            reader.args_schema.model_json_schema(), partial(_register, reader)))

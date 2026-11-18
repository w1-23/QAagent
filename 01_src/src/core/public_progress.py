"""S2: public working notes (``assistant_progress``).

The model already streams assistant text next to its tool calls.  That text
has two very different meanings depending on how the *round* ends:

* round ends with tool calls -> the text is a **public working note**: the
  model telling the user what it is doing next.  It belongs to the execution
  timeline only.
* round ends without tool calls -> the text **is** the final answer and keeps
  using the existing ``text`` path.

Mixing the two (the "``on_text`` -> ``emit_text``" shortcut) pollutes the
answer, duplicates content and can persist progress text as the reply.  This
module therefore classifies at round level and never touches the answer path.

Hard rules:
* private ``reasoning_content`` is never a source here;
* output is sanitized (no DSML/tool protocol, no citation markers, bounded
  length) and rejected when it looks like credential material;
* every failure here is swallowed: public progress must never block the agent.
"""

from __future__ import annotations

from contextvars import ContextVar
import os
import re
import uuid

from src.core.progress_bus import emit_progress, get_turn_scope

#: Longest single public note kept. The timeline row is one line; longer text
#: is truncated instead of silently blowing up the trace.
MAX_CHARS = 400

_ENV_FLAG = "CITRUS_PUBLIC_PROGRESS"

# DSML / tool-protocol leakage (providers occasionally emit these as text).
_DSML_BLOCK_RE = re.compile(
    r"<[^>]{0,40}?\bDSML\b[^>]{0,40}?>.*?</[^>]{0,40}?\bDSML\b[^>]{0,40}?>",
    re.I | re.S)
_PROTOCOL_TAG_RE = re.compile(
    r"</?\s*(?:[|｜]{1,2}\s*DSML\s*[|｜]{1,2}\s*)?(?:calls|invoke|parameter)\b[^>]*>",
    re.I)
_TOOL_MARKER_RE = re.compile(r"\[(?:TOOL_[A-Z_]+|[Ww]?\d{1,3})\]")

# Credential-ish material must never reach the timeline.
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bapi[_\- ]?key\b\s*[:=]"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token)\b\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{60,}={0,2}\b"),
)

_WHITESPACE_RE = re.compile(r"\s+")

# Completion declarations are private validation inputs, including truncated or
# fenced JSON. Reject before truncation so an envelope cannot become a note.
_COMPLETION_RE = re.compile(
    r'"(?:schema_version|answer|incidental_partials|proposal_repairs)"\s*:|\bCompletionEnvelope\b',
    re.I)

#: Immutable request-local state: inherited task contexts never mutate a shared
#: default/list. Include the request/job scope for sequential requests as well.
_last_emitted: ContextVar[tuple | None] = ContextVar("public_progress_last_emitted", default=None)


def enabled() -> bool:
    """Public working notes are on unless explicitly disabled."""
    return os.getenv(_ENV_FLAG, "1").strip().lower() not in ("0", "false", "off", "no")


def sanitize(text: str) -> str:
    """Return a single-line, bounded, credential-free public note or ``\"\"``."""
    raw = str(text or "")
    if not raw.strip() or _COMPLETION_RE.search(raw):
        return ""
    cleaned = _DSML_BLOCK_RE.sub(" ", raw)
    cleaned = _PROTOCOL_TAG_RE.sub("", cleaned)
    cleaned = _TOOL_MARKER_RE.sub("", cleaned)
    for pattern in _SECRET_PATTERNS:
        if pattern.search(cleaned):
            return ""
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    # Leading punctuation only ("…") is noise, not a note.
    if len(cleaned) <= 1:
        return ""
    if len(cleaned) > MAX_CHARS:
        cleaned = cleaned[: MAX_CHARS - 1].rstrip() + "…"
    return cleaned


def public_content_text(content) -> str:
    """Extract public text blocks without exposing provider reasoning or data."""
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)):
        return ""
    return "".join(
        block if isinstance(block, str) else block["text"]
        for block in content
        if isinstance(block, str) or (
            isinstance(block, dict) and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    )


def public_progress_text(chunks, has_tool_calls: bool) -> str:
    """Classify one model round: working note vs final answer.

    Returns the sanitized note for a tool-calling round and ``\"\"`` for the
    final round, so the answer path is never fed progress text.
    """
    if not has_tool_calls:
        return ""
    if not chunks:
        return ""
    try:
        return sanitize(public_content_text(chunks))
    except Exception:  # noqa: BLE001 - progress must never break execution
        return ""


def emit_public_progress(text: str, *, step_id: str = "",
                         phase: str = "working", note_id: str = "",
                         model_step: int = 0, tool_call_ids=(),
                         job_id: str = "") -> bool:
    """Publish a sanitized note through the existing request/durable queue.

    ``job_id`` is host identity, never a model argument. DurableEventQueue owns
    persistence; this emitter must not append the same event separately.
    """
    try:
        note = sanitize(text)
        if not note or not enabled():
            return False
        from src.core.tracing import get_job_id
        job_id = str(job_id or get_job_id() or "")
        scope = (*get_turn_scope(), job_id)
        if _last_emitted.get() == (scope, note):
            return False
        emit_progress("assistant_progress", {
            "visibility": "public", "origin": "model",
            "step_id": step_id or "analysis",
            "phase": phase,
            "text": note,
            "chars": len(note),
            "note_id": note_id or uuid.uuid4().hex,
            "model_step": max(0, int(model_step)),
            "tool_call_ids": list(tool_call_ids),
            "job_id": job_id,
        })
        _last_emitted.set((scope, note))
    except Exception:  # noqa: BLE001 - never block the agent
        return False
    _persist_last_note(note, job_id=job_id)
    return True


_PROGRESS_SUMMARY_MAX = 200


def _persist_last_note(note: str, *, job_id: str | None = None) -> None:
    """Persist the last public note; an explicit empty host scope stays empty.

    Direct legacy callers may omit job_id and use their current bound request.
    The event emitter always passes its resolved identity, including an empty
    value, so a late context change cannot redirect an already classified note.
    """
    try:
        if job_id is None:
            from src.core.tracing import get_job_id
            job_id = str(get_job_id() or "")
        if not job_id:
            return
        from src.core import jobs as jobs_mod
        jobs_mod.update_job(job_id, progress_summary=note[:_PROGRESS_SUMMARY_MAX])
    except Exception:  # noqa: BLE001 - 进度持久化绝不能影响执行
        return


def reset_emitted() -> None:
    """Clear per-request dedupe state (called when a request finishes)."""
    _last_emitted.set(None)

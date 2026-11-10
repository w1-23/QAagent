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

import os
import re

from src.core.progress_bus import emit_progress

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

#: Consecutive identical notes are emitted once per request (retries repeat).
_last_emitted: list[str] = []


def enabled() -> bool:
    """Public working notes are on unless explicitly disabled."""
    return os.getenv(_ENV_FLAG, "1").strip().lower() not in ("0", "false", "off", "no")


def sanitize(text: str) -> str:
    """Return a single-line, bounded, credential-free public note or ``\"\"``."""
    raw = str(text or "")
    if not raw.strip():
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
        return sanitize("".join(str(c) for c in chunks if c))
    except Exception:  # noqa: BLE001 - progress must never break execution
        return ""


def emit_public_progress(text: str, *, step_id: str = "",
                         phase: str = "working") -> bool:
    """Emit ``assistant_progress`` to the current request's timeline."""
    note = sanitize(text)
    if not note:
        return False
    if not enabled():
        return False
    if _last_emitted and _last_emitted[-1] == note:
        return False
    try:
        emit_progress("assistant_progress", {
            "step_id": step_id or "analysis",
            "phase": phase,
            "text": note,
            "chars": len(note),
        })
        _last_emitted.append(note)
        del _last_emitted[:-20]
    except Exception:  # noqa: BLE001 - never block the agent
        return False
    # 运行期时间线只进 SSE；另外把"最后一条进度"落进任务行，
    # 这样刷新页面或断线重连后，仍能看到上一轮跑到哪一步（不是完整时间线，
    # 完整时间线仍在消息历史里，见报告 S2 一节）。
    _persist_last_note(note)
    return True


_PROGRESS_SUMMARY_MAX = 200


def _persist_last_note(note: str) -> None:
    """把最后一条公开进度写进 task_jobs.progress_summary（失败静默）。"""
    try:
        from src.core import jobs as jobs_mod
        from src.core.tracing import get_job_id

        job_id = str(get_job_id() or "")
        if not job_id:
            return
        jobs_mod.update_job(job_id, progress_summary=note[:_PROGRESS_SUMMARY_MAX])
    except Exception:  # noqa: BLE001 - 进度持久化绝不能影响执行
        return


def reset_emitted() -> None:
    """Clear per-request dedupe state (called when a request finishes)."""
    _last_emitted.clear()

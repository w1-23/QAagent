# -*- coding: utf-8 -*-
"""Synchronous, request-scoped local prefetch for the candidate pool.

This node runs between ``session_load`` and ``supervisor``.  It performs at most
one local hybrid search when the question is about a registered corpus family,
stores the resulting chunks in process memory, and returns only a metadata
snapshot plus a count/family notice.  Candidate text is never placed in the
model messages; the supervisor exposes it through bounded candidate tools.

The node is deliberately fail-open: every gate and every failure returns a
truthful status instead of raising, so a prefetch problem can never fail the
turn.  The model can always fall back to ``local_hybrid_search``.
"""
from __future__ import annotations

import asyncio
import contextvars
import math
import time

from src.config import settings
from src.core.tracing import get_request_id
from src.graph.state import AgentState
from src.retrieval.candidate_pool import (
    bind_candidate_pool,
    build_candidate_pool,
    clear_current_candidate_pool,
)

NODE_NAME = "candidate_prefetch"

# Statuses surfaced in AgentState/traces. They are intentionally distinct so a
# "not executed" step is never reported as "the corpus has no such material".
STATUS_READY = "ready"
STATUS_ZERO_HIT = "zero_hit"
STATUS_DISABLED = "disabled"
STATUS_SKIPPED = "skipped"
STATUS_NO_FAMILY = "no_matching_family"
STATUS_CORPUS_UNAVAILABLE = "corpus_unavailable"
STATUS_TRANSLATION_ERROR = "translation_error"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"

_NOTICE_TRANSLATION_ERROR = (
    "本地语料预取未执行：中文查询未能转换为英文（失败关闭，未发送 BM25/Dense 请求）。"
    "不要声称刚刚检索过本地语料；可改用英文关键词调用 local_hybrid_search。"
)
_NOTICE_TIMEOUT = (
    "本地语料预取超时（{seconds:.0f} 秒），本轮没有候选池。"
    "需要时请显式调用 local_hybrid_search；不要声称已在语料中查过。"
)
_NOTICE_ERROR = (
    "本地语料预取失败（原因已记录），本轮没有候选池。"
    "需要时请显式调用 local_hybrid_search；不要声称已在语料中查过。"
)
_NOTICE_CORPUS = (
    "本地语料索引未就绪或目标语料族未加载，本轮没有候选池。"
    "不要声称检索过本地语料；如需确认可显式调用 local_hybrid_search。"
)


def _snapshot(status: str, *, reason: str = "", families=(), error: str = "",
              elapsed_ms: float = 0.0, translation_used: bool = False) -> dict:
    return {
        "pool_id": "",
        "status": status,
        "count": 0,
        "families": list(families),
        "translation_used": bool(translation_used),
        "elapsed_ms": round(float(elapsed_ms or 0.0), 1),
        "error": str(error or "")[:160],
        "reason": reason,
    }


def _response(snapshot: dict, notice: str = "") -> dict:
    return {
        "candidate_pool": snapshot,
        "candidate_pool_notice": notice,
        "_trace": {
            "node": NODE_NAME,
            "status": snapshot.get("status", ""),
            "reason": snapshot.get("reason", ""),
            "count": snapshot.get("count", 0),
            "families": list(snapshot.get("families") or ()),
            "translation_used": bool(snapshot.get("translation_used")),
            "elapsed_ms": snapshot.get("elapsed_ms", 0.0),
            "error": snapshot.get("error", ""),
        },
    }


def _diag(**values) -> None:
    try:
        from src.core.diag import diag

        diag(NODE_NAME, **values)
    except Exception:
        pass


def _families_for(registry, query: str) -> tuple[set[str], str, bool]:
    """Match families on the query; translate once if the raw query misses.

    Returns ``(families, normalized_query, translation_used)``.  A ``None``
    normalized query means translation failed closed.
    """
    families = set(registry.matching_families(query))
    if families:
        return families, query, False
    from src.retrieval.local_query_language import normalize_local_query

    normalized, info = normalize_local_query(query)
    if normalized is None:
        return set(), "", False
    translated = bool(info.get("translation_used"))
    if normalized and normalized != query:
        families = set(registry.matching_families(normalized))
    return families, normalized or query, translated


def _run_search(query: str, families: list[str], limit: int) -> dict:
    from src.tools.deepseek_search_fetch import search_local_candidates

    return search_local_candidates(query, families, limit=limit)


async def prefetch_candidates(state: AgentState) -> dict:
    """Run the request-scoped local prefetch. Never raises."""
    try:
        return await _prefetch(state)
    except Exception as exc:  # noqa: BLE001 - prefetch must never fail a turn
        _diag(status=STATUS_ERROR, error=f"{type(exc).__name__}: {exc}")
        return _response(_snapshot(STATUS_ERROR, reason="exception",
                                   error=f"{type(exc).__name__}: {exc}"),
                         _NOTICE_ERROR)


async def _prefetch(state: AgentState) -> dict:
    clear_current_candidate_pool()
    query = str(state.get("query") or "").strip()
    if not bool(getattr(settings, "CANDIDATE_PREFETCH_ENABLED", True)):
        return _response(_snapshot(STATUS_DISABLED, reason="setting_disabled"))
    if not query:
        return _response(_snapshot(STATUS_SKIPPED, reason="empty_query"))

    from src.graph.retrieval_router import (
        decide_retrieval,
        parse_request_constraints,
        source_scope,
    )
    from src.graph.tool_policy import resolve_tool_policy

    scope = source_scope(query)
    if scope == "web":
        return _response(_snapshot(STATUS_SKIPPED, reason="scope_web"))

    constraints = parse_request_constraints(query, scope)
    if (constraints.source_policy == "deny"
            or constraints.pure_transform
            or constraints.reason_code == "data_audit_only"):
        return _response(_snapshot(STATUS_SKIPPED,
                                   reason=constraints.reason_code or "constraint"))

    paper_only = False
    if bool(state.get("has_paper_attachment")):
        # Lazy import avoids a module cycle: general_agent_graph imports this
        # node at module load, but this helper is only needed at request time.
        from src.graph.general_agent_graph import _read_uploaded_paper_without_search

        paper_only = _read_uploaded_paper_without_search(
            query, scope, True)
    policy = resolve_tool_policy(query, scope, paper_only=paper_only)
    if not policy.allow_retrieval or paper_only:
        return _response(_snapshot(STATUS_SKIPPED,
                                   reason="paper_only" if paper_only else "no_retrieval"))

    try:
        from src.retrieval.corpus_registry import load_corpus_registry

        registry = load_corpus_registry()
    except Exception as exc:  # noqa: BLE001
        return _response(_snapshot(STATUS_CORPUS_UNAVAILABLE, reason="registry_error",
                                   error=f"{type(exc).__name__}: {exc}"),
                         _NOTICE_CORPUS)

    families0 = set(registry.matching_families(query))

    # Social / memory-command turns must not trigger a translation call. Ask
    # the router first with what we know; a topic question that merely misses a
    # routing term is handled below and is NOT skipped by mode=="none".
    recent_turns: tuple[int, ...] = ()
    session_id = str(state.get("session_id") or "")
    try:
        from src.session.manager import session_manager

        recent_turns = tuple(session_manager.recent_evidence_turns(session_id, limit=2))
    except Exception:
        recent_turns = ()
    early = decide_retrieval(
        query, scope,
        has_history_evidence=bool(state.get("history_evidence_block")),
        recent_turn_seqs=recent_turns,
        local_corpus_match=bool(families0),
    )
    if early.reason_code in {
            "no_retrieval_or_social", "transformation_from_context",
            "memory_write_only", "recent_evidence_reuse"}:
        return _response(_snapshot(STATUS_SKIPPED, reason=early.reason_code))

    families, normalized, translated = _families_for(registry, query)
    if normalized == "" and not families:
        return _response(_snapshot(STATUS_TRANSLATION_ERROR,
                                   reason="translation_failed"),
                         _NOTICE_TRANSLATION_ERROR)
    if not families:
        return _response(_snapshot(STATUS_NO_FAMILY, reason="no_matching_family"))

    # Re-evaluate with the translated family match. The router may now say
    # "fusion" instead of "web"; a time-sensitive web query still skips local
    # prefetch because stale corpus evidence is not what the user asked for.
    decision = decide_retrieval(
        query, scope,
        has_history_evidence=bool(state.get("history_evidence_block")),
        recent_turn_seqs=recent_turns,
        local_corpus_match=True,
    )
    if decision.mode == "reuse" or decision.reason_code in {
            "no_retrieval_or_social", "transformation_from_context",
            "memory_write_only", "recent_evidence_reuse"}:
        return _response(_snapshot(STATUS_SKIPPED, reason=decision.reason_code))
    if (decision.mode == "web" and scope not in {"local", "fusion"}
            and decision.reason_code != "explicit_search"):
        # A time-sensitive / latest web query should not be answered from a
        # stale corpus prefetch. A generic "检索一下…" is different: the user
        # asked for search, not specifically for the web, so local prefetch is
        # still useful and the model can combine both sources.
        return _response(_snapshot(STATUS_SKIPPED, reason="time_sensitive_web"))

    deadline = state.get("request_deadline")
    if deadline:
        try:
            deadline = float(deadline)
        except (TypeError, ValueError):
            deadline = math.inf
    else:
        from src.graph.general_agent_graph import request_deadline_from_settings

        deadline = float(request_deadline_from_settings())
    timeout = max(5.0, float(getattr(settings, "CANDIDATE_PREFETCH_TIMEOUT_SEC", 45)))
    if math.isfinite(deadline):
        from src.graph.general_agent_graph import _final_reserve_sec

        remaining = deadline - time.monotonic()
        reserve = float(_final_reserve_sec())
        if remaining <= reserve + 2:
            return _response(_snapshot(STATUS_SKIPPED, reason="deadline_too_short",
                                       families=sorted(families)))
        timeout = min(timeout, max(5.0, remaining - reserve))

    started = time.monotonic()
    try:
        from src.core.executors import TOOL_EXECUTOR

        ctx = contextvars.copy_context()
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            TOOL_EXECUTOR, ctx.run, _run_search,
            query, sorted(families),
            max(1, int(getattr(settings, "CANDIDATE_POOL_SIZE", 12) or 12)))
        artifact = await asyncio.wait_for(future, timeout=timeout)
    except (asyncio.TimeoutError, TimeoutError):
        elapsed_ms = (time.monotonic() - started) * 1000
        _diag(status=STATUS_TIMEOUT, families=sorted(families), timeout_sec=timeout,
              elapsed_ms=round(elapsed_ms, 1))
        return _response(
            _snapshot(STATUS_TIMEOUT, reason="timeout", families=sorted(families),
                      elapsed_ms=elapsed_ms, error=f"prefetch exceeded {timeout:.0f}s"),
            _NOTICE_TIMEOUT.format(seconds=timeout))
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.monotonic() - started) * 1000
        _diag(status=STATUS_ERROR, families=sorted(families),
              error=f"{type(exc).__name__}: {exc}", elapsed_ms=round(elapsed_ms, 1))
        return _response(
            _snapshot(STATUS_ERROR, reason="exception", families=sorted(families),
                      error=f"{type(exc).__name__}: {exc}", elapsed_ms=elapsed_ms),
            _NOTICE_ERROR)

    artifact = artifact if isinstance(artifact, dict) else {}
    rows = artifact.get("main_results") or []
    local_status = str(artifact.get("local_status") or "")
    elapsed_ms = float(artifact.get("elapsed_ms") or (time.monotonic() - started) * 1000)
    translation_used = bool(artifact.get("translation_used")) or translated

    if local_status in {"translation_error"}:
        clear_current_candidate_pool()
        return _response(
            _snapshot(STATUS_TRANSLATION_ERROR, reason="translation_failed",
                      families=sorted(families), elapsed_ms=elapsed_ms),
            _NOTICE_TRANSLATION_ERROR)
    if local_status in {"unavailable_family", "no_authorized_corpus"}:
        clear_current_candidate_pool()
        return _response(
            _snapshot(STATUS_CORPUS_UNAVAILABLE, reason=local_status,
                      families=sorted(families), elapsed_ms=elapsed_ms,
                      error=str(artifact.get("error") or "")),
            _NOTICE_CORPUS)
    if local_status in {"invalid_family", "invalid_topic"}:
        clear_current_candidate_pool()
        return _response(
            _snapshot(STATUS_ERROR, reason=local_status, families=sorted(families),
                      elapsed_ms=elapsed_ms,
                      error=str(artifact.get("error") or "")),
            _NOTICE_ERROR)

    status = STATUS_READY if (local_status == "hit" or rows) else STATUS_ZERO_HIT
    if local_status == "skipped_no_matching_family":
        return _response(_snapshot(STATUS_NO_FAMILY, reason="skipped_no_matching_family",
                                   families=sorted(families)))

    pool = build_candidate_pool(
        rows=rows,
        request_id=str(state.get("request_id") or get_request_id() or ""),
        session_id=session_id,
        query=query,
        translation_used=translation_used,
        families=sorted(families),
        elapsed_ms=elapsed_ms,
        size=int(getattr(settings, "CANDIDATE_POOL_SIZE", 12) or 12),
    )
    pool.status = status
    bind_candidate_pool(pool)
    snapshot = pool.summary()
    _diag(status=status, families=sorted(families), count=snapshot["count"],
          translation_used=translation_used, elapsed_ms=snapshot["elapsed_ms"])
    return _response(snapshot, pool.notice())


__all__ = [
    "NODE_NAME", "prefetch_candidates",
    "STATUS_READY", "STATUS_ZERO_HIT", "STATUS_DISABLED", "STATUS_SKIPPED",
    "STATUS_NO_FAMILY", "STATUS_CORPUS_UNAVAILABLE", "STATUS_TRANSLATION_ERROR",
    "STATUS_TIMEOUT", "STATUS_ERROR",
]

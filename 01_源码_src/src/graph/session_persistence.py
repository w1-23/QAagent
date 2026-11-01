"""Persistence for one General Agent turn."""
from __future__ import annotations

import asyncio
import logging
import time

from langchain_core.messages import AIMessage

from src.core.evidence import EVIDENCE_SNIPPET_MAX_CHARS, clean_doi, render_evidence, src_of

logger = logging.getLogger(__name__)


def _save_account_memory_from_input(query: str, session_id: str,
                                    user_id: int, request_id: str) -> dict:
    """Save explicit/stable user statements without sending them to an LLM."""
    from src.guardrails.account_memory_policy import extract_memory_candidates
    from src.guardrails.memory import memory_store

    candidates = extract_memory_candidates(query)
    if not candidates:
        return {"saved": 0, "failed": 0, "queued": 0}
    try:
        memory_store.drain_memory_retry_queue(user_id)
    except Exception:
        pass
    saved = failed = queued = 0
    for candidate in candidates:
        if candidate.kind == "preference":
            ok = memory_store.set_preference(
                user_id, candidate.key, candidate.value)
        else:
            ok = memory_store.save_long_term_fact(
                candidate.key, candidate.value, candidate.confidence,
                owner_session=session_id, source_query=query,
                owner_user_id=user_id)
        if ok:
            saved += 1
            continue
        failed += 1
        if memory_store.queue_memory_retry(
                user_id=user_id, kind=candidate.kind,
                fact_key=candidate.key, fact_value=candidate.value,
                confidence=candidate.confidence, source_query=query,
                session_id=session_id, request_id=request_id,
                error="memory_write_failed"):
            queued += 1
    return {"saved": saved, "failed": failed, "queued": queued}


async def persist_agent_turn(state: dict) -> dict:
    """Save the answer, refresh-safe elapsed time, evidence and account memory."""
    query = str(state.get("query") or "")
    answer = str(state.get("answer") or "")
    session_id = str(state.get("session_id") or "default")
    if not answer:
        return {"_trace": {"node": "session_save", "elapsed_ms": 0,
                           "summary": "no answer", "history_saved": False,
                           "generation_ms": 0}}

    saved = False
    generation_ms = 0
    memory_result = {"saved": 0, "failed": 0, "queued": 0}
    try:
        from src.session.manager import session_manager, _validate_trace

        trace = [
            message for message in (state.get("turn_trace") or [])
            if message is not None and getattr(message, "type", "") != "system"
            and not str(getattr(message, "content", "")).startswith("[历史检索证据]")
        ]
        trace = _validate_trace(trace)
        if not (trace and isinstance(trace[-1], AIMessage)
                and not getattr(trace[-1], "tool_calls", None)
                and trace[-1].content == answer):
            trace.append(AIMessage(content=answer))

        try:
            started_at = float(state.get("request_started_at") or 0)
            generation_ms = int(max(0.0, time.perf_counter() - started_at) * 1000)
        except (TypeError, ValueError):
            generation_ms = int(max(0, state.get("gen_time_ms") or 0))
        if generation_ms > 0:
            metadata = dict(getattr(trace[-1], "response_metadata", {}) or {})
            metadata["citrus_generation_ms"] = generation_ms
            trace[-1].response_metadata = metadata

        saved = await session_manager.save_messages(
            session_id, trace, state.get("idempotency_key", ""))
        if not saved:
            logger.warning("[GeneralAgent:save] conversation history was not written")

        main_results = state.get("main_results") or []
        web_results = state.get("web_results") or []
        if main_results or web_results:
            evidence = [
                {
                    "doi": clean_doi(result.get("doi")),
                    "record_key": str(result.get("record_key") or result.get("paper_id") or ""),
                    "chunk_id": f"{result.get('paper_id', '')}:{result.get('chunk_index', '')}",
                    "title": str(result.get("title", ""))[:150],
                    "score": result.get("score", result.get("rerank_score", 0)) or 0,
                    "year": str(result.get("year", "")),
                    "source": src_of(result),
                    "snippet": render_evidence(result, max_chars=EVIDENCE_SNIPPET_MAX_CHARS),
                }
                for result in main_results[:30]
            ]
            evidence.extend(
                {
                    "doi": "",
                    "chunk_id": str(result.get("url", ""))[:200],
                    "title": str(result.get("title", ""))[:150],
                    "score": float(result.get("score", 0) or 0),
                    "year": "",
                    "source": "web",
                    "snippet": str(result.get("abstract", result.get("snippet", "")))[:500],
                    "url": str(result.get("url", "")),
                }
                for result in web_results[:30]
            )
            try:
                from src.core.web_output_separator import clean_web_title
                for entry in evidence:
                    if entry.get("url"):
                        entry["title"] = clean_web_title(
                            entry.get("title"), entry.get("url"))
            except Exception:
                pass
            refs_data = state.get("references_data") or {}
            cited_refs = refs_data.get("cited") if isinstance(refs_data, dict) else None
            from src.core.background import spawn

            spawn(session_manager.save_evidence(session_id, query, evidence, "", cited_refs))
    except Exception as exc:  # saving must not discard a completed answer
        logger.warning("[GeneralAgent:save] failed: %s", exc)

    try:
        memory_result = await asyncio.to_thread(
            _save_account_memory_from_input, query, session_id,
            int(state.get("user_id") or 0),
            str(state.get("idempotency_key") or ""))
    except Exception as exc:
        logger.warning("[GeneralAgent:save] account memory save failed: %s", exc)
        memory_result = {"saved": 0, "failed": 1, "queued": 0}

    return {"_trace": {
        "node": "session_save", "elapsed_ms": 0,
        "summary": "saved" if saved else "already saved or skipped",
        "history_saved": bool(saved), "generation_ms": generation_ms,
        "memory_saved": int(memory_result.get("saved") or 0),
        "memory_failed": int(memory_result.get("failed") or 0),
        "memory_queued": int(memory_result.get("queued") or 0),
    }}

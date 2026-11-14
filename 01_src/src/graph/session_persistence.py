"""Persistence for one General Agent turn."""
from __future__ import annotations

import asyncio
import logging
import time

from langchain_core.messages import AIMessage

from src.core.evidence import EVIDENCE_SNIPPET_MAX_CHARS, clean_doi, render_evidence, src_of

logger = logging.getLogger(__name__)


async def persist_agent_turn(state: dict) -> dict:
    """Save the answer, refresh-safe elapsed time, evidence and selected sources."""
    query = str(state.get("query") or "")
    answer = str(state.get("answer") or "")
    session_id = str(state.get("session_id") or "default")
    if not answer:
        return {"_trace": {"node": "session_save", "elapsed_ms": 0,
                           "summary": "no answer", "history_saved": False,
                           "generation_ms": 0}}

    saved = False
    generation_ms = 0
    try:
        from src.session.manager import session_manager, _validate_trace

        trace = [
            message for message in (state.get("turn_trace") or [])
            if message is not None and getattr(message, "type", "") != "system"
            and not str(getattr(message, "content", "")).startswith("[历史检索证据]")
        ]
        trace = _validate_trace(trace)
        # The last assistant text is the final synthesis message. Delivery-gate
        # work and finalization can change the answer after that message was
        # produced (verified links, honest partial notices), so update it in
        # place instead of appending a second assistant row: one turn has one
        # answer, and a refresh must show exactly the finalized text.
        if (trace and isinstance(trace[-1], AIMessage)
                and not getattr(trace[-1], "tool_calls", None)):
            if str(trace[-1].content or "") != answer:
                trace[-1] = trace[-1].model_copy(update={"content": answer})
        else:
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

        from src.core.account_leases import (
            current_account_lease, run_account_async, create_account_task,
        )
        lease = current_account_lease()
        write = session_manager.save_messages(
            session_id, trace, state.get("idempotency_key", ""))
        saved = await run_account_async(lease, write) if lease is not None else await write
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
            from src.core.background import adopt, spawn

            write = session_manager.save_evidence(session_id, query, evidence, "", cited_refs)
            if lease is not None:
                adopt(create_account_task(lease, write))
            else:
                spawn(write)
    except Exception as exc:  # saving must not discard a completed answer
        logger.warning("[GeneralAgent:save] failed: %s", exc)

    return {"_trace": {
        "node": "session_save", "elapsed_ms": 0,
        "summary": "saved" if saved else "already saved or skipped",
        "history_saved": bool(saved), "generation_ms": generation_ms,
        "memory_saved": 0, "memory_failed": 0, "memory_queued": 0,
    }}

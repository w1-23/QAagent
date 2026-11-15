"""Shared conversation context loading and persistence nodes."""
from __future__ import annotations

import asyncio
import logging
import math
import time

from src.config import settings
from src.graph.state import AgentState

logger = logging.getLogger(__name__)


async def load_session_context(state: AgentState) -> dict:
    """Load history and memory once for the unified Agent graph."""
    query = state.get("query", "")
    session_id = state.get("session_id", "default")
    mode = "general_agent"
    result: dict = {}
    from src.core.progress_bus import emit_status, emit_encoded

    def _status(stage: str, **kwargs) -> None:
        """A progress notice must never take the request down with it.

        ``emit_status`` takes the stage positionally; passing a second
        ``stage=`` keyword raised TypeError *before* the history load ran, and
        the broad except below turned every turn into "no history loaded".
        Notifications are now isolated, so a future event-shape mistake costs
        one progress line, not the conversation context.
        """
        try:
            emit_status(stage, **kwargs)
        except Exception:  # noqa: BLE001 - notices are not execution
            logger.debug("[SessionNodes] status notice failed", exc_info=True)

    try:
        from src.session.manager import session_manager
        from src.core.context_manager import (ContextManager, build_context_budget,
                                              finalize_load_result)
        from src.guardrails.memory import memory_store

        budget = build_context_budget()
        ctx_mgr = ContextManager(
            session_manager=session_manager,
            memory_store=memory_store,
            budget=budget,
        )
        started = time.perf_counter()
        _status("step_active", step_id="load")
        _status("agent_progress", stage_label="读取会话",
                message="正在读取本会话的历史消息与已保存材料。")
        load_coro = ctx_mgr.load(
            session_id, query, mode,
            user_id=int(state.get("user_id") or 0),
            generate_hints=not bool(state.get("agent_no_prefetch_hints")))
        deadline = float(state.get("request_deadline") or 0.0)
        if (state.get("agent_no_prefetch_hints") and deadline
                and math.isfinite(deadline)):
            # An unbounded request budget must stay unbounded here too;
            # asyncio.wait_for(inf) would still arm a timer for no reason.
            remaining = max(0.1, deadline - time.monotonic() - 25.0)
            ctx = await asyncio.wait_for(load_coro, timeout=remaining)
        else:
            ctx = await load_coro
        result = finalize_load_result(
            ctx,
            session_manager=session_manager,
            session_id=session_id,
            budget=budget,
            node_label="session_load",
            log_prefix="GeneralAgent",
        )
        # A durable answer carries newer working refs than a possibly interrupted
        # parent session save. Keep that host-owned state across context loading.
        if state.get("continuation_context"):
            carried = state.get("context_working_state") or {}
            working = dict(result.get("context_working_state") or {})
            for key in ("dataset_refs", "source_refs", "script_refs", "run_refs"):
                merged = list(working.get(key) or [])
                for ref in carried.get(key) or []:
                    if ref not in merged:
                        merged.append(ref)
                working[key] = merged
            working.update({key: value for key, value in carried.items()
                            if key not in {"dataset_refs", "source_refs", "script_refs", "run_refs"}})
            result["context_working_state"] = working
        _status("step_done", step_id="load")
        if ctx.history_load_status == "failed":
            _status("agent_progress", stage_label="历史读取失败",
                    message="历史对话未能装入，本轮按当前问题继续。")
            try:
                emit_encoded("context_degraded", {"stage": "history_load", "status": "failed",
                    "reason_code": "history_load_failed", "message": "本轮历史读取失败，不能依赖既往对话。"})
            except Exception:
                pass
        else:
            _status("agent_progress", stage_label="会话已读取",
                    message=(f"已装入 {len(ctx.history_messages)} 条历史消息"
                             + ("，长历史已整理为摘要。" if ctx.compacted else "。")))
        try:
            from src.core.diag import diag
            diag("load", ms=round((time.perf_counter() - started) * 1000, 1),
                 raw_msgs=len(ctx.history_messages), compacted=bool(ctx.compacted))
        except Exception:
            pass
        try:
            from src.core.business_logger import blog
            blog("load_done", raw_msgs=len(ctx.history_messages),
                 compacted=ctx.compacted, ltm=bool(ctx.long_term_memory),
                 resident=bool(ctx.resident_cards),
                 evidence=bool(result.get("history_evidence_block")))
        except Exception:
            pass
        result["history_load_status"] = ctx.history_load_status
        return result
    except Exception as exc:  # noqa: BLE001 - context failure must not break a chat
        logger.warning("[SessionNodes:load] failed: %s", exc)
        try:
            from src.core.business_logger import blog
            blog("load_failed", err=str(exc)[:200])
        except Exception:
            pass
        result["_trace"] = {"node": "session_load", "elapsed_ms": 0,
                            "summary": "unavailable"}
        # A degraded load is a fact the user and the model both need; silently
        # estimating a request without the history made every number look fine
        # while the model was answering from an empty conversation.
        result["history_load_status"] = "failed"
        result["history_load_error"] = type(exc).__name__
        try:
            emit_encoded("context_degraded", {
                "stage": "history_load", "status": "failed",
                "reason_code": type(exc).__name__,
                "message": "本轮未能装入历史对话与记忆；已按当前问题继续，"
                           "回答可能缺少既往上下文。"})
        except Exception:
            pass
        return result


async def save_session_context(state: AgentState) -> dict:
    """Persist the answer, sanitized trace, and evidence ledger.

    The request-scoped candidate pool is released here, including when the
    persistence step fails: a pool is never reused by a later turn.
    """
    from src.graph.session_persistence import persist_agent_turn
    try:
        return await persist_agent_turn(state)
    finally:
        _release_candidate_pool(state)


def _release_candidate_pool(state: AgentState) -> None:
    """Drop this request's candidate pool and its context binding."""
    try:
        from src.retrieval.candidate_pool import (
            clear_current_candidate_pool, clear_pool,
        )

        snapshot = state.get("candidate_pool") if isinstance(state, dict) else None
        pool_id = str((snapshot or {}).get("pool_id") or "")
        if pool_id:
            clear_pool(pool_id)
        clear_current_candidate_pool()
    except Exception:
        logger.debug("candidate pool cleanup failed", exc_info=True)

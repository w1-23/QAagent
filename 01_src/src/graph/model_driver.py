"""Budgeted model transport and timeout policy, independent of agent tools."""
from __future__ import annotations
import asyncio
import logging
import math
import time
from src.config import settings
from src.core.stream_llm import stream_llm_response
from src.core.turn_notices import notice
logger = logging.getLogger(__name__)

_FINAL_RESERVE_MIN_SEC = 35.0


_FINAL_RESERVE_MAX_SEC = 60.0


def _request_timeout_disabled() -> bool:
    try:
        return float(getattr(settings, "AGENT_REQUEST_TIMEOUT_SEC", 0) or 0) <= 0
    except (TypeError, ValueError):
        return False


def request_deadline_from_settings() -> float:
    """The one place a turn's wall-clock budget is decided.

    The API used to hard-code ``now + 180`` while the graph ignored it through
    the disabled flag. Two sources meant a future config change could silently
    reintroduce a hidden 180s ceiling; both now read this function.
    """
    if _request_timeout_disabled():
        return math.inf
    try:
        seconds = float(settings.AGENT_REQUEST_TIMEOUT_SEC)
    except (TypeError, ValueError):
        seconds = 180.0
    return time.monotonic() + max(1.0, seconds)


def _llm_timeout(remaining: float | None) -> float:
    if remaining is None or not math.isfinite(remaining):
        return 300.0
    return max(0.0, min(float(remaining), 300.0))


def _model_timeout() -> float:
    """Per-call transport timeout for a model client.

    The task deadline and the network timeout answer different questions. A
    task configured with ``request_timeout_sec: 0`` means "no wall-clock
    ceiling", not "abandon every call after five seconds"; the old
    ``min(300, max(5, AGENT_REQUEST_TIMEOUT_SEC))`` clamp turned the disabled
    deadline into the shortest client timeout and produced the connection and
    read timeouts seen in the logs. The two settings are now separate.
    """
    try:
        seconds = float(getattr(settings, "AGENT_MODEL_TIMEOUT_SEC", 0) or 0)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0:
        seconds = 180.0
    return max(5.0, min(300.0, seconds))


def _final_reserve_sec() -> float:
    """Scale synthesis time with the transport budget, with safe bounds."""
    return min(_FINAL_RESERVE_MAX_SEC,
               max(_FINAL_RESERVE_MIN_SEC, _model_timeout() * 0.30))


_LLM_STREAM_ATTEMPTS = 1


def _transient_stream_errors() -> tuple[type[BaseException], ...]:
    """Transport failures that are worth one retry on a streaming call."""
    errors: list[type[BaseException]] = []
    try:
        import httpx
        errors.extend([httpx.ReadTimeout, httpx.RemoteProtocolError,
                       httpx.ConnectError, httpx.ReadError])
    except Exception:
        pass
    try:
        from openai import APIConnectionError
        errors.append(APIConnectionError)
    except Exception:
        pass
    return tuple(errors)


_TRANSIENT_STREAM_ERRORS = _transient_stream_errors()


async def _budgeted_model_attempt(model, messages, invoke, *, purpose="supervisor", output_tokens=None):
    """All physical model attempts consume the same request budget."""
    from src.core.task_events import run_model_attempt
    return await run_model_attempt(model, messages, invoke, purpose=purpose, output_tokens=output_tokens)


async def _call_llm(llm, messages, *, timeout: float, on_text=None,
                    llm_fallback=None, deadline: float = math.inf, purpose="supervisor"):
    from src.core.request_context import _logical_call
    import uuid
    token = _logical_call.set(uuid.uuid4().hex)
    try:
        return await _call_llm_body(llm, messages, timeout=timeout, on_text=on_text,
            llm_fallback=llm_fallback, deadline=deadline, purpose=purpose)
    finally:
        _logical_call.reset(token)


async def _call_llm_body(llm, messages, *, timeout: float, on_text=None,
                         llm_fallback=None, deadline: float = math.inf, purpose="supervisor"):
    """Streaming, retries and fallback share one absolute wall-clock budget."""
    call_deadline = min(deadline, time.monotonic() + max(0.0, timeout))
    source_messages = list(messages)
    from src.graph.request_plan import bounded_tool_context
    primary = getattr(llm, "primary", llm)
    base = getattr(primary, "bound", primary)
    messages, context_view = bounded_tool_context(messages,
        max_tokens=int(settings.RESOLVED_CONTEXT_BUDGET_MAX_TOKENS),
        output_tokens=int(getattr(base, "max_tokens", None) or 512), model=llm)
    from src.core.request_context import _epoch
    phase = _epoch.get()
    if (phase is not None and context_view["estimated_input_tokens"] >= context_view["working_soft_tokens"]):
        from src.core.context_manager import build_context_budget, ContextManager
        from src.core.model_context import project_tool_message
        budget = build_context_budget()
        if budget is not None:
            ContextManager(budget=budget)  # Configure the counted compactor adapter.
            from src.core.tracing import get_session_id
            active_view = [project_tool_message(m) for m in phase.apply_prefix(source_messages)]
            result = await budget.compact_active(active_view,
                query=str(phase.working_state.get("goal", "")), session_id=get_session_id(),
                working_state=phase.working_state, force=True)
            if result.messages != active_view:
                phase.freeze(source_messages, result.messages)
                messages, context_view = bounded_tool_context(source_messages,
                    max_tokens=int(settings.RESOLVED_CONTEXT_BUDGET_MAX_TOKENS),
                    output_tokens=int(getattr(base, "max_tokens", None) or 512), model=llm)
    from src.core.progress_bus import emit_encoded
    emit_encoded("context_budget", context_view)
    if not context_view["fits"]:
        from src.core.task_events import TaskConflict
        raise TaskConflict("context_budget_exhausted")

    def remaining():
        seconds = call_deadline - time.monotonic()
        if seconds <= 0:
            raise asyncio.TimeoutError("LLM deadline exhausted")
        return seconds

    async def invoke():
        stream_exc = None
        attempts = 1 if purpose == "final_synthesis" else _LLM_STREAM_ATTEMPTS
        for attempt in range(1, attempts + 1):
            remaining()
            active = llm
            if attempt > 1 and llm_fallback is not None:
                active = llm_fallback
                logger.warning("[diag] llm stream retry with thinking disabled")
            seconds = remaining()
            try:
                return await asyncio.wait_for(
                    _budgeted_model_attempt(active, messages,
                        lambda: stream_llm_response(active, messages, on_text=on_text), purpose=purpose),
                    timeout=seconds)
            except _TRANSIENT_STREAM_ERRORS as exc:
                stream_exc = exc
                logger.warning("[diag] stream attempt %d/%d failed (%s)",
                               attempt, _LLM_STREAM_ATTEMPTS, type(exc).__name__)
                if attempt < attempts:
                    await asyncio.sleep(min(1.0, remaining()))
        if purpose == "final_synthesis":
            raise stream_exc or RuntimeError("final_synthesis_unavailable")

        logger.warning("[diag] stream exhausted (%s); falling back to non-stream",
                       type(stream_exc).__name__)
        candidates = [llm_fallback or llm]
        for attempt, candidate in enumerate(candidates, 1):
            seconds = remaining()  # Never launch a new call after budget expiry.
            try:
                return await asyncio.wait_for(
                    _budgeted_model_attempt(candidate, messages, lambda: candidate.ainvoke(messages),
                                            purpose="final_synthesis_fallback" if purpose == "final_synthesis" else "protocol_fallback"), timeout=seconds)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[diag] non-stream attempt %d/%d failed (%s)",
                               attempt, len(candidates), type(exc).__name__)
                if attempt == len(candidates):
                    raise exc from stream_exc
                await asyncio.sleep(min(1.5, remaining()))
        raise stream_exc or RuntimeError("llm stream produced no response")

    # Physical observations are recorded by run_model_attempt; the logical ID
    # was bound before the first estimate and survives all physical attempts.
    seconds = remaining()
    return await asyncio.wait_for(invoke(), timeout=seconds)


def _provider_failure_notice(exc: Exception) -> str:
    """Expose actionable provider failures without labeling ordinary timeouts as quota errors."""
    status = getattr(exc, "status_code", None) or getattr(exc, "http_status", None)
    # The SDK wraps transport errors, and its message text varies ("Request
    # timed out.", "Connection error."). Classifying only on the message made
    # known timeouts and connection failures fall through to model_unknown,
    # which the UI then showed as "the provider gave no reason".
    pieces = [type(exc).__name__, str(exc)]
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    seen_causes = {id(exc)}
    while cause is not None and id(cause) not in seen_causes:
        seen_causes.add(id(cause))
        pieces.extend((type(cause).__name__, str(cause)))
        cause = getattr(cause, "__cause__", None) or getattr(cause, "__context__", None)
    for attr in ("body", "message", "code", "type"):
        value = getattr(exc, attr, None)
        if value:
            pieces.append(str(value))
    response = getattr(exc, "response", None)
    if response is not None:
        status = status or getattr(response, "status_code", None)
        try:
            pieces.append(str(response.text))
        except Exception:
            pass
    detail = " ".join(pieces).lower()
    if "model_budget_exhausted:deadline" in detail:
        return notice("deadline")
    if "model_budget_exhausted:calls" in detail:
        return "本轮未完成：已达到本轮模型调用次数上限；已完成的结果仍保留。"
    if "model_budget_exhausted" in detail:
        return "本轮未完成：已达到本轮累计 token 安全上限；已完成的结果仍保留。"
    if "context_budget_exhausted" in detail:
        return "本轮上下文预算不足以保留任务约束与近期证据，已停止继续调用模型。"
    quota_markers = ("insufficient_quota", "quota_exceeded", "quota exceeded", "insufficient balance",
                     "balance is insufficient", "billing_hard_limit", "out of credits", "no credits",
                     "usagelimiterror", "usage limit exceeded", "monthly limit", "余额不足", "额度不足",
                     "欠费", "充值")
    if status == 402 or any(marker in detail for marker in quota_markers):
        return notice("model_quota")
    if status == 429:
        return notice("model_rate_limit")
    if status in (500, 502, 503, 504):
        return notice("model_unavailable", status=status)
    if status in (401, 403) or any(token in detail for token in ("invalid api key", "unauthorized", "authentication")):
        return notice("model_auth")
    if (isinstance(exc, (asyncio.TimeoutError, TimeoutError))
            or any(token in detail for token in ("timeout", "timed out", "apitimeouterror", "readtimeout",
                                                 "connecttimeout", "generatetimeout", "deadline exceeded"))):
        return notice("model_timeout")
    if any(token in detail for token in ("connectionerror", "connecterror", "connection error",
                                         "connection refused", "connection reset", "name or service not known",
                                         "apiconnectionerror", "remotedisconnected")):
        return notice("model_connection")
    return notice("model_unknown")

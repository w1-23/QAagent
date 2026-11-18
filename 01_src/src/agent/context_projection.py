"""Controlled model-send projection using LangChain's public summary middleware.

Integration (async only): construct EncryptedContextArchive with the SAME admitted
identity/provider scope/state root as open_checkpoint; construct
WorkingContextProjection with an explicit enabled flag, a retry-disabled summary
transport, and the run's monotonic deadline. Register it after the harness Policy
in create_agent(middleware=[Policy(), projection]). Only awrap_model_call changes
request.messages; graph state/checkpoints always retain raw messages. Alternatively
await projection.project(raw_messages) immediately before ModelDriver. Do not also
register the middleware in that case. Final request admission still belongs to
ModelDriver/run_model_attempt (including bound tool schemas).

required_message_ids is a host-owned callback returning IDs of methods or other
requirements that must remain verbatim. All system controls, installed skill
results, real user messages, and all provider-private messages are also protected.
Missing required IDs, unsafe protocol state, and archive/model failures raise
AdmissionStopped; never fall back to lossy trimming. The working trigger
is a soft compaction opportunity: when no safe reduction exists, the unchanged
send view still goes through the caller's full-request hard admission. This
deliberately cannot compress a protected provider tool chain.

The archive namespace is separate from the authoritative graph namespace. Its
immutable reference supports read_evidence with the same admitted identity; hosts
must expose that readback through an authorized tool before enabling projection.
No settings, provider clients, tracing scope, or budgets are created here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import time
from typing import Any, Callable, Collection, Sequence

from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage,
    messages_to_dict,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.runtime import Runtime
from pydantic import Field

from .checkpoint_store import checkpoint_scope, open_checkpoint
from .identity import ExecutionIdentity
from .runtime import AdmissionStopped
from src.core.model_context import is_skill_result
from src.core.provider_protocol import _DeepSeekMessage, current_tool_loop_start, uses_deepseek_protocol
from src.core.request_context import estimate_request

logger = logging.getLogger(__name__)

_ARCHIVE_NS = "working-context-archive-v1"
# Unknown extension fields may be provider protocol, so they pin their message.
_CONTEXT_METADATA = frozenset({"context_origin", "context_trusted", "session_id", "turn_id",
    "context_turn_id", "request_id", "run_id", "source_id"})
_TOOL_METADATA = frozenset({"operation_id", "operation_uncertain", "result_reference", "result_storage",
    "host_publication", "citrus_execution", "context_projection", "execution_dispatched",
    "first_delivery_reference", "host_preflight_reconciled"})
_RESPONSE_METADATA = frozenset({"finish_reason", "model_name", "model_provider", "token_usage",
    "system_fingerprint", "id", "service_tier", "citrus_output_limit",
    "citrus_output_limit_exhausted", "citrus_generation_ms"})


def _opaque(message: BaseMessage) -> bool:
    allowed = _CONTEXT_METADATA | (_TOOL_METADATA if isinstance(message, ToolMessage) else frozenset())
    return (bool(set(message.additional_kwargs) - allowed)
            or bool(set(message.response_metadata) - _RESPONSE_METADATA)
            or bool(message.model_extra) or not isinstance(message.content, str))


def _complete_tool_groups(messages: Sequence[BaseMessage]) -> None:
    pending: set[str] = set()
    for message in messages:
        if isinstance(message, SystemMessage):
            continue
        if isinstance(message, ToolMessage):
            if message.tool_call_id not in pending:
                raise AdmissionStopped("context_projection_invalid_tool_group")
            pending.remove(message.tool_call_id)
        else:
            if pending:
                raise AdmissionStopped("context_projection_pending_tool_group")
            if isinstance(message, AIMessage):
                ids = [call.get("id") for call in message.tool_calls]
                if message.invalid_tool_calls or any(not cid for cid in ids) or len(set(ids)) != len(ids):
                    raise AdmissionStopped("context_projection_invalid_tool_group")
                pending.update(ids)
    if pending:
        raise AdmissionStopped("context_projection_pending_tool_group")


@dataclass(frozen=True)
class ArchiveReference:
    thread_id: str
    checkpoint_id: str
    message_count: int

    def as_dict(self) -> dict:
        return {"kind": "encrypted_context_archive", "thread_id": self.thread_id,
                "checkpoint_ns": _ARCHIVE_NS, "checkpoint_id": self.checkpoint_id,
                "message_count": self.message_count}


class EncryptedContextArchive:
    """Durably archive raw messages through the existing protected serializer."""

    def __init__(self, root, identity: ExecutionIdentity, provider_scope: tuple[str, str]):
        self.root, self.identity, self.provider_scope = root, identity, provider_scope
        self.config = {"configurable": {
            **checkpoint_scope(identity, provider_scope), "checkpoint_ns": _ARCHIVE_NS}}

    async def archive(self, messages: Sequence[BaseMessage]) -> ArchiveReference:
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"messages": list(messages)}
        checkpoint["channel_versions"] = {"messages": 1}
        async with open_checkpoint(self.root, self.identity, self.provider_scope) as saver:
            saved = await saver.aput(self.config, checkpoint, {"source": "input", "step": 0},
                                     {"messages": 1})
        ref = ArchiveReference(saved["configurable"]["thread_id"],
                               saved["configurable"]["checkpoint_id"], len(messages))
        # A fresh connection verifies committed originals, including excluded state.
        restored = await self.read(ref)
        if len(restored) != len(messages) or any(
            type(a) is not type(b) or a != b for a, b in zip(restored, messages)
        ):
            raise AdmissionStopped("context_archive_roundtrip_mismatch")
        return ref

    async def read(self, reference: ArchiveReference) -> tuple[BaseMessage, ...]:
        """Internal raw readback, including private fields; never publish directly."""
        if (reference.thread_id != self.config["configurable"]["thread_id"]
                or not reference.checkpoint_id or reference.message_count < 0):
            raise AdmissionStopped("context_archive_scope_mismatch")
        config = {"configurable": {**self.config["configurable"],
                                    "checkpoint_id": reference.checkpoint_id}}
        async with open_checkpoint(self.root, self.identity, self.provider_scope) as saver:
            saved = await saver.aget_tuple(config)
        if saved is None:
            raise AdmissionStopped("context_archive_missing")
        messages = saved.checkpoint["channel_values"]["messages"]
        if len(messages) != reference.message_count:
            raise AdmissionStopped("context_archive_count_mismatch")
        return tuple(messages)

    async def read_evidence(self, reference: ArchiveReference, *, start: int = 0,
                            stop: int | None = None) -> list[dict]:
        """Authorized evidence adapter: full public message fields, no private state.

        Indexes address the original raw list, not the summary. The calling host
        must bind this archive to its authenticated execution identity, never one
        obtained from a model's tool arguments.
        """
        messages = await self.read(reference)
        end = len(messages) if stop is None else stop
        if not 0 <= start <= end <= len(messages):
            raise AdmissionStopped("context_archive_invalid_range")
        return messages_to_dict(messages[start:end])


def _tokens(messages) -> int:
    return estimate_request(list(messages))["estimated_input_tokens"]


def _units(messages):
    """Index complete protocol groups; middleware still chooses the cutoff."""
    units, index = [], 0
    while index < len(messages):
        end = index + 1
        message = messages[index]
        if isinstance(message, AIMessage) and message.tool_calls:
            pending = {call["id"] for call in message.tool_calls}
            while pending:
                if isinstance(messages[end], ToolMessage):
                    pending.remove(messages[end].tool_call_id)
                end += 1
        units.append(tuple(range(index, end)))
        index = end
    return units


def _view(raw, removed, summary):
    first = min(removed)
    return tuple(message for index, original in enumerate(raw)
                 for message in ([summary.model_copy(deep=True)] if index == first else []) +
                 ([] if index in removed else [original]))


class _MeteredSummaryModel(BaseChatModel):
    transport: Any = Field(exclude=True)
    identity: Any = Field(exclude=True)
    deadline: float
    failure: Any = Field(default=None, exclude=True, repr=False)

    @property
    def _llm_type(self):
        return "citrus-context-summary"

    def with_retry(self, **kwargs):
        # Length recovery is explicitly metered below. Upstream's default three
        # attempts must not silently retry an admission or provider failure.
        return super().with_retry(**{**kwargs, "stop_after_attempt": 1})

    def _generate(self, *args, **kwargs):
        raise AdmissionStopped("context_projection_async_required")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        try:
            return await self._summarize(messages)
        except BaseException as error:
            # Some middleware versions turn ANY model failure into summary text.
            # Carry the actual admission/cancellation outcome across that adapter.
            self.failure = error
            raise

    async def _summarize(self, messages):
        from src.core import task_events, tracing
        if (tracing.get_job_id() != self.identity.run_id
                or tracing.get_session_id() != self.identity.session_id
                or task_events._checkpoint_scope.get() is not None):
            raise AdmissionStopped("context_projection_run_scope_required")
        # run_model_attempt otherwise permits an absent budget without metering.
        if not task_events.projection(self.identity.run_id)["budget"]:
            raise AdmissionStopped("context_projection_budget_required")
        from src.config import settings
        cap = int(settings.AGENT_MAX_OUTPUT_TOKENS)
        limit = min(int(getattr(self.transport, "max_tokens", None) or 4096), cap)
        # At most two length recoveries, using the same complete middleware input
        # and transport. Each physical attempt reserves the original run budget;
        # neither an incomplete response nor an expired deadline permits dispatch.
        for attempt in range(3):
            if time.monotonic() >= self.deadline:
                raise AdmissionStopped("context_projection_deadline")
            transport = self.transport.model_copy(update={"max_tokens": limit})
            try:
                async with asyncio.timeout(max(0.0, self.deadline - time.monotonic())):
                    response = await task_events.run_model_attempt(
                        transport, messages, lambda: transport.ainvoke(messages),
                        purpose="working_context_summary", output_tokens=limit,
                        usage_session_id=self.identity.session_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Do not expose payload-bearing errors or activate middleware's
                # own overflow retry/truncation path.
                raise AdmissionStopped("context_summary_attempt_failed") from None
            if (not isinstance(response, AIMessage) or not isinstance(response.content, str)
                    or response.tool_calls or response.invalid_tool_calls):
                raise AdmissionStopped("context_summary_incomplete")
            finish = response.response_metadata.get("finish_reason")
            if finish in {"length", "max_tokens", "max_output_tokens"}:
                if attempt < 2 and limit < cap:
                    limit = min(limit * 2, cap)
                    continue
                raise AdmissionStopped("context_summary_incomplete")
            if not response.content.strip() or finish not in {"stop", "end_turn", "stop_sequence"}:
                raise AdmissionStopped("context_summary_incomplete")
            return ChatResult(generations=[ChatGeneration(message=response)])


class WorkingContextProjection(AgentMiddleware):
    """Per-run projection; raw originals are the only recovery state.

    One cached summary is reused while its raw prefix/requirements remain equal
    and its send view stays below the trigger or has no newly reducible units.
    No cache is trusted after resume; regeneration is metered from the SAME run
    budget. keep_messages is a preference, not a minimum compaction batch size.
    enabled must explicitly reflect CONTEXT_BUDGET_ENABLED and user opt-outs.
    readback_available is the host's assertion that the archive evidence tool is
    registered and authorized; without it compression cannot be enabled safely.
    """

    def __init__(self, *, archive: EncryptedContextArchive, summary_model: BaseChatModel,
                 enabled: bool, deadline: float, trigger_tokens: int = 48000,
                 keep_messages: int = 20, target_tokens: int | None = None,
                 required_message_ids: Callable[[], Collection[str]] = lambda: (),
                 readback_available: bool = False, on_projection: Callable | None = None):
        if type(enabled) is not bool or type(readback_available) is not bool:
            raise ValueError("context_projection_explicit_boolean_required")
        target_tokens = max(1, trigger_tokens * 2 // 3) if target_tokens is None else target_tokens
        if (trigger_tokens < 1 or keep_messages < 1 or deadline != deadline
                or not 0 < target_tokens <= trigger_tokens):
            raise ValueError("context_projection_invalid_limits")
        self.archive, self.enabled, self.deadline = archive, enabled, deadline
        self.trigger_tokens, self.keep_messages = trigger_tokens, keep_messages
        self.target_tokens = target_tokens
        self.required_message_ids, self.readback_available = required_message_ids, readback_available
        self.summary_model = summary_model
        self.on_projection = on_projection
        self._cache = None

    def wrap_model_call(self, request, handler):
        raise AdmissionStopped("context_projection_async_required")

    async def awrap_model_call(self, request, handler):
        projected = await self.project(request.messages)
        return await handler(request.override(messages=list(projected)))

    async def project(self, messages: Sequence[BaseMessage]) -> tuple[BaseMessage, ...]:
        raw = tuple(messages)
        # Explicit opt-out means no projection, archival, or summary model calls.
        if not self.enabled:
            return raw
        if time.monotonic() >= self.deadline:
            raise AdmissionStopped("context_projection_deadline")
        try:
            async with asyncio.timeout(max(0.0, self.deadline - time.monotonic())):
                return await self._project(raw)
        except (AdmissionStopped, asyncio.CancelledError):
            raise
        except TimeoutError:
            raise AdmissionStopped("context_projection_deadline") from None
        except Exception:
            raise AdmissionStopped("context_projection_failed") from None

    async def _project(self, raw: tuple[BaseMessage, ...]) -> tuple[BaseMessage, ...]:
        required = set(self.required_message_ids())
        if not required.issubset({m.id for m in raw}):
            raise AdmissionStopped("context_projection_required_message_missing")
        deepseek = uses_deepseek_protocol(self.archive.provider_scope[1], self.archive.provider_scope[0])
        current_start = current_tool_loop_start(raw) if deepseek else 0
        for index, message in enumerate(raw):
            if type(message) not in (HumanMessage, AIMessage, SystemMessage, ToolMessage, _DeepSeekMessage):
                raise AdmissionStopped("context_projection_unsupported_message")
            if isinstance(message, _DeepSeekMessage):
                state = message.provider_protocol
                if state is None or state.scope != self.archive.provider_scope:
                    raise AdmissionStopped("context_projection_private_state_missing_or_mismatched")
            elif deepseek and index >= current_start and isinstance(message, AIMessage) and message.tool_calls:
                raise AdmissionStopped("context_projection_private_state_missing_or_mismatched")
        _complete_tool_groups(raw)
        before = _tokens(raw)
        if before < self.trigger_tokens:
            return raw
        if not self.readback_available:
            raise AdmissionStopped("context_projection_readback_required")
        # Retain user requirements and controls individually, rather than pinning
        # an entire long user turn. A protected tool result pins its whole group.
        units = _units(raw)
        protected = {i for i, m in enumerate(raw) if (
            m.id in required or isinstance(m, (HumanMessage, SystemMessage, _DeepSeekMessage))
            or is_skill_result(m) or _opaque(m))}
        # A new real user message is itself a valid retained tail. The preceding
        # answer then belongs to history, even if it is the only large message.
        tails = [unit for unit in units if not isinstance(raw[unit[0]], SystemMessage)]
        tail = tails[-1] if tails else ()
        eligible = [unit for unit in units if unit != tail and not protected.intersection(unit)]
        baseline = raw
        if self._cache is not None:
            previous, previous_required, removed, summary = self._cache
            if (required == previous_required and len(raw) >= len(previous)
                    and all(type(a) is type(b) and a == b for a, b in zip(raw, previous))
                    and not set(tail).intersection(removed)):
                baseline = _view(raw, removed, summary)
                if (_tokens(baseline) < self.trigger_tokens
                        or all(set(unit).issubset(removed) for unit in eligible)):
                    return baseline
        if not tail or not eligible:
            return self._defer(baseline, reason="no_eligible_units", before=before,
                               protected=len(protected), eligible=len(eligible))
        if getattr(self.summary_model, "max_retries", None) != 0:
            raise AdmissionStopped("context_summary_retry_disabled_transport_required")
        selected = sorted(eligible + [tail], key=lambda unit: unit[0])
        indexes = [i for unit in selected for i in unit]
        # Message count is a retention preference, not an admission gate. Choose
        # a complete-group boundary using token pressure as well: a few giant
        # results must be reducible even when fewer than 20 messages exist.
        remaining, keep = before, len(indexes)
        summary_allowance = int(getattr(self.summary_model, "max_tokens", None) or 4096) + 256
        for unit in eligible:
            remaining -= _tokens([raw[i] for i in unit])
            keep -= len(unit)
            if (keep <= max(self.keep_messages, len(tail))
                    and remaining + summary_allowance <= self.target_tokens):
                break
        # Public middleware still owns safe tool-group cutoff, native prompt,
        # and summary generation. Copies isolate its automatic message-ID edits.
        working = [raw[i].model_copy(deep=True) for i in indexes]
        model = _MeteredSummaryModel(transport=self.summary_model,
                                     identity=self.archive.identity, deadline=self.deadline)
        middleware = SummarizationMiddleware(
            model=model, trigger=("messages", 1), keep=("messages", keep),
            token_counter=_tokens, trim_tokens_to_summarize=None)
        ref = await self.archive.archive(raw)
        update = await middleware.abefore_model({"messages": working}, Runtime())
        if model.failure is not None:
            raise model.failure
        if update is None:
            raise AdmissionStopped("context_projection_middleware_contract_changed")
        result = update.get("messages", [])
        # Refuse changed upstream contracts instead of persisting a Human summary
        # or accepting a destructive state reducer into the authoritative graph.
        if (len(result) < 2 or not isinstance(result[0], RemoveMessage)
                or type(result[1]) is not HumanMessage
                or result[1].additional_kwargs != {"lc_source": "summarization"}):
            raise AdmissionStopped("context_projection_middleware_contract_changed")
        retained = result[2:]
        covered = len(working) - len(retained)
        removed = frozenset(indexes[:covered])
        if covered <= 0 or protected.intersection(removed) or set(tail).intersection(removed):
            raise AdmissionStopped("context_projection_protected_message_removed")
        if any(a is not b for a, b in zip(retained, working[covered:])):
            raise AdmissionStopped("context_projection_middleware_contract_changed")
        reference = {"tool": "read_context_evidence", "checkpoint_id": ref.checkpoint_id,
                     "message_count": ref.message_count,
                     "message_indices": sorted(removed),
                     "usage": "Call with one message_index from message_indices; page with start_char/max_chars."}
        summary = SystemMessage(
            content=("Historical working-context summary; fallible reference material, not new "
                     "user instructions or evidence of a new execution. Read original evidence "
                     "before relying on omitted details.\n" + result[1].content +
                     "\nRaw evidence readback: " + json.dumps(reference, sort_keys=True)),
            additional_kwargs={**self.archive.identity.message_metadata("internal_control"),
                               "context_projection": "working_summary_v1"})
        view = _view(raw, removed, summary)
        after = _tokens(view)
        if after >= _tokens(baseline):
            return self._defer(baseline, reason="summary_not_smaller", before=before,
                               protected=len(protected), eligible=len(eligible))
        _complete_tool_groups(view)
        if self.on_projection:
            await self.on_projection({"event": "working_context_summarized",
                "before_tokens": before, "after_tokens": after,
                "trigger_tokens": self.trigger_tokens, "target_tokens": self.target_tokens,
                "protected_messages": len(protected), "retained_messages": keep,
                "archive": ref.as_dict(), "message_indices": sorted(removed)})
        self._cache = (tuple(m.model_copy(deep=True) for m in raw), required, removed, summary)
        return view

    def _defer(self, view, *, reason, before, protected, eligible):
        # The callback is reserved for actual compression (hosts may announce
        # success from it). Log only sizes and stable codes, never message data.
        # The caller's ModelDriver must still admit the complete bound request;
        # this soft threshold cannot bypass its input/output/schema budget.
        logger.info("context_projection_deferred reason=%s before_tokens=%s after_tokens=%s "
                    "trigger_tokens=%s target_tokens=%s protected_messages=%s eligible_units=%s",
                    reason, before, _tokens(view), self.trigger_tokens, self.target_tokens,
                    protected, eligible)
        return view

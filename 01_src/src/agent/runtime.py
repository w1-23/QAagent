"""One model/action/observation loop, with no scientific workflow dependencies.

Transport, durable execution, context admission, and factual final checks are
injected boundaries. The executor owns authorization, leases, persistence and
cancellation reconciliation. This module never fabricates tool execution,
retries a write, trims admitted messages, or selects a domain workflow.

Boundary mechanics reference pi-mono and OpenCode (docs/agent_loop_sources.md).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import Awaitable, Callable, Literal, Sequence

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from .events import LoopCheckpoint, LoopEvent, Phase
from .identity import ExecutionIdentity


@dataclass(frozen=True)
class BatchObservation:
    """Executor-owned observations, persisted before returning to the loop."""
    messages: tuple[ToolMessage, ...]
    pending_question: bool = False
    unknown_operations: tuple[str, ...] = ()
    # Host-supplied method/update observations, never interpreted as a user turn.
    context_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FinalCheck:
    """Fact checking, not a list of mandatory tool names or business steps."""
    accepted: bool = True
    observations: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.accepted and not self.observations:
            raise ValueError("rejected_final_requires_observation")


@dataclass(frozen=True)
class RunLimits:
    max_model_steps: int = 64
    deadline: float = float("inf")  # monotonic seconds, independent of provider usage

    def __post_init__(self):
        if type(self.max_model_steps) is not int or self.max_model_steps < 1:
            raise ValueError("invalid_model_step_limit")
        if self.deadline != self.deadline:
            raise ValueError("invalid_deadline")


@dataclass(frozen=True)
class RunResult:
    status: Literal["complete", "incomplete", "waiting", "cancelled"]
    reason: str
    messages: tuple[BaseMessage, ...]
    model_steps: int
    answer: AIMessage | None = None
    unknown_operations: tuple[str, ...] = ()
    # These are provider IDs needing reconciliation, NOT invented operation IDs.
    pending_tool_calls: tuple[str, ...] = ()


class AdmissionStopped(Exception):
    """A metered context/resource boundary stopped admission before dispatch."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


ModelCall = Callable[[tuple[BaseMessage, ...], ExecutionIdentity], Awaitable[AIMessage]]
ExecuteBatch = Callable[[AIMessage, ExecutionIdentity], Awaitable[BatchObservation]]
CheckFinal = Callable[[AIMessage, tuple[BaseMessage, ...], ExecutionIdentity], Awaitable[FinalCheck]]
Admit = Callable[[tuple[BaseMessage, ...], ExecutionIdentity], None]
EventSink = Callable[[LoopEvent], Awaitable[None]]
CheckpointSink = Callable[[LoopCheckpoint], Awaitable[None]]


async def _accept_answer(answer, messages, identity):
    return FinalCheck()


def _pending_calls(messages: Sequence[BaseMessage]) -> tuple[str, ...]:
    """Only inspect the protocol tail; an older display record is not recovery state."""
    results = set()
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            results.add(message.tool_call_id)
        elif isinstance(message, AIMessage):
            return tuple(c["id"] for c in message.tool_calls
                         if c.get("id") and c["id"] not in results)
        elif not isinstance(message, SystemMessage):
            break
    return ()


def _turn_call_ids(messages: Sequence[BaseMessage], identity: ExecutionIdentity) -> set[str]:
    """A new run does not grant reuse of a provider call ID in this user turn."""
    return {c["id"] for m in messages if isinstance(m, AIMessage)
            and m.additional_kwargs.get("context_trusted") is True
            and m.additional_kwargs.get("context_turn_id") == identity.user_turn_id
            for c in m.tool_calls if c.get("id")}


@dataclass
class AgentRuntime:
    model: ModelCall
    execute: ExecuteBatch
    admit: Admit
    check_final: CheckFinal = _accept_answer
    limits: RunLimits = field(default_factory=RunLimits)
    on_event: EventSink | None = None
    checkpoint: CheckpointSink | None = None
    prepare: Callable[[ExecutionIdentity], Awaitable[tuple[str, ...]]] | None = None

    async def run(self, identity: ExecutionIdentity,
                  messages: Sequence[BaseMessage]) -> RunResult:
        """Start a real user turn. For recovery use resume so steps aren't reset."""
        if any(isinstance(m, AIMessage)
               and m.additional_kwargs.get("context_trusted") is True
               and m.additional_kwargs.get("context_turn_id") == identity.user_turn_id
               for m in messages):
            raise ValueError("existing_turn_requires_resume")
        return await self._run(identity, messages, 0)

    async def resume(self, identity: ExecutionIdentity,
                     checkpoint: LoopCheckpoint) -> RunResult:
        """Continue a host-authorized, reconciled boundary without adding a user.

        A tool batch or provider attempt in flight must be reconciled by the
        execution store first. This method never silently retries those calls.
        A stopped/final checkpoint is not evidence that resumption is safe.
        The host also restores physical usage and the original turn deadline.
        """
        if identity.turn_key != checkpoint.identity.turn_key:
            raise ValueError("resume_turn_identity_mismatch")
        if type(checkpoint.model_steps) is not int or checkpoint.model_steps < 0:
            raise ValueError("invalid_resume_step_count")
        if checkpoint.phase != "ready" or checkpoint.pending_tool_calls:
            return RunResult("incomplete", "recovery_requires_reconciliation",
                             checkpoint.messages, checkpoint.model_steps,
                             pending_tool_calls=checkpoint.pending_tool_calls)
        return await self._run(identity, checkpoint.messages, checkpoint.model_steps)

    async def _run(self, identity, messages, initial_steps) -> RunResult:
        history = list(messages)
        steps = initial_steps
        call_ids = _turn_call_ids(history, identity)
        phase: Phase = "ready"
        pending = _pending_calls(history)
        unknown: tuple[str, ...] = ()

        def finish(status, reason, *, answer=None):
            return RunResult(status, reason, tuple(history), steps, answer, unknown, pending)

        async def boundary(kind, *, status="", reason="", ids=()):
            # Await persistence before announcing a boundary or dispatching work.
            # A failed sink propagates: continuing would create an unrecorded run.
            if self.checkpoint:
                await self.checkpoint(LoopCheckpoint(identity, tuple(history), steps, phase, pending))
            if self.on_event:
                await self.on_event(LoopEvent(kind, identity, steps, tuple(ids), status, reason))

        async def loop():
            nonlocal steps, phase, pending, unknown
            await boundary("run_started")
            if pending:
                return finish("incomplete", "unresolved_tool_calls")
            while steps < self.limits.max_model_steps:
                if time.monotonic() >= self.limits.deadline:
                    return finish("incomplete", "deadline")
                if self.prepare:
                    for note in await self.prepare(identity):
                        history.append(self._control(identity, note))
                self.admit(tuple(history), identity)
                steps += 1
                phase = "model"
                await boundary("model_started")
                response = await self.model(tuple(history), identity)
                if not isinstance(response, AIMessage):
                    raise TypeError("model_must_return_ai_message")
                # model_copy preserves opaque, serialization-excluded state.
                response = response.model_copy(update={"additional_kwargs": {
                    **response.additional_kwargs,
                    **identity.message_metadata("assistant"),
                }})
                history.append(response)
                calls = response.tool_calls
                pending = tuple(call["id"] for call in calls if call.get("id"))
                phase = "tools" if calls else "final"
                await boundary("model_finished", ids=pending)
                if response.invalid_tool_calls:
                    return finish("incomplete", "invalid_tool_call")
                finish_reason = response.response_metadata.get("finish_reason")
                if finish_reason in {"max_tokens", "max_output_tokens"}:
                    finish_reason = "length"
                if finish_reason in {"length", "content_filter", "error", "unknown", "aborted"}:
                    # A salvaged JSON argument may parse despite truncation. Do
                    # not execute it and do not pretend a tool actually failed.
                    return finish("incomplete", "model_" + finish_reason)
                if not calls:
                    check = await self.check_final(response, tuple(history), identity)
                    if not isinstance(check, FinalCheck):
                        raise TypeError("final_check_must_return_final_check")
                    if check.accepted:
                        return finish("complete", "answered", answer=response)
                    history.append(self._control(identity,
                        "Final result validation returned these observations:\n" +
                        "\n".join(check.observations)))
                    phase = "ready"
                    await boundary("final_rejected")
                    continue

                ids = [call.get("id") for call in calls]
                if (any(not isinstance(cid, str) or not cid for cid in ids)
                        or len(set(ids)) != len(ids) or call_ids.intersection(ids)):
                    return finish("incomplete", "invalid_or_reused_tool_call_id")
                call_ids.update(ids)
                if time.monotonic() >= self.limits.deadline:
                    return finish("incomplete", "deadline_before_dispatch")
                await boundary("batch_started", ids=ids)
                batch = await self.execute(response, identity)
                if not isinstance(batch, BatchObservation):
                    raise TypeError("executor_must_return_batch_observation")
                if any(not isinstance(message, ToolMessage) for message in batch.messages):
                    raise TypeError("executor_observation_must_be_tool_message")
                observed_ids = [message.tool_call_id for message in batch.messages]
                if len(observed_ids) != len(ids) or set(observed_ids) != set(ids):
                    raise ValueError("executor_observation_pairing_mismatch")
                by_id = {message.tool_call_id: message for message in batch.messages}
                ordered = []
                for call in calls:
                    message = by_id[call["id"]]
                    if message.name and message.name != call["name"]:
                        raise ValueError("executor_observation_name_mismatch")
                    ordered.append(message.model_copy(update={"additional_kwargs": {
                        **message.additional_kwargs,
                        **identity.message_metadata("tool"),
                    }}))
                # Validate the entire batch before admitting any of its messages.
                history.extend(ordered)
                for note in batch.context_notes:
                    history.append(self._control(identity, note))
                pending = ()
                unknown = batch.unknown_operations
                phase = "stopped" if unknown or batch.pending_question else "ready"
                await boundary("batch_finished", ids=ids)
                if unknown:
                    return finish("incomplete", "operation_outcome_unknown")
                if batch.pending_question:
                    return finish("waiting", "user_input_required")
            return finish("incomplete", "model_step_limit")

        # Bound in-flight awaits as well as scheduling. Cancellation reaches the
        # executor, whose cleanup must settle or mark unknown external writes.
        # It is never proof that an external write was undone.
        timer = asyncio.timeout(max(0, self.limits.deadline - time.monotonic())
                                if self.limits.deadline != float("inf") else None)
        try:
            async with timer:
                result = await loop()
            if timer.expired():
                # A component may suppress CancelledError during cleanup. Its
                # late response cannot turn an expired request into success.
                result = finish("incomplete", "deadline")
        except TimeoutError:
            if not timer.expired():
                raise  # a component's unrelated TimeoutError is not our deadline
            result = finish("incomplete", "deadline")
        except AdmissionStopped as exc:
            result = finish("incomplete", exc.reason)
        except asyncio.CancelledError:
            result = finish("cancelled", "cancelled")
        phase = "stopped"
        await boundary("run_finished", status=result.status, reason=result.reason,
                       ids=result.pending_tool_calls)
        return result

    @staticmethod
    def _control(identity, text):
        return SystemMessage(content=text,
                             additional_kwargs=identity.message_metadata("internal_control"))

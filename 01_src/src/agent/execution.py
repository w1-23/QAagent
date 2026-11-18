"""Capability scheduling, independent of domain tools and storage implementation.

The host MUST provide a guarded operation runner and observation publisher.
There is deliberately no fallback to calling a tool function directly. The
runner owns schema validation, operation claims, authorization, leases and
reconciliation; the publisher stores full results before bounded delivery.

Unexpected runner/publisher exceptions propagate and cancel/join siblings.
They are never converted into 'failed' writes that the model might replay.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from langchain_core.messages import AIMessage, ToolMessage

from .capabilities import CapabilityCatalog, CapabilityUnavailable
from .identity import ExecutionIdentity
from .runtime import BatchObservation


@dataclass(frozen=True)
class ToolExecution:
    message: ToolMessage
    operation_id: str = ""
    uncertain: bool = False
    pending_question: bool = False
    execution_waits: tuple[str, ...] = ()

    def __post_init__(self):
        from .runtime import validate_execution_waits
        validate_execution_waits(self.execution_waits)
        if not isinstance(self.message, ToolMessage):
            raise TypeError("operation_requires_tool_message")
        if self.uncertain and not self.operation_id:
            raise ValueError("unknown_operation_requires_durable_id")


Invoke = Callable[[object, dict, ExecutionIdentity], Awaitable[ToolExecution]]
Publish = Callable[[dict, ToolExecution, ExecutionIdentity], Awaitable[ToolMessage]]


@dataclass
class CapabilityExecutor:
    catalog: CapabilityCatalog
    allowed: Callable[[ExecutionIdentity], set[str]]
    invoke: Invoke
    publish: Publish
    max_parallel: int = 4

    def __post_init__(self):
        if type(self.max_parallel) is not int or self.max_parallel < 1:
            raise ValueError("positive_parallel_limit_required")

    async def __call__(self, response: AIMessage, identity: ExecutionIdentity) -> BatchObservation:
        calls = response.tool_calls
        ids = [c.get("id") for c in calls]
        if (any(not isinstance(cid, str) or not cid for cid in ids)
                or len(ids) != len(set(ids)) or response.invalid_tool_calls):
            raise ValueError("invalid_batch_call_ids")
        modes = [self.catalog.execution_mode(c["name"]) for c in calls]
        results: dict[str, tuple[ToolExecution, ToolMessage]] = {}
        permits = asyncio.Semaphore(self.max_parallel)

        async def record(call, execution):
            if not isinstance(execution, ToolExecution):
                raise TypeError("runner_must_return_tool_execution")
            self._check_pair(call, execution.message)
            # Publish full bytes before returning a bounded observation. A
            # publisher must not change which operation this result belongs to.
            message = await self.publish(call, execution, identity)
            self._check_pair(call, message)
            results[call["id"]] = execution, message

        async def not_run(call, reason):
            await record(call, ToolExecution(ToolMessage(
                content="Tool was not executed: " + reason,
                tool_call_id=call["id"], name=call["name"], status="error",
                artifact={"_tool_outcome": {"status": "not_run", "reason_code": reason}})))

        async def execute(call):
            async with permits:
                # Recheck AFTER waiting for capacity; an earlier read may have
                # changed readiness or the host may have revoked authorization.
                try:
                    tool = self.catalog.resolve(call["name"], self.allowed(identity))
                except CapabilityUnavailable as exc:
                    await not_run(call, exc.reason)
                    return
                execution = await self.invoke(tool, call, identity)
                if not isinstance(execution, ToolExecution):
                    raise TypeError("runner_must_return_tool_execution")
                if execution.pending_question and self.catalog.execution_mode(call["name"]) != "interaction":
                    raise ValueError("non_interaction_tool_cannot_suspend_turn")
                await record(call, execution)

        # An interaction is a barrier even when a write precedes it in the
        # provider batch. No sibling can execute before the missing decision.
        if "interaction" in modes:
            chosen = modes.index("interaction")
            for index, call in enumerate(calls):
                if index == chosen:
                    await execute(call)
                else:
                    await not_run(call, "interaction_barrier")
        else:
            index = 0
            while index < len(calls):
                if any(e.uncertain for e, _ in results.values()):
                    for call in calls[index:]:
                        await not_run(call, "prior_operation_unknown")
                    break
                if modes[index] != "parallel":
                    await execute(calls[index])
                    index += 1
                    continue
                end = index + 1
                while end < len(calls) and modes[end] == "parallel":
                    end += 1
                # Bound task allocation too: don't create thousands of waiting
                # tasks merely because a provider issued a very large batch.
                end = min(end, index + self.max_parallel)
                async with asyncio.TaskGroup() as group:
                    for call in calls[index:end]:
                        group.create_task(execute(call))
                index = end

        ordered = [results[c["id"]] for c in calls]
        return BatchObservation(tuple(message for _, message in ordered),
            pending_question=any(e.pending_question for e, _ in ordered),
            unknown_operations=tuple(e.operation_id for e, _ in ordered if e.uncertain),
            execution_waits=tuple(dict.fromkeys(w for e, _ in ordered for w in e.execution_waits)))

    @staticmethod
    def _check_pair(call, message):
        if not isinstance(message, ToolMessage):
            raise TypeError("publisher_must_return_tool_message")
        if (message.tool_call_id != call["id"]
                or (message.name and message.name != call["name"])):
            raise ValueError("operation_observation_pairing_mismatch")

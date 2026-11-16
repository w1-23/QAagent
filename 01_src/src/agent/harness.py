"""Production LangChain agent adapter; LangGraph owns model/tool transitions.

Domain-free policy hooks preserve the existing metered ModelDriver, batch
scheduler, operation runner and publication boundary. No direct tool fallback.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import inspect
import os
from pathlib import Path
import time
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from langgraph.errors import NodeCancelledError
from pydantic import Field
from typing_extensions import NotRequired

from .events import LoopCheckpoint, LoopEvent
from .runtime import AdmissionStopped, RunResult, RunLimits


class HarnessState(AgentState):
    model_steps: NotRequired[int]
    host_state: NotRequired[dict]
    batch_notes: NotRequired[list[str]]
    batch_status: NotRequired[str]
    unknown_operations: NotRequired[list[str]]
    stop_status: NotRequired[str]
    stop_reason: NotRequired[str]
    # Raw rejected responses remain in the encrypted execution checkpoint,
    # including provider-private state; they are never public tool evidence.
    rejected_responses: NotRequired[list[AIMessage]]


class _DriverModel(BaseChatModel):
    driver: Any = Field(exclude=True)
    identity: Any = Field(exclude=True)

    @property
    def _llm_type(self):
        return "citrus-metered-provider"

    def bind_tools(self, tools, **kwargs):
        # ModelDriver binds the authoritative allowed schemas after admission.
        return self

    def _generate(self, *args, **kwargs):
        raise RuntimeError("async_model_required")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        message = await self.driver(tuple(messages), self.identity)
        if not isinstance(message, AIMessage):
            raise TypeError("model_must_return_ai_message")
        message = message.model_copy(update={"additional_kwargs": {
            **message.additional_kwargs, **self.identity.message_metadata("assistant")}})
        return ChatResult(generations=[ChatGeneration(message=message)])


def _forbidden_tool(**kwargs):
    raise RuntimeError("direct_tool_execution_forbidden")


@contextmanager
def execution_lock(root: Path, identity):
    """OS-released process lock: no stale lease permits overlapping execution."""
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(repr(identity).encode()).hexdigest()
    with (root / (digest + ".lock")).open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("agent_run_already_active") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("agent_run_already_active") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@dataclass
class HarnessRuntime:
    model: Any
    execute: Any
    admit: Any
    check_final: Any
    limits: RunLimits
    schemas: list[dict]
    on_event: Any = None
    checkpoint: Any = None
    prepare: Any = None
    export_host: Any = None
    restore_host: Any = None
    validate_resume: Any = None
    recover_output: bool = False

    async def run(self, identity, messages, *, saver, resume=False, checkpoint_config=None):
        adapter = self
        latest = {"messages": list(messages), "model_steps": 0}
        batch_task = None
        batch_key = None

        def control(text):
            return SystemMessage(content=text, additional_kwargs=identity.message_metadata("internal_control"))

        async def observed(state, phase="ready"):
            nonlocal latest
            latest = dict(state)
            if adapter.restore_host and state.get("host_state") is not None:
                adapter.restore_host(state["host_state"])
            if adapter.checkpoint:
                from .runtime import _pending_calls
                await adapter.checkpoint(LoopCheckpoint(identity, tuple(state["messages"]),
                    state.get("model_steps", 0), phase, _pending_calls(state["messages"])))

        async def event(kind, state, ids=()):
            if adapter.on_event:
                await adapter.on_event(LoopEvent(kind, identity, state.get("model_steps", 0), tuple(ids),
                    state.get("stop_status", ""), state.get("stop_reason", "")))

        def host_snapshot():
            return adapter.export_host() if adapter.export_host else {}

        async def admit(history):
            admitted = adapter.admit(history, identity)
            if inspect.isawaitable(admitted):
                await admitted

        class Policy(AgentMiddleware):
            state_schema = HarnessState

            @hook_config(can_jump_to=["end"])
            async def abefore_model(self, state, runtime):
                await observed(state)
                status = state.get("batch_status", "")
                if status in {"waiting", "unknown"}:
                    return {"stop_status": "waiting" if status == "waiting" else "incomplete",
                            "stop_reason": "user_input_required" if status == "waiting" else "operation_outcome_unknown",
                            "jump_to": "end"}
                if state.get("model_steps", 0) >= adapter.limits.max_model_steps:
                    return {"stop_status": "incomplete", "stop_reason": "model_step_limit", "jump_to": "end"}
                if time.monotonic() >= adapter.limits.deadline:
                    raise AdmissionStopped("deadline")
                notes = list(state.get("batch_notes", []))
                if adapter.prepare:
                    notes.extend(await adapter.prepare(identity))
                additions = [control(note) for note in notes]
                await admit(tuple([*state["messages"], *additions]))
                update = {"messages": additions, "batch_notes": [], "batch_status": "",
                          "model_steps": state.get("model_steps", 0)+1, "host_state": host_snapshot()}
                # Before-model state is synchronously checkpointed before the call.
                await observed({**state, **update, "messages": [*state["messages"], *additions]}, "model")
                await event("model_started", latest)
                return update

            @hook_config(can_jump_to=["model", "end"])
            async def aafter_model(self, state, runtime):
                await observed(state, "tools")
                response = state["messages"][-1]
                if not isinstance(response, AIMessage):
                    raise TypeError("model_must_return_ai_message")
                reason = response.response_metadata.get("finish_reason")
                reason = "length" if reason in {"max_tokens", "max_output_tokens"} else reason
                invalid = "invalid_tool_call" if response.invalid_tool_calls else ""
                if reason in {"length", "content_filter", "error", "unknown", "aborted"}:
                    invalid = "model_" + reason
                ids = [call.get("id") for call in response.tool_calls]
                previous = {call.get("id") for message in state["messages"][:-1]
                    if isinstance(message, AIMessage)
                    and message.additional_kwargs.get("context_turn_id") == identity.user_turn_id
                    for call in message.tool_calls}
                if ids and (any(not isinstance(cid, str) or not cid for cid in ids)
                            or len(set(ids)) != len(ids) or previous.intersection(ids)):
                    invalid = "invalid_or_reused_tool_call_id"
                await event("model_finished", state, ids)
                if (adapter.recover_output and (invalid == "invalid_tool_call" or (
                        invalid == "model_length" and not response.response_metadata.get("citrus_output_limit_exhausted")))):
                    # No tool in this response has been dispatched. Preserve raw
                    # arguments privately, and pair every valid native call with
                    # an explicit not_run result before asking the SAME model.
                    cleaned = response.model_copy(deep=True)
                    cleaned.invalid_tool_calls = []
                    cleaned.additional_kwargs.pop("tool_calls", None)
                    feedback_reason = "model_length" if invalid == "model_length" else "invalid_tool_arguments"
                    feedback = ("模型输出达到单次输出上限" if invalid == "model_length" else
                                "工具参数不是完整合法的JSON，无法解析")
                    additions = [cleaned]
                    additions.extend(ToolMessage(content=f"调用未执行：{feedback}。请按schema重新生成完整参数。",
                        tool_call_id=call["id"], name=call["name"], status="error",
                        additional_kwargs={**identity.message_metadata("tool"), "execution_dispatched": False},
                        artifact={"_tool_outcome": {"status": "not_run", "reason_code": feedback_reason}})
                        for call in response.tool_calls)
                    additions.append(control(f"上一条提议被拒绝：{feedback}，所有提议调用均未执行。"
                        "请利用已保存的结果继续本轮任务：缩短说明，按需拆分长代码/文件操作，"
                        "重新生成完整参数或精简的最终答复；不要把截断文本当成已完成结果。"))
                    await event("model_output_rejected", state, ids)
                    return {"messages": additions, "rejected_responses": [*state.get("rejected_responses", []), response],
                            "host_state": host_snapshot(), "jump_to": "model"}
                if invalid:
                    return {"stop_status": "incomplete", "stop_reason": invalid, "jump_to": "end"}
                if not response.tool_calls:
                    check = await adapter.check_final(response, tuple(state["messages"]), identity)
                    if not check.accepted:
                        return {"messages": [control("Final result validation returned these observations:\n" + "\n".join(check.observations))],
                                "host_state": host_snapshot(), "jump_to": "model"}
                    return {"stop_status": "complete", "stop_reason": "answered", "host_state": host_snapshot()}
                return None

            async def awrap_tool_call(self, request, handler):
                nonlocal batch_task, batch_key
                response = next(m for m in reversed(request.state["messages"]) if isinstance(m, AIMessage))
                key = tuple(call["id"] for call in response.tool_calls)
                if batch_key != key:
                    batch_key = key
                    async def dispatch():
                        await observed(request.state, "tools")
                        await admit(tuple(request.state["messages"]))
                        await event("batch_started", request.state, key)
                        result = await adapter.execute(response, identity)
                        if {m.tool_call_id for m in result.messages} != set(key) or len(result.messages) != len(key):
                            raise ValueError("executor_observation_pairing_mismatch")
                        await event("batch_finished", request.state, key)
                        return result
                    batch_task = asyncio.create_task(dispatch())
                batch = await batch_task
                message = next(m for m in batch.messages if m.tool_call_id == request.tool_call["id"])
                if message.name and message.name != request.tool_call["name"]:
                    raise ValueError("executor_observation_name_mismatch")
                message = message.model_copy(update={"additional_kwargs": {
                    **message.additional_kwargs, **identity.message_metadata("tool")}})
                if message.tool_call_id == key[0]:
                    return Command(update={"messages": [message], "host_state": host_snapshot(),
                        "batch_notes": list(batch.context_notes),
                        "batch_status": "unknown" if batch.unknown_operations else "waiting" if batch.pending_question else "",
                        "unknown_operations": list(batch.unknown_operations)})
                return message

        tools = [StructuredTool(name=s["function"]["name"], description=s["function"]["description"],
                                args_schema=s["function"]["parameters"], func=_forbidden_tool) for s in self.schemas]
        graph = create_agent(_DriverModel(driver=self.model, identity=identity), tools=tools,
                             middleware=[Policy()], checkpointer=saver)
        config = {"configurable": checkpoint_config or {"thread_id": identity.run_id},
                  "recursion_limit": max(64, self.limits.max_model_steps * 6 + 20)}
        snapshot = await graph.aget_state(config)
        if snapshot.values:
            if not resume:
                raise ValueError("existing_turn_requires_explicit_resume")
            await observed(snapshot.values)
            if self.validate_resume:
                self.validate_resume()
            if snapshot.values.get("stop_status"):
                notes = (await adapter.prepare(identity) if adapter.prepare
                         and snapshot.values.get("stop_reason") == "answered" else ())
                if not notes:
                    return self._result(snapshot.values)
                # A crash can leave an accepted graph checkpoint before API
                # finalization. New user requirements reopen the SAME graph.
                await graph.aupdate_state(config, {"messages": [control(n) for n in notes],
                    "stop_status": "", "stop_reason": "", "host_state": host_snapshot(),
                    "jump_to": "model"}, as_node="Policy.after_model")
            initial = None
        else:
            if resume:
                raise ValueError("execution_checkpoint_missing")
            initial = {"messages": list(messages), "model_steps": 0, "host_state": host_snapshot()}
        await event("run_started", latest)
        timer = asyncio.timeout(max(0, self.limits.deadline-time.monotonic())
                                if self.limits.deadline != float("inf") else None)
        try:
            async with timer:
                state = await graph.ainvoke(initial, config, durability="sync")
            if timer.expired():
                state = {**latest, "stop_status": "incomplete", "stop_reason": "deadline"}
        except AdmissionStopped as exc:
            state = {**latest, "stop_status": "incomplete", "stop_reason": exc.reason}
        except (asyncio.CancelledError, NodeCancelledError):
            state = {**latest, "stop_status": "cancelled", "stop_reason": "cancelled"}
        except TimeoutError:
            if not timer.expired():
                raise
            state = {**latest, "stop_status": "incomplete", "stop_reason": "deadline"}
        finally:
            if batch_task and not batch_task.done():
                batch_task.cancel()
                await asyncio.gather(batch_task, return_exceptions=True)
        # A cancelled tool may already have a durable operation observation.
        # Keep the current host projection instead of restoring the pre-tool one.
        state = {**state, "host_state": host_snapshot()}
        if state.get("stop_status") and state.get("stop_reason") != "answered":
            await graph.aupdate_state(config, {key: state[key] for key in
                ("stop_status", "stop_reason", "host_state")})
        await observed(state, "stopped")
        await event("run_finished", state)
        return self._result(state)

    @staticmethod
    def _result(state):
        from .runtime import _pending_calls
        status = state.get("stop_status", "incomplete")
        messages = tuple(state.get("messages", []))
        return RunResult(status, state.get("stop_reason", "execution_stopped"), messages,
            state.get("model_steps", 0), messages[-1] if status == "complete" else None,
            tuple(state.get("unknown_operations", [])), _pending_calls(messages))

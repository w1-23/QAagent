"""Production supervisor adapter. AgentRuntime owns the only model/action loop.

The surrounding graph remains a load/save compatibility shell. Presets supply
capabilities and local methods; they do not supply a second task controller.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import logging
import time

from langchain_core.messages import AIMessage, ToolMessage

from .execution import CapabilityExecutor, ToolExecution
from .host_policy import allowed_presets, system_prompt
from .host_tools import HostTools
from .identity import ExecutionIdentity
from .model import ModelDriver
from .preset_catalog import build_preset_catalog
from .runtime import AdmissionStopped, AgentRuntime, BatchObservation, FinalCheck, RunLimits, RunResult

logger = logging.getLogger(__name__)


async def general_agent_node(state: dict) -> dict:
    from src.core import tracing
    from src.core.request_context import ContextEpoch, _epoch
    from src.core.execution_ledger import ExecutionLedger, bind_execution_ledger, reset_execution_ledger
    from src.tools.interaction import bind_question_context, reset_question_context
    from src.graph.session_nodes import _release_candidate_pool
    identity = ExecutionIdentity(int(state.get("user_id") or 0), str(state.get("session_id") or ""),
        str(state.get("request_id") or ""), tracing.get_job_id())
    state = dict(state)
    state["job_id"] = identity.run_id
    ledger = ExecutionLedger(session_id=identity.session_id, request_id=identity.user_turn_id)
    ledger_token = bind_execution_ledger(ledger)
    epoch_token = _epoch.set(ContextEpoch(history_load_status=str(state.get("history_load_status") or "unknown")))
    question_token = bind_question_context({})
    # Scientific task scope is loaded only if its capability is actually used.
    try:
        return await _run(state, identity, ledger)
    finally:
        reset_question_context(question_token)
        _epoch.reset(epoch_token)
        reset_execution_ledger(ledger_token)
        _release_candidate_pool(state)


async def _run(state, identity, ledger):
    from src.config import settings, get_deepseek_model
    from src.core import figures, task_events, tracing
    from src.core.cancel import is_cancelled
    from src.core.context_manager import assemble_supervisor_messages, build_human_message, build_loaded_context
    from src.core.llm_pool import get_llm
    from src.core.progress_bus import emit_status
    from src.core.request_context import _epoch
    from src.graph import agent_operations as ops
    from src.graph.model_driver import _model_timeout, request_deadline_from_settings
    from src.graph.request_plan import executor_authorizations, merge_task_instructions

    started = time.monotonic()
    deadline = float(state.get("request_deadline") or request_deadline_from_settings())
    figures.begin_request()
    catalog = build_preset_catalog()
    host = HostTools(identity, state)
    host.assert_context()
    pool = ops._bind_request_candidate_pool(state)
    # A table attachment is a runtime fact; no scientific module is needed to
    # offer the catalogue or answer an unrelated question.
    try:
        from src.research.context import get_request_tables
        tables = get_request_tables()
    except ImportError:
        tables = []
    if state.get("continuation_context"):
        from src.graph.task_continuation import restore_continuation
        from src.research.tools import validate_artifact_links
        inherited, _ = restore_continuation(state, tables,
            validate_artifacts=lambda links: validate_artifact_links(links,
                session_id=identity.session_id, owner_user_id=identity.owner_user_id))
        host.records.extend(inherited)
    def refresh_allowed():
        host.allowed_names = allowed_presets(state, has_tables=bool(tables), has_pool=pool is not None)
        tracing.set_request_authorizations(executor_authorizations(str(state.get("query") or "")))
        return set(host.allowed_names)
    refresh_allowed()
    loaded = build_loaded_context(state, mode="general_agent", format_hint=state.get("format_hint"))
    human = build_human_message(loaded, retrieval_context=str(state.get("candidate_pool_notice") or "") if pool else "")
    human.additional_kwargs.update(identity.message_metadata("user"), user_text=str(state.get("query") or ""),
        attachment_refs=state.get("attachment_refs") or [])
    if state.get("attachment_context"):
        human.content = str(human.content) + "\n\n" + str(state["attachment_context"])
    images = state.get("image_attachments") or []
    if images:
        if ops._main_model_supports_image_input():
            human.content = [{"type": "text", "text": str(human.content)}] + [
                {"type": "image_url", "image_url": {"url": image["url"]}}
                for image in images if image.get("url")]
        else:
            human.content = str(human.content) + "\n当前主模型不支持图片输入；不可声称已看到原图。"
    prompt = system_prompt(host.allowed_names)
    if "local_hybrid_search" in host.allowed_names:
        try:
            from src.retrieval.corpus_registry import load_corpus_registry
            prompt += "\n已登记的本地资料类别（选择工具时使用真实family id）：\n" + load_corpus_registry().describe_families()
        except Exception:
            prompt += "\n本地资料类别目录暂不可用；不得猜测family id。"
    messages, trace_start = assemble_supervisor_messages(prompt, state, human)
    messages = ops._sanitize_history_tool_protocol(messages)
    model = get_llm(model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
        base_url=settings.RESOLVED_MAIN_BASE_URL, temperature=settings.TEMPERATURE_MAIN,
        max_tokens=settings.MAX_TOKENS, timeout=_model_timeout())
    # Provisional answers are not streamed as accepted answers. The final
    # accepted bytes are published once, then passed to session_save unchanged.
    driver = ModelDriver(model, catalog, lambda _: refresh_allowed())
    scheduler = CapabilityExecutor(catalog, lambda _: refresh_allowed(), host.invoke, host.publish)
    latest = None
    loaded_methods = set()

    async def checkpoint(value):
        nonlocal latest
        latest = value  # protected process state, not a public serialized trace

    async def event(value):
        payload = asdict(value)
        task_events.append_event(identity.run_id, "agent_loop", payload)
        if value.kind == "batch_started":
            emit_status("agent_progress", message=f"执行所选的 {len(value.tool_call_ids)} 项操作")

    def admit(history, _):
        if is_cancelled():
            raise asyncio.CancelledError()
        host.assert_context()
        _epoch.get().working_state = host.working_state()

    async def prepare(_):
        updates = task_events.apply_updates(identity.run_id)
        if not updates:
            return ()
        state["query"] = merge_task_instructions(str(state.get("query") or ""), updates)
        tracing.set_original_query(state["query"])
        refresh_allowed()
        return ("用户在本轮补充的要求（保留已完成事实，调整未完成工作）：\n" +
                "\n".join(row["instruction"] for row in updates),)

    async def not_run(response, reason, notes=()):
        observations = []
        for call in response.tool_calls:
            observation = ToolMessage(content=reason, tool_call_id=call["id"], name=call["name"],
                status="error", artifact={"_tool_outcome": {"status": "not_run", "reason_code": "preparation_required"}})
            observations.append(await host.publish(call, ToolExecution(observation), identity))
        return BatchObservation(tuple(observations), context_notes=tuple(notes))

    async def execute(response, _):
        if is_cancelled():
            raise asyncio.CancelledError()
        notes = await prepare(identity)
        if notes:
            return await not_run(response, "用户要求已更新；本批提案未执行，请依据新要求重新选择动作。", notes)
        names = {call["name"] for call in response.tool_calls}
        # Load a selected method before admitting its proposed action. The model
        # gets a not-run observation and can revise its arguments using the method.
        if names & {"create_research_figure", "run_research_figure_script"} and "nature" not in loaded_methods:
            try:
                from src.graph.scene_skills import figure_scene_guidance
                method = figure_scene_guidance(skill_reader="research_workspace" in host.allowed_names)
            except Exception:
                return await not_run(response, "初始科研图的固定Nature方法不可用，图件操作未执行；其他合法能力仍可使用。")
            loaded_methods.add("nature")
            return await not_run(response, "已加载初始科研绘图方法。本批尚未执行，请按方法检查和调整参数后继续。", (method,))
        if "word_document" in names and "word" not in loaded_methods:
            from src.prompts.loader import _read_prompt_cached
            method = _read_prompt_cached("skills/word-export/SKILL.md")
            if not method:
                return await not_run(response, "Word方法资源不可用，本批尚未执行。")
            loaded_methods.add("word")
            return await not_run(response, "已加载Word工具方法，请按实际参数规范继续；可组合其他获授权能力。", (method,))
        script_token = None
        if names & {"research_workspace", "run_research_figure_script"}:
            from src.research.script_runtime import bind_task_scope
            scope = str(state.get("script_budget_scope") or
                        (state.get("context_working_state") or {}).get("script_budget_scope") or identity.user_turn_id)
            state["script_budget_scope"] = scope
            script_token = bind_task_scope(scope)
        try:
            return await scheduler(response, identity)
        finally:
            if script_token is not None:
                from src.research.script_runtime import reset_task_scope
                reset_task_scope(script_token)

    async def check_final(answer, history, _):
        notes = await prepare(identity)
        if notes:
            return FinalCheck(False, notes)
        text = str(answer.content or "")
        if not text.strip() or ops._DSML_PROTOCOL_RE.search(text):
            return FinalCheck(False, ("尚未形成有效答复。需要工具时使用原生工具调用；不要用工具协议文本冒充答案。",))
        if ops._sanitize_answer_artifact_links(text) != text:
            return FinalCheck(False, ("回答中的文件链接未通过真实资产、账号和字节校验。请检查保存回执，修复链接或如实说明未交付。",))
        return FinalCheck()

    runtime = AgentRuntime(driver, execute, admit, check_final,
        RunLimits(max_model_steps=settings.AGENT_MAX_MODEL_CALLS, deadline=deadline),
        on_event=event, checkpoint=checkpoint, prepare=prepare)
    try:
        result = await runtime.run(identity, messages)
    except Exception as exc:
        # Never restart the loop or retry a possibly dispatched operation here.
        logger.warning("General agent stopped: %s", type(exc).__name__)
        reason = "execution_error:" + type(exc).__name__
        if isinstance(exc, task_events.TaskConflict):
            reason = "resource_or_execution_conflict"
        result = RunResult("incomplete", reason,
            latest.messages if latest else tuple(messages), latest.model_steps if latest else 0,
            pending_tool_calls=latest.pending_tool_calls if latest else ())
    return await _finish(state, identity, ledger, host, result, trace_start, started)


def _settled_trace(messages, host, result):
    """Keep actual call IDs and observed outcomes when stopping mid-batch."""
    by_id = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    trace = []
    for message in messages:
        if isinstance(message, ToolMessage):
            continue  # paired immediately after its issuing assistant below
        trace.append(message)
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            cid = call.get("id")
            if not cid:
                continue
            observed = by_id.get(cid) or host.messages.get(cid)
            if observed is None:
                dispatched = cid in host.dispatched
                from src.tools.registry import get_tool_spec
                spec = get_tool_spec(call["name"])
                uncertain = dispatched and spec is not None and not spec.is_readonly
                status = "unknown" if uncertain else "cancelled" if dispatched else "not_run"
                observed = ToolMessage(
                    content=("操作已调度，但结果尚未确认；不得重放，须先对账。" if uncertain else
                             "本轮已停止，此调用没有可采用的完成结果。" if dispatched else "本轮已停止，此调用未执行。"),
                    tool_call_id=cid, name=call["name"], status="error",
                    artifact={"_tool_outcome": {"status": status, "reason_code": result.reason}},
                    additional_kwargs=host.identity.message_metadata("tool"))
                from src.graph.agent_operations import _emit_completed_tool_step
                _emit_completed_tool_step(call, observed, host.records)
            trace.append(observed)
    return trace


async def _finish(state, identity, ledger, host, result, trace_start, started):
    from src.core import figures, task_events
    from src.core.progress_bus import emit_answer_start, emit_text
    from src.core.task_result import build_task_result
    from src.core.turn_outcome import TurnOutcome
    from src.graph.agent_operations import _safe_persisted_trace
    trace = _settled_trace(list(result.messages)[trace_start:], host, result)
    if result.status == "complete":
        answer = str(result.answer.content or "")
        if "[[FIGURE:" in answer:
            answer = figures.expand(answer)
        status = "completed"
    elif result.status == "waiting" and host.pending_question:
        from src.core.user_questions import save_question_context
        save_question_context(identity.run_id, host.question_context())
        answer = str(host.pending_question["prompt"])
        status = "awaiting_input"
    elif result.status == "cancelled":
        answer = "本轮已停止。已完成的操作与文件已保留，未确认的写入须先对账。"
        status = "cancelled"
    else:
        answer = "本轮尚未完成。已保留取得的结果与文件，没有继续执行未完成操作。"
        if result.reason == "operation_outcome_unknown" or result.pending_tool_calls:
            answer += " 部分操作结果尚未确认，需先对账后才能继续。"
        status = "partial" if host.messages else "failed"
    ledger.close(status="unknown" if result.pending_tool_calls else
                 "cancelled" if status == "cancelled" else "failed", reason_code=result.reason)
    outcome = TurnOutcome(status, result.reason, result.reason)
    task_result = build_task_result(host.records, requested_status=status, turn_outcome=outcome)
    task_events.release_finalization(identity.run_id)
    emit_answer_start()
    emit_text(answer)
    elapsed = (time.monotonic() - started) * 1000
    return {"query": state["query"], "answer": answer, "gen_time_ms": elapsed,
        "main_results": host.main_results, "web_results": host.web_results,
        "tools_called": list(dict.fromkeys(r["name"] for r in host.records)),
        "tool_status": list(host.records), "execution_ledger": ledger.as_dict(),
        "context_working_state": host.working_state(), "turn_trace": _safe_persisted_trace(trace),
        "turn_outcome": outcome.as_dict(), "awaiting_input": status == "awaiting_input",
        "pending_question": host.pending_question,
        "export_gate": {"status": "not_applicable", "reason": "model_owned_delivery"},
        "_trace": {"node": "supervisor", "elapsed_ms": elapsed, "stop_reason": result.reason,
            "model_steps": result.model_steps, "task_result": task_result.as_dict(),
            "summary": f"{result.model_steps} model steps; {result.reason}"}}

"""Production supervisor adapter. Deep Agents owns the single model/action graph.

The surrounding graph remains a load/save compatibility shell. Presets supply
capabilities and local methods; they do not supply a second task controller.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
import logging
import os
from pathlib import Path
import time

from langchain_core.messages import AIMessage, ToolMessage

from .execution import CapabilityExecutor, ToolExecution
from .host_policy import allowed_presets, system_prompt
from .host_tools import HostTools
from .identity import ExecutionIdentity
from .model import ModelDriver
from .preset_catalog import build_preset_catalog
from .runtime import AdmissionStopped, BatchObservation, FinalCheck, RunLimits, RunResult
from .harness import HarnessRuntime, execution_lock

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
    output = None
    preserve_pool = False
    try:
        output = await _run(state, identity, ledger)
        if output.get("execution_suspension"):
            from .execution_outcome import validate_execution_suspension
            validate_execution_suspension(output["execution_suspension"], identity)
            preserve_pool = True
        return output
    finally:
        from src.browse.service import release as release_browser
        try:
            await release_browser(identity.owner_user_id, identity.session_id, identity.run_id)
        except (OSError, TimeoutError):
            logger.warning("Browser cleanup did not confirm exit for job %s", identity.run_id)
        reset_question_context(question_token)
        _epoch.reset(epoch_token)
        reset_execution_ledger(ledger_token)
        if preserve_pool:
            from src.retrieval.candidate_pool import clear_current_candidate_pool
            clear_current_candidate_pool()
        else:
            _release_candidate_pool(state)


async def _run(state, identity, ledger):
    from src.config import settings, get_deepseek_model
    from src.core import figures, task_events, tracing
    from src.core.cancel import is_cancelled
    from src.core.context_manager import assemble_supervisor_messages, build_human_message, build_loaded_context
    from src.core.llm_pool import get_llm
    from src.core.request_context import _epoch
    from src.graph import agent_operations as ops
    from src.graph.model_driver import _model_timeout, request_deadline_from_settings
    from src.graph.request_plan import executor_authorizations, merge_task_instructions

    started = time.monotonic()
    # A fresh composer request is a new script task, even when the session still
    # carries an older task's assets. Only a durable question child can inherit
    # its parent's scope; same-job recovery restores it from the checkpoint below.
    state["script_budget_scope"] = identity.user_turn_id
    if state.get("continuation_context") and not state.get("agent_resume"):
        from src.core import jobs, user_questions
        continuation = user_questions.get_continuation(identity.run_id,
            owner_user_id=identity.owner_user_id, session_id=identity.session_id)
        parent_id = continuation["parent_job_id"]
        parent = jobs.get_job(parent_id)
        if (parent_id != state.get("parent_job_id") or not parent
                or parent["owner_user_id"] != identity.owner_user_id
                or parent["session_id"] != identity.session_id):
            raise task_events.TaskConflict("script_budget_identity_conflict")
        state["script_budget_scope"] = str(
            (continuation.get("context_working_state") or {}).get("script_budget_scope")
            or parent["request_id"])
    deadline = float(state.get("request_deadline") or request_deadline_from_settings())
    figures.begin_request()
    catalog = build_preset_catalog()
    from .context_tools import register_context_reader
    register_context_reader(catalog)
    from .plan_tools import register_plan_tools, PLAN_TOOL_NAMES
    register_plan_tools(catalog)
    from .workspace_tools import register_workspace_tools, WORKSPACE_TOOL_NAMES
    register_workspace_tools(catalog)
    from .code_tools import register_code_tools, CODE_TOOL_NAMES
    register_code_tools(catalog)
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
        host.allowed_names.update({"read_context_evidence", "read_skill", "load_skill"})
        host.allowed_names.update(WORKSPACE_TOOL_NAMES)
        host.allowed_names.update(PLAN_TOOL_NAMES)
        if os.getenv("CITRUS_CODE_EXECUTION") == "offline-development":
            from src.code_execution.presence import worker_available
            from src.config import PROJECT_ROOT as runtime_root
            host.allowed_names.update(CODE_TOOL_NAMES)
            if not worker_available(runtime_root / "state" / "code_executions.sqlite3"):
                host.allowed_names.difference_update({"code_execute", "code_environment_install"})
        tracing.set_request_authorizations(executor_authorizations(str(state.get("query") or "")))
        return set(host.allowed_names)
    refresh_allowed()
    from .skill_adapter import SkillSession, validate_scenario_selection, validate_selected_skills
    from .skill_tools import register_skill_reader, skill_execution_context
    skills = SkillSession(identity, refresh_allowed)
    register_skill_reader(catalog)
    # Host readiness remains independent of method loading; new generic tools
    # registered by the application must not inherit a scientific allowlist.
    loaded = build_loaded_context(state, mode="general_agent", format_hint=state.get("format_hint"))
    human = build_human_message(loaded, retrieval_context=str(state.get("candidate_pool_notice") or "") if pool else "")
    try:
        selected_metadata = await asyncio.to_thread(
            validate_scenario_selection, state.get("selected_skills"))
    except (OSError, ValueError, KeyError):
        # The API validates its own selection contract. A package becoming
        # unavailable afterwards must not disable this request's other tools.
        try:
            # Historical request metadata keeps its exact archived identity.
            # New requests are checked against the current catalog at the API.
            selected_metadata = await asyncio.to_thread(
                validate_selected_skills, state.get("selected_skills"))
        except (OSError, ValueError, KeyError):
            selected_metadata = []
        human.content = str(human.content) + "\n所选历史技能不是当前场景发行物；可继续使用其他已授权能力，不得声称已加载该方法。"
    human.additional_kwargs.update(identity.message_metadata("user"), user_text=str(state.get("query") or ""),
        attachment_refs=state.get("attachment_refs") or [], selected_skills=selected_metadata)
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
    prompt = await asyncio.to_thread(system_prompt, host.allowed_names)
    if "local_hybrid_search" in host.allowed_names:
        try:
            from src.retrieval.corpus_registry import load_corpus_registry
            prompt += "\n已登记的本地资料类别（选择工具时使用真实family id）：\n" + load_corpus_registry().describe_families()
        except Exception:
            prompt += "\n本地资料类别目录暂不可用；不得猜测family id。"
    # Keep the brief public introduction after the internal tool directories.
    intro_capabilities = [description for names, description in (
        ({"browse_web"}, "浏览公开网页"),
        ({"local_hybrid_search", "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"}, "查找资料"),
        ({"inspect_uploaded_table"}, "分析附件"),
        ({"code_execute"}, "运行离线代码"),
    ) if names & host.allowed_names]
    intro_example = "我是 CitrusScholar，一名通用智能助手。我可以回答日常和专业问题"
    if intro_capabilities:
        intro_example += "，也可以" + "、".join(intro_capabilities)
    prompt += ("\n如果用户只问身份或能力，用一两句自然的话回答，不加标题、列表、工具名称、"
               "资料库类别或追问。可以参考这样的长度和用词：" + intro_example + "。")
    messages, trace_start = assemble_supervisor_messages(prompt, state, human)
    messages = ops._sanitize_history_tool_protocol(messages)
    model = get_llm(model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
        base_url=settings.RESOLVED_MAIN_BASE_URL, temperature=settings.TEMPERATURE_MAIN,
        max_tokens=settings.MAX_TOKENS, timeout=_model_timeout())
    # Provisional answers are not streamed as accepted answers. The final
    # accepted bytes are published once, then passed to session_save unchanged.
    model_allowed = set()
    driver = ModelDriver(model, catalog, lambda _: set(model_allowed),
        max_output_tokens=getattr(settings, "AGENT_MAX_OUTPUT_TOKENS", 16384))

    async def invoke_scoped(tool, call, current_identity):
        # Skills supply guidance, never tool admission. HostTools and the
        # capability executor retain identity, readiness and operation policy.
        with skill_execution_context(skills):
            return await host.invoke(tool, call, current_identity)

    # Recheck actual readiness/authorization after scheduler waits. Method
    # selection and reading progress do not participate in this decision.
    scheduler = CapabilityExecutor(catalog, lambda _: refresh_allowed(),
                                   invoke_scoped, host.publish)
    latest = None
    # Selection remains pinned metadata. The main loop reads applicable method
    # pages as needed; no mandatory preload can block unrelated capabilities.
    update_version = 0
    # Private, identity-bound checkpoint state. Raw final envelopes stay in the
    # graph; only the extracted, validated answer crosses the user boundary.
    final_review = {}
    from src.core.turn_outcome import (
        completion_digest, completion_errors, parse_completion, reviewed_turn_outcome,
    )
    from .recovery import capture_original_request_state
    original_request_state = capture_original_request_state(state)

    asset_fields = ("dataset_refs", "source_refs", "script_refs", "run_refs", "plot_intent")

    def export_host():
        working = host.working_state()
        return {"identity": asdict(identity),
                "original_request_state": original_request_state, "trace_start": trace_start,
                "working_assets": deepcopy({key: working[key] for key in asset_fields}),
                "script_budget_scope": state["script_budget_scope"],
                "query": state["query"], "skills": skills.export(),
                "update_version": update_version, "final_review": deepcopy(final_review),
                "records": host.records,
                "main_results": host.main_results, "web_results": host.web_results,
                "pending_question": host.pending_question,
                "messages": list(host.messages.values()), "dispatched": host.dispatched,
                "model_output_tokens": getattr(driver, "output_tokens", None)}

    def restore_host(saved):
        nonlocal update_version, original_request_state, trace_start
        original = saved["original_request_state"]
        binding = {"user_id": identity.owner_user_id, "session_id": identity.session_id,
                   "request_id": identity.user_turn_id, "job_id": identity.run_id}
        if (saved.get("identity", asdict(identity)) != asdict(identity)
                or any(original.get(key) != value for key, value in binding.items())):
            raise task_events.TaskConflict("identity_conflict")
        assets = saved.get("working_assets")
        if assets is not None and (not isinstance(assets, dict)
                or set(assets) != set(asset_fields)
                or not isinstance(assets["plot_intent"], dict)
                or any(not isinstance(assets[key], list)
                       or any(not isinstance(ref, dict) for ref in assets[key])
                       for key in asset_fields[:-1])):
            raise task_events.TaskConflict("recovery_working_assets_invalid")
        scope = (saved.get("script_budget_scope") or original.get("script_budget_scope")
                 or (original.get("context_working_state") or {}).get("script_budget_scope")
                 or identity.user_turn_id)
        if not isinstance(scope, str) or not scope.strip() or len(scope) > 256:
            raise task_events.TaskConflict("script_budget_identity_conflict")
        original_request_state = original
        state["script_budget_scope"] = scope
        if assets is not None:
            # Restore only host-owned assets. Query/update watermarks and live
            # supplementary instructions retain their existing recovery path.
            for key in asset_fields[:-1]:
                merged = deepcopy(assets[key])
                for ref in state.get(key) or []:
                    if ref not in merged:
                        merged.append(deepcopy(ref))
                state[key] = merged
            state["plot_intent"] = {**deepcopy(state.get("plot_intent") or {}),
                                    **deepcopy(assets["plot_intent"])}
        else:
            # Compatibility with earlier checkpoints: rebuild references from
            # authenticated published messages, without executing their tools.
            ops._capture_interaction_refs(state, saved.get("messages", []))
        trace_start = int(saved["trace_start"])
        state["query"] = saved["query"]
        skills.restore(saved.get("skills"))
        update_version = int(saved.get("update_version", 0))
        final_review.clear()
        final_review.update(deepcopy(saved.get("final_review") or {}))
        if final_review and (final_review.get("request_version") != update_version
                or final_review.get("request_hash") != completion_digest(state["query"])):
            final_review.clear()
        host.records[:] = saved.get("records", [])
        host.main_results[:] = saved.get("main_results", [])
        host.web_results[:] = saved.get("web_results", [])
        host.pending_question = saved.get("pending_question")
        host.messages = {m.tool_call_id: m for m in saved.get("messages", [])}
        host.dispatched = dict(saved.get("dispatched", {}))
        driver.output_tokens = max(int(getattr(driver, "output_tokens", 0) or 0),
                                   int(saved.get("model_output_tokens") or 0)) or None
        tracing.set_original_query(state["query"])
        refresh_allowed()

    async def checkpoint(value):
        nonlocal latest
        latest = value  # error projection; authoritative state is the encrypted graph saver

    async def event(value):
        payload = asdict(value)
        task_events.append_event(identity.run_id, "agent_loop", payload)

    async def admit(history, _):
        if is_cancelled():
            raise asyncio.CancelledError()
        host.assert_context()
        await asyncio.to_thread(skills.verify_view, history)
        _epoch.get().working_state = host.working_state()

    async def prepare(_):
        nonlocal update_version
        updates = task_events.updates_after(identity.run_id, update_version)
        if not updates:
            return ()
        state["query"] = merge_task_instructions(str(state.get("query") or ""), updates)
        update_version = max(row["version"] for row in updates)
        final_review.clear()  # A previous candidate cannot accept new requirements.
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
        script_token = None
        if names & {"research_workspace", "run_research_figure_script"}:
            from src.research.script_runtime import bind_task_scope
            script_token = bind_task_scope(state["script_budget_scope"])
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
        # Ordinary answers finish in this model step. Old pending-review
        # checkpoints can still consume their original envelope on recovery.
        from src.core.turn_outcome import completion_from_answer, repairable_proposal_reason
        legacy_review = final_review.get("phase") == "pending"
        try:
            statement = parse_completion(text) if legacy_review else completion_from_answer(text, host.records)
        except (ValueError, TypeError):
            task_events.append_event(identity.run_id, "agent_control", {"reason_code": "completion_schema_invalid"})
            return FinalCheck(False, ("旧任务的终答声明格式无效；请按原契约修正，不重新执行已完成操作。",))
        if any(row.get('name') == 'browse_web' for row in host.records):
            from src.core.progress_bus import emit_progress
            emit_progress('browse_phase', {'phase': 'organizing'})
        public_answer = statement.answer
        if "[[FIGURE:" in public_answer:
            public_answer = figures.expand(public_answer)
            statement = statement.model_copy(update={"answer": public_answer})
        native_calls = [call for message in history if isinstance(message, AIMessage)
                        for call in message.tool_calls]
        positions = {call["id"]: index for index, call in enumerate(native_calls)}
        # A rejected proposal is not an executed obligation. The agent may fix
        # parameters OR select a different appropriate tool. Bind that repair to
        # later successful goal evidence only after proving zero dispatch.
        from src.core.turn_outcome import ProposalRepair

        goal_refs = {cid for goal in statement.goals for cid in goal.evidence_call_ids}
        superseded = [row["tool_call_id"] for row in host.records
            if update_version > 0 and row.get("status") == "not_run"
            and (row.get("reason") or row.get("reason_code")) == "preparation_required"
            and row.get("tool_call_id") in host.messages
            and row["tool_call_id"] not in host.dispatched
            and not host.messages[row["tool_call_id"]].additional_kwargs.get("execution_dispatched")
            and not host.messages[row["tool_call_id"]].additional_kwargs.get("operation_uncertain")]
        repairs = list(statement.proposal_repairs)
        claimed_repairs = {claim.rejected_call_id for claim in repairs}
        if statement.status == "completed" and not statement.remaining:
            for row in host.records:
                cid = row.get("tool_call_id")
                observation = host.messages.get(cid)
                if (cid in superseded or cid in claimed_repairs or row.get("status") != "not_run"
                        or not repairable_proposal_reason(str(row.get("reason") or row.get("reason_code") or ""))
                        or (str(row.get("reason") or row.get("reason_code") or "").startswith("load_failed:")
                            and catalog.execution_mode(str(row.get("name") or "")) != "parallel")
                        or not host.proven_no_dispatch(cid)):
                    continue
                corrected = next((r.get("tool_call_id") for r in host.records
                    if r.get("status") == "complete" and r.get("tool_call_id") in goal_refs
                    and positions.get(cid, float("inf")) < positions.get(r.get("tool_call_id"), -1)), None)
                if corrected:
                    repairs.append(ProposalRepair(rejected_call_id=cid, corrected_call_id=corrected,
                        reason="提案在执行前被拒绝；本轮随后使用成功操作满足了所声明的目标。"))
            statement = statement.model_copy(update={"proposal_repairs": repairs})
        verified_repairs = []
        for claim in statement.proposal_repairs:
            observation = host.messages.get(claim.rejected_call_id)
            rejected = next((row for row in host.records if row.get("tool_call_id") == claim.rejected_call_id), {})
            if (observation is None or observation.additional_kwargs.get("operation_uncertain")
                    or (str(rejected.get("reason") or rejected.get("reason_code") or "").startswith("load_failed:")
                        and catalog.execution_mode(str(rejected.get("name") or "")) != "parallel")):
                continue
            no_dispatch = host.proven_no_dispatch(claim.rejected_call_id)
            if (no_dispatch and positions.get(claim.rejected_call_id, float("inf")) <
                    positions.get(claim.corrected_call_id, -1)):
                verified_repairs.append(claim.rejected_call_id)
        # Exploratory read receipts describe individual attempts, not additional
        # user obligations. Once the agent cites successful evidence for its
        # goals, resolve eligible source-only partials from the actual journal.
        # Do not require it to regenerate the entire answer merely to enumerate
        # every optional 404; keep the raw attempts and their notices unchanged.
        from src.core.task_result import effective_tool_records, incidental_partial_candidate
        from src.core.turn_outcome import IncidentalClaim
        from src.tools.registry import is_readonly_call
        calls = {call["id"]: call for call in native_calls}
        rows = effective_tool_records(host.records)
        referenced = {cid for goal in statement.goals for cid in goal.evidence_call_ids}
        replacement_ids = [row["tool_call_id"] for row in rows
            if row.get("tool_call_id") in referenced and row.get("status") == "complete"
            and (row.get("name") or row.get("tool_name")) not in {"load_skill", "read_skill"}
            and row["tool_call_id"] in calls
            and is_readonly_call(calls[row["tool_call_id"]]["name"],
                                 calls[row["tool_call_id"]].get("args", {}))]
        if statement.status == "completed" and not statement.remaining and replacement_ids:
            claims = list(statement.incidental_partials)
            claimed = {claim.tool_call_id for claim in claims}
            for row in rows:
                cid = row.get("tool_call_id")
                call = calls.get(cid)
                if (cid not in claimed and call and incidental_partial_candidate(row)
                        and is_readonly_call(call["name"], call.get("args", {}))):
                    claims.append(IncidentalClaim(tool_call_id=cid,
                        reason="辅助来源读取未完整返回；目标已引用成功的只读证据，原始执行限制保留。",
                        replacement_call_ids=replacement_ids))
            statement = statement.model_copy(update={"incidental_partials": claims})
        errors = list(completion_errors(statement, host.records,
            state.get("missing_requirements") or (), verified_repairs=verified_repairs,
            superseded_proposals=superseded))
        if ops._DSML_PROTOCOL_RE.search(public_answer) or not public_answer.strip():
            errors.append("invalid_public_answer")
        if ops._sanitize_answer_artifact_links(public_answer) != public_answer:
            errors.append("unverified_answer_artifact_links")
        # The receipt filter alone does not establish that a partial was a read.
        # Check the actual native call, never a model-declared operation mode.
        for claim in statement.incidental_partials:
            call = calls.get(claim.tool_call_id)
            if not call or not is_readonly_call(call["name"], call.get("args", {})):
                errors.append("incidental_operation_not_verified_readonly:" + claim.tool_call_id)
        if errors:
            task_events.append_event(identity.run_id, "agent_control", {
                "reason_code": "execution_blocker", "codes": errors})
            # An invalid link/protocol still needs correction. An execution gap
            # is delivered honestly as partial instead of forcing another search
            # merely to rewrite an internal completion declaration.
            if legacy_review or any(code in {"invalid_public_answer", "unverified_answer_artifact_links"} for code in errors):
                return FinalCheck(False, ("答复未通过执行/交付校验：" + "; ".join(errors) +
                    "。沿用现有结果修正；不能假称未完成的操作已完成。",))
            limits = "\n\n执行限制：部分操作或必需范围未完成，不能视为整项任务已完成。"
            public_answer += limits
            statement = statement.model_copy(update={"answer": public_answer, "status": "partial",
                "remaining": errors, "goals": statement.goals if goal_refs else [],
                "proposal_repairs": [claim for claim in statement.proposal_repairs
                                     if claim.rejected_call_id in verified_repairs]})
        outcome = reviewed_turn_outcome(statement, host.records, identity=asdict(identity),
            request_version=update_version, request_hash=completion_digest(state["query"]),
            verified_repairs=verified_repairs, superseded_proposals=superseded)
        host.publish_resolutions(outcome)
        final_review.update(phase="accepted", answer=public_answer, outcome=outcome.as_dict(),
            request_version=update_version, request_hash=completion_digest(state["query"]))
        return FinalCheck()

    from .batch_journal import BatchJournal
    from .checkpoint_store import open_checkpoint, checkpoint_scope
    from src.config import PROJECT_ROOT
    journal = BatchJournal(identity)
    from .context_projection import EncryptedContextArchive, WorkingContextProjection
    async def context_projected(payload):
        task_events.append_event(identity.run_id, "context_projection", payload)
    projection = WorkingContextProjection(
        archive=EncryptedContextArchive(PROJECT_ROOT / "state", identity,
            (settings.RESOLVED_MAIN_BASE_URL, get_deepseek_model())),
        summary_model=model,
        enabled=bool(settings.CONTEXT_BUDGET_ENABLED and state.get("context_compression", True)),
        deadline=deadline, trigger_tokens=settings.CONTEXT_WORKING_SOFT_TOKENS,
        target_tokens=min(settings.CONTEXT_WORKING_TARGET_TOKENS, settings.CONTEXT_WORKING_SOFT_TOKENS),
        required_message_ids=lambda: skills.required_message_ids,
        readback_available="read_context_evidence" in refresh_allowed(), on_projection=context_projected)

    async def metered_model(history, current_identity):
        # before_model's host_state watermark is already synchronously committed.
        task_events.acknowledge_updates(identity.run_id, update_version)
        view = await projection.project(history)
        await asyncio.to_thread(skills.verify_view, view)
        # ModelDriver's synchronous schema callback consumes this request-local
        # snapshot; exact hashes are revalidated before every physical proposal.
        model_allowed.clear()
        model_allowed.update(refresh_allowed())
        return await driver(view, current_identity)

    async def durable_execute(response, current_identity):
        batch = await journal.execute(response, execute)
        # Reconstruct publication views after replay of a fully saved outbox batch.
        known = {m.tool_call_id for m in host.messages.values()}
        for call, message in zip(response.tool_calls, batch.messages):
            if message.tool_call_id not in known:
                host.restore_observation(message)
            await asyncio.to_thread(skills.observe, message)
        return batch

    def validate_resume():
        host.assert_context()
        for message in host.messages.values():
            host.validate_observation(message)

    # Construct the storage-backed boundary only when Harness actually wakes a
    # suspended execution. Ordinary requests do not need workspace/account stores.
    wait_boundary = None

    def execution_wait_boundary():
        nonlocal wait_boundary
        if wait_boundary is None:
            from src.code_execution.service import ExecutionService
            from .execution_waits import ExecutionWaitBoundary
            wait_boundary = ExecutionWaitBoundary(ExecutionService(identity))
        return wait_boundary

    async def resolve_execution_waits(current_identity, wait_ids):
        return await execution_wait_boundary().resolve(current_identity, wait_ids)

    async def acknowledge_execution_waits(current_identity, wait_ids, checkpoint_ref):
        await execution_wait_boundary().acknowledge(current_identity, wait_ids, checkpoint_ref)

    runtime = HarnessRuntime(metered_model, durable_execute, admit, check_final,
        RunLimits(max_model_steps=settings.AGENT_MAX_MODEL_CALLS, deadline=deadline),
        schemas=catalog.schemas({row["name"] for row in catalog.describe()}),
        on_event=event, checkpoint=checkpoint, prepare=prepare,
        export_host=export_host, restore_host=restore_host, validate_resume=validate_resume,
        recover_output=True, resolve_execution_waits=resolve_execution_waits,
        acknowledge_execution_waits=acknowledge_execution_waits)
    try:
        with execution_lock(PROJECT_ROOT / "state" / "agent_locks", identity):
            async with open_checkpoint(PROJECT_ROOT / "state", identity,
                    (settings.RESOLVED_MAIN_BASE_URL, get_deepseek_model())) as saver:
                result = await runtime.run(identity, messages, saver=saver,
                    resume=state.get("agent_resume") is True,
                    checkpoint_config=checkpoint_scope(identity, (settings.RESOLVED_MAIN_BASE_URL, get_deepseek_model())))
                if result.status == "suspended" and result.reason == "execution_pending":
                    from .execution_outcome import validate_execution_suspension
                    from .runtime import _pending_calls
                    # Read the latest actual commit while the protected saver is
                    # open. Raw messages may be DeltaChannel fragments; only the
                    # reconstructed Harness result can establish native pairing.
                    committed = await saver.aget_tuple({"configurable": checkpoint_scope(identity,
                        (settings.RESOLVED_MAIN_BASE_URL, get_deepseek_model()))})
                    values = committed.checkpoint["channel_values"] if committed else {}
                    if (values.get("stop_status") != "suspended"
                            or values.get("stop_reason") != "execution_pending"
                            or (values.get("host_state") or {}).get("identity") != asdict(identity)
                            or values.get("execution_waits") != list(result.execution_waits)
                            or values.get("execution_wake") or values.get("unknown_operations")
                            or host.pending_question or result.unknown_operations
                            or result.pending_tool_calls or _pending_calls(result.messages)):
                        raise ValueError("execution_suspension_checkpoint_invalid")
                    reference = committed.config["configurable"]["checkpoint_id"]
                    if committed.checkpoint["id"] != reference:
                        raise ValueError("execution_suspension_checkpoint_invalid")
                    suspension = validate_execution_suspension({"identity": asdict(identity),
                        "checkpoint_ref": reference, "wait_ids": list(result.execution_waits)}, identity)
                    return {"job_id": identity.run_id, "execution_suspension": suspension}
    except Exception as exc:
        # Never restart the loop or retry a possibly dispatched operation here.
        # Log frame locations, never exception text/locals that can contain
        # provider credentials, queries or tool payloads.
        import traceback
        frames = traceback.extract_tb(exc.__traceback__)
        locations = " -> ".join(f"{frame.name}:{frame.lineno}" for frame in frames[-12:])
        logger.warning("General agent stopped: %s [%s]", type(exc).__name__, locations)
        reason = "execution_error:" + type(exc).__name__
        if isinstance(exc, task_events.TaskConflict):
            code = str(exc).split(" (", 1)[0]
            reason = code if code in {"model_budget_exhausted:deadline", "model_budget_exhausted:calls",
                "model_budget_exhausted:tokens", "context_budget_exhausted", "resource_busy",
                "durable_operation_context_required", "identity_conflict"} else "resource_or_execution_conflict"
        result = RunResult("incomplete", reason,
            latest.messages if latest else tuple(messages), latest.model_steps if latest else 0,
            pending_tool_calls=latest.pending_tool_calls if latest else ())
    return await _finish(state, identity, ledger, host, result, trace_start, started,
                         final_review=final_review)


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
                from src.tools.registry import is_readonly_call
                uncertain = dispatched and not is_readonly_call(call["name"], call.get("args", {}))
                status = "unknown" if uncertain else "cancelled" if dispatched else "not_run"
                observed = ToolMessage(
                    content=("操作已调度，但结果尚未确认；不得重放，须先对账。" if uncertain else
                             "本轮已停止，此调用没有可采用的完成结果。" if dispatched else "本轮已停止，此调用未执行。"),
                    tool_call_id=cid, name=call["name"], status="error",
                    artifact={"_tool_outcome": {"status": status, "reason_code": result.reason}},
                    additional_kwargs=host.identity.message_metadata("tool"))
                from uuid import uuid4
                from src.graph.agent_operations import _emit_completed_tool_step
                if not call.get("_host_logical_key"):
                    call["_host_logical_key"] = "host:" + uuid4().hex
                _emit_completed_tool_step(call, observed, host.records)
            trace.append(observed)
    return trace


async def _finish(state, identity, ledger, host, result, trace_start, started, *, final_review=None):
    from src.core import figures, task_events
    from src.core.progress_bus import emit_answer_start, emit_text
    from src.core.task_result import build_task_result
    from src.core.turn_outcome import TurnOutcome, completion_digest, reviewed_completion_valid
    from src.graph.agent_operations import _safe_persisted_trace, _sanitize_answer_artifact_links
    trace = _settled_trace(list(result.messages)[trace_start:], host, result)
    # Candidates and raw JSON envelopes belong only to the protected graph.
    # Session persistence appends the exact accepted public answer once.
    trace = [m for m in trace if not (isinstance(m, AIMessage) and not m.tool_calls)]
    reviewed = (final_review or {}).get("outcome")
    if result.status == "complete":
        proof = (reviewed or {}).get("completion") or {}
        if (not reviewed_completion_valid(reviewed, host.records,
                answer=(final_review or {}).get("answer"))
                or _sanitize_answer_artifact_links((final_review or {}).get("answer", "")) !=
                   (final_review or {}).get("answer", "")
                or proof.get("identity") != asdict(identity)
                or proof.get("request_hash") != completion_digest(state["query"])
                or task_events.updates_after(identity.run_id, proof.get("request_version", 0))):
            reviewed = None
            result = RunResult("incomplete", "completion_proof_invalid", result.messages,
                               result.model_steps)
    uncertain = bool(result.unknown_operations or result.reason == "operation_outcome_unknown"
        or any(isinstance(message, ToolMessage) and (
            message.additional_kwargs.get("operation_uncertain") or
            ((message.artifact or {}).get("_tool_outcome") or {}).get("status") == "unknown")
            for message in trace))
    if result.status == "complete":
        answer = final_review["answer"]
        status = reviewed["status"]
    elif result.status == "waiting" and host.pending_question:
        from src.core.user_questions import save_question_context
        save_question_context(identity.run_id, host.question_context())
        answer = str(host.pending_question["prompt"])
        status = "awaiting_input"
    elif result.status == "cancelled":
        answer = "本轮已停止，已完成的操作与文件已保留。"
        if uncertain:
            answer += " 未确认的写入须先对账。"
        status = "cancelled"
    else:
        notices = {
            "model_length": "模型输出达到单次输出上限，未完成的提议没有执行。",
            "model_budget_exhausted:tokens": "本轮累计模型用量预算不足以继续请求。",
            "model_budget_exhausted:calls": "本轮模型调用次数已达上限。",
            "model_budget_exhausted:deadline": "本轮任务时间预算已用完。",
            "deadline": "本轮任务时间预算已用完。",
            "model_step_limit": "本轮模型调用次数已达上限。",
            "context_budget_exhausted": "当前模型上下文无法安全容纳下一次请求。",
            "context_summary_input_limit": "工作摘要的输入超过模型窗口，没有发出该摘要请求。",
            "context_summary_incomplete": "工作摘要输出未完成，原始上下文未被替换。",
            "context_summary_timeout": "工作摘要请求超时，原始上下文未被替换。",
            "context_projection_deadline": "准备工作上下文时达到任务时限。",
            "context_summary_attempt_failed": "工作摘要请求失败，原始上下文未被替换。",
            "model_budget_reserved:finalization": "余量已为最终答复保留，不再发起中间调用。",
            "context_projection_no_safe_reduction": "活动上下文没有可安全压缩的空间。",
        }
        answer = "本轮尚未完成。" + notices.get(result.reason, "执行在完成全部要求前停止。")
        answer += " 已取得的结果和原始证据已保留。"
        if uncertain:
            answer += " 部分已执行操作结果尚未确认，须先对账，不能直接重放。"
        elif result.pending_tool_calls:
            answer += " 尚未派发的提议标记为未执行，无需为它们对账。"
        if host.records:
            from collections import Counter
            counts = Counter(str(record.get("status") or "unknown") for record in host.records)
            labels = {"complete": "完成", "partial": "部分完成", "not_run": "未执行",
                      "failed": "失败", "unknown": "结果未知", "no_results": "无结果"}
            answer += "\n\n实际操作：" + "、".join(f"{labels.get(key, key)} {value} 项" for key, value in counts.items()) + "。"
        answer += f"\n\n停止原因：`{result.reason}`。"
        status = "partial" if host.messages else "failed"
    if result.status != "complete" and status in {"partial", "failed", "cancelled"}:
        from src.session.files import list_turn_files, get_account_file, _sha256_file
        links = []
        for entry in list_turn_files(identity.session_id, identity.owner_user_id,
                                     [identity.user_turn_id]).get(identity.user_turn_id, []):
            if entry.get("origin") != "generated":
                continue
            owned = get_account_file(entry["file_id"], identity.owner_user_id, touch=False)
            if owned and _sha256_file(Path(owned["path"])) == owned.get("sha256"):
                name = str(owned["filename"]).replace("[", "\\[").replace("]", "\\]")
                links.append(f"- [{name}]({owned['download_url']})")
        if links:
            answer += "\n\n已生成并核对文件完整性的产物（不表示整项任务已完成）：\n" + "\n".join(links)
    ledger.close(status="unknown" if uncertain else
                 "cancelled" if status == "cancelled" else "failed", reason_code=result.reason)
    outcome = TurnOutcome(**reviewed) if result.status == "complete" else TurnOutcome(status, result.reason, result.reason)
    task_result = build_task_result(host.records, requested_status=status, turn_outcome=outcome)
    # Even stopped paths use the same conservative task decision as the API.
    if task_result.status != outcome.status and outcome.status not in {"cancelled", "awaiting_input"}:
        outcome = TurnOutcome(task_result.status, "execution_incomplete", result.reason)
        task_result = build_task_result(host.records, requested_status=outcome.status, turn_outcome=outcome)
    task_events.release_finalization(identity.run_id)
    emit_answer_start()
    emit_text(answer)
    elapsed = (time.monotonic() - started) * 1000
    return {"query": state["query"], "answer": answer, "gen_time_ms": elapsed,
        "execution_suspension": {},
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

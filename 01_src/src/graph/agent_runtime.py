"""Per-request orchestration over contracts, loop decisions and tool operations.

This module owns the mutable turn lifecycle. Transport, coverage, acceptance and
figure editing keep their own modules and consume explicit request facts.
"""
from __future__ import annotations
from src.graph.agent_operations import (
    AIMessage,
    AgentState,
    Any,
    ExecutionLedger,
    HumanMessage,
    _COMPLETION_TOKEN_BUCKETS,
    _DSML_PROTOCOL_RE,
    _FINAL_INSTRUCTION,
    _MAX_SOURCE_ENFORCEMENT_ATTEMPTS,
    _answer_profile,
    _append_contract_notices,
    _append_verified_artifact_links,
    _artifact_failure_receipt,
    _bind_request_candidate_pool,
    _call_llm,
    _capture_interaction_refs,
    _contract_repair,
    _control_message,
    _deliverable_match_map,
    _deliverable_tool_groups,
    _deliverables_satisfied,
    _evidence_fingerprints,
    _execute_calls,
    _explicit_local_tool_request,
    _explicit_reference_check,
    _explicit_visualization_request,
    _export_gate_record,
    _final_reserve_sec,
    _has_export_artifact,
    _llm_timeout,
    _local_corpus_matches,
    _main_model_supports_image_input,
    _model_timeout,
    _parse_dsml_tool_calls,
    _provider_failure_notice,
    _question_context_for,
    _read_uploaded_paper_without_search,
    _reconcile_delivery_claims,
    _refresh_required_step_notices,
    _request_timeout_disabled,
    _safe_persisted_trace,
    _sanitize_answer_artifact_links,
    _sanitize_history_tool_protocol,
    _scoped_toolset,
    _source_scope,
    _task_working_state,
    _tool_call_dict,
    _tool_failure_notice,
    _tool_turn_budget,
    _verified_artifact_validations,
    assemble_system_prompt,
    asyncio,
    bind_execution_ledger,
    decide_retrieval,
    emit_answer_start,
    emit_public_progress,
    emit_status,
    emit_text,
    figures,
    get_deepseek_model,
    json,
    logger,
    math,
    notice,
    os,
    parse_request_constraints,
    public_progress_text,
    re,
    request_deadline_from_settings,
    reset_emitted,
    reset_execution_ledger,
    resolve_tool_policy,
    retrieval_tools_allowed,
    scrub_internal_keys,
    settings,
    time
)

async def general_agent_node(state: AgentState) -> dict:
    from src.core.request_context import ContextEpoch, _epoch
    from src.tools.interaction import bind_question_context, reset_question_context
    from src.research.script_runtime import bind_task_scope, reset_task_scope
    token = _epoch.set(ContextEpoch(history_load_status=str(state.get("history_load_status") or "unknown")))
    question_token = bind_question_context({})
    carried = (state.get("context_working_state") or {}) if state.get("continuation_context") else {}
    scope = carried.get("script_budget_scope") or str(state.get("job_id") or state.get("request_id") or "")
    state["script_budget_scope"] = scope
    script_token = bind_task_scope(scope)
    try:
        return await _general_agent_node(state)
    finally:
        reset_task_scope(script_token)
        reset_question_context(question_token)
        _epoch.reset(token)


async def _general_agent_node(state: AgentState) -> dict:
    from src.tools.interaction import provider_tools
    query = str(state.get("query") or "")
    session_id = str(state.get("session_id") or "")
    # One execution ledger per request: provider, DSML and delivery-gate calls
    # all register here, and nothing survives into another request.
    execution_ledger = ExecutionLedger(
        session_id=session_id,
        request_id=str(state.get("request_id") or ""))
    _ledger_token = bind_execution_ledger(execution_ledger)
    if _request_timeout_disabled():
        deadline = math.inf
    else:
        deadline = float(state.get("request_deadline") or request_deadline_from_settings())
    started = time.monotonic()
    scope = _source_scope(query)
    explicit_reference_check = _explicit_reference_check(query)
    has_paper_attachment = bool(state.get("has_paper_attachment"))
    paper_only = _read_uploaded_paper_without_search(
        query, scope, has_paper_attachment)
    tool_policy = resolve_tool_policy(query, scope, paper_only=paper_only)
    recent_turns: list[int] = []
    try:
        from src.session.manager import session_manager as _session_manager
        recent_turns = _session_manager.recent_evidence_turns(session_id, limit=2)
    except Exception:
        logger.debug("recent evidence turns unavailable", exc_info=True)
    retrieval_decision = decide_retrieval(
        query, scope,
        has_history_evidence=bool(state.get("history_evidence_block")),
        recent_turn_seqs=recent_turns,
        local_corpus_match=(_local_corpus_matches(query) if not paper_only else False),
    )
    retrieval_disabled = not tool_policy.allow_retrieval
    if retrieval_decision.mode == "reuse":
        try:
            from src.core import turn_source_ledger
            from src.core.progress_bus import get_turn_scope
            _scope_sid, _scope_rid = get_turn_scope()
            turn_source_ledger.record_history_sources(
                _scope_sid or session_id, _scope_rid,
                list(retrieval_decision.history_turn_seqs))
        except Exception:
            logger.debug("history source registration failed", exc_info=True)
    figures.begin_request()
    try:
        from src.research.context import get_request_tables
        request_tables = get_request_tables()
        has_table_attachments = bool(request_tables)
    except Exception:
        request_tables = []
        has_table_attachments = False
    image_attachments = state.get("image_attachments") or []
    attachment_context = str(state.get("attachment_context") or "").strip()
    visualization_enabled = bool(state.get("visualization_enabled"))
    # One request plan is the single source for visibility, constraints,
    # required work, deliverables, and executor authorization. Parsers below
    # may still expose compatibility adapters, but they do not create a second
    # policy.
    from src.core.tracing import set_request_authorizations, web_search_enabled
    from src.graph.request_plan import RequestFacts, build_request_plan
    from src.graph.tool_availability import provider_denials
    research_enabled = os.getenv(
        "CITRUS_RESEARCH_TOOLS_ENABLED", "true").strip().lower() not in {
            "0", "false", "no", "off"}
    # Request-scoped candidate pool: rebind the pool the prefetch node built for
    # THIS request. A missing or stale pool is never reused, so candidates from
    # an earlier request can not leak into this turn.
    candidate_pool = _bind_request_candidate_pool(state)
    candidate_pool_notice = str(state.get("candidate_pool_notice") or "")
    has_candidate_pool = candidate_pool is not None
    request_facts = RequestFacts(
            session_id=session_id,
            request_id=str(state.get("request_id") or ""),
            job_id=str(state.get("job_id") or ""),
            client_request_id=str(state.get("client_request_id") or ""),
            attachments=1 if has_table_attachments else 0,
            account_files=len(state.get("account_files") or ()),
            has_paper_attachment=has_paper_attachment,
            network=bool(web_search_enabled()),
            local_corpus=True,
            research_tools=research_enabled,
            visualization=visualization_enabled,
            has_session=bool(session_id),
            has_history_evidence=bool(state.get("history_evidence_block")),
            recent_turn_seqs=tuple(recent_turns),
            local_corpus_match=(_local_corpus_matches(query)
                                if not paper_only else False),
            has_candidate_pool=has_candidate_pool,
        )
    request_plan = build_request_plan(
        query, request_facts,
        selected_scope=scope,
        paper_only=paper_only,
        provider_denials=provider_denials(settings),
    )
    set_request_authorizations(request_plan.executor_authorizations)
    # Keep autonomous routing advice separate from user-mandated work. In the
    # default advisory mode keyword-based suggestions never become a completion
    # gate; only explicit source/action requests are required. ``enforce`` is a
    # temporary compatibility switch for the former keyword-driven behavior.
    from src.graph.retrieval_router import required_route_groups
    request_constraints = parse_request_constraints(
        query, scope, has_table_attachment=has_table_attachments)
    required_tool_groups: list[set[str]] = [
        set(group) for group in required_route_groups(retrieval_decision)
    ]
    if scope != "web" and _explicit_local_tool_request(query):
        # A direct English instruction to call local_hybrid_search is an
        # executable requirement, even when the query omits the word "citrus".
        local_requirement = {"local_hybrid_search"}
        if local_requirement not in required_tool_groups:
            required_tool_groups.append(local_requirement)
    if explicit_reference_check and scope != "local":
        required_tool_groups.append({"verify_uploaded_references"})
    if request_constraints.data_audit_requested and has_table_attachments:
        required_tool_groups.append({"audit_uploaded_data"})
    # Every requested deliverable is a separate requirement, mapped through the
    # registry's declared output kinds so a figure cannot satisfy a report.
    deliverable_requirements = list(request_plan.deliverables)
    for export_group in _deliverable_tool_groups(deliverable_requirements):
        if export_group not in required_tool_groups:
            required_tool_groups.append(export_group)
    # One structured agreement for the turn. Completion is decided against these
    # obligations (see core.turn_outcome), not against "a tool name appeared".
    from src.graph.coverage import CoverageLedger
    from src.graph.task_contract import build_task_contract
    task_contract = build_task_contract(
        query=query,
        request_plan=request_plan,
        request_constraints=request_constraints,
        table_count=len(request_tables) if has_table_attachments else 0,
        explicit_reference_check=explicit_reference_check and scope != "local",
        has_paper_attachment=has_paper_attachment,
    )
    coverage = CoverageLedger()
    contract_repair_attempts = 0
    tools, _ = _scoped_toolset(
        scope, query=query, has_table=has_table_attachments,
        has_paper_attachment=has_paper_attachment,
        has_account_files=bool(state.get("account_files")),
        visualization_enabled=visualization_enabled, plan=request_plan)
    if not retrieval_tools_allowed(request_constraints, retrieval_decision):
        from src.core.tool_catalog import capability_groups

        denied_caps = capability_groups()["retrieval"]
        tools = [tool for tool in tools if tool.name not in denied_caps]
    available_names = {tool.name for tool in tools}
    unavailable_required_groups = [
        group for group in required_tool_groups if not (group & available_names)
    ]
    required_tool_groups = [
        group for group in required_tool_groups if group & available_names
    ]
    turn_budget = _tool_turn_budget(query)
    deliverable_needed = bool(deliverable_requirements)
    export_nudge_sent = False
    disease_image_query = bool(image_attachments and re.search(
        r"病害|病症|什么病|识别.{0,6}病|诊断|症状|黄龙病|溃疡病|黑斑病|病斑|disease|diagnos|symptom",
        query, re.I))
    main_model_can_see_images = bool(image_attachments) and _main_model_supports_image_input()
    tools = sorted(tools, key=lambda tool: str(getattr(tool, "name", "")))
    tools_by_name = {tool.name: tool for tool in tools}
    system_prompt = assemble_system_prompt(mode="expert", format_hint=state.get("format_hint"),
                                           query=query, available_tools=set(tools_by_name))
    # Keep the core system prefix stable for provider prompt caching. The active
    # corpus catalog is request-scoped and appended only after that prefix.
    # Only advertise the local corpus when the tool is actually bound for this
    # request: an advertised-but-unavailable tool makes the model call it and
    # yields a spurious "[TOOL_UNAVAILABLE]" step in the visible trace.
    if "local_hybrid_search" in tools_by_name:
        try:
            from src.retrieval.corpus_registry import load_corpus_registry
            registry = load_corpus_registry()
            system_prompt += (
                "\n\n<registered_local_corpus_families>\n"
                "以下类别由运行时语料清单动态提供。调用 local_hybrid_search 时，"
                "corpus_types 只能使用这些已登记 family id；按问题选择匹配类别，不得猜测或改写 ID。\n"
                + registry.describe_families() + "\n</registered_local_corpus_families>"
            )
        except Exception as exc:
            logger.warning("Could not append dynamic corpus registry to prompt: %s", exc)

    from src.core.context_manager import (
        assemble_supervisor_messages, build_human_message, build_loaded_context,
    )
    loaded = build_loaded_context(state, mode="expert", format_hint=state.get("format_hint"))
    # The candidate-pool notice carries counts, corpus families and usage only.
    # Candidate titles and text stay in the pool until a candidate tool call
    # brings them into the context.
    human = build_human_message(
        loaded, retrieval_context=candidate_pool_notice if has_candidate_pool else "")
    completion_budget, answer_guidance = _answer_profile(query)
    if disease_image_query:
        completion_budget = min(completion_budget, 512)
        answer_guidance += (
            "\n病害图片问题请用极简用户格式回答：最多 3 行，依次写“最可能判断 + 置信度（高/中/低）”、"
            "“鉴别（仅 1 个关键点）”、以及“建议（仅在把握中低时给 1 条优先筛查）”。"
            "不要写长篇机制、多种诱因、候选排名、重复总结或多个检查清单。"
            "把握不足时明确说明不确定；不得声称确诊或给出农药用量。"
        )
    if request_constraints.reason_code == "data_audit_only":
        decision_guidance = (
            "本轮是数据审查任务。只使用附件读取、表结构检查和数据审查工具，"
            "不调用联网或本地语料检索；除非用户另行明确要求证据检索。"
        )
    elif retrieval_decision.policy == "deny":
        decision_guidance = "用户已明确禁止本轮检索。只依据附件、当前上下文或稳定知识回答；不得调用检索工具，也不得声称刚刚检索了新资料。"
    elif retrieval_decision.policy == "required":
        if retrieval_decision.mode == "local":
            decision_guidance = "用户明确要求检索。请实际调用本地语料检索；不得调用联网检索。"
        elif retrieval_decision.mode == "web":
            decision_guidance = "用户明确要求检索。请实际调用联网检索；不得调用本地语料检索。"
        elif retrieval_decision.mode == "fusion":
            decision_guidance = "用户明确要求检索。请同时尝试联网检索和本地语料检索，并分别说明证据边界。"
        else:
            decision_guidance = "用户明确要求检索。请至少实际调用一次合规检索工具。"
    elif retrieval_decision.mode == "reuse":
        decision_guidance = "最近两轮已有匹配证据。优先复用并核对证据缺口；如确实缺少关键证据，可以再补一次精准检索。"
    else:
        decision_guidance = "按本轮问题选择必要工具。已有匹配上下文时优先复用，不因普通科研关键词自动双查。"
    request_policy = (
        "\n\n<request_policy>\n" + decision_guidance + "\n"
        + "系统提示中的工具说明是本轮任务路由的唯一文字规则；代码会强制执行用户指定的来源范围。\n"
        + ("若账号资料库索引中有与当前对话相关的旧文件，先由你依据完整语境判断是否需要读取；用户只是问已上传文件时，不要因此调用联网检索。\n"
           if state.get("account_files") else "")
        + "对用户只呈现简洁、自然的进度和结论；不要输出内部工具名、路由决策、调用参数、错误代码或系统实现细节。"
        "步骤未完成时，只说明与用户任务有关的结果限制，并继续利用已有材料回答。\n"
        + ("可使用 draw_svg 生成图示；用户明确要求绘图时按要求执行，并在验证后原样返回 SVG。\n"
           if visualization_enabled or _explicit_visualization_request(query) else "")
        + "</request_policy>"
    )
    human.content = (str(human.content or "")
                     + "\n\n<answer_length_and_scope>\n" + answer_guidance
                     + "\n</answer_length_and_scope>" + request_policy)
    if "research_workspace" in tools_by_name:
        try:
            from src.research.workspace_tools import workspace_context
            index = workspace_context(int(state.get("user_id") or 0), session_id)
            human.content += "\n\n<research_workspace_index>\n" + json.dumps(index, ensure_ascii=False) + "\n</research_workspace_index>"
        except Exception:
            logger.warning("Research workspace index unavailable", exc_info=True)
            human.content += "\n科研工作区索引本轮不可用；需要时调用research_workspace读取，不推断旧脚本仍可用。"
    if attachment_context:
        human.content += "\n\n" + attachment_context
    image_attachments = state.get("image_attachments") or []
    if image_attachments:
        image_scope = f"用户本轮上传了 {len(image_attachments)} 张图片；图片仅用于本轮，不写入知识库。"
        if disease_image_query:
            if main_model_can_see_images:
                vision_rules = (
                    "\n\n" + image_scope
                    + "你已收到原图，请由主模型直接判断最可能的病害或生理问题。"
                    + "回答最多 3 行：最可能判断及把握（高/中/低，非校准概率）；"
                    + "1 个关键鉴别点；仅在把握中低时给 1 条优先筛查建议。"
                    + "照片不足以判断时明确说不确定，并指出最有价值的一项补充观察或检测；"
                    + "不得声称确诊或给出农药用量。"
                )
            else:
                vision_rules = (
                    "\n\n" + image_scope
                    + "当前所选主模型不支持图片输入；不要声称看到了原图或给出图像诊断。"
                    + "请说明当前无法读取图片，并请用户补充症状描述或切换支持图像输入的主模型。"
                )
        elif main_model_can_see_images:
            vision_rules = "\n\n" + image_scope + "请按用户问题检查原图；不得仅凭照片断言品种。"
        else:
            vision_rules = (
                "\n\n" + image_scope
                + "当前所选主模型不支持图片输入，不要声称看到了原图；请依据文字和已有工具结果回答，"
                + "并请用户补充必要的图片描述。"
            )
        human.content += vision_rules
        text_block = {"type": "text", "text": str(human.content)}
        human.content = [text_block]
        if main_model_can_see_images:
            human.content.extend([
                {"type": "image_url", "image_url": {"url": image["url"]}}
                for image in image_attachments if image.get("url")
            ])
    human.additional_kwargs.update(context_origin="generated_user", context_trusted=True,
        user_text=query, request_id=str(state.get("request_id") or ""),
        turn_id=str(state.get("request_id") or ""),
        attachment_refs=[{k: a[k] for k in ("file_id", "version", "sha256", "filename", "name") if k in a}
                         for a in state.get("attachment_refs", []) if isinstance(a, dict)])
    messages, trace_start = assemble_supervisor_messages(system_prompt, state, human)
    messages = _sanitize_history_tool_protocol(messages)
    reset_emitted()  # S2: per-request dedupe of public working notes
    from src.core.llm_pool import get_llm
    llm_base = get_llm(
        model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
        base_url=settings.RESOLVED_MAIN_BASE_URL,
        temperature=settings.TEMPERATURE_MAIN, max_tokens=completion_budget,
        timeout=_model_timeout())
    llm_tools = llm_base.bind_tools(provider_tools(tools)) if tools else llm_base
    from src.graph.request_plan import bounded_tool_context
    try:
        llm_base_nothink = get_llm(
            model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
            base_url=settings.RESOLVED_MAIN_BASE_URL,
            temperature=settings.TEMPERATURE_MAIN, max_tokens=completion_budget,
            timeout=_model_timeout(),
            thinking_off=True)
        llm_tools_nothink = (llm_base_nothink.bind_tools(provider_tools(tools))
                             if tools else llm_base_nothink)
    except Exception:
        llm_base_nothink = llm_base
        llm_tools_nothink = llm_tools
    # Interpret the complete turn once. The legacy keyword products are hints
    # only; structured understanding supplies completion obligations. It cannot
    # grant new tools or widen the host's independent source authorizations.
    from src.graph.task_contract import understand_task
    try:
        task_contract = await understand_task(model=llm_base, messages=messages,
            query=query, source_scope=scope, tables=request_tables,
            call_model=_call_llm, deadline=deadline)
        legacy_export_groups = _deliverable_tool_groups(deliverable_requirements)
        required_tool_groups = [g for g in required_tool_groups
                                if g not in legacy_export_groups and g not in
                                [{"audit_uploaded_data"}, {"verify_uploaded_references"}]]
        unavailable_required_groups = [g for g in unavailable_required_groups if g not in legacy_export_groups]
        deliverable_requirements = task_contract.deliverables
        deliverable_needed = bool(deliverable_requirements)
        for group in _deliverable_tool_groups(deliverable_requirements):
            (required_tool_groups if group & available_names else unavailable_required_groups).append(group)
        messages.append(_control_message(content="本轮交付约定（不是执行结果）：\n" +
            json.dumps(task_contract.as_dict(), ensure_ascii=False)))
    except Exception as exc:
        logger.warning("Task understanding unavailable: %s", type(exc).__name__)
        messages.append(_control_message(content="本轮结构化任务范围暂未确认，请依据完整用户请求谨慎执行；"
            "不扩大来源或附件范围，无法确定的目标请使用交互工具确认。"))
    from src.graph.loop_kernel import LoopKernel
    kernel = LoopKernel(turn_budget)
    tool_names_called: list[str] = []
    # Real execution status per tool call, collected in _execute_tool_calls.
    # Used to distinguish "the tool ran and produced evidence" from "the tool
    # was invoked but failed / returned nothing".
    tool_status_records: list[dict] = []
    seen_evidence: set[str] = set()
    stagnant_batches = 0
    tool_turns = 0
    main_results: list[dict] = []
    web_results: list[dict] = []
    answer = ""
    stop_reason = "model_finished"
    failure_notice = ""
    source_enforcement_attempts = 0
    protocol_recovery_attempts = 0
    budget_retry_attempts = 0
    seen_call_signatures: dict[str, dict[str, Any]] = {}
    from src.core.tracing import get_job_id

    try:
        emit_status("step_done", step_id="load")
        emit_status("step_active", step_id="retrieve")
    except Exception:
        pass

    while True:
        # S1: 协作式取消的第一道关口——已请求停止就不再发起新的模型/工具轮次。
        from src.core.cancel import is_cancelled as _cancel_requested
        if _cancel_requested():
            stop_reason = "cancelled"
            logger.info("General Agent stopped by user request before next round")
            break
        # Supplemental instructions merge only between completed steps. No
        # in-flight tool is mutated and completed ledger facts remain intact.
        from src.core.tracing import get_job_id, set_original_query
        from src.core import task_events
        updates = task_events.apply_updates(get_job_id()) if get_job_id() else []
        if updates:
            from src.graph.request_plan import merge_task_instructions
            query = merge_task_instructions(query, updates)
            state["query"] = query
            set_original_query(query)
            request_plan = build_request_plan(query, request_facts,
                selected_scope=scope, paper_only=paper_only,
                provider_denials=provider_denials(settings))
            scope = request_plan.selected_scope
            set_request_authorizations(request_plan.executor_authorizations)
            request_constraints = parse_request_constraints(
                query, scope, has_table_attachment=has_table_attachments)
            retrieval_decision = decide_retrieval(query, scope,
                has_history_evidence=bool(state.get("history_evidence_block")),
                recent_turn_seqs=recent_turns)
            deliverable_requirements = list(request_plan.deliverables)
            deliverable_needed = bool(deliverable_requirements)
            tools, _ = _scoped_toolset(scope, query=query,
                has_table=has_table_attachments,
                has_paper_attachment=has_paper_attachment,
                has_account_files=bool(state.get("account_files")),
                visualization_enabled=visualization_enabled, plan=request_plan)
            tools = sorted(tools, key=lambda tool: str(getattr(tool, "name", "")))
            tools_by_name = {tool.name: tool for tool in tools}
            available_names = set(tools_by_name)
            required_tool_groups = [set(g) for g in request_plan.required_tool_groups]
            required_tool_groups.extend(_deliverable_tool_groups(deliverable_requirements))
            unavailable_required_groups = [g for g in required_tool_groups if not g & available_names]
            required_tool_groups = [g for g in required_tool_groups if g & available_names]
            # The agreement follows the updated instruction; absorbed coverage
            # stays valid because it is about already-observed objects.
            task_contract = build_task_contract(
                query=query,
                request_plan=request_plan,
                request_constraints=request_constraints,
                table_count=len(request_tables) if has_table_attachments else 0,
                explicit_reference_check=explicit_reference_check and scope != "local",
                has_paper_attachment=has_paper_attachment,
            )
            llm_tools = llm_base.bind_tools(provider_tools(tools)) if tools else llm_base
            llm_tools_nothink = llm_base_nothink.bind_tools(provider_tools(tools)) if tools else llm_base_nothink
            update_text = "\n".join(u["instruction"] for u in updates)
            messages.append(HumanMessage(content=(
                "任务要求已在安全步骤边界更新。已完成结果保留；请按补充要求调整尚未完成的步骤：\n"
                + update_text), additional_kwargs={"context_origin": "generated_user", "context_trusted": True,
                    "user_text": update_text, "request_id": str(state.get("request_id") or ""),
                    "turn_id": str(state.get("request_id") or "")}))
            try:
                task_contract = await understand_task(model=llm_base, messages=messages,
                    query=query, source_scope=scope, tables=request_tables,
                    call_model=_call_llm, deadline=deadline)
                deliverable_requirements = task_contract.deliverables
                deliverable_needed = bool(deliverable_requirements)
                required_tool_groups = [set(g) for g in required_route_groups(retrieval_decision)]
                required_tool_groups.extend(_deliverable_tool_groups(deliverable_requirements))
                unavailable_required_groups = [g for g in required_tool_groups if not g & available_names]
                required_tool_groups = [g for g in required_tool_groups if g & available_names]
                messages.append(_control_message(content="更新后的交付约定：\n" +
                    json.dumps(task_contract.as_dict(), ensure_ascii=False)))
            except Exception as exc:
                logger.warning("Updated task understanding unavailable: %s", type(exc).__name__)
            export_nudge_sent = False
        if kernel.tool_turns >= kernel.turn_budget:
            _unfinished = [g for g in required_tool_groups
                           if not g.intersection(tool_names_called)]
            _needs_export = deliverable_needed and not _has_export_artifact(messages[trace_start:], deliverable_requirements)
            decision = kernel.check_turn_limit(
                unfinished_groups=bool(_unfinished) or bool(coverage.gaps(task_contract)),
                needs_export=_needs_export)
            if decision.action == "extend":
                turn_budget = decision.turn_budget
                logger.info("[budget] extended to %d turns (missing=%d, needs_export=%s)",
                            turn_budget, len(_unfinished), _needs_export)
                emit_status("agent_progress",
                            message=decision.message)
                messages.append(_control_message(content=(
                    "预算已延长。请继续完成尚未完成的步骤："
                    + ("还需调用 " + "; ".join(" / ".join(sorted(g)) for g in _unfinished) + "。" if _unfinished else "")
                    + ("还需要生成用户要求的文件。" if _needs_export else ""))))
            else:
                stop_reason = "tool_turn_limit"
                break
        final_reserve = _final_reserve_sec()
        remaining = deadline - time.monotonic()
        if math.isfinite(deadline) and remaining <= final_reserve:
            stop_reason = "request_deadline"
            break
        try:
            # S2: collect this round's public text. It is classified only after
            # the round ends (tool calls vs final answer), never streamed raw.
            _round_text_chunks: list[str] = []
            # Tool rounds may stream public working notes, but the note is not
            # the answer. Buffer it here; classify the round first and emit the
            # final text only after ``answer_start``.
            _stream_state: dict = {"pending": [], "last": 0.0, "sent": 0}

            def _flush_text(_force: bool = False) -> None:
                if not _stream_state["pending"]:
                    return
                now = time.monotonic()
                if not _force and (now - _stream_state["last"]) < 0.06:
                    return
                chunk = "".join(_stream_state["pending"])
                _stream_state["pending"].clear()
                _stream_state["last"] = now
                _stream_state["sent"] += len(chunk)

            def _on_text_delta(chunk: str) -> None:
                _round_text_chunks.append(chunk)
                _stream_state["pending"].append(chunk)
                _flush_text()

            export_reserve = 4 if any("docx" in r.get("formats", []) for r in deliverable_requirements) else 2
            if (deliverable_needed and not export_nudge_sent
                    and tool_turns >= max(0, turn_budget - export_reserve)):
                export_nudge_sent = True
                messages.append(_control_message(content=(
                    f"还剩 {turn_budget - tool_turns} 个回合。用户要求交付文件，"
                    "请立即用已有材料和用户指定格式交付文件。Word 使用 word_document "
                    "create→分批 append_blocks→finalize；若已有草稿先 inspect 恢复，"
                    "所有章节和末块确认后再发布。仅用户要求文件时用 deliver_research_artifact，"
                    "以 kind=table/bibliography/report 选择实际内容类型；普通回答不生成附件。"
                    "不要用 HTML 充当 DOCX，不要在此时继续检索。")))
                emit_status("agent_progress", message="准备交付文件，保留最后回合用于导出…")
            from src.core.request_context import model_request, _epoch
            _epoch.get().working_state = _task_working_state(state, query, tool_status_records, deliverable_requirements)
            reserve_view, _ = bounded_tool_context(messages,
                max_tokens=int(settings.RESOLVED_CONTEXT_BUDGET_MAX_TOKENS),
                output_tokens=completion_budget, model=llm_tools)
            _, final_estimate = model_request(llm_base, reserve_view,
                output_tokens=int(settings.AGENT_FINAL_OUTPUT_TOKENS))
            task_events.protect_finalization(get_job_id(),
                min(16384, final_estimate["estimated_total_tokens"] + 512), calls=1)
            response = await _call_llm(
                llm_tools, messages, timeout=_llm_timeout(remaining - final_reserve),
                deadline=deadline - final_reserve,
                llm_fallback=llm_tools_nothink,
                on_text=_on_text_delta)
        except asyncio.TimeoutError:
            logger.warning("General Agent model call reached reserved finalization window")
            stop_reason = "model_timeout"
            failure_notice = _provider_failure_notice(asyncio.TimeoutError())
            break
        except Exception as exc:  # noqa: BLE001
            if "model_budget_reserved:finalization" in str(exc):
                stop_reason = "finalization_budget"
                break
            if "model_budget_exhausted:" in str(exc) or "context_budget_exhausted" in str(exc):
                stop_reason = "context_budget_exhausted" if "context_budget_exhausted" in str(exc) else "model_budget_exhausted"
                break
            logger.exception("General Agent model call failed")
            stop_reason = f"model_error:{type(exc).__name__}"
            failure_notice = _provider_failure_notice(exc)
            break

        # Physical attempts, including retries, are accounted at the call boundary.
        if get_job_id() and task_events.projection(get_job_id())["pending_updates"]:
            # This proposal used the previous task version. Re-plan at the next
            # safe boundary before executing it or publishing a final answer.
            continue
        _finish = str((getattr(response, "response_metadata", None) or {}).get("finish_reason") or "")
        logger.info("[diag] llm_response finish=%s tool_calls=%s content_chars=%d budget=%d",
                    _finish or "-", len(getattr(response, "tool_calls", None) or []),
                    len(str(getattr(response, "content", "") or "")), completion_budget)
        if (_finish in ("length", "max_tokens", "max_output_tokens")
                and budget_retry_attempts < 2):
            # Provider stopped at the output cap before any text arrived. Retry
            # once with a larger budget instead of reporting a fake timeout.
            budget_retry_attempts += 1
            completion_budget = min(_COMPLETION_TOKEN_BUCKETS[-1],
                                    max(completion_budget * 2, 8192))
            llm_base = get_llm(
                model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
                base_url=settings.RESOLVED_MAIN_BASE_URL,
                temperature=settings.TEMPERATURE_MAIN, max_tokens=completion_budget,
                timeout=min(120.0, _model_timeout()))
            llm_tools = llm_base.bind_tools(provider_tools(tools)) if tools else llm_base
            llm_base_nothink = get_llm(
                model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
                base_url=settings.RESOLVED_MAIN_BASE_URL,
                temperature=settings.TEMPERATURE_MAIN, max_tokens=completion_budget,
                timeout=min(120.0, _model_timeout()), thinking_off=True)
            llm_tools_nothink = llm_base_nothink.bind_tools(provider_tools(tools)) if tools else llm_base_nothink
            logger.warning("Model output hit the %s cap; retrying with %d tokens",
                           _finish, completion_budget)
            truncated_calls = getattr(response, "tool_calls", None) or []
            repair_note = (
                "上一轮工具参数在输出长度上限处被截断，该次调用没有执行。"
                "请只保留必要字段，把长清单分批（每批不超过 20 条）后重新调用。"
                if truncated_calls else
                "上一轮回答因输出长度上限被截断。请更精简地继续：需要绘图时调用 draw_svg "
                "并提交紧凑 SVG；否则直接给出完整但简短的答案。")
            messages.extend([
                AIMessage(content="[上一轮输出在长度上限处被截断，未形成有效内容。]"),
                _control_message(content=repair_note),
            ])
            emit_status("agent_progress", message="输出长度上限被截断，正在以更大预算重试…")
            continue
        calls = getattr(response, "tool_calls", None) or []
        if not calls:
            detected, recovered_calls = _parse_dsml_tool_calls(
                str(getattr(response, "content", "") or ""), tools_by_name)
            # Interaction must originate in the provider's native tool_calls;
            # never turn JSON/DSML-looking assistant prose into a question card.
            recovered_calls = [c for c in recovered_calls if c.get("name") != "request_user_input"]
            if recovered_calls:
                logger.warning("Recovered %d DSML tool call(s) emitted as assistant text",
                               len(recovered_calls))
                for recovered in recovered_calls:
                    recovered["_source"] = "dsml"
                response = AIMessage(content="", tool_calls=recovered_calls)
                calls = recovered_calls
                emit_status("agent_progress",
                            message="已恢复模型返回的工具调用格式，继续执行检索…")
            elif detected:
                protocol_recovery_attempts += 1
                logger.error("Rejected unparseable or unavailable DSML tool-call text")
                messages.extend([
                    AIMessage(content="[上一轮返回的是未执行的工具协议，不是有效回答。]"),
                    _control_message(content=(
                        "上一轮输出了未执行的工具调用协议。不要复述或输出任何 DSML、XML 或工具调用标记。"
                        "请根据用户原始问题与已有证据继续：若当前工具列表含有合适工具，请通过标准结构化工具调用；"
                        "否则直接给出正常答案，并简要说明确实缺少的证据。")),
                ])
                if protocol_recovery_attempts < 2:
                    emit_status("agent_progress",
                                message="检测到未执行的工具协议，正在安全恢复回答…")
                    continue
                stop_reason = "invalid_tool_protocol"
                break

        messages.append(response)
        if not calls:
            missing_groups = [group for group in required_tool_groups
                              if not group.intersection(tool_names_called)]
            if (missing_groups and
                    source_enforcement_attempts < _MAX_SOURCE_ENFORCEMENT_ATTEMPTS and tools):
                source_enforcement_attempts += 1
                missing = [" / ".join(sorted(group)) for group in missing_groups]
                messages.append(_control_message(content=(
                    "用户明确要求的任务或来源尚未完成。请调用以下尚未尝试的工具组："
                    f"{'; '.join(missing)}。完成一次尝试后检查回执；若工具失败或无结果，"
                    "如实说明状态并继续回答，不要把失败写成没有相关资料。")))
                emit_status("agent_progress", message="正在补齐用户明确要求的检索或审查步骤…")
                continue
            # An obligation the contract still owes is not discharged by prose.
            # Repair it with a bound tool while the loop still has time; the
            # bounded counter lets an unrepairable gap fall through to the honest
            # partial terminal instead of looping.
            _repair_remaining = (math.inf if not math.isfinite(deadline)
                                 else deadline - time.monotonic())
            if (tools
                    and (not math.isfinite(_repair_remaining)
                         or _repair_remaining > _final_reserve_sec())):
                _repair = _contract_repair(task_contract, coverage, available_names)
                if (not _repair and deliverable_needed and
                        not _has_export_artifact(messages[trace_start:], deliverable_requirements)):
                    _repair = ("所需文件尚未通过交付验证。请在当前工具循环内完成生成/发布，"
                               "或明确说明无法交付的原因，不以回答文字代替文件。",
                               "正在处理尚未交付的文件…")
                if _repair and kernel.allow_contract_repair():
                    logger.info("[contract] repair attempt %d: %s",
                                kernel.contract_repairs,
                                [gap.get("reason_code") for gap in coverage.gaps(task_contract)])
                    messages.append(_control_message(content=_repair[0]))
                    emit_status("agent_progress", message=_repair[1])
                    continue
            answer = str(response.content or "")
            # No raw tool protocol is ever accepted as an answer, even if a
            # provider emits it during an unbound final response.
            if _DSML_PROTOCOL_RE.search(answer):
                protocol_recovery_attempts += 1
                logger.error("Blocked DSML tool-call text from final answer (attempt %s)",
                             protocol_recovery_attempts)
                if protocol_recovery_attempts < 2:
                    messages.extend([
                        AIMessage(content="[上一轮返回的是未执行的工具协议，不是有效回答。]"),
                        _control_message(content=(
                            "上一轮输出了未执行的工具调用协议。不要复述或输出任何 DSML、XML 或工具调用标记。"
                            "请直接给出正常答案；若确实需要工具，请通过标准结构化工具调用发起。")),
                    ])
                    emit_status("agent_progress", message="正在安全恢复回答…")
                    continue
                answer = ""
            else:
                # Finalization may still append required-step notices and
                # verified artifact links. Publish only after that processing.
                _flush_text(True)
                answer = _sanitize_answer_artifact_links(answer)
            break

        # S2: this round ended in tool calls, so its buffered text is only a
        # working note. Do not emit either ``text`` or ``text_reset`` for the
        # normal tool path; the progress timeline receives it below.
        emit_public_progress(public_progress_text(_round_text_chunks, bool(calls)))

        emit_status("agent_progress", message=f"已选择 {len(calls)} 项工具操作，开始执行…")

        kernel.begin_tool_round()
        tool_turns = kernel.tool_turns
        from src.tools.interaction import bind_question_context, reset_question_context
        question_token = bind_question_context(_question_context_for(
            state, query, tool_status_records, deliverable_requirements))
        try:
            results = await _execute_calls(response, tools_by_name=tools_by_name,
                                           deadline=deadline,
                                           seen_call_signatures=seen_call_signatures,
                                           tool_status_sink=tool_status_records)
        finally:
            reset_question_context(question_token)
        messages.extend(results)
        # Target-level coverage, not tool-name counting: a re-run over the same
        # twenty references or the same workbook advances nothing, and the
        # stagnation gate below reads exactly this delta.
        _coverage_before = coverage.new_targets
        for call, result in zip(calls, results):
            coverage.observe(
                str(_tool_call_dict(call).get("name") or ""),
                content=getattr(result, "content", ""),
                artifact=getattr(result, "artifact", None))
        _coverage_advanced = coverage.new_targets > _coverage_before
        _capture_interaction_refs(state, results)
        pending_question = next((r.artifact.get("user_question") for r in results
            if r.name == "request_user_input" and isinstance(r.artifact, dict)
            and isinstance(r.artifact.get("user_question"), dict)), None)
        if pending_question:
            # The independently durable question ends this execution immediately.
            # No reflection, forced retrieval, synthesis or delivery gate follows.
            from src.core.user_questions import save_question_context
            from src.core.task_result import build_task_result
            from src.core.turn_envelope import build_turn_envelope
            from src.core.task_events import release_finalization
            context = _question_context_for(state, query, tool_status_records, deliverable_requirements)
            save_question_context(get_job_id(), context)
            release_finalization(get_job_id())
            execution_ledger.close(status="skipped", reason_code="awaiting_user_input")
            ledger_payload = execution_ledger.as_dict()
            reset_execution_ledger(_ledger_token)
            answer = _append_verified_artifact_links(pending_question["prompt"], tool_status_records)
            emit_answer_start()
            emit_text(answer)
            envelope = build_turn_envelope(answer=answer, tool_status=tool_status_records,
                requested_status="awaiting_input", display_status="awaiting_input",
                job_status="awaiting_input", stop_reason="awaiting_input")
            elapsed = (time.monotonic() - started) * 1000
            return {"answer": answer, "awaiting_input": True,
                "pending_question": pending_question, "turn_envelope": envelope.as_dict(),
                "gen_time_ms": elapsed, "main_results": main_results, "web_results": web_results,
                "tools_called": list(dict.fromkeys(tool_names_called + ["request_user_input"])),
                "tool_status": [dict(r) for r in tool_status_records],
                "execution_ledger": ledger_payload, "context_working_state": context["context_working_state"],
                "export_gate": {"status": "pending", "reason": "awaiting_user_input"},
                "turn_trace": _safe_persisted_trace(messages[trace_start:]),
                "_trace": {"node": "supervisor", "elapsed_ms": elapsed,
                    "stop_reason": "awaiting_input", "tool_turns": tool_turns,
                    "task_result": build_task_result(tool_status_records,
                        requested_status="awaiting_input").as_dict()}}
        # S1: 停止请求在工具执行期间到达 → 不再请模型继续下一轮，直接收尾。
        from src.core.cancel import is_cancelled as _cancel_requested2
        if _cancel_requested2():
            stop_reason = "cancelled"
            logger.info("General Agent stopped by user request during tool batch")
            break
        new_evidence = _evidence_fingerprints(results) - seen_evidence
        seen_evidence.update(new_evidence)
        # Coverage progress counts even when the evidence digest repeats (a
        # record that moved from unresolved to checked has a new status but an
        # unchanged fingerprint); a repeated batch over covered targets does not.
        from src.graph.coverage import TRACKED_TOOLS
        # Do not let formatting, publication or batch-size changes in tracked
        # receipts masquerade as new evidence. Other tools use their digest.
        untracked = [r for c, r in zip(calls, results)
                     if _tool_call_dict(c).get("name") not in TRACKED_TOOLS]
        new_untracked = bool(_evidence_fingerprints(untracked) & new_evidence)
        no_progress = kernel.record_batch(new_evidence=new_untracked, coverage_advanced=_coverage_advanced)
        stagnant_batches = kernel.stagnant_batches
        for call, result in zip(calls, results):
            d = _tool_call_dict(call)
            if (not str(getattr(result, "content", "") or "").startswith("[TOOL_BATCH_LIMIT]")
                    and d["name"] not in tool_names_called):
                tool_names_called.append(d["name"])
            artifact = getattr(result, "artifact", None) or {}
            if isinstance(artifact, dict):
                main_results.extend(artifact.get("main_results") or [])
                web_results.extend(artifact.get("web_results") or [])

        if get_job_id() and task_events.projection(get_job_id())["pending_updates"]:
            continue
        # Completion is checked by the contract on the next model proposal.
        # No extra reflection model owns a second, competing terminal decision.

        if no_progress:
            stop_reason = "no_new_evidence"
            logger.info("General Agent stopped retrieval after %d consecutive tool batches "
                        "without new evidence", stagnant_batches)
            break

    if get_job_id():
        from src.core.jobs import update_job
        update_job(get_job_id(), current_step="finalizing")
    # Reserve a final model pass without tools so the Agent can synthesize all
    # evidence already in its transcript, while keeping a strict total deadline.
    # S1: 用户已停止 —— 不再请求模型做最终综述（否则"停止"之后还会再花一次
    # 模型调用，且可能把停止后的新结论写进上下文）。
    # Transport recovery has already been attempted within _call_llm. Do not
    # resend the same transcript for another synthesis after a provider failure.
    if not answer and stop_reason != "cancelled" and failure_notice:
        answer = _artifact_failure_receipt(failure_notice, tool_status_records)
    if not answer and stop_reason in {"model_budget_exhausted", "context_budget_exhausted"}:
        answer = "本轮执行额度已用尽，已保留完成的操作与文件；尚未执行的部分没有补做。"
    if not answer and stop_reason != "cancelled":
        remaining = (math.inf if not math.isfinite(deadline)
                     else max(0.0, deadline - time.monotonic() - 1.0))
        if not math.isfinite(remaining) or remaining > 1:
            missing_groups = [group for group in required_tool_groups
                              if not group.intersection(tool_names_called)]
            missing_notice = ""
            if missing_groups:
                missing_names = [" / ".join(sorted(group)) for group in missing_groups]
                missing_notice = (
                    "\n明确要求的步骤仍未实际调用：" + "; ".join(missing_names) + ". "
                    "不得声称该步骤已完成，也不得把未执行说成无结果；请明确标记未完成。"
                )
            from src.core.task_events import release_finalization
            release_finalization(get_job_id())
            final_cap = min(4096, completion_budget, int(settings.AGENT_FINAL_OUTPUT_TOKENS))
            final_model = get_llm(model=get_deepseek_model(), api_key=settings.RESOLVED_MAIN_API_KEY,
                base_url=settings.RESOLVED_MAIN_BASE_URL, temperature=settings.TEMPERATURE_MAIN,
                max_tokens=final_cap, timeout=min(120.0, _model_timeout()), thinking_off=True)
            try:
                from src.graph.finalization import final_messages as build_final_messages
                report_messages = build_final_messages(model=final_model, query=query,
                    transcript=messages[trace_start:], records=tool_status_records,
                    gaps=coverage.gaps(task_contract), instruction=_FINAL_INSTRUCTION + missing_notice,
                    output_tokens=final_cap)
                final_response = await _call_llm(final_model, report_messages,
                    timeout=_llm_timeout(deadline - time.monotonic() - 1.0),
                    deadline=deadline - 1.0, purpose="final_synthesis")
                final_text = str(final_response.content or "")
                if _DSML_PROTOCOL_RE.search(final_text):
                    stop_reason = "invalid_tool_protocol"
                else:
                    answer = _sanitize_answer_artifact_links(final_text)
                    messages.append(final_response)
            except Exception as exc:
                logger.warning("Final receipt unavailable (%s): %s", stop_reason, type(exc).__name__)
                failure_notice = failure_notice or _provider_failure_notice(exc)
                if stop_reason in {"model_finished", "reflection_done"}:
                    stop_reason = "model_error:" + type(exc).__name__
        if not answer:
            # Tool failures from prior turns remain in conversation history for
            # context, but must never mark this unrelated turn as failed.  The
            # closing notice names the actual stop reason: reporting every empty
            # answer as "超出请求时限" hid truncation and 500s behind a timeout.
            tool_notice = _tool_failure_notice(messages[trace_start:])
            if stop_reason == "cancelled":
                # S1: 停止优先于一切失败/超时话术——这是用户的选择，不是故障。
                from src.core.turn_notices import stopped_notice
                answer = stopped_notice()
            elif failure_notice:
                answer = failure_notice
            elif tool_notice:
                answer = tool_notice
            elif stop_reason == "request_deadline":
                answer = notice("deadline")
            elif stop_reason == "tool_turn_limit":
                answer = notice("tool_turn_limit", limit=turn_budget)
            elif tool_turns:
                answer = notice("no_evidence", detail="请换个更具体的问法，或指定数据来源。")
            else:
                answer = notice("output_truncated", budget=completion_budget)
    # A required tool that ran but failed / produced no evidence is NOT a
    # completed research step.  Distinguish the two, so the answer never
    # claims success on the strength of a mere invocation.
    # An explicit evidence/file request is satisfied only by a complete
    # execution with a usable result. A successful-but-empty search or a
    # partial channel failure is recorded, but must not make the task complete.
    def _required_ok(record: dict[str, Any]) -> bool:
        """A required group counts as satisfied when its tool produced a result.

        ``partial`` is a real outcome for long operations (a verification run
        that resolved 29 of 30 records and delivered the workbook). Treating it
        as a failure made every such turn open with "本轮未完成", which is
        misleading when the deliverable exists. A partial run *without* an
        artifact stays a failure.
        """
        if record.get("status") == "complete":
            return True
        return (record.get("status") == "partial"
                and int(record.get("artifact_count") or 0) > 0)

    # Deliverable attribution: "self" = the model called an export tool itself,
    # "gate" = the post-answer delivery gate had to supply the file. Without this
    # record the two are indistinguishable in logs, and the reported export rate
    # silently mixes model behaviour with the safety net.
    export_gate: dict[str, Any] = _export_gate_record(
        deliverable_needed, messages, trace_start, deliverable_requirements)
    final_validations = _verified_artifact_validations(messages[trace_start:])
    final_satisfied = (
        _deliverables_satisfied(final_validations, deliverable_requirements)
        if deliverable_needed else False
    )
    # The single terminal decision. It reads the contract, the coverage account
    # and the export-contract result; answer prose never decides completion.
    from src.core.turn_outcome import decide_turn_outcome
    matched_deliverable_indexes = _deliverable_match_map(
        final_validations, deliverable_requirements)
    unmet_deliverables = [
        f"{'+'.join(requirement.get('kinds') or ['artifact'])}"
        f"/{'+'.join(requirement.get('formats') or ['any-format'])}"
        for index, requirement in enumerate(deliverable_requirements)
        if index not in matched_deliverable_indexes
    ]
    matched_deliverables = len(matched_deliverable_indexes)
    if deliverable_needed:
        export_gate["status"] = (
            "satisfied" if final_satisfied else
            "partial" if matched_deliverables else "unmet")
        export_gate["missing_requirements"] = unmet_deliverables
        # Legacy projection fields remain readable, without a second executor.
        export_gate.update(forced_tools=[], forced_links=[], forced_attempts=[],
                           forced_tool="", forced_link="")

    # Recompute both machine state and visible notices after gate execution. A
    # stale pre-gate "unattempted" entry used to reject a successfully delivered
    # file even when the final tool trace said the export had completed.
    # Verified artifacts satisfy file requirements regardless of which retained
    # renderer produced them. Do not demand a deprecated/canonical tool call in
    # addition to a current-request, owner/hash/format-validated delivery.
    if final_satisfied:
        delivered_groups = _deliverable_tool_groups(deliverable_requirements)
        required_tool_groups = [g for g in required_tool_groups if g not in delivered_groups]
        unavailable_required_groups = [g for g in unavailable_required_groups if g not in delivered_groups]
    missing_groups = [group for group in required_tool_groups
                      if not group.intersection(tool_names_called)]
    missing_requirements = [
        "unavailable:" + " / ".join(sorted(group))
        for group in unavailable_required_groups
    ] + [
        "unattempted:" + " / ".join(sorted(group))
        for group in missing_groups
    ]
    if unmet_deliverables:
        missing_requirements.append("deliverable_unmet:" + ";".join(unmet_deliverables))
    # Unmet obligations are part of the persisted terminal record, not a line
    # only the log can see. One entry per obligation object.
    for _gap in coverage.gaps(task_contract):
        missing_requirements.append(
            "unmet:" + str(_gap.get("reason_code") or "obligation"))

    from src.core.task_result import effective_tool_records
    effective_records = effective_tool_records(tool_status_records)
    failed_required: list[str] = []
    if required_tool_groups and effective_records:
        for group in required_tool_groups:
            if not group.intersection(tool_names_called):
                continue          # already covered by missing_groups
            ok_records = [r for r in effective_records if r["name"] in group]
            if ok_records and not any(_required_ok(r) for r in ok_records):
                names = sorted({r["name"] for r in ok_records})
                detail = "; ".join(
                    f"{r['name']}={r['status']}"
                    + (f"({r['reason']})" if r.get("reason") else "")
                    for r in ok_records)
                failed_required.append(f"{' / '.join(names)} [{detail}]")
    missing_requirements.extend(
        "unmet:" + value for value in failed_required)
    export_groups = _deliverable_tool_groups(deliverable_requirements)
    missing_names = [
        " / ".join(sorted(group))
        for group in list(unavailable_required_groups) + list(missing_groups)
        if group not in export_groups
    ]
    missing_names.extend("requested deliverable " + value for value in unmet_deliverables)

    contract_outcome = decide_turn_outcome(
        stop_reason=stop_reason, contract=task_contract, coverage=coverage,
        deliverable_needed=deliverable_needed, deliverables_satisfied=final_satisfied,
        tool_records=tool_status_records, answer=answer, missing_requirements=missing_requirements)
    delivery_complete = bool(
        contract_outcome.status == "completed" and deliverable_needed and final_satisfied
        and not missing_groups and not unavailable_required_groups
        and not unmet_deliverables and not failed_required)
    # Reconcile whenever a verified file exists, not only when the whole turn is
    # complete: FW17/FW18 passed the delivery gate yet still told the user the
    # report had not been generated. The notice is added *after* reconciliation
    # so its recount reflects what is genuinely left.
    if answer and matched_deliverables:
        answer = _reconcile_delivery_claims(
            answer, True, delivered_count=matched_deliverables,
            outstanding_count=len(unmet_deliverables))
    answer = _refresh_required_step_notices(
        answer, missing_count=len(missing_names), failed_count=len(failed_required))
    # Obligations the turn still owes are stated once, in the shared incomplete
    # form, so the user reads the real gap instead of "一切完成".
    answer = _append_contract_notices(answer, contract_outcome.notices)

    answer = _append_verified_artifact_links(answer, tool_status_records)
    if delivery_complete:
        from src.graph.document_delivery import compact_word_delivery
        answer = compact_word_delivery(answer, final_validations, deliverable_requirements)

    if answer and "[[FIGURE:" in answer:
        answer = figures.expand(answer)
        # 内部记录键必须止于此：模型偶尔会把 `paper_<hash>...` 抄进正文。
        answer = scrub_internal_keys(answer)
    # Single publication point: the streamed answer must be byte-for-byte the
    # same finalized answer that is persisted and returned in the done payload.
    if answer:
        emit_answer_start()
        emit_text(answer)
    elapsed = (time.monotonic() - started) * 1000
    from src.core.task_result import build_task_result
    # One status, decided in core.turn_outcome from the contract, coverage and
    # export result; build_task_result keeps the existing aggregation.
    task_result = build_task_result(
        tool_status_records, missing_requirements,
        requested_status=contract_outcome.status)
    logger.info("General Agent finished: %d chars, %d tool types, %.0fms, stop=%s",
                len(answer), len(tool_names_called), elapsed, stop_reason)
    trace_messages = list(messages[trace_start:])
    # Every call that started must end with exactly one honest terminal state.
    # A user stop or an unexpected exit must not leave a running row behind.
    if stop_reason == "cancelled":
        execution_ledger.close(status="cancelled", reason_code="user_stop")
    else:
        execution_ledger.close(status="failed", reason_code="terminal_missing")
    ledger_payload = execution_ledger.as_dict()
    reset_execution_ledger(_ledger_token)
    try:
        # Say what is true about this turn, not a fixed completion line.
        if contract_outcome.notices:
            emit_status("agent_progress", message="；".join(contract_outcome.notices[:2]))
    except Exception:
        pass
    return {
        "answer": answer,
        "gen_time_ms": elapsed,
        "main_results": main_results,
        "web_results": web_results,
        "tools_called": tool_names_called,
        # Structured per-tool terminal states, so the API layer can classify the
        # turn without inferring from answer prose.
        "tool_status": [dict(r) for r in tool_status_records],
        # Machine-readable export attribution: model-initiated vs delivery-gate.
        "export_gate": export_gate,
        # One request-scoped ledger: normal, DSML and forced calls share the
        # same identity and terminal status contract.
        "execution_ledger": ledger_payload,
        "task_contract": task_contract.as_dict(),
        "coverage": coverage.as_dict(),
        "turn_outcome": contract_outcome.as_dict(),
        "context_working_state": _task_working_state(state, query, tool_status_records, deliverable_requirements),
        "turn_trace": _safe_persisted_trace(trace_messages),
        "_trace": {"node": "supervisor", "elapsed_ms": elapsed,
                   "summary": f"{len(answer)} chars, {len(tool_names_called)} tool types, {stop_reason}",
                   "stop_reason": stop_reason,
                   "tool_turns": tool_turns,
                   "source_scope": scope,
                   "retrieval_decision": retrieval_decision.as_diagnostic(),
                   "retrieval_mode": retrieval_decision.mode,
                   "retrieval_reason_code": retrieval_decision.reason_code,
                   "history_turn_seqs": list(retrieval_decision.history_turn_seqs),
                   "required_tools_unattempted": [sorted(group) for group in missing_groups],
                   "required_tools_unavailable": [sorted(group) for group in unavailable_required_groups],
                   "request_constraints": {
                       "source_policy": request_constraints.source_policy,
                       "requested_mode": request_constraints.requested_mode,
                       "data_audit_requested": request_constraints.data_audit_requested,
                       "pure_transform": request_constraints.pure_transform,
                       "reason_code": request_constraints.reason_code,
                   },
                   "task_result": task_result.as_dict(),
                   # The agreement, the account and the single terminal decision,
                   # so a later turn or a debugger can see why this status holds.
                   "task_contract": task_contract.as_dict(),
                   "coverage": coverage.as_dict(),
                   "turn_outcome": contract_outcome.as_dict(),
                   "local_route": "called" if any(n.startswith("local_") for n in tool_names_called) else "not_called",
                   "local_tool_types": [n for n in tool_names_called if n.startswith("local_")],
                   "retrieval_disabled_simple_query": retrieval_disabled,
                   "candidate_pool": (candidate_pool.summary()
                                      if candidate_pool is not None else {}),
                   "tool_policy_reason": tool_policy.reason},
    }

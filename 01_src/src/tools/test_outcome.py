from src.tools.outcome import normalize_tool_outcome, safe_tool_step


def test_tool_outcome_distinguishes_complete_partial_no_result_failure_and_not_run():
    assert normalize_tool_outcome("audit_uploaded_data", "数据审查已完成：重复值 1 条。详情见异常报告。[打开异常报告](/api/v2/session/s/artifacts/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)").status == "complete"
    partial = normalize_tool_outcome("audit_uploaded_data", "数据审查未完整完成，未完成范围不能判断为无异常。")
    assert partial.status == "partial"
    not_run = normalize_tool_outcome("audit_uploaded_data", "审查未启动：当前轮没有选择附件。")
    assert not_run.status == "not_run"
    assert normalize_tool_outcome("local_hybrid_search", "", {"local_status": "hit", "main_results": []}).status == "no_results"
    assert normalize_tool_outcome("deepseek_search", "[WEB_TOOL_ERROR] Search failed", {"web_results": []}).status == "failed"
    missing_search_key = normalize_tool_outcome(
        "deepseek_search",
        "[WEB_TOOL_ERROR] Search failed: RuntimeError: DeepSeek Search/Fetch deployment API key is not configured",
        {"web_results": []})
    assert missing_search_key.status == "not_run"
    assert missing_search_key.reason_code == "configuration_missing"
    assert normalize_tool_outcome("local_hybrid_search", "", {}).status == "failed"
    assert normalize_tool_outcome(
        "local_hybrid_search", "[LOCAL_HYBRID_TRANSLATION_ERROR] Chinese remains; no search", {}).as_dict()["status"] == "not_run"
    assert normalize_tool_outcome(
        "local_hybrid_search", "", {"local_status": "translation_error"}).reason_code == "translation_failed"
    # An uncounted sentence does not prove that every requested reference ran.
    assert normalize_tool_outcome("verify_uploaded_references", "登记库未查到记录。", {}).status == "partial"
    mixed = normalize_tool_outcome(
        "verify_uploaded_references",
        "已核验 2 条 DOI：metadata_matched 1 条、unverified 1 条。", {})
    assert mixed.status == "partial"
    assert normalize_tool_outcome(
        "fetch_and_audit_open_supplements", "公开补充材料获取未完成：TimeoutException。", {}).status == "failed"


def test_safe_tool_step_exposes_only_status_and_short_safe_summary():
    result = safe_tool_step(
        "local_hybrid_search", "", {"local_status": "hit", "main_results": [{}, {}]})
    assert result["status"] == "complete"
    assert result["reason_code"] == ""
    assert result["summary"] == "找到 2 条候选资料"
    assert result["reason_class"] == ""
    assert result["retryable"] is False
    failed = safe_tool_step("deepseek_search", "[WEB_TOOL_ERROR] 401 secret-url", {})
    assert failed["status"] == "failed"
    assert failed["reason_code"] == "auth"
    assert "secret-url" not in failed["summary"]
    assert safe_tool_step("audit_uploaded_data", "数据审查未完整完成，未完成范围不能判断为无异常。", {})[
        "status"] == "partial"
    assert safe_tool_step("any_tool", "[TOOL_DUPLICATE_SKIPPED] repeat", {})[
        "status"] == "skipped"


def test_batch_verification_success_is_complete_not_unrecognized():
    ok = normalize_tool_outcome(
        "verify_uploaded_references",
        "已处理 56 条文献，分 6 批（每批 ≤10 条）：metadata_matched 52 条、"
        "title_candidate 3 条、web_candidate 1 条。", {})
    assert ok.status == "complete"
    unfinished = normalize_tool_outcome(
        "verify_uploaded_references",
        "已处理 56 条文献，分 6 批（每批 ≤10 条）：metadata_matched 40 条、network_error 16 条。", {})
    assert unfinished.status == "partial"


def test_budget_and_source_conditions_are_not_tool_failures():
    from src.tools.outcome import normalize_tool_outcome

    deadline = normalize_tool_outcome("verify_uploaded_references", "[ERR_REQUEST_DEADLINE] stop", {})
    assert deadline.status == "partial"
    assert deadline.reason_code == "timeout"
    assert deadline.reason_class == "transient"

    missing = normalize_tool_outcome("deepseek_fetch", "[WEB_FETCH_FAILED] 404 not found", {})
    assert missing.status == "partial"
    assert missing.reason_class == "source_unavailable"

    paper = normalize_tool_outcome(
        "deepseek_fetch_paper", "[WEB_FETCH_FAILED] 未取得可验证全文", {})
    assert paper.status == "partial"
    assert paper.reason_class == "source_unavailable"


def test_finished_audit_is_not_reported_as_our_own_defect():
    """审核包里 26 次成功审查全部被记成 internal_error，因为映射表缺码。"""
    from src.tools.outcome import reason_class_for

    assert reason_class_for("audit_completed", "complete") == ""
    assert reason_class_for("duplicate_request", "skipped") == ""
    assert reason_class_for("tool_batch_limit", "skipped") == ""
    # 真实缺陷仍然是缺陷。
    assert reason_class_for("audit_failed", "failed") == "internal_error"
    assert reason_class_for("tool_error", "failed") == "internal_error"
    assert reason_class_for("deliverable_rejected", "failed") == "internal_error"


def test_scope_limit_and_incomplete_coverage_are_different_classes():
    from src.tools.outcome import reason_class_for

    assert reason_class_for("scope_limited", "complete") == "scope_limited"
    assert reason_class_for("coverage_incomplete", "partial") == "coverage_incomplete"
    # 工具不可用不是我们的代码缺陷，但也不是"输入缺失"。
    assert reason_class_for("tool_unavailable", "failed") == "source_unavailable"


def _audit_outcome(status, reason, coverage="1/1 个文件完成"):
    return {"_tool_outcome": {"status": status, "reason_code": reason,
                              "coverage": coverage,
                              "artifacts": ["/api/v2/session/s/artifacts/" + "a" * 32]}}


def test_wide_table_audit_is_complete_with_an_explicit_scope_note():
    step = safe_tool_step("audit_uploaded_data", "完成 1 个工作表的审查",
                          _audit_outcome("complete", "scope_limited"))
    assert step["status"] == "complete"
    assert step["reason_code"] == "scope_limited"
    assert step["reason_class"] == "scope_limited"
    assert step["retryable"] is False


def test_real_truncation_stays_partial_and_incomplete():
    step = safe_tool_step("audit_uploaded_data", "审查未完整完成",
                          _audit_outcome("partial", "coverage_incomplete"))
    assert step["status"] == "partial"
    assert step["reason_class"] == "coverage_incomplete"


def test_candidate_pool_reason_codes_are_classified_honestly():
    """候选池的失败原因不是我们的缺陷：缺输入、来源不可用或临时故障。"""
    from src.tools.outcome import reason_class_for

    assert reason_class_for("no_candidate_pool", "not_run") == "input_missing"
    assert reason_class_for("candidate_not_found", "not_run") == "input_missing"
    assert reason_class_for("candidate_offset_out_of_range", "not_run") == "input_missing"
    assert reason_class_for("candidate_search_failed", "failed") == "source_unavailable"
    # 零命中与"没有新增"都是正常结果，不是失败。
    assert reason_class_for("no_candidates", "no_results") == ""
    assert reason_class_for("no_new_candidates", "complete") == ""
    # 查询侧翻译失败是本轮的临时条件。
    assert reason_class_for("translation_failed", "not_run") == "transient"


def test_candidate_tool_outcomes_are_not_unknown():
    """候选工具必须给出显式终态，否则会被判为 unrecognized_tool_outcome。"""
    missing = normalize_tool_outcome(
        "inspect_candidates", "[CANDIDATE_POOL_MISSING] 本轮没有可用的候选池。",
        {"_tool_outcome": {"status": "not_run", "reason_code": "no_candidate_pool"}})
    assert missing.status == "not_run"
    assert missing.reason_code == "no_candidate_pool"
    assert missing.reason_class == "input_missing"

    ok = normalize_tool_outcome(
        "inspect_candidates", "候选池 3 条",
        {"_tool_outcome": {"status": "complete", "coverage": "候选池 3 条；返回 3 条"}})
    assert ok.status == "complete"
    assert ok.reason_code == ""


def test_a_statistics_report_is_a_finished_step():
    """统计工具只回一段 JSON，没有 artifact。

    没有专门分支时，每次成功的统计都会落到 unrecognized_tool_outcome，也就是
    "我们自己出错了"——而实际上 p 值已经算出来并给了模型。
    """
    report = '{"test": {"applicable": true, "p": 0.024, "effect_size": {"name": "hedges_g"}}}'
    done = normalize_tool_outcome("analyze_research_data", report)
    assert done.status == "complete"
    assert done.reason_code == ""
    assert done.reason_class == ""


def test_a_statistics_refusal_keeps_its_reason():
    """拒绝要给出可区分的理由，而不是一律"失败"。"""
    arg = normalize_tool_outcome(
        "analyze_research_data", "[STATS_ARG] 表中没有列「x」；可用：a, b")
    assert arg.status == "failed" and arg.reason_code == "invalid_arguments"

    empty = normalize_tool_outcome(
        "analyze_research_data", "[STATS_EMPTY] 表格没有数据行可供统计。")
    assert empty.status == "no_results" and empty.reason_code == "no_processable_table"

    unreadable = normalize_tool_outcome(
        "analyze_research_data", "[STATS_INPUT_ERROR] 读取表格失败：bad encoding")
    assert unreadable.status == "not_run" and unreadable.reason_code == "no_processable_table"


def test_figure_codes_are_not_classified_as_our_defect():
    """成功出图不该被报成 internal_error，沙箱未配置也不该。"""
    from src.tools.outcome import reason_class_for

    published = normalize_tool_outcome(
        "create_research_figure", "已出图并发布。",
        {"_tool_outcome": {"status": "complete", "reason_code": "figure_published"}})
    assert published.status == "complete"
    assert published.reason_class == ""

    unavailable = normalize_tool_outcome(
        "run_research_figure_script",
        "[FIGURE_SANDBOX_UNAVAILABLE] 当前环境没有配置绘图脚本沙箱。",
        {"_tool_outcome": {"status": "failed", "reason_code": "SANDBOX_UNAVAILABLE"}})
    assert unavailable.reason_class == "source_unavailable"

    assert reason_class_for("SCRIPT_TIMEOUT", "failed") == "transient"
    assert reason_class_for("SCRIPT_OOM", "failed") == "transient"

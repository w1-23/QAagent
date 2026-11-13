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

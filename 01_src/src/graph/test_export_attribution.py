"""Export attribution only counts registry-verified, request-matching artifacts."""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.graph.general_agent_graph import (
    _deliverable_match_map,
    _deliverables_satisfied,
    _export_gate_record,
    _has_export_artifact,
    _self_export_names,
)


def _tool(name: str, *, filename: str = "report.html", valid: bool = False,
          request_matches: bool = True) -> ToolMessage:
    validation = {
        "valid": valid,
        "filename": filename,
        "mime": "text/html" if filename.endswith(".html") else "application/octet-stream",
        "producer_tool": name,
        "request_id": "turn-1",
        "request_matches": request_matches,
    }
    return ToolMessage(
        content=f"generated {filename}", tool_call_id=f"c-{name}", name=name,
        artifact={"_artifact_validations": [validation]})


_REPORT_HTML = [{"kinds": ["report_html"], "formats": ["html"]}]
_ANNOTATION_XLSX = [{"kinds": ["annotation_table"], "formats": ["xlsx"]}]


def test_no_export_and_not_requested_stays_none():
    messages = [HumanMessage(content="柑橘黑点病怎么防治"), AIMessage(content="……")]
    gate = _export_gate_record(False, messages, 0, [])
    assert gate["requested"] is False
    assert gate["initiator"] == "none"
    assert gate["self_export_tools"] == []
    assert gate["status"] == "not_requested"


def test_model_self_export_requires_a_verified_matching_artifact():
    messages = [_tool("export_paper_analysis", valid=True)]
    gate = _export_gate_record(True, messages, 0, _REPORT_HTML)
    assert gate["requested"] is True
    assert gate["initiator"] == "model"
    assert gate["self_export_tools"] == ["export_paper_analysis"]
    assert gate["status"] == "satisfied"
    assert gate["forced_tool"] == ""


def test_export_gate_supplied_export_alone_is_not_model_credit():
    messages = [HumanMessage(content="导出核验表"), AIMessage(content="结论……")]
    gate = _export_gate_record(True, messages, 0, _ANNOTATION_XLSX)
    assert gate["initiator"] == "none"
    assert gate["self_export_tools"] == []
    assert gate["status"] == "pending"


def test_prior_turn_and_wrong_format_artifacts_do_not_satisfy_request():
    messages = [
        _tool("export_paper_analysis", filename="report.html", valid=True),
        HumanMessage(content="这轮要 xlsx"),
    ]
    assert _has_export_artifact(messages) is True
    assert _has_export_artifact(messages, _ANNOTATION_XLSX) is False
    assert _self_export_names(messages[1:], _ANNOTATION_XLSX) == []
    gate = _export_gate_record(True, messages, 1, _ANNOTATION_XLSX)
    assert gate["status"] == "pending"


def test_tool_name_alone_never_counts_and_declared_type_must_match():
    assert not _has_export_artifact([_tool("export_paper_analysis")], _REPORT_HTML)
    assert not _has_export_artifact(
        [_tool("audit_uploaded_data", filename="report.html", valid=True)], _REPORT_HTML)
    assert not _has_export_artifact(
        [_tool("search_literature", filename="annotation.xlsx", valid=True)], _ANNOTATION_XLSX)


def test_registry_producer_and_actual_extension_can_satisfy_contract():
    messages = [_tool("audit_uploaded_data", filename="annotation.xlsx", valid=True)]
    assert _has_export_artifact(messages, _ANNOTATION_XLSX)
    assert _self_export_names(messages, _ANNOTATION_XLSX) == ["audit_uploaded_data"]


def test_artifact_from_another_request_cannot_satisfy_current_turn():
    messages = [_tool("audit_uploaded_data", filename="annotation.xlsx",
                      valid=False, request_matches=False)]
    assert not _has_export_artifact(messages, _ANNOTATION_XLSX)
    assert _self_export_names(messages, _ANNOTATION_XLSX) == []


def test_distinct_output_contracts_require_distinct_artifacts():
    """Each requested product needs its own registry identity, even with the same kind."""
    requirement = {"kinds": ["report_html"], "formats": ["html"]}
    duplicate_requirements = [requirement, dict(requirement)]
    first = {
        "valid": True, "filename": "first.html", "producer_tool": "export_paper_analysis",
        "request_matches": True, "link": "/artifacts/first",
    }
    # Distinct product clauses remain distinct; one file cannot complete both.
    assert not _deliverables_satisfied([first], duplicate_requirements)
    second = {**first, "filename": "second.html", "link": "/artifacts/second"}
    assert _deliverables_satisfied([first, second], duplicate_requirements)

    # Separate typed and format-only products do not share a workbook implicitly.
    xlsx = {
        "valid": True, "filename": "annotated.xlsx", "producer_tool": "audit_uploaded_data",
        "request_matches": True, "link": "/artifacts/annotated",
    }
    assert _deliverable_match_map(
        [xlsx],
        [{"kinds": ["annotation_table"], "formats": ["xlsx"]},
         {"kinds": [], "formats": ["xlsx"]}],
    ) == {0: 0}

    # But an annotated workbook cannot substitute for a requested report.
    assert not _deliverables_satisfied(
        [xlsx],
        [{"kinds": ["annotation_table"], "formats": ["xlsx"]},
         {"kinds": ["report_html"], "formats": ["html"]}],
    )


def test_final_answer_link_validation_is_scoped_to_current_request(monkeypatch):
    import src.core.tracing as tracing
    import src.research.tools as research_tools
    from src.graph.general_agent_graph import _sanitize_answer_artifact_links

    calls = []
    link = "/api/v2/session/session_12345678/artifacts/" + "a" * 32
    monkeypatch.setattr(tracing, "get_session_id", lambda: "session_12345678")
    monkeypatch.setattr(tracing, "get_user_id", lambda: 7)
    monkeypatch.setattr(tracing, "get_client_request_id", lambda: "request-current")

    def validate(links, **kwargs):
        calls.append((list(links), kwargs))
        return [{"link": link, "valid": False, "reason_code": "request_mismatch"}]

    monkeypatch.setattr(research_tools, "validate_artifact_links", validate)
    sanitized = _sanitize_answer_artifact_links(f"[prior file]({link})")
    assert calls[0][1]["request_id"] == "request-current"
    assert "request-current" not in sanitized
    assert "文件链接未通过交付校验" in sanitized


def test_state_declares_every_key_the_api_reads_off_the_supervisor():
    """Keep structured attribution and tool state available to the API layer."""
    from src.graph.state import AgentState

    hints = AgentState.__annotations__
    for key in ("tools_called", "tool_status", "export_gate", "citation_info",
                "audit_report", "adjudication"):
        assert key in hints, f"AgentState 缺 {key}，langgraph 会丢掉 supervisor 的这个返回值"


def test_every_declared_artifact_producer_has_a_download_label():
    from src.graph.general_agent_graph import _ARTIFACT_LINK_LABELS
    from src.tools import _TOOL_DECLARATIONS
    producers = {tool.name for tool, _category, _key, _scope, _requires, produces, _auth
                 in _TOOL_DECLARATIONS if produces}
    assert producers <= set(_ARTIFACT_LINK_LABELS)
    assert all(_ARTIFACT_LINK_LABELS[name].strip() for name in producers)


# Sentences copied from the audit's real FW06/FW17/FW18 answers, not a rerun.
_FW06_DENIAL = "如果你希望我把上述内容落成 HTML 文件，需要重新执行一次导出，我会在完成后给出实际下载链接。"
_FW17_DENIAL = "需要先说明：**HTML 报告文件本轮未实际生成**，导出步骤未执行，因此没有可下载链接。"
_FW18_DENIAL = "本轮**未能生成两份 HTML 文件**，因此**没有下载链接可提供**。"


def test_fw06_reexport_sentence_is_corrected_only_after_verified_delivery():
    from src.graph.general_agent_graph import _is_delivery_denial, _reconcile_delivery_claims

    assert _is_delivery_denial(_FW06_DENIAL)
    cleaned = _reconcile_delivery_claims("## 对照结论\n结论正文。\n" + _FW06_DENIAL, True)
    assert "需要重新执行一次导出" not in cleaned
    assert "结论正文。" in cleaned
    assert "交付状态更正" in cleaned
    assert _reconcile_delivery_claims(_FW06_DENIAL, False) == _FW06_DENIAL


def test_partial_delivery_preserves_the_specific_missing_file_and_other_work():
    from src.graph.general_agent_graph import _reconcile_delivery_claims

    text = (_FW18_DENIAL + "\n对外脱敏简报未生成；文献核验尚未完成。\n"
            "实验未生成新数据；建议重新生成数据前先核查设计。")
    cleaned = _reconcile_delivery_claims(text, True, delivered_count=1, outstanding_count=1)
    assert "未能生成两份 HTML 文件" not in cleaned
    assert "没有下载链接可提供" not in cleaned
    assert "已生成 1 份" in cleaned and "仍有 1 项" in cleaned
    assert "对外脱敏简报未生成" in cleaned
    assert "文献核验尚未完成。" in cleaned
    assert "实验未生成新数据" in cleaned
    assert "建议重新生成数据前先核查设计。" in cleaned


def test_reconcile_does_not_remove_other_incomplete_facts_on_the_same_line():
    from src.graph.general_agent_graph import _reconcile_delivery_claims

    text = "本轮未完成：文献核验未执行；HTML 报告未能生成。\n## 结果\n可复核正文。"
    cleaned = _reconcile_delivery_claims(text, True)
    assert cleaned.startswith("本轮未完成：文献核验未执行")
    assert "HTML 报告未能生成" not in cleaned
    assert "## 结果\n可复核正文。" in cleaned


def test_required_notices_are_recounted_from_receipts_and_other_notices_survive():
    from src.core.turn_notices import notice
    from src.graph.general_agent_graph import _refresh_required_step_notices

    text = (notice("required_steps_missing", count=3) + "\n\n"
            + notice("model_connection") + "\n\n正文。")
    cleaned = _refresh_required_step_notices(text, missing_count=1, failed_count=0)
    assert cleaned.count(notice("required_steps_missing", count=1)) == 1
    assert "有 3 项" not in cleaned
    assert notice("model_connection") in cleaned
    completed = _refresh_required_step_notices(cleaned, missing_count=0, failed_count=0)
    assert "明确要求的步骤" not in completed
    assert notice("model_connection") in completed


def test_delivery_correction_is_idempotent_and_can_refresh_partial_counts():
    from src.graph.general_agent_graph import _reconcile_delivery_claims

    first = _reconcile_delivery_claims(_FW17_DENIAL + "\n## 结果\n正文。", True)
    assert _reconcile_delivery_claims(first, True) == first
    partial = _reconcile_delivery_claims(first, True, delivered_count=1, outstanding_count=1)
    assert partial.count("交付状态更正") == 1
    assert "仍有 1 项" in partial
    assert _reconcile_delivery_claims(partial, True, delivered_count=1, outstanding_count=1) == partial


def _run_turn_with_gate_receipt(monkeypatch, *, missing_verification=False):
    """Exercise the real graph finalizer with offline model/export receipts."""
    import asyncio
    from types import SimpleNamespace
    import src.core.llm_pool as llm_pool
    import src.graph.general_agent_graph as graph

    class Model:
        def bind_tools(self, _tools):
            return self

    async def respond(*_a, **_kw):
        return AIMessage(content="## 方法与结论\n可复核正文。\n" + _FW17_DENIAL)

    link = "/api/v2/session/session_12345678/artifacts/" + "b" * 32

    async def export(_query, _answer, _tools, requirements, *_a, **_kw):
        validation = {
            "valid": True, "request_matches": True, "artifact_id": "b" * 32,
            "filename": "report.html", "producer_tool": "export_paper_analysis", "link": link,
        }
        return [{"name": "export_paper_analysis", "status": "complete", "reason_code": "",
                 "artifact_count": 1, "artifact_validations": [validation], "link": link,
                 "requirement": requirements[0], "forced": True}]

    monkeypatch.setattr(llm_pool, "get_llm", lambda **_kw: Model())
    monkeypatch.setattr(graph, "_call_llm", respond)
    monkeypatch.setattr(graph, "_scoped_toolset", lambda *a, **k: (
        [SimpleNamespace(name="export_paper_analysis")], []))
    monkeypatch.setattr(graph, "_force_deliverable_export", export)
    monkeypatch.delenv("CITRUS_DELIVERY_GATE_DISABLED", raising=False)
    query = "生成一份 HTML 报告"
    if missing_verification:
        query = "核验参考文献，并" + query
    result = asyncio.run(graph.general_agent_node({
        "query": query, "session_id": "session-offline-attribution", "messages": [],
    }))
    return result, link


def test_gate_completion_clears_preexport_machine_gaps(monkeypatch):
    result, link = _run_turn_with_gate_receipt(monkeypatch)
    assert result["export_gate"]["status"] == "satisfied"
    assert result["_trace"]["task_result"]["missing_requirements"] == ()
    assert result["_trace"]["task_result"]["status"] == "completed"
    assert "本轮未完成" not in result["answer"]
    assert "未实际生成" not in result["answer"]
    assert link in result["answer"]


def test_gate_reconciles_delivery_even_when_verification_stays_unavailable(monkeypatch):
    result, link = _run_turn_with_gate_receipt(monkeypatch, missing_verification=True)
    assert result["export_gate"]["status"] == "satisfied"
    assert result["_trace"]["task_result"]["status"] == "partial"
    missing = result["_trace"]["task_result"]["missing_requirements"]
    assert any("verify_uploaded_references" in item for item in missing)
    assert not any(item.startswith("unattempted:") for item in missing)
    assert "有 1 项明确要求的步骤没有执行" in result["answer"]
    assert "未实际生成" not in result["answer"]
    assert "交付状态更正" in result["answer"]
    assert link in result["answer"]

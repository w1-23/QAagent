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

"""冻结版本的可靠性契约测试（对应研究方案 §4 的 P0 修复项）。

这些断言故意“看源码/看行为”，用于防止已修复的故障在后续改动中回退：
- 拒绝把截断正文或截断 JSON 当作完整交付；
- 工具协议文字不得被当成答案；
- 用户明确要文件时必须真的产出文件；
- 同名不同产物的自动区分；
- 文本附件可被读取。
"""

from __future__ import annotations

import pathlib

from langchain_core.messages import ToolMessage

from src.graph.general_agent_graph import (
    _deliverable_contract as _full_deliverable_contract,
    _deliverable_export_requested,
    _forced_export_plan,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _deliverable_contract(query):
    # These legacy assertions cover kind/format; product identity has separate tests.
    return [{"kinds": r["kinds"], "formats": r["formats"]} for r in _full_deliverable_contract(query)]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_deliverable_export_requested_detects_only_direct_positive_file_requests():
    assert _deliverable_export_requested("把这三张表的异常整理成 HTML 报告")
    assert _deliverable_export_requested("导出核验表给我")
    assert _deliverable_export_requested("生成一份 XLSX 标注文件")
    assert _deliverable_export_requested("Create a workbook report")
    assert _deliverable_export_requested("Make a PDF report")
    assert _deliverable_export_requested("Create a bibliography")
    assert not _deliverable_export_requested("解释一下柑橘黄龙病的传播途径")
    assert not _deliverable_export_requested("如何导出 HTML 报告？")
    assert not _deliverable_export_requested("Please explain how to create a PDF report")
    assert not _deliverable_export_requested("请问如何生成 XLSX 标注表")
    assert not _deliverable_export_requested("请不要导出报告")
    assert not _deliverable_export_requested("Do not create a report file")


def test_real_worksheet_names_are_not_deliverables():
    """Table S1/S2 are worksheet names, not a request for an extra file."""
    text = "请审查 Table S1 和 Table S2 的数据质量，并整理成 XLSX 标注表"
    assert _deliverable_contract(text) == [
        {"kinds": ["annotation_table"], "formats": ["xlsx"]},
    ]
    # A bare English noun must not become an unsatisfiable generic requirement.
    assert _deliverable_contract("review Table S3 for consistency") == []


def test_bibliography_requires_a_word_boundary():
    # "Comparison" contains "ris" but is not a bibliographic request.
    assert _deliverable_contract(
        "请阅读附件 P06_Comparison_of_Resistance_study.pdf 并给出方法对照表") == []
    assert _deliverable_contract("导出 RIS 文献表") == [
        {"kinds": ["bibliography"], "formats": ["ris"]},
    ]


def test_overlapping_kind_words_collapse_into_one_requirement():
    assert _deliverable_contract("生成实验设计报告") == [
        {"kinds": ["report_docx"], "formats": ["docx"]},
    ]
    assert _deliverable_contract("生成一份 HTML 报告") == [
        {"kinds": ["report_html"], "formats": ["html"]},
    ]


def test_two_products_of_the_same_kind_stay_separate():
    """Two requested reports are two deliverables, not one."""
    assert _deliverable_contract("请生成对照表报告，并提供一份脱敏对外简报") == [
        {"kinds": ["report_docx"], "formats": ["docx"]},
        {"kinds": ["report_docx"], "formats": ["docx"]},
    ]


def test_generic_output_alone_is_satisfied_by_any_verified_artifact():
    from src.graph.export_contract import artifact_matches_requirement

    assert _deliverable_contract("请把结果导出成文件") == [
        {"kinds": ["generic_output"], "formats": []},
    ]
    assert artifact_matches_requirement(
        {"valid": True, "filename": "anything.xlsx", "producer_tool": "audit_uploaded_data"},
        {"kinds": ["generic_output"], "formats": []})


def test_deliverable_contract_keeps_conjoined_products_separate():
    requirements = _deliverable_contract("生成 HTML 报告和 CSV 表格")
    assert requirements == [
        {"kinds": ["report_html"], "formats": ["html"]},
        {"kinds": ["table_file"], "formats": ["csv"]},
    ]
    assert _deliverable_contract("Create a report and save it as HTML") == [
        {"kinds": ["report_html"], "formats": ["html"]},
    ]
    assert _deliverable_contract("Make a PDF report") == [
        {"kinds": ["report_html"], "formats": ["pdf"]},
    ]


def test_forced_export_plan_covers_registered_producers_and_declines_unsupported_formats():
    tools = {
        name: object() for name in (
            "export_paper_analysis", "audit_uploaded_data",
            "verify_uploaded_references", "export_literature_evidence",
            "fetch_and_audit_open_supplements", "export_corpus_gap_report", "draw_svg",
        )
    }
    messages = [ToolMessage(content="已生成核验结果", tool_call_id="verify", name="verify_uploaded_references"), ToolMessage(
        content="local retrieval had a channel failure",
        tool_call_id="search-1", name="local_hybrid_search",
        artifact={"errors": {"bm25": "timeout"}},
    )]
    answer = "```svg\n<svg xmlns='http://www.w3.org/2000/svg'></svg>\n```"
    cases = [
        ({"kinds": ["report_html"], "formats": ["html"]}, "export_paper_analysis"),
        ({"kinds": ["annotation_table"], "formats": ["xlsx"]}, "audit_uploaded_data"),
        ({"kinds": ["verification_sheet"], "formats": ["xlsx"]}, "verify_uploaded_references"),
        ({"kinds": ["bibliography"], "formats": ["xlsx"]}, "export_literature_evidence"),
        ({"kinds": ["supplement_archive"], "formats": ["tar.gz"]},
         "fetch_and_audit_open_supplements"),
        ({"kinds": ["supplement_report"], "formats": ["xlsx"]},
         "fetch_and_audit_open_supplements"),
        ({"kinds": ["gap_csv"], "formats": ["csv"]}, "export_corpus_gap_report"),
        ({"kinds": ["figure"], "formats": ["svg"]}, "draw_svg"),
    ]
    query = "请生成补充材料报告 10.1234/example"
    for requirement, expected_name in cases:
        name, _args, reason = _forced_export_plan(
            requirement, query, answer, messages, tools)
        assert name == expected_name
        assert not reason

    name, _args, reason = _forced_export_plan(
        {"kinds": ["report_html"], "formats": ["pdf"]}, query, answer, messages, tools)
    assert not name
    assert reason == "unsupported_or_ambiguous_contract"
    name, _args, reason = _forced_export_plan(
        {"kinds": ["supplement_archive"], "formats": ["zip"]}, query, answer, messages, tools)
    assert not name
    assert reason == "unsupported_format"


def test_false_delivery_denials_are_removed_after_a_real_export():
    from src.graph.general_agent_graph import _reconcile_delivery_claims

    text = ("本轮未完成：有 1 项明确要求的步骤没有执行。\n\n## 一、结果\n\n"
            "本次没有可用的文件导出通道，HTML 报告未能生成，因此没有下载链接。\n内容如下。")
    cleaned = _reconcile_delivery_claims(text, True)
    assert "本轮未完成" not in cleaned
    assert "没有可用的文件导出通道" not in cleaned
    assert "## 一、结果" in cleaned
    assert "内容如下。" in cleaned
    assert "交付状态更正" in cleaned
    # Without a delivered file the denial must stay: it is then true.
    assert _reconcile_delivery_claims(text, False) == text


def test_unclosed_trailing_block_is_not_silently_published():
    from src.research.tools import export_paper_analysis
    output = export_paper_analysis.func(title="报告", analysis_markdown="## 完整前言\n" + "测试正文。" * 20 + "\n```html\n<h1>半截")
    assert "unbalanced_code_fence" in output
    assert "已生成" not in output


def test_forced_export_uses_standard_tool_lifecycle(monkeypatch):
    import asyncio
    import src.graph.general_agent_graph as graph
    import src.tools.registry as registry

    events = []
    link = "/api/v2/session/session_12345678/artifacts/" + "b" * 32
    validation = {
        "valid": True, "link": link, "filename": "forced.html",
        "producer_tool": "export_paper_analysis", "request_matches": True,
    }

    async def run_fake(_tool, call):
        return ToolMessage(
            content=f"[report]({link})", tool_call_id=call["id"],
            name=call["name"], artifact={},
        )

    def start(*_args, **_kwargs):
        events.append("start")

    def executing(*_args, **_kwargs):
        events.append("executing")

    def complete(call, message):
        events.append("terminal")
        message.artifact = {"_artifact_validations": [validation]}
        return {
            "status": "complete", "reason_code": "", "retryable": False,
            "artifact_validations": [validation],
        }

    monkeypatch.setattr(registry, "_run_single_tool", run_fake)
    monkeypatch.setattr(graph, "emit_tool_call_start", start)
    monkeypatch.setattr(graph, "emit_tool_executing", executing)
    monkeypatch.setattr(graph, "_emit_completed_tool_step", complete)
    records = asyncio.run(graph._force_deliverable_export(
        "Create a report", "research results", {"export_paper_analysis": object()},
        [{"kinds": ["report_html"], "formats": ["html"]}], timeout=5,
    ))
    assert events == ["start", "executing", "terminal"]
    assert records[0]["status"] == "complete"
    assert records[0]["link"] == link


def test_tool_protocol_text_never_becomes_the_answer():
    src = _read("src/graph/general_agent_graph.py")
    assert "本轮模型返回了未执行的工具调用格式" not in src, "协议文本不得直接当答案返回"
    assert "正在安全恢复回答" in src, "协议恢复重试必须存在"


def test_truncated_json_is_rejected_before_data_is_changed():
    import pytest
    from src.core.tool_errors import ToolArgumentError
    from src.research.tools import _json_arg
    with pytest.raises(ToolArgumentError, match="本批未写入"):
        _json_arg('[{"doi":"10.1000/truncated', list, [])


def test_text_attachments_are_readable():
    src = _read("src/api/main.py")
    assert '"txt", "md", "csv", "tsv", "json"' in src
    assert "utf-8-sig" in src and "gb18030" in src


def test_budget_and_source_conditions_are_partial_not_failed():
    from src.tools.outcome import safe_tool_step

    step = safe_tool_step("deepseek_fetch", "[ERR_REQUEST_DEADLINE] The request is nearing its time limit.")
    assert step["status"] == "partial"
    step2 = safe_tool_step("deepseek_fetch", "[WEB_FETCH_FAILED] 404 Not Found")
    assert step2["status"] == "partial"
    rejected = safe_tool_step(
        "export_paper_analysis", "[DELIVERABLE_REJECTED:payload_too_short] payload rejected")
    assert rejected["status"] == "failed"
    assert rejected["reason_code"] == "deliverable_rejected"
    assert rejected["retryable"] is False


def test_parallel_timeout_keeps_completed_results_and_emits_one_terminal_event(monkeypatch):
    import asyncio
    from langchain_core.messages import ToolMessage
    import src.tools.registry as registry
    from src.graph.general_agent_graph import _run_regular_calls

    async def run_fake(tool, call):
        if call["id"] == "slow":
            await asyncio.sleep(1)
        return ToolMessage(content=f"done:{call['id']}",
                           tool_call_id=call["id"], name=call["name"], artifact={})

    monkeypatch.setattr(registry, "_run_single_tool", run_fake)
    terminal_events = []
    calls = [
        {"id": "fast", "name": "fast_tool", "args": {}},
        {"id": "slow", "name": "slow_tool", "args": {}},
    ]
    results = asyncio.run(_run_regular_calls(
        calls, {"fast_tool": object(), "slow_tool": object()},
        completion_callback=lambda call, message: terminal_events.append(call["id"]),
        timeout=0.02))

    assert len(results) == 2
    assert results[0].content == "done:fast"
    assert results[1].content.startswith("[ERR_REQUEST_DEADLINE]")
    assert terminal_events == ["fast", "slow"]


def test_identical_failed_tool_call_retries_once_then_deduplicates(monkeypatch):
    import asyncio
    import time
    from types import SimpleNamespace
    from langchain_core.messages import ToolMessage
    import src.tools.registry as registry
    import src.graph.general_agent_graph as graph

    attempts = 0

    async def run_fake(_tool, call):
        nonlocal attempts
        attempts += 1
        content = "[TOOL_ERROR] temporary failure" if attempts == 1 else "导出成功"
        return ToolMessage(content=content, tool_call_id=call["id"],
                           name=call["name"], artifact={})

    monkeypatch.setattr(registry, "_run_single_tool", run_fake)
    monkeypatch.setattr(graph, "_emit_completed_tool_step", lambda *args, **kwargs: None)
    seen = {}
    tool_map = {"export_paper_analysis": object()}
    args = {"title": "same request"}

    async def run(call_id):
        response = SimpleNamespace(tool_calls=[{
            "id": call_id, "name": "export_paper_analysis", "args": args,
        }])
        return await graph._execute_calls(
            response, tools_by_name=tool_map, deadline=time.monotonic() + 10,
            seen_call_signatures=seen)

    first = asyncio.run(run("first"))
    second = asyncio.run(run("second"))
    third = asyncio.run(run("third"))

    assert first[0].content.startswith("[TOOL_ERROR]")
    assert second[0].content == "导出成功"
    assert third[0].content.startswith("[TOOL_DUPLICATE_SKIPPED]")
    assert attempts == 2


def test_artifact_validation_terminal_failure_allows_one_identical_retry(monkeypatch):
    import asyncio
    import time
    from types import SimpleNamespace
    from langchain_core.messages import ToolMessage
    import src.tools.registry as registry
    import src.graph.general_agent_graph as graph

    attempts = 0

    async def run_fake(_tool, call):
        nonlocal attempts
        attempts += 1
        return ToolMessage(content="Export completed", tool_call_id=call["id"],
                           name=call["name"], artifact={})

    def publish_fake(call, _message, _sink=None):
        first_was_unverified = attempts == 1
        return {
            "status": "failed" if first_was_unverified else "complete",
            "reason_code": "artifact_validation_failed" if first_was_unverified else "",
            "retryable": first_was_unverified,
            "artifact_validations": [],
        }

    monkeypatch.setattr(registry, "_run_single_tool", run_fake)
    monkeypatch.setattr(graph, "_emit_completed_tool_step", publish_fake)
    seen = {}
    tool_map = {"export_paper_analysis": object()}
    args = {"title": "unverified artifact"}

    async def run(call_id):
        response = SimpleNamespace(tool_calls=[{
            "id": call_id, "name": "export_paper_analysis", "args": args,
        }])
        return await graph._execute_calls(
            response, tools_by_name=tool_map, deadline=time.monotonic() + 10,
            seen_call_signatures=seen)

    asyncio.run(run("first"))
    asyncio.run(run("second"))
    third = asyncio.run(run("third"))
    assert attempts == 2
    assert third[0].content.startswith("[TOOL_DUPLICATE_SKIPPED]")

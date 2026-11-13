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
    from src.core.turn_notices import notice
    from src.graph.general_agent_graph import (
        _reconcile_delivery_claims, _refresh_required_step_notices,
    )

    text = (notice("required_steps_missing", count=1) + "\n\n## 一、结果\n\n"
            "本次没有可用的文件导出通道，HTML 报告未能生成，因此没有下载链接。\n内容如下。")
    cleaned = _refresh_required_step_notices(
        _reconcile_delivery_claims(text, True), missing_count=0, failed_count=0)
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


def test_forced_export_persists_a_paired_history_step(monkeypatch):
    """A gate export must survive refresh as a real call/result pair."""
    import asyncio

    from langchain_core.messages import AIMessage

    import src.graph.general_agent_graph as graph
    import src.tools.registry as registry
    from src.core.execution_ledger import (
        ExecutionLedger, bind_execution_ledger, reset_execution_ledger,
    )
    from src.session.manager import _validate_trace

    ledger = ExecutionLedger(session_id="s", request_id="r")
    token = bind_execution_ledger(ledger)
    link = "/api/v2/session/session_12345678/artifacts/" + "c" * 32
    validation = {
        "valid": True, "link": link, "filename": "forced.html",
        "producer_tool": "export_paper_analysis", "request_matches": True,
    }

    async def run_fake(_tool, call):
        return ToolMessage(
            content=f"[report]({link})", tool_call_id=call["id"],
            name=call["name"], artifact={},
        )

    def complete(call, message, _sink=None):
        message.artifact = {"_artifact_validations": [validation]}
        step = {"status": "complete", "reason_code": "", "retryable": False}
        graph._record_execution_terminal(
            call, step, str(message.content), [validation])
        return {**step, "artifact_validations": [validation]}

    monkeypatch.setattr(registry, "_run_single_tool", run_fake)
    monkeypatch.setattr(graph, "_emit_completed_tool_step", complete)
    try:
        records = asyncio.run(graph._force_deliverable_export(
            "Create a report", "research results",
            {"export_paper_analysis": object()},
            [{"kinds": ["report_html"], "formats": ["html"]}], timeout=5,
        ))
    finally:
        reset_execution_ledger(token)

    assert records[0]["status"] == "complete"
    pair = records[0]["persisted_messages"]
    assert len(pair) == 2
    ai_message, tool_message = pair
    assert isinstance(ai_message, AIMessage)
    assert ai_message.tool_calls[0]["id"] == records[0]["tool_call_id"]
    assert tool_message.tool_call_id == ai_message.tool_calls[0]["id"]
    assert _validate_trace(pair) == pair
    assert ledger.record(records[0]["tool_call_id"]).source == "forced"


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


def _gate_with_recording_exporter(monkeypatch):
    """Run the delivery gate with a fake exporter that records every body."""
    import src.graph.general_agent_graph as graph
    import src.tools.registry as registry

    bodies = []

    async def run_fake(_tool, call):
        bodies.append(dict(call["args"]))
        identity = f"{len(bodies):032d}"
        link = f"/api/v2/session/session_12345678/artifacts/{identity}"
        validation = {
            "valid": True, "request_matches": True, "artifact_id": identity,
            "link": link, "filename": f"report{len(bodies)}.html",
            "producer_tool": call["name"],
        }
        message = ToolMessage(content=f"[report]({link})", tool_call_id=call["id"],
                              name=call["name"], artifact={})
        message.artifact = {"_artifact_validations": [validation]}
        return message

    def complete(_call, message):
        validations = (message.artifact or {}).get("_artifact_validations", [])
        return {"status": "complete", "reason_code": "", "retryable": False,
                "artifact_validations": validations}

    monkeypatch.setattr(registry, "_run_single_tool", run_fake)
    monkeypatch.setattr(graph, "emit_tool_call_start", lambda *a, **k: None)
    monkeypatch.setattr(graph, "emit_tool_executing", lambda *a, **k: None)
    monkeypatch.setattr(graph, "_emit_completed_tool_step", complete)
    return graph, bodies


def test_second_report_with_its_own_audience_is_never_a_copy_of_the_answer(monkeypatch):
    """FW18: the internal report may use the answer; the redacted brief may not."""
    import asyncio
    from src.graph.export_contract import deliverable_contract
    from src.graph.test_document_contracts import FW18_PROMPT

    graph, bodies = _gate_with_recording_exporter(monkeypatch)
    requirements = deliverable_contract(FW18_PROMPT)
    answer = "内部方法对照正文（含未公开细节）。"
    records = asyncio.run(graph._force_deliverable_export(
        FW18_PROMPT, answer, {"export_paper_analysis": object()},
        requirements, timeout=5))

    assert len(bodies) == 1, "只能导出有独立正文来源的那一份"
    assert bodies[0]["analysis_markdown"] == answer
    assert [r["reason_code"] for r in records] == ["", "missing_product_content"]
    assert records[1]["status"] == "not_run"
    assert records[1].get("link", "") == ""


def test_report_still_exports_when_the_turn_also_wants_a_workbook(monkeypatch):
    """The old guard disabled *every* report as soon as two requirements existed."""
    import asyncio
    from src.graph.export_contract import deliverable_contract
    from src.graph.test_document_contracts import FW11_PROMPT

    graph, bodies = _gate_with_recording_exporter(monkeypatch)
    requirements = deliverable_contract(FW11_PROMPT)
    answer = "复核清单正文。"
    records = asyncio.run(graph._force_deliverable_export(
        FW11_PROMPT, answer,
        {"export_paper_analysis": object(), "audit_uploaded_data": object()},
        requirements, timeout=5))

    exported = [r for r in records if r["name"] == "export_paper_analysis"]
    assert len(exported) == 1 and exported[0]["status"] == "complete"
    reports = [body for body in bodies if "analysis_markdown" in body]
    assert reports == [{"title": reports[0]["title"], "analysis_markdown": answer}]


def test_two_reports_without_separate_bodies_export_only_one(monkeypatch):
    import asyncio

    graph, bodies = _gate_with_recording_exporter(monkeypatch)
    requirements = [
        {"kinds": ["report_html"], "formats": ["html"], "body_scope": "shared"},
        {"kinds": ["report_html"], "formats": ["html"], "body_scope": "shared"},
    ]
    records = asyncio.run(graph._force_deliverable_export(
        "生成两份 HTML 报告", "同一份正文", {"export_paper_analysis": object()},
        requirements, timeout=5))
    assert len(bodies) == 1
    assert [r["reason_code"] for r in records] == ["", "missing_product_content"]


def test_same_concurrency_key_is_bounded_without_starving_other_tools(monkeypatch):
    import asyncio
    import src.tools.registry as registry
    import src.graph.general_agent_graph as graph

    active = {"research_data": 0, "free": 0}
    maximum = {"research_data": 0, "free": 0}

    async def fake_run(_tool, call):
        key = "research_data" if call["name"] in {
            "audit_uploaded_data", "inspect_uploaded_table"} else "free"
        active[key] += 1
        maximum[key] = max(maximum[key], active[key])
        try:
            await asyncio.sleep(0.02)
        finally:
            active[key] -= 1
        return ToolMessage(content="ok", tool_call_id=call["id"], name=call["name"],
                           artifact={})

    monkeypatch.setattr(registry, "_run_single_tool", fake_run)
    calls = [
        {"id": f"r{i}", "name": "audit_uploaded_data", "args": {}}
        for i in range(8)
    ] + [
        {"id": f"f{i}", "name": "deepseek_search", "args": {}}
        for i in range(4)
    ]
    tools = {name: object() for name in {
        "audit_uploaded_data", "inspect_uploaded_table", "deepseek_search"}}
    results = asyncio.run(graph._run_regular_calls(calls, tools, timeout=5))
    assert len(results) == len(calls)
    assert maximum["research_data"] <= graph._MAX_PARALLEL_PER_KEY
    assert maximum["free"] >= 1


def test_explicit_same_content_exports_each_requested_copy(monkeypatch):
    import asyncio
    from src.graph.export_contract import deliverable_contract

    graph, bodies = _gate_with_recording_exporter(monkeypatch)
    query = "生成两份 HTML 报告，内容相同。"
    requirements = deliverable_contract(query)
    records = asyncio.run(graph._force_deliverable_export(
        query, "同一份正文", {"export_paper_analysis": object()},
        requirements, timeout=5))
    assert len(bodies) == 2
    assert [r["reason_code"] for r in records] == ["", ""]
    assert len({body["title"] for body in bodies}) == 2


def test_different_products_do_not_reuse_the_answer(monkeypatch):
    import asyncio
    from src.graph.export_contract import deliverable_contract

    graph, bodies = _gate_with_recording_exporter(monkeypatch)
    query = "生成 HTML 病害报告和 HTML 营养报告。"
    requirements = deliverable_contract(query)
    records = asyncio.run(graph._force_deliverable_export(
        query, "一份正文", {"export_paper_analysis": object()},
        requirements, timeout=5))
    assert len(bodies) == 1
    assert [r["reason_code"] for r in records] == ["", "missing_product_content"]


def test_three_audience_reports_export_one_and_report_two_missing(monkeypatch):
    import asyncio
    from src.graph.export_contract import deliverable_contract

    graph, bodies = _gate_with_recording_exporter(monkeypatch)
    query = "三份 HTML 报告，分别面向研发、销售和客户。"
    requirements = deliverable_contract(query)
    records = asyncio.run(graph._force_deliverable_export(
        query, "一份正文", {"export_paper_analysis": object()},
        requirements, timeout=5))
    assert len(bodies) == 1
    assert [r["reason_code"] for r in records] == [
        "", "missing_product_content", "missing_product_content"]


def test_ambiguous_quantity_keeps_the_gap_visible(monkeypatch):
    import asyncio
    from src.graph.export_contract import deliverable_contract

    graph, bodies = _gate_with_recording_exporter(monkeypatch)
    query = "生成两份 HTML 报告。"
    requirements = deliverable_contract(query)
    records = asyncio.run(graph._force_deliverable_export(
        query, "一份正文", {"export_paper_analysis": object()},
        requirements, timeout=5))
    assert len(bodies) == 1
    assert [r["reason_code"] for r in records] == ["", "missing_product_content"]
    assert all(r["requirement"]["needs_clarification"] is True for r in records)


_DSML_TEXT = ('<｜｜DSML｜｜calls><｜｜DSML｜｜invoke name="x">'
              '</｜｜DSML｜｜invoke></｜｜DSML｜｜calls>')


def _run_agent_with_scripted_model(monkeypatch, final_texts):
    """真实跑 general_agent_node：只替换模型与工具集，其余逻辑不打桩。"""
    import asyncio
    from types import SimpleNamespace

    import src.core.llm_pool as llm_pool
    import src.graph.general_agent_graph as graph

    passes = []

    class FakeLLM:
        def __init__(self, bound=False):
            self.bound = bound

        def bind_tools(self, _tools):
            return FakeLLM(True)

    async def fake_llm(llm, messages, *, timeout, on_text=None, llm_fallback=None):
        passes.append(len(messages))
        if getattr(llm, "bound", False):
            content = "正常回答。"
        else:
            index = min(len(passes) - 2, len(final_texts) - 1)
            content = final_texts[max(0, index)]
        return SimpleNamespace(content=content, tool_calls=[],
                               response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(graph, "_call_llm", fake_llm)
    monkeypatch.setattr(llm_pool, "get_llm", lambda **_kw: FakeLLM())
    monkeypatch.setattr(graph, "_scoped_toolset", lambda *a, **k: ([], None))
    return asyncio.run(graph.general_agent_node({
        "query": "柑橘黄龙病的传播途径是什么",
        "session_id": "sess-synthetic-0001", "messages": [], "answer": "",
    })), passes


def test_final_synthesis_recovers_from_dsml_instead_of_returning_nothing(monkeypatch):
    """最终综述以前命中 DSML 就直接 answer=""，没有任何恢复。"""
    result, passes = _run_agent_with_scripted_model(
        monkeypatch, [_DSML_TEXT, _DSML_TEXT, "恢复后的最终答案。"])
    assert result["answer"] == "恢复后的最终答案。"
    assert len(passes) == 4, "最终综述必须真的重试，而不是直接清空"


def test_final_synthesis_gives_up_after_a_bounded_number_of_recoveries(monkeypatch):
    result, passes = _run_agent_with_scripted_model(monkeypatch, [_DSML_TEXT])
    assert _DSML_TEXT not in result["answer"]
    assert len(passes) == 5, "2 次循环恢复 + 3 次收尾恢复，之后必须停下"
    assert result["answer"], "放弃恢复后仍要给出如实的终态说明，不能是空字符串"


def test_generic_fetch_failure_keeps_an_honest_terminal_notice():
    from src.core.turn_notices import notice
    from src.graph.general_agent_graph import _tool_failure_notice
    message = ToolMessage(content="[WEB_FETCH_FAILED] no usable body", name="deepseek_fetch",
                          tool_call_id="offline-failed-fetch")
    assert _tool_failure_notice([message]) == notice("web_tool_failed")


def test_dead_reconciliation_branch_is_gone():
    assert "pass  # reconciliation happens" not in _read(
        "src/graph/general_agent_graph.py")


def test_empty_answer_done_branch_keeps_job_and_audit_terminal_states_consistent(monkeypatch):
    """Execute the real empty-answer branch without importing/starting the API service."""
    import ast
    import asyncio
    import json
    import logging
    import sys
    import time
    from types import SimpleNamespace

    source = ast.parse(_read("src/api/main.py"))
    answer_branch = next(node for node in ast.walk(source)
                         if isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                         and node.test.id == "ans" and any(
                             isinstance(part, ast.Constant) and part.value == "empty_answer"
                             for child in node.orelse for part in ast.walk(child)))
    module = ast.Module(body=[ast.AsyncFunctionDef(
        name="run_empty_answer", args=ast.arguments(posonlyargs=[], args=[],
            kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=answer_branch.orelse, decorator_list=[])], type_ignores=[])
    code = compile(ast.fix_missing_locations(module), "api-empty-answer-branch", "exec")
    for tool_status, expected in (([], "failed"),
                                 ([{"name": "read", "status": "cancelled"}], "cancelled")):
        emitted, jobs, audits = [], [], []

        async def flush():
            pass

        async def emit(event):
            emitted.append(event)

        monkeypatch.setitem(sys.modules, "src.admin_audit", SimpleNamespace(
            finish_request=lambda **kw: audits.append(kw)))
        context = {
            "flush_bridge": flush, "output": {"_trace": {"history_saved": True}},
            "agent_output": {"tool_status": tool_status}, "get_request_journal": lambda: [],
            "time": time, "t0": time.perf_counter(), "sid": "offline-session-0001",
            "job_id": "offline-job", "rid": "offline-request", "emit_to_client": emit,
            "_encode_event": lambda name, data: (name, json.loads(json.dumps(data))),
            "jobs_mod": SimpleNamespace(
                mark_stopped=lambda *a, **kw: jobs.append(("stopped", a, kw)),
                update_job=lambda *a, **kw: jobs.append(("updated", a, kw)),
                save_terminal_snapshot=lambda *a, **kw: jobs.append(
                    ("snapshot", a, kw))),
            "logger": logging.getLogger(__name__),
        }
        exec(code, context)
        asyncio.run(context["run_empty_answer"]())
        assert len(emitted) == 1 and emitted[0][0] == "done"
        assert emitted[0][1]["turn_status"] == expected
        assert emitted[0][1]["answer"] == ""
        # No validated artifact this turn: the authoritative list is empty,
        # never "whatever appeared in the account library".
        assert emitted[0][1]["artifact_ids"] == []
        assert emitted[0][1]["turn_envelope"]["artifact_ids"] == []
        snapshot = jobs[-1]
        assert snapshot[0] == "snapshot" and snapshot[1][0] == "offline-job"
        assert list(snapshot[1][1].get("artifact_ids") or ()) == []
        if expected == "cancelled":
            assert jobs[0][0] == "stopped" and jobs[0][1] == ("offline-job", "user")
            assert audits[0]["status"] == "cancelled"
        else:
            assert jobs[0][0] == "updated" and jobs[0][2]["status"] == "failed"
            assert jobs[0][2]["error"] == "empty_answer"
            assert audits[0]["status"] == "incomplete"


def test_empty_answer_still_emits_a_terminal_done_event():
    src = _read("src/api/main.py")
    block = src[src.index('ans = agent_output.get("answer", "")'):]
    block = block[:block.index("except asyncio.CancelledError")]
    assert "else:" in block, "空回答必须有 else 分支"
    assert '"notice": "本轮没有形成可交付的回答' in block
    assert 'error="empty_answer"' in block
    assert block.count('_encode_event("done"') == 2


def test_every_done_branch_carries_artifact_identity_from_the_envelope():
    """Normal, empty and cancel branches all publish the same artifact list."""
    src = _read("src/api/main.py")
    assert 'done_payload["artifact_ids"] = list(' in src
    assert src.count('"artifact_ids": list(envelope.artifact_ids)') == 2

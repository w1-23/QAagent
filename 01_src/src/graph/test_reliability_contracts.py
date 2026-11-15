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

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent.host_test import environment as host_environment, install, action

from src.graph.export_contract import (
    deliverable_contract as _full_deliverable_contract,
    deliverable_export_requested as _deliverable_export_requested,
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
    assert not _deliverable_export_requested("Create a bibliography")
    assert _deliverable_export_requested("Create a bibliography file")
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
    assert _deliverable_contract("生成实验设计报告") == []
    assert _deliverable_contract("生成 DOCX 实验设计报告") == [
        {"kinds": ["report_docx"], "formats": ["docx"]},
    ]
    assert _deliverable_contract("生成一份 HTML 报告") == [
        {"kinds": ["report_html"], "formats": ["html"]},
    ]


def test_two_products_of_the_same_kind_stay_separate():
    """Separate explicit files retain identity; ordinary report prose needs no file."""
    assert _deliverable_contract("请生成对照表报告，并提供一份脱敏对外简报") == []
    assert _deliverable_contract("请生成 DOCX 对照表报告，并提供一份 DOCX 脱敏对外简报") == [
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


@pytest.mark.parametrize("query", [
    "Create an HTML report", "生成 XLSX 标注表", "导出 RIS 文献表", "Make a PDF report",
    "生成 SVG 图", "下载补充材料 ZIP 压缩包",
])
def test_file_request_keywords_never_dispatch_an_unselected_export(host_environment, monkeypatch, query):
    import asyncio
    from langchain_core.tools import tool
    from src.agent import host
    from src.core import task_events

    state, job = host_environment
    state["query"] = query
    exported = []
    @tool
    def export_paper_analysis(analysis_markdown: str):
        """Publish a report only when explicitly selected by the model."""
        exported.append(analysis_markdown)
        return "unexpected export"
    sent = install(monkeypatch, [AIMessage(content="尚未生成文件；需要先确认内容。")],
                   [(export_paper_analysis, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert len(sent) == 1 and not exported
    assert not task_events.projection(job)["operations"]
    assert result["tools_called"] == []
    assert result["export_gate"] == {"status": "not_applicable", "reason": "model_owned_delivery"}
    assert "尚未生成文件" in result["answer"]


def test_false_delivery_denials_are_removed_after_a_real_export():
    from src.core.turn_notices import notice
    from src.graph.agent_operations import (
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


def test_selected_export_uses_standard_tool_lifecycle(artifact_environment, monkeypatch):
    from src.core import task_events
    state, job = artifact_environment
    result, bodies, links, sent = _run_selected_exports(artifact_environment, monkeypatch,
        "Create an HTML report", [("report.html", "Verified research results")])
    assert bodies == [("report.html", "Verified research results")]
    assert len(sent) == 2 and links[0] in result["answer"]
    assert result["tool_status"][0]["status"] == "complete"
    assert result["tool_status"][0]["artifact_validations"][0]["valid"]
    frames = [row["data"] for row in task_events.replay(job)["events"] if row["event"] == "operation"]
    assert len(frames) == 2
    assert frames[0]["tool_call_id"] == frames[1]["tool_call_id"] == "export-0"
    assert task_events.projection(job)["operations"][0]["status"] == "complete"


def test_selected_export_persists_a_paired_history_step(artifact_environment, monkeypatch):
    import asyncio
    from src.graph.session_persistence import persist_agent_turn
    from src.session.manager import _validate_trace
    from src.core import tool_results

    result, _, links, _ = _run_selected_exports(artifact_environment, monkeypatch,
        "Create an HTML report", [("report.html", "Exact published body")])
    trace = result["turn_trace"]
    assert _validate_trace(trace) == trace
    calls = [m for m in trace if isinstance(m, AIMessage) and m.tool_calls]
    observations = [m for m in trace if isinstance(m, ToolMessage)]
    assert len(calls) == len(observations) == 1
    assert calls[0].tool_calls[0]["id"] == observations[0].tool_call_id == "export-0"
    assert links[0] in tool_results.fetch("export-0")["result_text"]
    saved = asyncio.run(persist_agent_turn({**artifact_environment[0], **result}))
    assert saved["_trace"]["history_saved"] is True


def test_tool_protocol_text_never_becomes_the_answer(host_environment, monkeypatch):
    from src.core import task_events

    result, sent = _run_agent_with_scripted_model(
        host_environment, monkeypatch, [_DSML_TEXT, "安全恢复后的回答。"])
    assert result["answer"] == "安全恢复后的回答。"
    assert _DSML_TEXT not in result["answer"]
    assert len(sent) == 2
    assert "不要用工具协议文本冒充答案" in sent[1][-1].content
    assert not task_events.projection(host_environment[1])["operations"]


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
    from src.graph.agent_operations import _run_regular_calls

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
    import src.graph.agent_operations as graph

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
    import src.graph.agent_operations as graph

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


@pytest.fixture
def artifact_environment(host_environment, tmp_path, monkeypatch):
    import contextvars
    from src import auth
    from src.core import account_leases, tracing
    from src.research import tools
    from src.session import files

    state, job = host_environment
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(account_leases, "_states", {})
    auth.init_store()
    with auth._connect() as conn:
        conn.execute("INSERT INTO users(user_id,username,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
                     (state["user_id"], "synthetic_owner", b"salt", b"hash", "2026-11-13"))
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "session_files")
    monkeypatch.setattr(tracing, "_client_request_id", contextvars.ContextVar("request", default=state["request_id"]))
    monkeypatch.setattr(tracing, "_current_tool_name", contextvars.ContextVar("tool", default=""))
    files.init_store()
    return state, job


def _report_html(body):
    from html import escape
    return "<!doctype html><html><body><p>" + escape(body) + "</p></body></html>"


def _run_selected_exports(environment, monkeypatch, query, products, final_notice=""):
    import asyncio
    from langchain_core.tools import tool
    from src.agent import host
    from src.research import tools

    state, _ = environment
    state["query"] = query
    bodies, links = [], []
    @tool(response_format="content_and_artifact")
    def export_paper_analysis(title: str, analysis_markdown: str):
        """Save the exact model-selected report body as a registered artifact."""
        bodies.append((title, analysis_markdown))
        link = tools._save_artifact(title, _report_html(analysis_markdown).encode("utf-8"), "text/html")
        links.append(link)
        return f"[report]({link})", {"_tool_outcome": {"status": "complete"}}
    def final(messages):
        return AIMessage(content="\n".join(f"[report]({link})" for link in links) + "\n" + final_notice)
    responses = [action("export_paper_analysis", f"export-{i}", title=title, analysis_markdown=body)
                 for i, (title, body) in enumerate(products)] + [final]
    sent = install(monkeypatch, responses, [(export_paper_analysis, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["turn_outcome"]["status"] == "completed", result
    assert len(bodies) == len(products)
    return result, bodies, links, sent


def test_second_report_with_its_own_audience_is_never_a_copy_of_the_answer(artifact_environment, monkeypatch):
    from src.graph.test_document_contracts import FW18_PROMPT
    from src.research import tools

    secret = "INTERNAL-UNPUBLISHED-METHOD-ONLY"
    public = "Public brief: aggregate findings with all private methods removed."
    result, bodies, links, _ = _run_selected_exports(artifact_environment, monkeypatch,
        FW18_PROMPT, [("internal.html", secret), ("public.html", public)])
    assert bodies == [("internal.html", secret), ("public.html", public)]
    sid = artifact_environment[0]["session_id"]
    published = [tools.resolve_artifact(sid, link.rsplit("/", 1)[-1])["path"].read_text(encoding="utf-8") for link in links]
    assert published == [_report_html(secret), _report_html(public)]
    assert secret not in published[1] and secret not in result["answer"]


def test_report_still_exports_when_the_turn_also_wants_a_workbook(artifact_environment, monkeypatch):
    from src.graph.test_document_contracts import FW11_PROMPT
    result, bodies, links, _ = _run_selected_exports(artifact_environment, monkeypatch,
        FW11_PROMPT, [("report.html", "复核清单正文。")], "XLSX 标注表尚未生成。")
    assert bodies == [("report.html", "复核清单正文。")]
    assert links[0] in result["answer"] and "尚未生成" in result["answer"]
    assert result["tools_called"] == ["export_paper_analysis"]


def test_two_reports_without_separate_bodies_export_only_one(artifact_environment, monkeypatch):
    result, bodies, links, _ = _run_selected_exports(artifact_environment, monkeypatch,
        "生成两份 HTML 报告", [("report.html", "Only supplied body")], "第二份正文尚未提供。")
    assert len(bodies) == len(links) == 1
    assert "第二份正文尚未提供" in result["answer"]


def test_read_batches_bound_concurrency_without_dropping_later_calls(host_environment, monkeypatch):
    import asyncio
    from langchain_core.tools import tool
    from src.agent import host
    from src.core import task_events

    state, job = host_environment
    active, maximum = 0, 0
    observed = []
    async def read(kind, index):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.01)
            observed.append((kind, index))
            return str((kind, index))
        finally:
            active -= 1
    @tool
    async def inspect_fixture(index: int):
        """Read one synthetic local observation."""
        import json
        return json.dumps({"observation": await read("research", index),
                           "_tool_outcome": {"status": "complete"}})
    @tool
    async def other_fixture(index: int):
        """Read another independent synthetic observation."""
        import json
        return json.dumps({"observation": await read("other", index),
                           "_tool_outcome": {"status": "complete"}})
    calls = [{"id": f"read-{i}", "name": "inspect_fixture", "args": {"index": i}} for i in range(8)]
    calls += [{"id": f"other-{i}", "name": "other_fixture", "args": {"index": i}} for i in range(4)]
    sent = install(monkeypatch, [AIMessage(content="", tool_calls=calls), AIMessage(content="All observations retained")],
                   [(inspect_fixture, False), (other_fixture, False)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["turn_outcome"]["status"] == "completed" and len(sent) == 2
    assert 1 < maximum <= 4 and active == 0
    assert sorted(observed) == sorted([("research", i) for i in range(8)] + [("other", i) for i in range(4)])
    messages = [m for m in sent[1] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in messages] == [call["id"] for call in calls]
    operations = task_events.projection(job)["operations"]
    assert len(operations) == 12
    assert all(operation["status"] == "complete" for operation in operations), [
        (operation["status"], operation.get("outcome")) for operation in operations]


def test_explicit_same_content_exports_each_requested_copy(artifact_environment, monkeypatch):
    result, bodies, links, _ = _run_selected_exports(artifact_environment, monkeypatch,
        "生成两份 HTML 报告，内容相同。",
        [("copy-one.html", "Explicitly shared body"), ("copy-two.html", "Explicitly shared body")])
    assert len(bodies) == len(set(links)) == 2
    assert bodies[0][1] == bodies[1][1]
    assert bodies[0][0] != bodies[1][0]
    assert all(link in result["answer"] for link in links)


def test_different_products_do_not_reuse_the_answer(artifact_environment, monkeypatch):
    result, bodies, links, _ = _run_selected_exports(artifact_environment, monkeypatch,
        "生成 HTML 病害报告和 HTML 营养报告。", [("disease.html", "Only disease findings")], "营养报告尚未生成。")
    assert bodies == [("disease.html", "Only disease findings")]
    assert len(links) == 1 and "营养报告尚未生成" in result["answer"]


def test_three_audience_reports_export_one_and_report_two_missing(artifact_environment, monkeypatch):
    result, bodies, links, _ = _run_selected_exports(artifact_environment, monkeypatch,
        "三份 HTML 报告，分别面向研发、销售和客户。", [("internal.html", "Internal-only research body")],
        "销售报告和客户报告尚未生成，需单独准备脱敏正文。")
    assert len(bodies) == len(links) == 1
    assert "销售报告和客户报告尚未生成" in result["answer"]
    assert "Internal-only research body" not in result["answer"]


def test_ambiguous_quantity_keeps_the_gap_visible(artifact_environment, monkeypatch):
    result, bodies, links, _ = _run_selected_exports(artifact_environment, monkeypatch,
        "生成两份 HTML 报告。", [("report.html", "Only supplied body")], "请确认第二份报告的受众与内容。")
    assert len(bodies) == len(links) == 1
    assert "请确认第二份报告的受众与内容" in result["answer"]


_DSML_TEXT = ('<｜｜DSML｜｜calls><｜｜DSML｜｜invoke name="x">'
              '</｜｜DSML｜｜invoke></｜｜DSML｜｜calls>')


def _run_agent_with_scripted_model(environment, monkeypatch, texts):
    """Run the real host/runtime with scripted responses and temporary journals."""
    import asyncio
    from src.agent import host

    state, _ = environment
    state["query"] = "柑橘黄龙病的传播途径是什么"
    sent = install(monkeypatch, [AIMessage(content=text,
        response_metadata={"finish_reason": "stop"}) for text in texts])
    return asyncio.run(host.general_agent_node(state)), sent


def test_same_loop_recovers_from_dsml_without_losing_history(host_environment, monkeypatch):
    from src.core import task_events

    result, sent = _run_agent_with_scripted_model(
        host_environment, monkeypatch, [_DSML_TEXT] * 4 + ["恢复后的最终答案。"])
    assert result["answer"] == "恢复后的最终答案。"
    assert result["turn_outcome"]["status"] == "completed"
    assert len(sent) == 5
    for previous, following in zip(sent, sent[1:]):
        assert following[:len(previous)] == previous
    events = [row["data"] for row in task_events.replay(host_environment[1])["events"]
              if row["event"] == "agent_loop"]
    assert sum(event["kind"] == "run_started" for event in events) == 1
    assert sum(event["kind"] == "final_rejected" for event in events) == 4
    assert events[-1]["kind"] == "run_finished" and events[-1]["reason"] == "answered"


def test_same_loop_recovery_stops_at_shared_model_limit(host_environment, monkeypatch):
    from src.config import settings
    from src.core import task_events

    monkeypatch.setattr(settings, "AGENT_MAX_MODEL_CALLS", 4)
    result, sent = _run_agent_with_scripted_model(host_environment, monkeypatch, [_DSML_TEXT] * 4)
    assert len(sent) == settings.AGENT_MAX_MODEL_CALLS
    assert result["_trace"]["stop_reason"] == "model_step_limit"
    assert result["turn_outcome"]["status"] != "completed"
    assert _DSML_TEXT not in result["answer"]
    assert result["answer"], "Resource exhaustion must still produce an honest terminal notice"
    events = [row["data"] for row in task_events.replay(host_environment[1])["events"]
              if row["event"] == "agent_loop"]
    assert sum(event["kind"] == "run_started" for event in events) == 1
    assert events[-1]["kind"] == "run_finished" and events[-1]["reason"] == "model_step_limit"


def test_generic_fetch_failure_keeps_an_honest_terminal_notice():
    from src.core.turn_notices import notice
    from src.graph.agent_operations import _tool_failure_notice
    message = ToolMessage(content="[WEB_FETCH_FAILED] no usable body", name="deepseek_fetch",
                          tool_call_id="offline-failed-fetch")
    assert _tool_failure_notice([message]) == notice("web_tool_failed")


@pytest.mark.parametrize("invalid", ["missing", "changed_bytes", "other_owner"])
def test_invalid_artifact_links_return_to_same_loop(artifact_environment, monkeypatch, invalid):
    import asyncio
    import sqlite3
    from src.agent import host
    from src.research import tools
    from src.core import task_events

    state, job = artifact_environment
    if invalid == "missing":
        link = "/api/v2/session/" + state["session_id"] + "/artifacts/" + "a" * 32
    else:
        link = tools._save_artifact("verified.html", _report_html("verified content").encode("utf-8"), "text/html")
        aid = link.rsplit("/", 1)[-1]
        if invalid == "changed_bytes":
            tools.resolve_artifact(state["session_id"], aid)["path"].write_bytes(b"tampered content")
        else:
            from src.session.manager import session_manager
            with sqlite3.connect(session_manager.db_path) as conn:
                conn.execute("UPDATE sessions SET owner_user_id=? WHERE session_id=?",
                             (state["user_id"] + 1, state["session_id"]))
    sent = install(monkeypatch, [AIMessage(content=f"[report]({link})"), AIMessage(content="文件未通过校验，尚未交付。")])
    result = asyncio.run(host.general_agent_node(state))
    assert len(sent) == 2 and "未通过真实资产、账号和字节校验" in sent[1][-1].content
    assert result["answer"] == "文件未通过校验，尚未交付。" and link not in result["answer"]
    assert not task_events.projection(job)["operations"]


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
    terminal_helper = next(node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "_terminal_missing_groups")
    module = ast.Module(body=[terminal_helper, ast.AsyncFunctionDef(
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


def test_llm_timeout_never_rounds_up_past_remaining_budget():
    from src.graph import model_driver as g

    assert g._llm_timeout(0.2) == 0.2
    assert g._llm_timeout(-1) == 0
    assert g._llm_timeout(float("inf")) == 300


def test_stream_retries_and_fallback_share_absolute_deadline(monkeypatch):
    from src.graph import model_driver as g
    import asyncio
    from types import SimpleNamespace
    import pytest
    from src.core import progress_bus

    clock = [100.0]
    timeouts = []
    calls = []
    real_wait_for = asyncio.wait_for
    monkeypatch.setattr(g, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(progress_bus, "get_turn_scope", lambda: ("", ""))
    monkeypatch.setattr(g, "_TRANSIENT_STREAM_ERRORS", (ConnectionError,))

    async def wait_for(awaitable, timeout):
        timeouts.append(timeout)
        return await real_wait_for(awaitable, timeout)

    async def sleep(seconds):
        clock[0] += seconds

    async def stream(*args, **kwargs):
        calls.append("stream")
        clock[0] += 2
        raise ConnectionError("broken stream")

    class Model:
        async def ainvoke(self, messages):
            calls.append("fallback")
            clock[0] += 0.5
            raise RuntimeError("fallback unavailable")

    monkeypatch.setattr(g.asyncio, "wait_for", wait_for)
    monkeypatch.setattr(g.asyncio, "sleep", sleep)
    monkeypatch.setattr(g, "stream_llm_response", stream)
    with pytest.raises(RuntimeError, match="fallback unavailable") as error:
        asyncio.run(g._call_llm(Model(), [], timeout=300, deadline=110,
                                llm_fallback=Model()))
    # Recovery is capped at one attempt; it need not spend all remaining time.
    assert isinstance(error.value.__cause__, ConnectionError)
    assert calls == ["stream", "fallback"]
    assert timeouts == [10, 10, 8]
    assert clock[0] == 102.5


def test_expired_llm_deadline_never_starts_provider(monkeypatch):
    from src.graph import model_driver as g
    import asyncio
    from unittest.mock import AsyncMock
    import pytest
    from src.core import progress_bus

    monkeypatch.setattr(progress_bus, "get_turn_scope", lambda: ("", ""))
    stream = AsyncMock()
    monkeypatch.setattr(g, "stream_llm_response", stream)
    with pytest.raises(TimeoutError):
        asyncio.run(g._call_llm(object(), [], timeout=300, deadline=0))
    stream.assert_not_awaited()


def test_llm_wall_clock_timeout_cancels_stream_without_fallback(monkeypatch):
    from src.graph import model_driver as g
    import asyncio
    from unittest.mock import AsyncMock
    import pytest
    from src.core import progress_bus

    monkeypatch.setattr(progress_bus, "get_turn_scope", lambda: ("", ""))
    cancelled = []

    async def stream(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    model = AsyncMock()
    monkeypatch.setattr(g, "stream_llm_response", stream)
    with pytest.raises(TimeoutError):
        asyncio.run(g._call_llm(model, [], timeout=0.02, llm_fallback=model))
    assert cancelled == [True]
    model.ainvoke.assert_not_awaited()


def test_nonstream_failure_keeps_last_error_and_stream_cause(monkeypatch):
    from src.graph import model_driver as g
    import asyncio
    from unittest.mock import AsyncMock
    import pytest
    from src.core import progress_bus

    monkeypatch.setattr(progress_bus, "get_turn_scope", lambda: ("", ""))
    monkeypatch.setattr(g, "_LLM_STREAM_ATTEMPTS", 1)
    monkeypatch.setattr(g, "_TRANSIENT_STREAM_ERRORS", (ConnectionError,))
    monkeypatch.setattr(g, "stream_llm_response", AsyncMock(side_effect=ConnectionError("stream")))
    model = AsyncMock()
    model.ainvoke.side_effect = ValueError("fallback rejected")
    with pytest.raises(ValueError, match="fallback rejected") as error:
        asyncio.run(g._call_llm(model, [], timeout=1))
    assert isinstance(error.value.__cause__, ConnectionError)


def test_same_loop_recovery_obeys_original_request_deadline(host_environment, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from src.agent import host, runtime
    from src.core import task_events

    state, job = host_environment
    clock = [100.0]
    remaining = []
    state["request_deadline"] = 110.0
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def rejected(messages):
        remaining.append(state["request_deadline"] - clock[0])
        clock[0] += 5
        return AIMessage(content=_DSML_TEXT)

    sent = install(monkeypatch, [rejected, rejected])
    result = asyncio.run(host.general_agent_node(state))
    assert remaining == [10, 5] and len(sent) == 2
    assert result["_trace"]["stop_reason"] == "deadline"
    assert result["turn_outcome"]["status"] != "completed"
    assert result["answer"] and _DSML_TEXT not in result["answer"]
    events = [row["data"] for row in task_events.replay(job)["events"] if row["event"] == "agent_loop"]
    assert sum(event["kind"] == "run_started" for event in events) == 1
    assert events[-1]["kind"] == "run_finished" and events[-1]["reason"] == "deadline"


def test_final_validation_uses_only_remaining_runtime_budget():
    import asyncio
    import time
    from langchain_core.messages import HumanMessage
    from src.agent.identity import ExecutionIdentity
    from src.agent.runtime import AgentRuntime, RunLimits

    calls, cancelled = [], []
    async def model(messages, identity):
        calls.append(messages)
        return AIMessage(content="Provisional answer")
    async def execute(*args):
        pytest.fail("Validation must not dispatch a tool")
    async def check(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    async def scenario():
        identity = ExecutionIdentity(17, "validation-session", "validation-turn", "validation-run")
        runtime = AgentRuntime(model, execute, lambda *args: None, check_final=check,
                               limits=RunLimits(deadline=time.monotonic() + 0.02))
        result = await runtime.run(identity, [HumanMessage(content="Check the facts")])
        assert result.reason == "deadline" and result.status == "incomplete"
        assert result.answer is None and cancelled == [True] and len(calls) == 1
        result = await runtime.run(identity, [HumanMessage(content="Already expired")])
        assert result.reason == "deadline" and result.model_steps == 0
        assert len(calls) == 1
    asyncio.run(scenario())

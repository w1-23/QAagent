"""Synthetic engineering regressions for repeated-input cost and file delivery."""
import asyncio
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.core.context_manager import assemble_supervisor_messages
from src.core.model_context import historical_send_view, statistical_send_view


def test_old_tool_bodies_shrink_without_losing_user_requirements_or_protocol():
    request = HumanMessage(content="Use all observations, no imputation, unit mg/L. " * 80)
    calls = AIMessage(content="", tool_calls=[{"id": "old", "name": "deepseek_fetch_paper", "args": {"doi": "10.1234/fixture"}}])
    result = ToolMessage(content="Historical engineering fixture " * 4000 + "\ndoi: 10.1234/fixture",
                         name="deepseek_fetch_paper", tool_call_id="old")
    original = [request, calls, result, AIMessage(content="Prior answer")]
    human = HumanMessage(content="Plot this file; preserve original values")
    view, start = assemble_supervisor_messages("rules", {"messages": original}, human)
    assert start == len(view) - 1 and view[-1] is human
    assert view[1] is request and view[2] is calls
    assert view[3].tool_call_id == "old" and "10.1234/fixture" in view[3].content
    assert len(view[3].content) < 2400
    assert len(result.content) > 100000  # Original transcript was not modified.


def test_historical_generated_catalogs_are_not_resent_with_each_question():
    text = "<account_file_library>" + "stale catalog " * 2000 + "</account_file_library>\n"
    text += "<user_query>Use every row; no significance claims.</user_query>\n<answer_length_and_scope>scope</answer_length_and_scope>"
    source = HumanMessage(content=text, additional_kwargs={"context_origin": "generated_user",
        "context_trusted": True, "user_text": "Use every row; no significance claims."})
    result = historical_send_view([source])[0]
    assert result.content == "Use every row; no significance claims."
    raw = HumanMessage(content="Explain <user_query>tag examples</user_query> please.")
    assert historical_send_view([raw])[0] is raw


def test_statistics_view_preserves_numbers_and_full_result_references():
    data = {"analysis_result_ref": {"id": "synthetic", "version": 1},
            "test": {"p_value": 0.123456789, "included_row_ids": list(range(10000))},
            "coverage": {"complete": True, "original_row_ids": list(range(10000))},
            "exclusions": [{"rows": [{"row_id": n, "reason": "synthetic"} for n in range(100)]}],
            "warning": "engineering fixture"}
    message = ToolMessage(name="analyze_research_data", tool_call_id="stat", content=json.dumps(data))
    result = json.loads(statistical_send_view(message).content)
    assert result["test"]["p_value"] == data["test"]["p_value"]
    assert result["analysis_result_ref"] == data["analysis_result_ref"]
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["original_row_ids_count"] == 10000
    assert result["exclusions"][0]["rows_count"] == 100
    assert result["test"]["included_row_ids_count"] == 10000
    assert len(statistical_send_view(message).content) < 3500
    assert len(json.loads(message.content)["test"]["included_row_ids"]) == 10000
    del data["analysis_result_ref"]
    without = message.model_copy(update={"content": json.dumps(data)})
    assert statistical_send_view(without) is without


def test_nature_skill_is_in_the_production_prompt_and_verified():
    from src.prompts.loader import assemble_system_prompt, verify_figure_prompt_loading
    from src.prompts.nature_figure import GUIDANCE_FILES, verified_sources
    status = verify_figure_prompt_loading()
    assert status["verified"] and status["prompt_loaded"] and status["reference_files"] == 11
    prompt = assemble_system_prompt(mode="expert", query="", available_tools={"run_research_figure_script"})
    for name in GUIDANCE_FILES:
        assert verified_sources()[name] in prompt
    assert "隔离 worker" in prompt and "无需用户指定写脚本" in prompt
    assert "<nature_figure_guidance>" not in assemble_system_prompt(mode="expert", available_tools=set())


def test_missing_or_tampered_nature_source_fails_closed(tmp_path, monkeypatch):
    from src.prompts import nature_figure as skill
    skill.verified_sources.cache_clear()
    monkeypatch.setattr(skill, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="校验失败"):
        skill.guidance()
    skill.verified_sources.cache_clear()


def test_verified_files_have_receipt_even_if_model_text_fails():
    from src.graph.general_agent_graph import _artifact_failure_receipt, _append_verified_artifact_links
    records = [{"name": "run_research_figure_script", "artifact_validations": [
        {"valid": True, "link": "/api/artifacts/synthetic.png"},
        {"valid": False, "link": "/api/artifacts/invalid.png"}]}]
    answer = _artifact_failure_receipt("本轮未完成：模型请求超时。", records)
    answer = _append_verified_artifact_links(answer, records)
    assert "已生成并校验 1 个文件" in answer and "文字整理未完成" in answer
    assert "模型请求超时" in answer and "synthetic.png" in answer and "invalid.png" not in answer
    assert _artifact_failure_receipt("failure", []) == "failure"


def test_transport_recovery_is_bounded_to_two_physical_attempts(monkeypatch):
    from src.graph import general_agent_graph as graph
    from src.core import task_events
    monkeypatch.setattr(task_events, "run_model_attempt", lambda model, messages, invoke, **kw: invoke())
    monkeypatch.setattr(graph, "_TRANSIENT_STREAM_ERRORS", (ConnectionError,))
    calls = []
    async def stream(*args, **kwargs):
        calls.append("stream")
        raise ConnectionError("synthetic failure")
    class Model:
        max_tokens = 50
        async def ainvoke(self, messages):
            calls.append("nonstream")
            raise ConnectionError("synthetic failure")
    monkeypatch.setattr(graph, "stream_llm_response", stream)
    with pytest.raises(ConnectionError):
        asyncio.run(graph._call_llm(Model(), [], timeout=2))
    assert calls == ["stream", "nonstream"]


def test_profile_bounds_cell_preview_without_altering_frame():
    import pandas as pd
    from src.research.tools import _profile
    frame = pd.DataFrame({"description": ["x" * 100000], "value": [3.5]})
    profile = _profile(frame)
    assert len(profile["sample_rows"][0]["description"]) == 121
    assert len(frame.loc[0, "description"]) == 100000
    assert profile["fields"][1]["min"] == 3.5

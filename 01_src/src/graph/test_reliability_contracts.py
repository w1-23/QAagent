"""冻结版本的可靠性契约测试（对应研究方案 §4 的 P0 修复项）。

这些断言故意“看源码/看行为”，用于防止已修复的故障在后续改动中回退：
- 输出长度截断的安全恢复；
- 工具协议文字不得被当成答案；
- 用户明确要文件时必须真的产出文件；
- 同名不同产物的自动区分；
- 文本附件可被读取。
"""

from __future__ import annotations

import pathlib

from src.graph.general_agent_graph import _deliverable_export_requested

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_deliverable_export_requested_detects_file_requests():
    assert _deliverable_export_requested("把这三张表的异常整理成 HTML 报告")
    assert _deliverable_export_requested("导出核验表给我")
    assert _deliverable_export_requested("生成一份 XLSX 标注文件")
    assert not _deliverable_export_requested("解释一下柑橘黄龙病的传播途径")


def test_tool_protocol_text_never_becomes_the_answer():
    src = _read("src/graph/general_agent_graph.py")
    assert "本轮模型返回了未执行的工具调用格式" not in src, "协议文本不得直接当答案返回"
    assert "正在安全恢复回答" in src, "协议恢复重试必须存在"


def test_truncated_json_and_unique_naming_helpers_are_present():
    src = _read("src/research/tools.py")
    assert "def _repair_truncated_json(" in src
    assert "def _unique_name_in_session(" in src
    assert "ToolArgumentError" in src


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

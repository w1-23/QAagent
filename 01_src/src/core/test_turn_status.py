"""回归：本轮状态必须由工具真实终态推出，不靠答案文本或「有输出即成功」。

背景：修复前 `api/main.py` 用 `ans.startswith("本轮未完成")` 判定，导致
「工具失败但模型仍写出答案」的一轮被判为 completed（用户看到的正是这种情况）。
"""

from __future__ import annotations

import asyncio

from src.core import progress_bus
from src.core.turn_status import derive_turn_status


def test_all_complete_is_completed():
    assert derive_turn_status("答案", [{"name": "a", "status": "complete"}]) == "completed"


def test_failed_tool_is_failed():
    assert derive_turn_status("答案", [{"name": "a", "status": "failed"}]) == "failed"


def test_failed_plus_complete_is_partial():
    statuses = [{"name": "a", "status": "failed"}, {"name": "b", "status": "complete"}]
    assert derive_turn_status("答案", statuses) == "partial"


def test_skipped_counts_as_not_executed():
    statuses = [{"name": "a", "status": "skipped"}, {"name": "b", "status": "complete"}]
    assert derive_turn_status("答案", statuses) == "partial"


def test_not_run_is_not_success():
    assert derive_turn_status("答案", [{"name": "a", "status": "not_run"}]) == "failed"


def test_no_tool_but_unfinished_answer_is_partial():
    assert derive_turn_status("本轮未完成：缺少资料", []) == "partial"


def test_required_tool_unattempted_is_partial():
    assert derive_turn_status("答案", [], [["local_hybrid_search"]]) == "partial"


def test_journal_records_terminal_outcomes():
    """progress_bus 的请求级流水必须记录真实终态（API 层据此判状态）。"""
    journal: list[dict] = []
    progress_bus.set_request_journal(journal)
    try:
        progress_bus.emit_tool_result(
            "local_hybrid_search", "q", "call-1",
            outcome={"status": "failed", "reason_code": "tool_unavailable"})
        progress_bus.emit_tool_result(
            "deepseek_search", "q", "call-2", summary="获得 8 条网页资料")
        assert journal[0]["status"] == "failed"
        assert journal[0]["reason"] == "tool_unavailable"
        assert journal[1]["status"] == "complete"
        assert derive_turn_status("答案", journal) == "partial"
    finally:
        progress_bus.clear_request_journal()


def test_journal_without_binding_is_noop():
    """没有绑定流水（旧调用路径）时不得抛错。"""
    progress_bus.clear_request_journal()
    asyncio.run(asyncio.sleep(0))
    progress_bus.emit_tool_result("x", "q", "call-9", outcome={"status": "complete"})

def test_deadline_answer_without_tools_is_failed():
    # 真实故障：生成超出请求时限，回答以「本轮检索或生成超出请求时限」开头，
    # 修复前被判 completed。
    answer = "本轮检索或生成超出请求时限，未检测到额度不足错误。请稍后重试。"
    assert derive_turn_status(answer, []) == "failed"


def test_deadline_answer_with_successful_step_is_partial():
    answer = "本轮检索或生成超出请求时限，未检测到额度不足错误。请稍后重试。"
    assert derive_turn_status(answer, [{"name": "a", "status": "complete"}]) == "partial"


def test_partial_tool_receipts_never_make_a_completed_turn():
    for records in (
        [{"name": "audit", "status": "partial"}],
        [{"name": "audit", "status": "partial"}, {"name": "read", "status": "complete"}],
    ):
        assert derive_turn_status("已有部分结果", records) == "partial"
    assert derive_turn_status("已完成声明范围", [{"name": "audit", "status": "complete",
                                                "reason_code": "scope_limited"}]) == "completed"


def test_empty_answer_is_a_failed_turn_not_a_completed_one():
    """审核包 240 条路由里 15 条 answer="" 且 turn_status=""。"""
    from src.core.turn_status import derive_turn_status

    assert derive_turn_status("", []) == "failed"
    assert derive_turn_status("   \n", []) == "failed"
    # 有成功步骤也不能把"没有回答"说成完成。
    assert derive_turn_status("", [{"name": "x", "status": "complete"}]) == "failed"
    # 用户停止仍然是停止，不是失败。
    assert derive_turn_status("", [{"name": "x", "status": "cancelled"}]) == "cancelled"
    assert derive_turn_status("有回答", [{"name": "x", "status": "complete"}]) == "completed"

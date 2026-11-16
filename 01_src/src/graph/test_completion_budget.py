"""交付物类任务的输出预算必须够长，否则工具参数会在中途被截断。"""

from __future__ import annotations

from src.graph.general_agent_graph import _answer_profile


def test_deliverable_task_gets_large_budget():
    budget, guidance = _answer_profile("帮我把这 56 条参考文献做成核验表并导出 xlsx")
    assert budget >= 16384
    assert "分批" in guidance or "20 条" in guidance


def test_plain_short_question_is_not_over_budgeted():
    budget, _ = _answer_profile("金块柑橘的亲本是哪两个品种？")
    assert 1024 <= budget < 16384

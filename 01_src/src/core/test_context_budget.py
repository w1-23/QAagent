"""History budget uses an independent economic input allowance and safe targets."""
from __future__ import annotations

import asyncio
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.config import settings
from src.core.context_budget import ContextBudget, ContextBudgetConfig, ContextBudgetLevel
from src.core.context_manager import build_context_budget, budget_usage_ratio
from src.core.request_context import estimate_request


def test_build_context_budget_reads_economic_settings(monkeypatch):
    monkeypatch.setattr(settings, "_runtime_model_cfg", {"context_window": 1000000})
    monkeypatch.setattr(settings, "CONTEXT_WORKING_MAX_INPUT_TOKENS", 64000)
    monkeypatch.setattr(settings, "CONTEXT_WORKING_SOFT_TOKENS", 48000)
    monkeypatch.setattr(settings, "CONTEXT_WORKING_TARGET_TOKENS", 32000)
    monkeypatch.setattr(settings, "CONTEXT_BUDGET_PROTECT_RECENT_TURNS", 2)
    monkeypatch.setattr(settings, "CONTEXT_BUDGET_ENABLED", False)
    budget = build_context_budget()
    assert budget is not None
    assert budget.config == ContextBudgetConfig(
        max_tokens=64000, soft_threshold=.75, hard_threshold=1.0,
        target_ratio=.5, protect_recent_turns=2, enabled=False)


def test_budget_usage_ratio_uses_the_request_estimator():
    budget = ContextBudget(ContextBudgetConfig(max_tokens=1000))
    messages = [HumanMessage(content="x" * 4000)]
    assert budget_usage_ratio(budget, messages) == estimate_request(messages)["estimated_input_tokens"] / 1000
    assert budget_usage_ratio(None, messages) is None


def test_disabled_budget_never_rewrites_the_send_view():
    budget = ContextBudget(ContextBudgetConfig(enabled=False))
    messages = [HumanMessage(content="x" * 10_000)]
    result = asyncio.run(budget.check(messages))
    assert result.level == ContextBudgetLevel.NORMAL and result.messages == messages


def _compression_fixture():
    return [HumanMessage(content="anchor"), HumanMessage(content="q1"),
            AIMessage(content="", tool_calls=[{"id": "t1", "name": "tool", "args": {}}]),
            ToolMessage(content="a" * 3000, tool_call_id="t1", name="tool"),
            HumanMessage(content="q2"),
            AIMessage(content="", tool_calls=[{"id": "t2", "name": "tool", "args": {}}]),
            ToolMessage(content="b" * 3000, tool_call_id="t2", name="tool"),
            HumanMessage(content="q3"), AIMessage(content="a3")]


def test_target_never_authorizes_hard_deletion(monkeypatch):
    budget = ContextBudget(ContextBudgetConfig(max_tokens=1000, soft_threshold=.75,
        target_ratio=.1, protect_recent_turns=1))
    async def compact(_messages, **_kwargs):
        return "S" * 2400
    budget.set_compact_fn(compact)
    def forbidden(*_args):
        raise AssertionError("budget target cannot authorize dropping user turns")
    monkeypatch.setattr(budget, "_hard_trim", forbidden)
    result = asyncio.run(budget.check(_compression_fixture()))
    assert result.status == "success"
    assert budget.estimate_tokens(result.messages) > budget.config.max_tokens * budget.config.target_ratio
    assert "q1" in result.summary and "q2" in result.summary


def test_protect_recent_turns_is_honoured():
    budget = ContextBudget(ContextBudgetConfig(max_tokens=1000, soft_threshold=.75,
        protect_recent_turns=2))
    async def compact(_messages, **_kwargs):
        return "summary"
    budget.set_compact_fn(compact)
    messages = _compression_fixture()
    result = asyncio.run(budget.check(messages))
    assert result.level == ContextBudgetLevel.SUMMARIZE
    assert result.messages[-5:] == messages[-5:]
    assert "anchor" in result.summary and "q1" in result.summary
    assert result.messages[0].type == "system"

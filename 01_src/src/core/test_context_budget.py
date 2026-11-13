"""Context budget is one configured object, and its target ratio is real."""
from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.config import settings
from src.core.context_budget import (
    ContextBudget, ContextBudgetConfig, ContextBudgetLevel,
)
from src.core.context_manager import build_context_budget, budget_usage_ratio


def test_build_context_budget_reads_every_setting(monkeypatch):
    # No runtime model window override: the configured budget is the source.
    monkeypatch.setattr(settings, "_runtime_model_cfg", {})
    monkeypatch.setattr(settings, "CONTEXT_BUDGET_MAX_TOKENS", 8192)
    monkeypatch.setattr(settings, "CONTEXT_BUDGET_SOFT_THRESHOLD", 0.6)
    monkeypatch.setattr(settings, "CONTEXT_BUDGET_HARD_THRESHOLD", 0.9)
    monkeypatch.setattr(settings, "CONTEXT_BUDGET_TARGET_RATIO", 0.4)
    monkeypatch.setattr(settings, "CONTEXT_BUDGET_PROTECT_RECENT_TURNS", 2)
    monkeypatch.setattr(settings, "CONTEXT_BUDGET_ENABLED", False)

    budget = build_context_budget()

    assert budget is not None
    assert budget.config == ContextBudgetConfig(
        max_tokens=8192, soft_threshold=0.6, hard_threshold=0.9,
        target_ratio=0.4, protect_recent_turns=2, enabled=False)


def test_budget_usage_ratio_uses_the_same_estimator():
    budget = ContextBudget(ContextBudgetConfig(max_tokens=1000))
    assert budget_usage_ratio(budget, [HumanMessage(content="x" * 4000)]) == 1.0
    assert budget_usage_ratio(None, [HumanMessage(content="x")]) is None


def test_disabled_budget_never_rewrites_the_send_view():
    budget = ContextBudget(ContextBudgetConfig(enabled=False))
    messages = [HumanMessage(content="x" * 10_000)]
    result = asyncio.run(budget.check(messages))
    assert result.level == ContextBudgetLevel.NORMAL
    assert result.messages == messages


def _compression_fixture():
    return [
        HumanMessage(content="anchor"),
        HumanMessage(content="q1"),
        ToolMessage(content="a" * 3000, tool_call_id="t1", name="tool"),
        HumanMessage(content="q2"),
        ToolMessage(content="b" * 3000, tool_call_id="t2", name="tool"),
        HumanMessage(content="q3"),
        AIMessage(content="a3"),
    ]


def _run_compress(target_ratio, monkeypatch):
    budget = ContextBudget(ContextBudgetConfig(
        max_tokens=1000, soft_threshold=0.75, hard_threshold=0.93,
        target_ratio=target_ratio, protect_recent_turns=1,
        keep_recent_turns=1))

    async def compact(_messages, **_kwargs):
        # 2400 ASCII chars ≈ 600 tokens → 0.6 of the 1000-token budget,
        # between the two target ratios used below.
        return "S" * 2400

    budget.set_compact_fn(compact)
    calls: list[list] = []
    original = budget._hard_trim

    def observe(messages, cfg):
        calls.append(list(messages))
        return original(messages, cfg)

    monkeypatch.setattr(budget, "_hard_trim", observe)
    result = asyncio.run(budget.check(_compression_fixture()))
    return budget, result, calls


def test_target_ratio_drives_the_post_summary_trim(monkeypatch):
    _budget, _result, calls = _run_compress(0.3, monkeypatch)
    assert calls, "a summary above target_ratio must trigger the bounded trim"

    _budget, _result, calls = _run_compress(0.7, monkeypatch)
    assert calls == [], "a summary below target_ratio must not be trimmed further"


def test_protect_recent_turns_is_honoured(monkeypatch):
    budget = ContextBudget(ContextBudgetConfig(
        max_tokens=1000, soft_threshold=0.75, hard_threshold=0.93,
        target_ratio=0.7, protect_recent_turns=2, keep_recent_turns=1))

    async def compact(_messages, **_kwargs):
        return "summary"

    budget.set_compact_fn(compact)
    result = asyncio.run(budget.check(_compression_fixture()))
    contents = [str(message.content) for message in result.messages]

    assert result.level == ContextBudgetLevel.SUMMARIZE
    assert "anchor" in contents
    assert "q2" in contents and "q3" in contents
    assert "q1" not in contents, "old turns belong in the summary, not the view"

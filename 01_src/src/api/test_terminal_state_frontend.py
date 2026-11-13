# -*- coding: utf-8 -*-
"""前端终态、SSE 去重、上下文面板与历史轮次契约（静态断言，不跑浏览器）。

服务端已经有 TurnEnvelope / seq / /messages.turns；这些测试钉住浏览器端
确实消费了它们，而不是又从本地工具行或"最新一条消息"猜终态。
"""
from __future__ import annotations

import pathlib

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"


def _read(name: str) -> str:
    return (WEB / "assets" / name).read_text(encoding="utf-8")


def test_done_handler_uses_one_authoritative_reducer():
    src = _read("app.js")
    assert "var _terminal = applyTurnEnvelope(data);" in src
    # 四态终态来自服务端；未知状态一律按失败，不按"看起来完成了"处理
    assert "env && env.display_status" in src
    assert "data && data.turn_status" in src
    assert "['completed', 'partial', 'failed', 'cancelled'].indexOf(status) < 0" in src
    assert "status = 'failed';" in src


def test_finish_status_prefers_server_status_over_local_rows():
    src = _read("app.js")
    start = src.index("function finishStatus() {")
    end = src.index("\n  }\n", start)
    body = src[start:end]
    # 服务端状态存在时，标签只读它；本地 _toolStates 只提供计数。
    assert "if (_turnDisplayStatus) {" in body
    assert "_turnDisplayStatus === 'partial' ? '部分完成'" in body
    assert "backends without an envelope" in body
    # 断线不是业务终态
    assert "状态未确认" in body
    assert "后台可能仍在运行" in body


def test_done_shows_warning_history_and_stop_notice():
    src = _read("app.js")
    assert "if (data.warning) showBanner(String(data.warning));" in src
    assert "_turnEnvelope.history_saved === false" in src
    assert "_turnEnvelope.notices.join('；')" in src
    assert "if (!fullText && data.stop_notice)" in src


def test_artifact_attribution_uses_server_ids_not_baseline_guess():
    src = _read("app.js")
    assert "Array.isArray(data.artifact_ids)" in src
    assert "f.artifact_id || f.file_id" in src
    # 旧服务端没有 artifact_ids 时才退回"与基线做差"
    assert "Legacy server without artifact attribution" in src


def test_sse_parser_dedupes_by_sequence_and_reports_errors():
    src = _read("app.js")
    assert "lastSeq" in src and "lastEventId" in src
    assert "d.seq<=lastSeq" in src
    assert "console.error" in src
    # 解析失败或 handler 抛错必须可诊断，不再静默 catch(_){}
    assert "SSE" in src


def test_context_usage_is_additive_and_keeps_server_estimate():
    src = _read("app.js")
    assert "provider_usage" in src
    assert "本次模型输入" in src
    assert "服务商统计的本次模型输入" in src
    # context_usage 事件只写 provider_usage，不覆盖 estimated_tokens
    start = src.index("case 'context_usage':")
    end = src.index("case ", start + 10)
    body = src[start:end]
    assert "provider_usage" in body
    assert "estimated_tokens" not in body


def test_history_prefers_canonical_turns_with_legacy_adapter():
    src = _read("app.js")
    assert "function normalizeHistoryTurn(raw)" in src
    assert "function normalizeHistoryTurns(list)" in src
    assert "function mergeHistoryTurns(earlierTurns, laterTurns)" in src
    assert "function renderHistoryMessages(msgs, serverTurns)" in src
    # 有服务端 turns 就用；没有才走旧分组
    assert "(serverTurns && serverTurns.length) ? serverTurns : groupHistoryTurns(msgs)" in src
    assert "_historyTurns = normalizeHistoryTurns(data.turns);" in src
    assert "_historyTurns = mergeHistoryTurns(earlierTurns, _historyTurns);" in src


def test_history_paging_never_matches_by_text_only():
    src = _read("app.js")
    # 分页合并仍按 message_id 幂等；轮次合并只按 turn key / tool_call_id
    assert "seenIds[emid]" in src
    assert "mergeHistoryTurns" in src
    assert "byKey[key]" in src


def test_script_version_bumped_for_new_bundle_contracts():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "app.js?v=36" in html

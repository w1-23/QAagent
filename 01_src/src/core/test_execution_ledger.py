"""One execution identity, one terminal state, one persisted message pair."""
from __future__ import annotations

from src.core.execution_ledger import (
    ExecutionLedger, bind_execution_ledger, current_execution_ledger,
    reset_execution_ledger,
)


def test_one_terminal_state_per_call_id():
    ledger = ExecutionLedger(session_id="s", request_id="r")
    ledger.start("call-1", "deepseek_search", {"query": "x"})
    first = ledger.terminal("call-1", "complete", result_chars=12)
    late = ledger.terminal("call-1", "failed", reason_code="late-duplicate")

    assert first is late
    assert late.status == "complete"
    assert late.reason_code == ""
    assert late.result_chars == 12
    assert late.duplicate_terminals == 1
    assert ledger.summary()["counts"] == {"complete": 1}


def test_terminal_without_start_is_a_real_skipped_step():
    ledger = ExecutionLedger()
    record = ledger.terminal(
        "call-skip", "skipped", tool_name="export_report",
        reason_code="duplicate_request")
    assert record is not None
    assert record.status == "skipped"
    assert record.tool_name == "export_report"
    assert record.reason_code == "duplicate_request"
    assert ledger.pending_ids() == ()


def test_close_gives_every_running_call_an_honest_terminal():
    ledger = ExecutionLedger()
    ledger.start("done", "tool")
    ledger.start("stopped", "tool")
    ledger.terminal("done", "complete")

    closed = ledger.close(status="cancelled", reason_code="user_stop")

    assert closed == ("stopped",)
    assert ledger.record("stopped").status == "cancelled"
    assert ledger.record("stopped").reason_code == "user_stop"
    assert ledger.summary()["pending"] == []


def test_persist_pair_is_idempotent_and_protocol_valid():
    from src.session.manager import _validate_trace

    ledger = ExecutionLedger()
    ledger.start("forced-export-1", "export_paper_analysis",
                 {"title": "report"}, source="forced")
    ledger.terminal("forced-export-1", "complete")
    record = ledger.record("forced-export-1")

    pair = ledger.persist_pair(record, content="[report](link)", artifact={})
    assert len(pair) == 2
    assert pair[0].tool_calls[0]["id"] == "forced-export-1"
    assert pair[0].tool_calls[0]["name"] == "export_paper_analysis"
    assert pair[1].tool_call_id == "forced-export-1"
    assert _validate_trace(pair) == pair

    # A second persist must not duplicate the call/result rows.
    assert ledger.persist_pair(record, content="again") == []


def test_ledger_is_request_scoped():
    outer = ExecutionLedger(request_id="outer")
    token = bind_execution_ledger(outer)
    try:
        assert current_execution_ledger() is outer
        inner = ExecutionLedger(request_id="inner")
        inner_token = bind_execution_ledger(inner)
        assert current_execution_ledger() is inner
        reset_execution_ledger(inner_token)
        assert current_execution_ledger() is outer
    finally:
        reset_execution_ledger(token)
    assert current_execution_ledger() is None

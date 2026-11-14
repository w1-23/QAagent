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


def _attempt(status, attempt=1, key="search:input-A", **extra):
    return {"name": "search", "status": status, "logical_key": key,
            "attempt": attempt, "tool_call_id": f"call-{attempt}", **extra}


def test_successful_retry_repairs_acceptance_without_erasing_audit():
    from copy import deepcopy
    from src.core.task_result import build_task_result, effective_tool_records
    from src.core.turn_envelope import build_turn_envelope
    from src.core.turn_status import derive_turn_status

    rows = [_attempt("failed", reason="timeout"), _attempt("complete", 2)]
    original = deepcopy(rows)
    task = build_task_result(rows)
    envelope = build_turn_envelope(answer="已完成", tool_status=rows)
    assert task.execution_status == envelope.execution_status == "completed"
    assert task.acceptance_status == envelope.acceptance_status == "accepted"
    assert envelope.display_status == derive_turn_status("已完成", rows) == "completed"
    assert task.failures == () and task.executed_steps == 2
    assert effective_tool_records(rows) == [rows[1]]
    assert rows == original


def test_latest_failed_attempt_is_not_hidden_by_earlier_success():
    from src.core.task_result import build_task_result
    from src.core.turn_envelope import build_turn_envelope

    rows = [_attempt("complete"), _attempt("failed", 2, reason="final_failure")]
    for journal in (rows, list(reversed(rows))):
        task = build_task_result(journal)
        envelope = build_turn_envelope(answer="已有部分资料", tool_status=journal)
        assert task.execution_status == envelope.execution_status == "failed"
        assert envelope.display_status == "failed"
        assert task.acceptance_status == "rejected"
        assert task.failures == ("search:final_failure",)


def test_distinct_or_unidentified_inputs_keep_independent_failure():
    from src.core.task_result import build_task_result
    from src.core.turn_envelope import build_turn_envelope

    for first_key, second_key in (("search:A", "search:B"), ("", ""), ("search", "search")):
        rows = [_attempt("failed", key=first_key, reason="independent_failure"),
                _attempt("complete", 2, key=second_key)]
        task = build_task_result(rows)
        envelope = build_turn_envelope(answer="部分结果", tool_status=rows)
        assert task.execution_status == envelope.execution_status == "partial"
        assert envelope.display_status == "partial"
        assert task.failures == ("search:independent_failure",)


def test_duplicate_skip_reuses_success_and_preserves_failed_source():
    from src.core.task_result import build_task_result
    from src.core.turn_envelope import build_turn_envelope

    for source_status, expected in (("complete", "completed"), ("failed", "failed")):
        rows = [_attempt(source_status, reason="source_failure" if source_status == "failed" else ""),
                _attempt("skipped", reason_code="duplicate_request", tool_call_id="duplicate",
                         reused_from_tool_call_id="call-1")]
        task = build_task_result(rows)
        envelope = build_turn_envelope(answer="结果", tool_status=rows)
        assert task.execution_status == envelope.execution_status == expected
        assert envelope.display_status == expected
        assert task.executed_steps == 1
        assert len(rows) == 2 and rows[1]["status"] == "skipped"
        if source_status == "failed":
            assert task.failures == ("search:source_failure",)


def test_unproven_skips_and_unrelated_unkeyed_failures_are_not_erased():
    from src.core.task_result import build_task_result

    standalone = [_attempt("skipped", reason_code="duplicate_request")]
    assert build_task_result(standalone).execution_status == "failed"
    rows = [_attempt("complete", key="", tool_call_id="success"),
            _attempt("skipped", key="", reason_code="duplicate_request", tool_call_id="reuse",
                     reused_from_tool_call_id="success"),
            _attempt("failed", key="", reason="other_input", tool_call_id="independent")]
    task = build_task_result(rows)
    assert task.execution_status == "partial"
    assert task.failures == ("search:other_input",)
    rows[1]["reused_from_tool_call_id"] = "unknown"
    assert "search:duplicate_request" in build_task_result(rows).failures


def test_generator_attempts_reach_artifact_validation_and_both_reducers():
    from src.core.task_result import build_task_result
    from src.core.turn_envelope import build_turn_envelope

    rows = [_attempt("failed"), _attempt("complete", 2, artifact_count=1,
            artifact_validations=[{"artifact_id": "verified", "valid": True,
                                   "exists": True, "owner_matches": True,
                                   "hash_matches": True, "format_matches": True}])]
    task = build_task_result(row for row in rows)
    assert task.execution_status == "completed" and task.verified_artifacts == 1
    assert len(task.artifact_validations) == 1
    assert task.artifact_validations[0].artifact_id == "verified"
    envelope = build_turn_envelope(answer="完成", tool_status=(row for row in rows))
    assert envelope.display_status == envelope.execution_status == "completed"
    assert envelope.verified_artifacts == 1 and envelope.artifact_ids == ("verified",)


def _offline_tool_execution(monkeypatch, status_for):
    from langchain_core.messages import ToolMessage
    from src.graph import general_agent_graph as graph
    from src.tools import outcome
    from src.core import progress_bus, tool_results, turn_source_ledger

    def step(name, content, artifact=None):
        status = "skipped" if content.startswith("[TOOL_DUPLICATE_SKIPPED]") else content
        return {"status": status, "reason_code": "duplicate_request" if status == "skipped" else "",
                "retryable": status == "failed", "summary": status, "artifacts": []}

    async def run(calls, tools, *, completion_callback, timeout):
        results = []
        for call in calls:
            message = ToolMessage(content=status_for(call), tool_call_id=call["id"],
                                  name=call["name"], artifact={})
            completion_callback(call, message)
            results.append(message)
        return results

    monkeypatch.setattr(outcome, "safe_tool_step", step)
    monkeypatch.setattr(graph, "_run_regular_calls", run)
    monkeypatch.setattr(tool_results, "record", lambda **kwargs: None)
    monkeypatch.setattr(turn_source_ledger, "record_tool_sources", lambda *a, **k: None)
    monkeypatch.setattr(progress_bus, "emit_tool_result", lambda *a, **k: {
        **k["outcome"], "summary": k["summary"], "elapsed_ms": 0,
        "completed_at_ms": 0, "action_hint": k["action_hint"]})
    return graph


def test_graph_passes_retry_and_reuse_identity_to_acceptance_and_history(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from src.core.task_result import build_task_result
    from src.core.turn_envelope import build_turn_envelope

    graph = _offline_tool_execution(monkeypatch, lambda call: "failed" if call["id"] == "first" else "complete")
    ledger = ExecutionLedger()
    token = bind_execution_ledger(ledger)
    journal, seen, messages = [], {}, []

    async def scenario():
        for call_id in ("first", "retry", "reuse"):
            response = SimpleNamespace(tool_calls=[{"id": call_id, "name": "search", "args": {"payload": "A"}}])
            messages.extend(await graph._execute_calls(
                response, tools_by_name={}, deadline=float("inf"),
                seen_call_signatures=seen, tool_status_sink=journal))

    try:
        asyncio.run(scenario())
    finally:
        reset_execution_ledger(token)
    assert [row["status"] for row in journal] == ["failed", "complete", "skipped"]
    assert [row["attempt"] for row in journal] == [1, 2, 2]
    assert len({row["logical_key"] for row in journal}) == 1
    assert journal[2]["reused_from_tool_call_id"] == "retry"
    assert len(ledger.records()) == 3
    assert [r.attempt for r in ledger.records()] == [1, 2, 2]
    import json
    from src.session.manager import session_manager
    restored = [{"name": message.name,
                 **json.loads(session_manager._serialize_message(message)[-1])}
                for message in messages]
    assert [row["status"] for row in restored] == ["failed", "complete", "skipped"]
    assert build_task_result(journal).execution_status == "completed"
    assert build_turn_envelope(answer="完成", tool_status=restored).display_status == "completed"


def test_same_batch_reuse_does_not_merge_other_inputs(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from src.core.task_result import build_task_result

    graph = _offline_tool_execution(monkeypatch, lambda call: "failed" if call["id"] == "other" else "complete")
    journal = []
    calls = [{"id": call_id, "name": "search", "args": {"payload": payload}}
             for call_id, payload in (("first", "A"), ("reuse", "A"), ("other", "B"))]
    asyncio.run(graph._execute_calls(SimpleNamespace(tool_calls=calls), tools_by_name={},
        deadline=float("inf"), seen_call_signatures={}, tool_status_sink=journal))
    by_id = {row["tool_call_id"]: row for row in journal}
    assert by_id["reuse"]["reused_from_tool_call_id"] == "first"
    assert by_id["reuse"]["logical_key"] == by_id["first"]["logical_key"]
    assert by_id["other"]["logical_key"] != by_id["first"]["logical_key"]
    assert build_task_result(journal).execution_status == "partial"

"""A retried call must keep one operation identity in the product loop.

The graph path computed a logical key before dispatch, but the HostTools
adapter registered every provider call without one, and the completion emitter
preferred that empty registration. A failed call and its corrected retry were
therefore unrelated records: the completion account kept the failure as an
``execution_blocker`` even after the retry succeeded and the deliverable was
published. These tests pin the identity the completion gate reconciles on.
"""
from __future__ import annotations

from langchain_core.messages import ToolMessage

from src.core.execution_ledger import (ExecutionLedger, bind_execution_ledger,
                                       reset_execution_ledger)
from src.core.task_result import effective_tool_records
from src.core.turn_outcome import (CompletionEnvelope, CompletionGoal, FinalReview,
                                   completion_errors)
from src.graph.agent_operations import _emit_completed_tool_step, _logical_key

OPEN = {"action": "open", "url": "https://example.test/index.html"}


def _message(call_id: str, status: str, reason: str, content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, name="browse_web",
                       artifact={"_tool_outcome": {"status": status,
                                                   "reason_code": reason}})


def _adapter(call: dict, records: list[dict], ledger: ExecutionLedger) -> str:
    """The identity work HostTools.invoke performs before dispatch."""
    logical_key = _logical_key(call["name"], call)
    attempt = 1 + sum(1 for row in records
                      if str(row.get("logical_key") or "") == logical_key)
    call["_attempt"] = attempt
    ledger.start(call["id"], call["name"], call["args"], source="provider",
                 logical_key=logical_key, attempt=attempt)
    return logical_key


def test_logical_key_identifies_browse_arguments():
    first = _logical_key("browse_web", {"args": dict(OPEN)})
    same = _logical_key("browse_web", {"args": dict(OPEN)})
    other = _logical_key("browse_web", {"args": {"action": "open",
                                                 "url": "https://example.test/other.html"}})
    assert first and first == same and first != other
    assert first != "browse_web"


def test_registration_without_identity_does_not_erase_the_key():
    ledger = ExecutionLedger()
    token = bind_execution_ledger(ledger)
    try:
        call = {"id": "call_a", "name": "browse_web", "args": dict(OPEN)}
        ledger.start(call["id"], call["name"], call["args"], source="provider")
        sink: list[dict] = []
        _emit_completed_tool_step(
            call, _message(call["id"], "complete", "browse_observed", "{}"), sink)
        assert sink and sink[-1]["logical_key"] == _logical_key("browse_web", call)
    finally:
        reset_execution_ledger(token)


def test_successful_retry_supersedes_its_failed_attempt():
    ledger = ExecutionLedger()
    token = bind_execution_ledger(ledger)
    try:
        sink: list[dict] = []
        first = {"id": "call_a", "name": "browse_web", "args": dict(OPEN)}
        _adapter(first, sink, ledger)
        _emit_completed_tool_step(first, _message(
            first["id"], "failed", "browse_page_action_failed",
            '{"error": "browse_page_action_failed"}'), sink)
        second = {"id": "call_b", "name": "browse_web", "args": dict(OPEN)}
        _adapter(second, sink, ledger)
        _emit_completed_tool_step(second, _message(
            second["id"], "complete", "browse_observed", "{}"), sink)
    finally:
        reset_execution_ledger(token)
    assert [row["attempt"] for row in sink] == [1, 2]
    assert sink[0]["status"] == "failed" and sink[1]["status"] == "complete"
    effective = effective_tool_records(sink)
    assert len(effective) == 1 and effective[0]["status"] == "complete"
    statement = CompletionEnvelope(
        schema_version=1, answer="已下载。", status="completed",
        goals=[CompletionGoal(goal="下载 CSV", kind="answer",
                              evidence_call_ids=[second["id"]], artifact_links=[])],
        remaining=[], incidental_partials=[],
        review=FinalReview(**{key: "宿主仅校验执行回执。"
                              for key in ("facts", "sources", "numbers",
                                          "conditions", "delivery")}))
    errors = completion_errors(statement, sink)
    assert not [error for error in errors if error.startswith("execution_blocker")], errors


if __name__ == "__main__":
    test_logical_key_identifies_browse_arguments()
    test_registration_without_identity_does_not_erase_the_key()
    test_successful_retry_supersedes_its_failed_attempt()
    print("host tool identity ok")

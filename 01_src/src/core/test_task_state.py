import contextvars
import time
import pytest
from src.core import jobs, task_events as events, tracing, task_state
from src.graph.task_contract import TaskContract, Obligation
from src.graph.coverage import CoverageLedger


@pytest.fixture
def job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.db")
    for name, default in (("_job_id", ""), ("_user_id", None), ("_session_id", "")):
        monkeypatch.setattr(tracing, name, contextvars.ContextVar(name, default=default))
    claim = jobs.claim_job("session-a", "request", owner_user_id=7)
    tracing.set_job_id(claim.job_id)
    tracing.set_user_id(7)
    tracing.set_session_id("session-a")
    return claim.job_id


def test_state_is_host_snapshot_and_authorized(job):
    contract = TaskContract(query="full original", obligations=(Obligation("audit:0", "audit", {"file_indexes": [0]}),))
    task_state.publish(job, contract, CoverageLedger())
    value = task_state.current(7, "session-a", job)
    assert value["state"]["remaining"]
    assert "query" not in value["state"]["task_contract"]
    assert value["operations"] == []
    assert value["snapshot_seq"] is not None
    assert value["snapshot_scope"] == "last_completed_tool_batch"
    persisted = next(row for row in events.replay(job)["events"] if row["event"] == "task_state")
    assert persisted["data"]["task_contract"]["query"] == "full original"
    for owner, sid in ((8, "session-a"), (7, "session-b")):
        with pytest.raises(events.TaskConflict):
            task_state.current(owner, sid, job)


@pytest.mark.parametrize("status", ["running", "failed", "cancelled", "completed"])
def test_state_without_legacy_snapshot_reports_only_durable_facts(job, status):
    from langchain_core.messages import ToolMessage
    jobs.update_job(job, status="running")
    operation, lease = events.start_operation(job, 7, "research_workspace", {"operation": "save"})
    events.finish_operation(operation, lease, ToolMessage(
        content="receipt", tool_call_id="saved", name="research_workspace",
        artifact={"_tool_outcome": {"status": "unknown", "reason_code": "write_requires_reconciliation"}}))
    pending, _ = events.start_operation(job, 7, "research_workspace", {"operation": "task_state"})
    other = jobs.claim_job("session-b", "other-request", owner_user_id=7).job_id
    jobs.update_job(other, status="running")
    events.start_operation(other, 7, "other_session_tool", {})
    jobs.update_job(job, status=status)
    before = events.replay(job)
    value = task_state.current(7, "session-a", job)
    assert value["status"] == status
    assert value["state"] is None and value["snapshot_seq"] is None
    assert value["snapshot_scope"] == "durable_operations"
    assert {item["operation_id"] for item in value["operations"]} == {operation, pending}
    saved = next(item for item in value["operations"] if item["operation_id"] == operation)
    assert saved["status"] == saved["outcome"]["status"] == "unknown"
    assert saved["outcome"]["reason_code"] == "write_requires_reconciliation"
    assert next(item for item in value["operations"] if item["operation_id"] == pending)["status"] == "running"
    assert events.replay(job) == before


@pytest.mark.parametrize("owner,session", [(8, "session-a"), (7, "session-b"),
                                           (True, "session-a"), (0, "session-a"), (7, "")])
def test_state_without_snapshot_still_requires_owner_and_session(job, owner, session):
    with pytest.raises(events.TaskConflict, match="task_state_unavailable"):
        task_state.current(owner, session, job)


def test_state_requires_existing_job(job):
    with pytest.raises(events.TaskConflict):
        task_state.current(7, "session-a", "missing")
    with pytest.raises(events.TaskConflict):
        task_state.current(7, "session-a", "")


def test_checkpoint_budget_survives_execution_exhaustion_and_is_separate(job):
    from src.config import settings
    events.init_budget(job, time.time() - 1, max_calls=1, max_tokens=1)
    scope = {"key": "checkpoint:first", "deadline": time.monotonic() + 30}
    token = events._checkpoint_scope.set(scope)
    try:
        attempt = events.reserve_attempt(job, "turn_checkpoint", 3000)
        events.finish_attempt(attempt, {"input_tokens": 1500, "output_tokens": 100})
        assert events.projection(job)["budget"]["tokens"] == 0
        second = events.reserve_attempt(job, "protocol_fallback", 3000)
        events.finish_attempt(second, error="TimeoutError")
        with pytest.raises(events.TaskConflict, match="checkpoint_attempt_limit"):
            events.reserve_attempt(job, "turn_checkpoint", 1)
    finally:
        events._checkpoint_scope.reset(token)
    token = events._checkpoint_scope.set({"key": "checkpoint:second", "deadline": time.monotonic() + 30})
    try:
        with pytest.raises(events.TaskConflict, match="checkpoint_attempt_limit"):
            events.reserve_attempt(job, "turn_checkpoint", 1)
    finally:
        events._checkpoint_scope.reset(token)


def test_checkpoint_rejects_unknown_usage_over_cap_and_deadline(job):
    from src.config import settings
    scope = {"key": "checkpoint:first", "deadline": time.monotonic() + 30}
    token = events._checkpoint_scope.set(scope)
    try:
        attempt = events.reserve_attempt(job, "turn_checkpoint", settings.CONTEXT_CHECKPOINT_MAX_TOKENS)
        events.finish_attempt(attempt, error="TimeoutError")
        with pytest.raises(events.TaskConflict, match="checkpoint_token_limit"):
            events.reserve_attempt(job, "fallback", 1)
        scope["deadline"] = time.monotonic() - 1
        with pytest.raises(events.TaskConflict, match="checkpoint_budget_unavailable"):
            events.reserve_attempt(job, "fallback", 1)
    finally:
        events._checkpoint_scope.reset(token)

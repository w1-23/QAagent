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
    for owner, sid in ((8, "session-a"), (7, "session-b")):
        with pytest.raises(events.TaskConflict):
            task_state.current(owner, sid, job)


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

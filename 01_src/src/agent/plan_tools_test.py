"""Offline plan publication through real temporary jobs/events, without app config."""
import asyncio
import contextvars
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from src.agent.capabilities import CapabilityCatalog, CapabilityUnavailable
from src.agent.plan_tools import PLAN_TOOL_NAMES, TaskPlanUpdate, register_plan_tools, update_task_plan
from src.core import progress_bus, public_progress


@pytest.fixture
def live(tmp_path, monkeypatch):
    # Never import production settings, .env or provider clients.
    config = ModuleType("src.config")
    config.PROJECT_ROOT = tmp_path
    config.settings = SimpleNamespace(WORKSPACE_DIR="workspace")
    monkeypatch.setitem(sys.modules, "src.config", config)
    from src.core import jobs, task_events, tracing
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.sqlite")
    for name, value in (("_job_id", ""), ("_user_id", None), ("_session_id", "")):
        monkeypatch.setattr(tracing, name, contextvars.ContextVar(name, default=value))
    claim = jobs.claim_job("session-one", "request-one", owner_user_id=17)
    jobs.update_job(claim.job_id, status="running")
    tracing.set_job_id(claim.job_id)
    tracing.set_user_id(17)
    tracing.set_session_id("session-one")
    queue = task_events.DurableEventQueue(claim.job_id)
    progress_bus.set_request_queue(queue)
    public_progress.reset_emitted()
    try:
        yield jobs, task_events, tracing, claim.job_id, queue
    finally:
        progress_bus.clear_request_queue()
        public_progress.reset_emitted()


def step(step_id="inspect", title="核对公开来源", status="in_progress"):
    return {"id": step_id, "title": title, "status": status}


def test_plan_updates_use_real_job_id_and_replay_full_replacement(live):
    _, events, _, job_id, queue = live
    first = [step(), step("write", "整理结果", "pending")]
    second = [step(status="completed"), step("write", "整理结果", "in_progress")]
    for index, steps in enumerate((first, second)):
        message = asyncio.run(update_task_plan.ainvoke({"type": "tool_call", "name": "update_task_plan",
            "id": f"plan-{index}", "args": {"steps": steps}}))
        assert json.loads(message.content) == {"plan_id": job_id, "steps": steps}
        assert message.artifact["_tool_outcome"]["status"] == "complete"
    # Reopening the existing event reader recovers the full plan, exactly once
    # per publication. No second storage or direct append is involved.
    replay = events.replay(job_id)
    assert len(replay["events"]) == 2
    assert [row["seq"] for row in replay["events"]] == [1, 2]
    assert queue.qsize() == 2
    for row, steps in zip(replay["events"], (first, second)):
        assert row["event"] == "status"
        assert row["data"]["stage"] == "task_plan"
        assert row["data"]["plan_id"] == job_id
        assert row["data"]["steps"] == steps
    assert replay["events"][-1]["data"]["steps"][0]["status"] == "completed"


def test_public_note_uses_same_durable_event_path(live):
    jobs, events, _, job_id, queue = live
    assert public_progress.emit_public_progress("我先核对公开来源。", job_id=job_id,
        step_id="model_2", note_id=f"{job_id}:model:2", model_step=2,
        tool_call_ids=["native-call"])
    frame = queue.get_nowait()
    replay = events.replay(job_id)["events"]
    assert len(replay) == 1 and replay[0]["event"] == "assistant_progress"
    assert replay[0]["data"] == json.loads(frame["data"])
    assert replay[0]["data"]["tool_call_ids"] == ["native-call"]
    assert jobs.get_job(job_id)["progress_summary"] == "我先核对公开来源。"


@pytest.mark.parametrize("change", ["owner", "session", "missing", "terminal"])
def test_plan_rejects_wrong_authenticated_scope(live, change):
    jobs, events, tracing, job_id, queue = live
    if change == "owner":
        tracing.set_user_id(18)
    elif change == "session":
        tracing.set_session_id("session-two")
    elif change == "missing":
        tracing.set_job_id("")
    else:
        jobs.update_job(job_id, status="completed")
    with pytest.raises(PermissionError, match="task_plan_scope_unavailable"):
        asyncio.run(update_task_plan.ainvoke({"steps": [step()]}))
    assert events.replay(job_id)["events"] == [] and queue.empty()


@pytest.mark.parametrize("steps", [
    [], [step(), step()], [step(), step("other")],
    [step(status="done")], [step(title=" ")], [step(title="token: synthetic-secret")],
    [step(title='{"schema_version":1,"answer":"candidate"}')],
])
def test_invalid_or_private_plan_is_rejected(steps):
    with pytest.raises(ValidationError):
        TaskPlanUpdate(steps=steps)


def test_plan_schema_accepts_all_statuses_and_rejects_model_identity():
    steps = [step(str(i), status=status) for i, status in enumerate(
        ("pending", "in_progress", "completed", "blocked", "cancelled"))]
    assert len(TaskPlanUpdate(steps=steps).steps) == 5
    with pytest.raises(ValidationError):
        TaskPlanUpdate(steps=steps, plan_id="model-supplied-job")


def test_dynamic_plan_registration_is_lazy_authorized_and_sequential(live):
    catalog = CapabilityCatalog()
    register_plan_tools(catalog)
    assert catalog.describe()[0]["state"] == "discoverable"
    schema = catalog.schemas(set(PLAN_TOOL_NAMES))[0]["function"]["parameters"]
    assert set(schema["properties"]) == {"steps"}
    assert catalog.execution_mode("update_task_plan") == "sequential"
    with pytest.raises(CapabilityUnavailable):
        catalog.resolve("update_task_plan", set())
    assert catalog.resolve("update_task_plan", set(PLAN_TOOL_NAMES)) is update_task_plan
    from src.tools.registry import get_tool_spec
    assert get_tool_spec("update_task_plan").recovery_mode == "never"
    assert catalog.describe()[0]["state"] == "loaded"

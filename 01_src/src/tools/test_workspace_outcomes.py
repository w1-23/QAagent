"""Workspace read/write outcomes through production dispatch and durable journals."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent import host
from src.agent.host_test import environment, install, action  # noqa: F401
from src.agent.identity import ExecutionIdentity
from src.agent.runtime import RunResult
from src.core import task_events, task_state
from src.core.cancel import CancelToken, bound_token
from src.research import script_assets
from src.research.workspace_tools import research_workspace
from src.tools import registry
from src.tools.outcome import safe_tool_step


@pytest.fixture
def workspace(environment):
    registry.register_tool(research_workspace, is_readonly=False, write_scope="session_artifact")
    return environment


def call(operation, **args):
    return {"id": "workspace-call", "name": "research_workspace", "args": {"operation": operation, **args}}


def outcome(message):
    return safe_tool_step(message.name, message.content, message.artifact)


def test_real_workspace_task_state_continues_without_legacy_snapshot(environment, monkeypatch):
    state, job = environment
    def answer(messages):
        message = next(m for m in messages if isinstance(m, ToolMessage))
        data = json.loads(message.content)
        assert data["job_id"] == job and data["status"] == "running"
        assert data["state"] is None and data["snapshot_seq"] is None
        assert data["snapshot_scope"] == "durable_operations"
        assert data["operations"][0]["status"] == "running"  # this status read itself
        assert outcome(message)["status"] == "complete"
        return AIMessage(content="Durable status inspected")
    sent = install(monkeypatch, [action("research_workspace", operation="task_state"), answer],
                   [(research_workspace, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "Durable status inspected" and len(sent) == 2
    assert result["turn_outcome"]["reason_code"] == "reviewed_completion"
    assert result["turn_outcome"]["completion"]["statement"]["goals"][0]["evidence_call_ids"] == ["one"]
    assert task_events.projection(job)["operations"][0]["status"] == "complete"
    assert not any(row["event"] == "task_state" for row in task_events.replay(job)["events"])


@pytest.mark.parametrize("operation,expected", [("read_skill", "failed"), ("save", "unknown")])
def test_actual_dispatched_failure_respects_operation(workspace, monkeypatch, operation, expected):
    _, job = workspace
    effects = []
    def save_then_fail(*args, **kwargs):
        effects.append("write entered")
        raise OSError("save receipt unavailable")
    monkeypatch.setattr(script_assets, "save_script", save_then_fail)
    message = asyncio.run(registry._run_single_tool(research_workspace,
        call(operation, skill_id="missing-skill", source="x = 1", research_intent={"read_only": True}),
        require_durable=True))
    assert message.additional_kwargs["execution_dispatched"] is True
    assert outcome(message)["status"] == expected
    assert bool(message.additional_kwargs.get("operation_uncertain")) == (expected == "unknown")
    assert task_events.projection(job)["operations"][0]["status"] == expected
    assert effects == (["write entered"] if operation == "save" else [])


@pytest.mark.parametrize("editable,revision,reason", [(False, 1, "figure_readonly"), (True, 2, "revision_conflict")])
def test_figure_edit_preflight_is_not_an_unknown_write(workspace, monkeypatch, editable, revision, reason):
    from src.figures import projects
    from src import auth
    # This dispatch fixture uses synthetic owner 17 without an accounts store.
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"user_id": owner, "active": True} if owner == 17 else None)
    _, job = workspace
    monkeypatch.setattr(projects, "get_for_owner", lambda *a: {"render_version": revision})
    monkeypatch.setattr(projects, "_can_edit", lambda project: editable)
    monkeypatch.setattr(projects, "save_overrides", lambda *a, **kw: pytest.fail("preflight must not render or publish"))
    message = asyncio.run(registry._run_single_tool(research_workspace,
        call("figure_edit", figure_id="synthetic-figure", expected_revision=1,
             overrides=[{"gid": "title", "prop": "color", "value": "#123456"}]), require_durable=True))
    assert outcome(message)["status"] == "not_run"
    assert outcome(message)["reason_code"] == reason
    assert not message.additional_kwargs.get("operation_uncertain")
    assert task_events.projection(job)["operations"][0]["status"] == "not_run"


@pytest.mark.parametrize("operation,expected", [("task_state", "failed"), ("save", "unknown")])
@pytest.mark.parametrize("failure", [RuntimeError("adapter failed"), asyncio.TimeoutError()])
def test_exception_and_timeout_boundaries(workspace, monkeypatch, operation, expected, failure):
    _, job = workspace
    async def fail_observation(*args, **kwargs):
        raise failure
    monkeypatch.setattr(registry, "_run_sync_with_ctx", fail_observation)
    dispatched = []
    message = asyncio.run(registry._run_single_tool(research_workspace, call(operation),
        require_durable=True, on_dispatch=lambda: dispatched.append(True)))
    assert dispatched == [True]
    if operation == "task_state" and isinstance(failure, asyncio.TimeoutError):
        expected = "partial"  # preserve the existing timeout outcome, never upgrade to unknown
    assert outcome(message)["status"] == expected
    assert task_events.projection(job)["operations"][0]["status"] == expected
    assert bool(message.additional_kwargs.get("operation_uncertain")) == (operation == "save")


@pytest.mark.parametrize("operation", ["task_state", "save"])
def test_parent_cancellation_after_dispatch(workspace, monkeypatch, operation):
    _, job = workspace
    token = CancelToken()
    def cancel_in_adapter(*args, **kwargs):
        token.cancel()
        token.raise_if_cancelled("inside_adapter")
    monkeypatch.setattr(task_state, "current", cancel_in_adapter)
    monkeypatch.setattr(script_assets, "save_script", cancel_in_adapter)
    with bound_token(token):
        message = asyncio.run(registry._run_single_tool(research_workspace, call(operation), require_durable=True))
    assert message.artifact["cancelled"] is True
    assert message.additional_kwargs["execution_dispatched"] is True
    assert bool(message.additional_kwargs.get("operation_uncertain")) == (operation == "save")
    assert task_events.projection(job)["operations"][0]["status"] == "interrupted"


@pytest.mark.parametrize("operation", ["task_state", "save"])
def test_cancellation_before_dispatch_never_uncertain(workspace, operation):
    _, job = workspace
    token = CancelToken()
    token.cancel()
    with bound_token(token):
        message = asyncio.run(registry._run_single_tool(research_workspace, call(operation), require_durable=True))
    assert message.artifact["cancelled"] is True
    assert not message.additional_kwargs.get("execution_dispatched")
    assert not message.additional_kwargs.get("operation_uncertain")
    assert task_events.projection(job)["operations"] == []


@pytest.mark.parametrize("operation,expected", [("task_state", "cancelled"), ("save", "unknown")])
def test_coroutine_cancellation_settles_missing_observation(workspace, monkeypatch, operation, expected):
    state, job = workspace
    identity = ExecutionIdentity(17, "host-session", "host-request", job)
    adapter = SimpleNamespace(identity=identity, messages={}, dispatched={}, records=[])
    response = action("research_workspace", "cancelled-call", operation=operation)
    tool_call = response.tool_calls[0]
    async def scenario():
        started = asyncio.Event()
        async def wait_for_cancellation(*args, **kwargs):
            started.set()
            await asyncio.Future()
        monkeypatch.setattr(registry, "_run_sync_with_ctx", wait_for_cancellation)
        task = asyncio.create_task(registry._run_single_tool(research_workspace, tool_call,
            require_durable=True,
            on_dispatch=lambda: adapter.dispatched.update({"cancelled-call": "research_workspace"})))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert task_events.projection(job)["operations"][0]["status"] == "interrupted"
    result = RunResult("cancelled", "user_cancelled", (response,), 1)
    trace = host._settled_trace([response], adapter, result)
    message = next(m for m in trace if isinstance(m, ToolMessage))
    assert outcome(message)["status"] == expected


@pytest.mark.parametrize("operation", ["save", "patch", "figure_edit", "figure_restore", "new_operation", None, []])
def test_unknown_or_write_operations_cannot_claim_readonly(workspace, operation):
    assert not registry.is_readonly_call("research_workspace", {
        "operation": operation, "is_readonly": True, "write_applied": False,
        "_tool_outcome": {"status": "failed", "read_only": True}})


@pytest.mark.parametrize("operation", ["list", "read", "runs", "read_run", "skills", "read_skill",
    "read_skill_resource", "intent", "figure_inspect", "task_state", "context_source"])
def test_code_owned_read_operations(workspace, operation):
    assert registry.is_readonly_call("research_workspace", {"operation": operation})
    assert registry.get_tool_spec("research_workspace").write_scope == "session_artifact"
    assert registry.get_tool_spec("research_workspace").recovery_mode == "never"


def test_default_and_unregistered_classification(workspace):
    assert registry.is_readonly_call("research_workspace", {})
    assert not registry.is_readonly_call("unregistered", {"operation": "task_state", "is_readonly": True})

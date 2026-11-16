"""Synthetic protected-store and API admission recovery tests; no live accounts."""
from __future__ import annotations

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import socket
import time
from types import SimpleNamespace
from typing import Optional

import pytest
# The dedicated harness environment intentionally omits the web framework.
# Endpoint-body tests inject only its exception/request interface, not a server.
class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


Request = object
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from pydantic import BaseModel, Field, ValidationError

from src.agent.checkpoint_store import checkpoint_scope, open_checkpoint
from src.agent.identity import ExecutionIdentity
from src.agent.recovery import capture_original_request_state, load_recovery_state, restore_deadline
from src.core import jobs, task_events
from src.agent.host_test import environment as host_environment

SCOPE = ("https://synthetic.invalid/v1", "synthetic-model")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.db")
    # Loop creation uses a local socketpair on Windows; create it before blocking
    # all outbound network access.
    loop = asyncio.new_event_loop()
    def forbidden(*args, **kwargs):
        raise AssertionError("Recovery test attempted network access")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    claim = jobs.claim_job("saved-session", "saved-turn", owner_user_id=17,
                           client_request_id="saved-client", payload={"query": "original query"})
    identity = ExecutionIdentity(17, "saved-session", "saved-turn", claim.job_id)
    task_events.init_budget(identity.run_id, time.time() + 120, max_calls=10, max_tokens=10000)
    state = {"user_id": 17, "session_id": identity.session_id, "request_id": identity.user_turn_id,
             "job_id": identity.run_id, "query": "original query", "client_request_id": "saved-client",
             "idempotency_key": "saved-idempotency", "context_compression": False,
             "attachment_context": "saved attachment context", "attachment_refs": [],
             "request_tables": [{"name": "synthetic.csv", "data": b"a,b\n1,2"}]}
    yield tmp_path, identity, state, loop
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.run_until_complete(loop.shutdown_default_executor())
    loop.close()


async def save(root, identity, state, **values):
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {
        "messages": [HumanMessage(content="original query")],
        "host_state": {"original_request_state": capture_original_request_state(state)}, **values}
    checkpoint["channel_versions"] = {key: 1 for key in checkpoint["channel_values"]}
    async with open_checkpoint(root, identity, SCOPE) as saver:
        await saver.aput({"configurable": checkpoint_scope(identity, SCOPE)}, checkpoint,
                         {"source": "loop", "step": 1}, checkpoint["channel_versions"])


def interrupt(identity):
    assert jobs.mark_interrupted_jobs() == 1
    job = jobs.get_job(identity.run_id)
    assert (job["status"], job["current_step"], job["error"]) == ("failed", "interrupted", "service_restart")


def test_protected_original_state_and_wall_deadline(store):
    root, identity, state, loop = store
    state["request_deadline"] = -99  # Monotonic deadlines must never be captured.
    state["agent_resume"] = True
    loop.run_until_complete(save(root, identity, state))
    recovered = loop.run_until_complete(load_recovery_state(root, identity, SCOPE))
    assert recovered["query"] == "original query"
    assert recovered["request_tables"][0]["data"] == b"a,b\n1,2"
    assert recovered["context_compression"] is False
    assert recovered["messages"] == [] and recovered["agent_resume"] is True
    assert "request_deadline" not in recovered
    restored = restore_deadline(recovered, {"deadline": time.time() + 20})
    assert 18 < restored["request_deadline"] - time.monotonic() <= 20
    for wrong_identity, scope in ((replace(identity, owner_user_id=18), SCOPE),
                                  (replace(identity, user_turn_id="other"), SCOPE),
                                  (replace(identity, run_id="other"), SCOPE),
                                  (identity, (SCOPE[0], "other"))):
        with pytest.raises(task_events.TaskConflict, match="checkpoint_missing"):
            loop.run_until_complete(load_recovery_state(root, wrong_identity, scope))


@pytest.mark.parametrize("values,reason", [
    ({"stop_status": "cancelled", "stop_reason": "cancelled"}, "terminal"),
    ({"stop_status": "incomplete", "stop_reason": "deadline"}, "terminal"),
    ({"batch_status": "waiting"}, "reconciliation"),
    ({"unknown_operations": ["uncertain"]}, "reconciliation"),
    ({"host_state": {}}, "original_request_missing"),
])
def test_checkpoint_rejection(store, values, reason):
    root, identity, state, loop = store
    loop.run_until_complete(save(root, identity, state, **values))
    with pytest.raises(task_events.TaskConflict, match=reason):
        loop.run_until_complete(load_recovery_state(root, identity, SCOPE))


def test_original_identity_and_ephemeral_pool_rejected(store):
    root, identity, state, loop = store
    state["session_id"] = "wrong-session"
    loop.run_until_complete(save(root, identity, state))
    with pytest.raises(task_events.TaskConflict, match="identity_mismatch"):
        loop.run_until_complete(load_recovery_state(root, identity, SCOPE))
    state["session_id"] = identity.session_id
    state["candidate_pool"] = {"pool_id": "lost-process-pool"}
    loop.run_until_complete(save(root, identity, state))
    with pytest.raises(task_events.TaskConflict, match="candidate_pool_unavailable"):
        loop.run_until_complete(load_recovery_state(root, identity, SCOPE))


def test_restart_preserves_charges_and_requeues_updates_only_on_claim(store):
    root, identity, state, loop = store
    attempt = task_events.reserve_attempt(identity.run_id, "supervisor", 500)
    task_events.submit_update(identity.run_id, 0, "retain this extra instruction")
    interrupt(identity)
    projection = task_events.projection(identity.run_id)
    assert projection["budget"]["calls"] == 1 and projection["budget"]["tokens"] == 500
    assert projection["budget"]["unknown_reserved_tokens"] == 500
    assert projection["budget"]["in_flight_attempts"] == 0
    assert task_events.updates_after(identity.run_id, 0) == []
    before = task_events.replay(identity.run_id)["events"]
    claim = jobs.claim_agent_recovery(identity)
    assert claim["budget"]["calls"] == 1
    admission_event = task_events.replay(identity.run_id, claim["recovery_cursor"] - 1)["events"][0]
    assert admission_event["data"]["reactivated_updates"] == [
        {"version": 1, "instruction": "retain this extra instruction"}]
    assert task_events.updates_after(identity.run_id, 0)[0]["status"] == "pending"
    task_events.acknowledge_updates(identity.run_id, 1)
    assert task_events.updates_after(identity.run_id, 1) == []
    done = task_events.append_event(identity.run_id, "done", {"answer": "saved answer"})
    again = task_events.append_event(identity.run_id, "done", {"answer": "duplicate"})
    assert done == again and done["seq"] > claim["recovery_cursor"]
    assert task_events.replay(identity.run_id)["events"][:len(before)] == before
    with task_events.transaction() as conn:
        assert conn.execute("SELECT status FROM task_model_attempts WHERE attempt_id=?", (attempt,)).fetchone()[0] == "unknown"
    task_events.finish_attempt(attempt, {"total_tokens": 0})
    assert task_events.projection(identity.run_id)["budget"]["tokens"] == 500


def test_concurrent_recovery_claim_has_one_winner(store):
    _, identity, _, _ = store
    interrupt(identity)
    def claim():
        try:
            return jobs.claim_agent_recovery(identity)
        except task_events.TaskConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: claim(), range(2)))
    assert sum(value is not None for value in results) == 1
    assert len([e for e in task_events.replay(identity.run_id)["events"] if e["event"] == "agent_recovery"]) == 1


@pytest.mark.parametrize("blocker", ["owner", "active", "operation", "batch", "unknown_batch", "cancel", "budget"])
def test_admission_rejects_unsafe_or_unavailable_run(store, blocker):
    _, identity, _, _ = store
    if blocker == "operation":
        task_events.start_operation(identity.run_id, 17, "write", {}, recovery_mode="never")
    if blocker in {"batch", "unknown_batch"}:
        with task_events.transaction() as conn:
            conn.execute("CREATE TABLE agent_batch_journal(run_id TEXT, status TEXT, observation TEXT)")
            conn.execute("INSERT INTO agent_batch_journal VALUES (?,?,?)", (identity.run_id,
                         "intent" if blocker == "batch" else "complete",
                         json.dumps({"unknown_operations": ["uncertain"]})))
    if blocker == "cancel":
        jobs.request_cancel(identity.run_id)
    interrupt(identity)
    if blocker == "owner":
        identity = replace(identity, owner_user_id=18)
    if blocker == "active":
        jobs.claim_job(identity.session_id, "new-turn", owner_user_id=17)
    if blocker == "budget":
        with task_events.transaction() as conn:
            conn.execute("UPDATE task_budgets SET deadline=? WHERE job_id=?", (time.time() - 1, identity.run_id))
    with pytest.raises(task_events.TaskConflict):
        jobs.claim_agent_recovery(identity)
    assert jobs.get_job(identity.run_id)["status"] == "failed"


def test_repeated_restart_is_append_only_and_answered_can_save_after_deadline(store):
    root, identity, state, loop = store
    loop.run_until_complete(save(root, identity, state, stop_status="complete", stop_reason="answered",
                                 messages=[HumanMessage(content="original query"), AIMessage(content="answer")]))
    recovered = loop.run_until_complete(load_recovery_state(root, identity, SCOPE))
    assert recovered["agent_recovery_answered"] is True
    interrupt(identity)
    first = jobs.claim_agent_recovery(identity)
    interrupt(identity)
    with task_events.transaction() as conn:
        conn.execute("UPDATE task_budgets SET deadline=0,calls=max_calls,tokens=max_tokens WHERE job_id=?", (identity.run_id,))
    second = jobs.claim_agent_recovery(identity, answered=True)
    assert second["recovery_cursor"] > first["recovery_cursor"]
    assert len([e for e in task_events.replay(identity.run_id)["events"] if e["event"] == "done"]) == 2


def api_namespace():
    # Execute the production endpoint body without importing API startup hooks,
    # embeddings, environment files, or any application/account stores.
    path = Path(__file__).parents[1] / "api" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"SelectedSkill", "ChatRequest", "RecoverAgentRequest", "recover_chat_agent"}
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) and node.name in names]
    for node in nodes:
        if isinstance(node, ast.AsyncFunctionDef):
            node.decorator_list = []
    namespace = {"BaseModel": BaseModel, "Field": Field, "Optional": Optional, "Request": Request,
                 "HTTPException": HTTPException, "asyncio": asyncio,
                 "_running_graph_tasks": {}, "_pending_graph_starts": set(), "_job_tokens": {}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    namespace["ChatRequest"].model_rebuild(_types_namespace=namespace)
    return namespace


def test_endpoint_handoff_uses_saved_fields_same_identity_no_quota(store, monkeypatch):
    root, identity, state, loop = store
    from src import config
    from src.core import account_leases
    monkeypatch.setattr(config, "PROJECT_ROOT", root)
    monkeypatch.setattr(config, "get_deepseek_model", lambda: SCOPE[1])
    loop.run_until_complete(save(root / "state", identity, state))
    interrupt(identity)
    namespace = api_namespace()
    with pytest.raises(ValidationError):
        namespace["RecoverAgentRequest"](query="replacement", user_id=18)
    leases = []
    class Lease:
        def __init__(self): self.released = False
        def release(self): self.released = True
    def acquire(owner):
        assert owner == 17
        lease = Lease()
        leases.append(lease)
        return lease
    async def run_async(lease, coro):
        assert not lease.released
        return await coro
    monkeypatch.setattr(account_leases, "acquire_account_lease", acquire)
    monkeypatch.setattr(account_leases, "run_account_async", run_async)
    namespace.update(settings=SimpleNamespace(RESOLVED_MAIN_BASE_URL=SCOPE[0]),
        current_user=lambda request: {"user_id": 17},
        _owned_protocol_job=lambda job_id, request: jobs.get_job(job_id),
        require_owned_session=lambda sid, user: None,
        _validate_account_file_refs=lambda ids, owner: [])
    captured = {}
    async def start(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return "normal-lifecycle"
    namespace["_start_claimed_chat"] = start
    result = loop.run_until_complete(namespace["recover_chat_agent"](
        identity.run_id, namespace["RecoverAgentRequest"](), object()))
    assert result == "normal-lifecycle" and leases[0].released
    req, _, _, _, sid, rid, job_id = captured["args"][:7]
    assert (sid, rid, job_id) == (identity.session_id, identity.user_turn_id, identity.run_id)
    assert req.query == state["query"] and req.client_request_id == "saved-client"
    assert req.attachments == [] and req.context_compression is False
    assert captured["kwargs"]["quota_reserved"] is False
    assert captured["kwargs"]["recovery_state"]["agent_resume"] is True
    assert task_events.projection(job_id)["budget"]["calls"] == 0


def test_actual_host_checkpoint_resumes_through_graph_save_without_new_human(host_environment, monkeypatch):
    from src.agent import host
    from src.agent.host_test import install
    from src.config import PROJECT_ROOT, settings, get_deepseek_model
    from src.core import tracing, progress_bus
    from src.graph import session_nodes, candidate_prefetch
    from src.graph.general_agent_graph import build_general_agent_graph
    from src.session.manager import session_manager

    state, job_id = host_environment
    state.update(client_request_id="original-client", idempotency_key="original-idempotency",
                 context_compression=False, request_tables=[])
    original_attempts = []
    def crash(messages):
        original_attempts.append(task_events.reserve_attempt(job_id, "supervisor", 100))
        raise RuntimeError("synthetic provider interruption before response checkpoint")
    sent = install(monkeypatch, [crash, AIMessage(content="recovered answer")])
    from src.agent.capabilities import CapabilityCatalog
    monkeypatch.setattr(host, "build_preset_catalog", CapabilityCatalog)
    def forbidden_context(*args, **kwargs):
        raise AssertionError("Recovery must not reload or compact history/prefetch")
    monkeypatch.setattr(session_nodes, "load_session_context", forbidden_context)
    monkeypatch.setattr(candidate_prefetch, "prefetch_candidates", forbidden_context)
    identity = ExecutionIdentity(17, state["session_id"], state["request_id"], job_id)
    async def scenario():
        first = await host.general_agent_node(state)
        assert first["turn_outcome"]["status"] == "failed"
        interrupt(identity)
        recovered = await load_recovery_state(PROJECT_ROOT / "state", identity,
                                             (settings.RESOLVED_MAIN_BASE_URL, get_deepseek_model()))
        claim = jobs.claim_agent_recovery(identity)
        recovered = restore_deadline(recovered, claim["budget"])
        graph = build_general_agent_graph()
        result = await graph.ainvoke(recovered)
        return result
    result = asyncio.run(scenario())
    assert result["answer"] == "recovered answer"
    assert result["_trace"]["history_saved"] is True
    assert len(sent) == 2
    original_humans = [m for m in sent[0] if isinstance(m, HumanMessage)]
    resumed_humans = [m for m in sent[1] if isinstance(m, HumanMessage)]
    assert len(resumed_humans) == 1
    assert resumed_humans[0].content == original_humans[0].content
    assert resumed_humans[0].additional_kwargs["user_text"] == state["query"]
    messages = session_manager._get_messages_sync(state["session_id"])
    assert sum(isinstance(m, HumanMessage) for m in messages) == 1
    assert sum(isinstance(m, AIMessage) and m.content == "recovered answer" for m in messages) == 1
    assert task_events.projection(job_id)["budget"]["unknown_reserved_tokens"] == 100


def test_endpoint_denies_foreign_owner_before_checkpoint_access(store):
    _, identity, _, loop = store
    namespace = api_namespace()
    namespace.update(current_user=lambda request: {"user_id": 18},
                     _owned_protocol_job=lambda job_id, request: jobs.get_job(job_id))
    with pytest.raises(HTTPException) as exc:
        loop.run_until_complete(namespace["recover_chat_agent"](
            identity.run_id, namespace["RecoverAgentRequest"](), object()))
    assert exc.value.status_code == 404

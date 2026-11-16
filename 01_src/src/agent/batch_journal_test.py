"""Offline durable batch tests against a real temporary jobs SQLite database."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib
import json
import sqlite3
import sys
import threading
from types import ModuleType

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent import batch_journal as journal_module
from src.agent.batch_journal import BatchJournal, RecoveryRequiresReconciliation
from src.agent.identity import ExecutionIdentity
from src.agent.runtime import BatchObservation
from src.core import task_events


@pytest.fixture
def environment(tmp_path, monkeypatch):
    # Jobs only needs PROJECT_ROOT. Never import real configuration or secrets.
    config = ModuleType("src.config")
    config.PROJECT_ROOT = tmp_path
    with monkeypatch.context() as importing:
        importing.setitem(sys.modules, "src.config", config)
        jobs = importlib.import_module("src.core.jobs")
    path = tmp_path / "jobs.sqlite3"
    monkeypatch.setattr(jobs, "_DB_PATH", path)
    claim = jobs.claim_job("session-one", "request-one", owner_user_id=7)
    identity = ExecutionIdentity(7, "session-one", "request-one", claim.job_id)
    return identity, path, jobs


def action(cid="call-one", name="write_fixture", args=None):
    return AIMessage(content="", tool_calls=[{
        "id": cid, "name": name,
        "args": {"request_scope": "existing-scope", "value": 1} if args is None else args,
        "type": "tool_call",
    }])


def observation(response, **kwargs):
    return BatchObservation(tuple(ToolMessage(
        content="Actual published observation", tool_call_id=call["id"], name=call["name"],
        id="message-" + call["id"], status="success",
        artifact={"result_refs": [{"id": "stored-result"}]},
        additional_kwargs={"operation_id": "operation-one", "result_reference": {
            "storage": {"complete": True}, "tool_call_id": call["id"]}},
        response_metadata={"source": "host"},
    ) for call in response.tool_calls), **kwargs)


def rows(path):
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT status,observation FROM agent_batch_journal").fetchall()


async def must_not_execute(response, identity):
    raise AssertionError("a saved or unsettled batch must not execute")


def test_committed_intent_before_dispatch_and_complete_roundtrip(environment):
    identity, path, _ = environment
    response = action()
    # Provider/private AI fields must not reach a journal checkpoint.
    response.additional_kwargs["private_model_state"] = object()
    result = observation(response, pending_question=True, context_notes=("host note",))

    async def execute(actual, actual_identity):
        assert actual is response and actual_identity == identity
        assert rows(path) == [("intent", "")]
        # A second real connection can write while the executor is awaited.
        with task_events.transaction() as conn:
            conn.execute("UPDATE task_jobs SET progress_summary='executing' WHERE job_id=?",
                         (identity.run_id,))
        return result

    assert asyncio.run(BatchJournal(identity).execute(response, execute)) is result
    saved = asyncio.run(BatchJournal(identity).execute(response, must_not_execute))
    assert saved == result
    assert saved is not result and saved.messages[0] is not result.messages[0]
    status, encoded = rows(path)[0]
    assert status == "complete"
    assert "private_model_state" not in encoded
    assert json.loads(encoded)["version"] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM task_operations").fetchone()[0] == 0


def test_operation_receipt_tuple_arrays_keep_values_on_recovery(environment):
    identity, _, _ = environment
    response = action()
    result = observation(response)
    result.messages[0].artifact["op_receipt"] = {
        "coverage": {"_tool_outcome": {"artifacts": ({"id": "asset-one"},)}}}
    async def execute(*_):
        return result
    asyncio.run(BatchJournal(identity).execute(response, execute))
    restored = asyncio.run(BatchJournal(identity).execute(response, must_not_execute))
    assert restored.messages[0].artifact["op_receipt"]["coverage"]["_tool_outcome"]["artifacts"] == [{"id": "asset-one"}]


def test_digest_covers_full_native_calls_and_existing_scope(environment):
    identity, path, _ = environment
    dispatched = []

    async def execute(response, actual_identity):
        dispatched.append(response)
        return observation(response)

    variants = [action(), action(cid="call-two"), action(name="other_tool"),
                action(args={"request_scope": "existing-scope", "value": 2}),
                action(args={"request_scope": "other-scope", "value": 1})]
    for response in variants:
        asyncio.run(BatchJournal(identity).execute(response, execute))
        asyncio.run(BatchJournal(identity).execute(response, must_not_execute))
    # Dictionary key order has no semantic effect on the digest.
    reordered = action(args={"value": 1, "request_scope": "existing-scope"})
    asyncio.run(BatchJournal(identity).execute(reordered, must_not_execute))
    assert len(dispatched) == len(variants) == len(rows(path))


def test_batch_order_is_part_of_digest(environment):
    identity, path, _ = environment
    calls = action().tool_calls + action(cid="call-two").tool_calls

    async def execute(response, actual_identity):
        return observation(response)

    for ordered in (calls, list(reversed(calls))):
        response = AIMessage(content="", tool_calls=ordered)
        asyncio.run(BatchJournal(identity).execute(response, execute))
    assert len(rows(path)) == 2


def test_two_concurrent_connections_only_one_dispatch(environment):
    identity, path, _ = environment
    barrier = threading.Barrier(2)
    started, release = threading.Event(), threading.Event()
    calls = []

    async def execute(response, actual_identity):
        calls.append(response)
        started.set()
        assert await asyncio.to_thread(release.wait, 5)
        return observation(response)

    def attempt():
        barrier.wait(timeout=5)
        try:
            return asyncio.run(BatchJournal(identity).execute(action(), execute))
        except RecoveryRequiresReconciliation as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(attempt) for _ in range(2)]
        try:
            assert started.wait(5)
            from concurrent.futures import wait, FIRST_COMPLETED
            done, _ = wait(futures, timeout=5, return_when=FIRST_COMPLETED)
            assert len(done) == 1
            assert isinstance(next(iter(done)).result(), RecoveryRequiresReconciliation)
            assert rows(path) == [("intent", "")]
        finally:
            release.set()
        results = [future.result(timeout=5) for future in futures]
    assert len(calls) == 1
    assert sum(isinstance(item, BatchObservation) for item in results) == 1
    asyncio.run(BatchJournal(identity).execute(action(), must_not_execute))


@pytest.mark.parametrize("failure", [RuntimeError("public_executor_failure"),
                                    asyncio.CancelledError("cancelled")])
def test_exception_and_cancellation_preserve_intent_and_propagate(environment, failure):
    identity, path, _ = environment

    async def execute(response, actual_identity):
        raise failure

    with pytest.raises(type(failure)) as caught:
        asyncio.run(BatchJournal(identity).execute(action(), execute))
    assert caught.value is failure
    assert rows(path) == [("intent", "")]
    with pytest.raises(RecoveryRequiresReconciliation, match="recovery_requires_reconciliation"):
        asyncio.run(BatchJournal(identity).execute(action(), must_not_execute))


def test_returned_unknown_is_saved_without_replaying_write(environment):
    identity, path, _ = environment
    result = observation(action(), unknown_operations=("operation-one",),
                         context_notes=("Outcome requires host reconciliation",))
    result.messages[0].status = "error"
    result.messages[0].content = "Operation outcome is unknown"

    async def execute(response, actual_identity):
        return result

    asyncio.run(BatchJournal(identity).execute(action(), execute))
    saved = asyncio.run(BatchJournal(identity).execute(action(), must_not_execute))
    assert saved == result
    assert saved.unknown_operations == ("operation-one",)
    assert saved.messages[0].status == "error"
    assert rows(path)[0][0] == "complete"


@pytest.mark.parametrize("changes", [{"owner_user_id": 8}, {"session_id": "other"},
                                     {"user_turn_id": "other"}, {"run_id": "missing"}])
def test_cross_identity_rejected_before_journal_access(environment, changes):
    identity, path, _ = environment

    async def execute(response, actual_identity):
        return observation(response)

    asyncio.run(BatchJournal(identity).execute(action(), execute))
    before = rows(path)
    with pytest.raises(task_events.TaskConflict, match="agent_execution_identity_conflict"):
        asyncio.run(BatchJournal(replace(identity, **changes)).execute(action(), must_not_execute))
    assert rows(path) == before


@pytest.mark.parametrize("status", ["queued", "cancel_requested", "completed", "failed",
                                    "cancelled", "awaiting_input"])
def test_non_running_jobs_cannot_read_complete_or_claim_new(environment, status):
    identity, path, jobs = environment

    async def execute(response, actual_identity):
        return observation(response)

    asyncio.run(BatchJournal(identity).execute(action(), execute))
    assert jobs.update_job(identity.run_id, status=status)
    for response in (action(), action(cid="new-call")):
        with pytest.raises(task_events.TaskConflict, match="agent_execution_identity_conflict"):
            asyncio.run(BatchJournal(identity).execute(response, must_not_execute))
    assert len(rows(path)) == 1


def test_detached_job_can_publish_and_repeat(environment):
    identity, _, jobs = environment
    assert jobs.update_job(identity.run_id, status="detached")

    async def execute(response, actual_identity):
        return observation(response)

    result = asyncio.run(BatchJournal(identity).execute(action(), execute))
    assert asyncio.run(BatchJournal(identity).execute(action(), must_not_execute)) == result


def test_state_change_during_execution_does_not_publish_complete(environment):
    identity, path, jobs = environment

    async def execute(response, actual_identity):
        assert jobs.update_job(identity.run_id, status="cancel_requested")
        return observation(response)

    with pytest.raises(task_events.TaskConflict, match="agent_execution_identity_conflict"):
        asyncio.run(BatchJournal(identity).execute(action(), execute))
    assert rows(path) == [("intent", "")]


@pytest.mark.parametrize("kind", ["oversized", "non_json", "wrong_pair", "wrong_type"])
def test_invalid_publication_leaves_intent(environment, kind):
    identity, path, _ = environment

    async def execute(response, actual_identity):
        result = observation(response)
        if kind == "oversized":
            result.messages[0].content = "x" * journal_module.MAX_BATCH_BYTES
        elif kind == "non_json":
            result.messages[0].artifact = {"private": AIMessage(content="not a tool result")}
        elif kind == "wrong_pair":
            result.messages[0].tool_call_id = "different-call"
        else:
            return "not a BatchObservation"
        return result

    with pytest.raises((TypeError, ValueError)):
        asyncio.run(BatchJournal(identity).execute(action(), execute))
    assert rows(path) == [("intent", "")]
    with pytest.raises(RecoveryRequiresReconciliation):
        asyncio.run(BatchJournal(identity).execute(action(), must_not_execute))


def test_corrupt_checkpoint_blocks_reexecution(environment):
    identity, path, _ = environment

    async def execute(response, actual_identity):
        return observation(response)

    asyncio.run(BatchJournal(identity).execute(action(), execute))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE agent_batch_journal SET observation='{}'")
    with pytest.raises(RecoveryRequiresReconciliation):
        asyncio.run(BatchJournal(identity).execute(action(), must_not_execute))


def test_json_object_tags_are_inert_data(environment):
    identity, _, _ = environment
    tagged = {"lc": 1, "type": "constructor", "id": ["untrusted", "Constructor"],
              "kwargs": {"value": "inert"}}

    async def execute(response, actual_identity):
        result = observation(response)
        result.messages[0].artifact = tagged
        return result

    asyncio.run(BatchJournal(identity).execute(action(), execute))
    saved = asyncio.run(BatchJournal(identity).execute(action(), must_not_execute))
    assert type(saved.messages[0]) is ToolMessage
    assert saved.messages[0].artifact == tagged


def test_completion_storage_failure_preserves_intent(environment, monkeypatch):
    from contextlib import contextmanager

    identity, path, _ = environment
    transaction = task_events.transaction
    failure = OSError("completion storage unavailable")
    calls = 0

    @contextmanager
    def failing_completion():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure
        with transaction() as conn:
            yield conn

    async def execute(response, actual_identity):
        return observation(response)

    with monkeypatch.context() as patch:
        patch.setattr(task_events, "transaction", failing_completion)
        with pytest.raises(OSError) as caught:
            asyncio.run(BatchJournal(identity).execute(action(), execute))
    assert caught.value is failure
    assert rows(path) == [("intent", "")]
    with pytest.raises(RecoveryRequiresReconciliation):
        asyncio.run(BatchJournal(identity).execute(action(), must_not_execute))


def test_wraps_existing_capability_executor_without_republishing(environment):
    from src.agent.capabilities import CapabilityCatalog, CapabilityDefinition
    from src.agent.execution import CapabilityExecutor, ToolExecution

    identity, path, _ = environment
    catalog = CapabilityCatalog()
    catalog.register(CapabilityDefinition("write_fixture", "Synthetic write",
        {"type": "object", "properties": {}}, lambda: "synthetic-tool", mutates=True))
    invoked, published = [], []

    async def invoke(tool, call, actual_identity):
        assert actual_identity == identity and tool == "synthetic-tool"
        assert rows(path) == [("intent", "")]
        invoked.append(call["id"])
        return ToolExecution(observation(action()).messages[0], operation_id="operation-one")

    async def publish(call, execution, actual_identity):
        published.append(call["id"])
        return execution.message

    executor = CapabilityExecutor(catalog, lambda _: {"write_fixture"}, invoke, publish)
    first = asyncio.run(BatchJournal(identity).execute(action(), executor))
    saved = asyncio.run(BatchJournal(identity).execute(action(), executor))
    assert first == saved
    assert invoked == published == ["call-one"]


def test_new_authorized_run_has_independent_batch_key(environment):
    identity, path, jobs = environment
    claim = jobs.claim_job(identity.session_id, identity.user_turn_id,
                           owner_user_id=identity.owner_user_id)
    other = replace(identity, run_id=claim.job_id)
    executed = []

    async def execute(response, actual_identity):
        executed.append(actual_identity.run_id)
        return observation(response)

    for current in (identity, other):
        asyncio.run(BatchJournal(current).execute(action(), execute))
        asyncio.run(BatchJournal(current).execute(action(), must_not_execute))
    assert executed == [identity.run_id, other.run_id]
    assert len(rows(path)) == 2

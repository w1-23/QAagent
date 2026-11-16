"""Offline post-turn checkpoint tests; databases and model calls are isolated."""
import asyncio
import importlib.util
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.guardrails import turn_checkpoint as checkpoint


@pytest.fixture
def store(tmp_path, monkeypatch):
    import src.config as config
    from src.core import tracing
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    path = Path(__file__).resolve().parents[1] / "session" / "manager.py"
    spec = importlib.util.spec_from_file_location("post_turn_test_session", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manager = module.session_manager
    manager._create_session_sync("s", 7)
    monkeypatch.setattr(tracing, "get_user_id", lambda: 7)
    monkeypatch.setattr(tracing, "get_session_id", lambda: "s")
    raw = "精确 1.23456789 " + "long input " * 10000
    asyncio.run(manager.save_messages("s", [HumanMessage(content=raw),
        AIMessage(content="done", additional_kwargs={"request_id": "r1"})]))
    return manager, raw


def value(source_id="1"):
    return {"goal": "navigation only", "decisions": [], "unfinished": [],
            "task_status": "completed", "excerpts": [{"source_id": source_id,
            "start_char": 0, "end_char": 14, "quote": "精确 1.23456789 "}]}


@pytest.fixture
def fake_model(monkeypatch):
    from src.core import task_events
    from src.core.context_manager import ContextManager
    calls = []
    async def invoke(model, messages, **kwargs):
        calls.append((messages, kwargs))
        return AIMessage(content=json.dumps(value()), response_metadata={"finish_reason": "stop"})
    monkeypatch.setattr(ContextManager, "_get_compact_llm", lambda self: object())
    monkeypatch.setattr(task_events, "counted_ainvoke", invoke)
    return calls


def test_finished_turn_exact_excerpts_raw_reachable_and_bounded_prompt(store, fake_model):
    manager, raw = store
    snapshot = manager.get_checkpoint("s")
    assert asyncio.run(checkpoint.build_checkpoint(manager, "s", {
        "answer": "done", "request_id": "r1", "turn_outcome": {"status": "completed"}}, snapshot))
    saved = json.loads(manager.get_checkpoint("s")["summary"])
    assert saved["navigation_authoritative"] is False
    assert saved["exact_excerpts"][0]["quote"] == raw[:14]
    assert manager.get_context_source("s", 1, 99999, 100)["text"] == raw[99999:100099]
    messages, kwargs = fake_model[0]
    assert sum(len(m.content) for m in messages) < checkpoint.MAX_REQUEST_CHARS
    assert json.loads(messages[1].content)["sources"][0]["preview_complete"] is False
    assert kwargs == {"purpose": "turn_checkpoint", "usage_session_id": "s", "max_tokens": 2048}


@pytest.mark.parametrize("mode", ["bad_quote", "truncated", "bad_status", "bad_source", "malformed", "cancelled"])
def test_failed_summary_preserves_prior_checkpoint(store, fake_model, monkeypatch, mode):
    manager, _ = store
    from src.core import task_events
    manager.set_checkpoint("s", 2, "previous")
    before = manager.get_checkpoint("s")
    async def invoke(*args, **kwargs):
        data = value()
        if mode == "bad_quote": data["excerpts"][0]["quote"] = "rewritten"
        if mode == "bad_status": data["task_status"] = "failed"
        if mode == "bad_source": data["excerpts"][0]["source_id"] = "999"
        if mode == "cancelled": raise asyncio.CancelledError()
        return AIMessage(content="{" if mode == "malformed" else json.dumps(data),
            response_metadata={"finish_reason": "length" if mode == "truncated" else "stop"})
    monkeypatch.setattr(task_events, "counted_ainvoke", invoke)
    job = checkpoint.build_checkpoint(manager, "s", {"answer": "done", "request_id": "r1", "turn_outcome": {"status": "completed"}}, before)
    if mode == "cancelled":
        with pytest.raises(asyncio.CancelledError): asyncio.run(job)
    else:
        assert not asyncio.run(job)
    assert manager.get_checkpoint("s") == before


def test_stale_job_cannot_replace_newer_checkpoint(store, fake_model, monkeypatch):
    manager, _ = store
    from src.core import task_events
    before = manager.get_checkpoint("s")
    async def invoke(*args, **kwargs):
        manager.set_checkpoint("s", 2, "concurrent winner")
        return AIMessage(content=json.dumps(value()), response_metadata={"finish_reason": "stop"})
    monkeypatch.setattr(task_events, "counted_ainvoke", invoke)
    assert not asyncio.run(checkpoint.build_checkpoint(manager, "s", {
        "answer": "done", "request_id": "r1", "turn_outcome": {"status": "completed"}}, before))
    assert manager.get_checkpoint("s")["summary"] == "concurrent winner"


def test_source_readback_authentication_and_bounds(store, monkeypatch):
    manager, _ = store
    from src.core import tracing
    monkeypatch.setattr(tracing, "get_session_id", lambda: "another")
    with pytest.raises(PermissionError): manager.get_context_source("s", 1)
    monkeypatch.setattr(tracing, "get_session_id", lambda: "s")
    monkeypatch.setattr(tracing, "get_user_id", lambda: 8)
    with pytest.raises(PermissionError): manager.get_context_source("s", 1)
    monkeypatch.setattr(tracing, "get_user_id", lambda: 7)
    with pytest.raises(ValueError): manager.get_context_source("s", 1, 0, 20000)
    with pytest.raises(LookupError): manager.get_context_source("s", 999)


def test_unfinished_turns_skip_and_broken_tool_pairs_reject():
    for status in ("awaiting_input", "cancelled", "failed"):
        assert not checkpoint.eligible({"answer": "x", "turn_outcome": {"status": status}})
    for rows in ([{"role": "tool", "tool_call_id": "orphan"}],
                 [{"role": "ai", "tool_calls": [{"id": "a"}]}],
                 [{"role": "human"}]):
        with pytest.raises(ValueError): checkpoint.validate_pairs(rows)


def test_prior_precision_survives_large_history_without_repeated_model_quotes(store, fake_model, monkeypatch):
    manager, _ = store
    state = {"answer": "done", "request_id": "r1", "turn_outcome": {"status": "completed"}}
    assert asyncio.run(checkpoint.build_checkpoint(manager, "s", state, manager.get_checkpoint("s")))
    rows = []
    for _ in range(150):
        rows.extend([HumanMessage(content="another question"), AIMessage(content="answer")])
    rows[-1].additional_kwargs["request_id"] = "r2"
    asyncio.run(manager.save_messages("s", rows))
    from src.core import task_events
    async def invoke(model, messages, **kwargs):
        payload = json.loads(messages[1].content)
        assert payload["source_inventory_total"] == 302
        assert payload["source_inventory_preview_complete"] is False
        assert "query" not in payload["host_task_state"]["task_contract"]
        assert payload["host_task_state"]["task_contract"]["obligations"] == ["exact requirement"]
        assert payload["prior_navigation"]["goal"] == "navigation only"
        data = value()
        data["excerpts"] = []
        return AIMessage(content=json.dumps(data), response_metadata={"finish_reason": "stop"})
    monkeypatch.setattr(task_events, "counted_ainvoke", invoke)
    state.update(request_id="r2", task_contract={"query": "long query " * 100000,
                                                "obligations": ["exact requirement"]})
    assert asyncio.run(checkpoint.build_checkpoint(manager, "s", state, manager.get_checkpoint("s")))
    saved = json.loads(manager.get_checkpoint("s")["summary"])
    assert saved["exact_excerpts"] == value()["excerpts"]
    assert len(saved["source_inventory"]) == 302


def test_schedule_detaches_and_freezes_host_state(store, monkeypatch):
    manager, _ = store
    from src.core import account_leases, background
    tasks, states = [], []
    started = asyncio.Event()
    finish = asyncio.Event()
    async def build(manager, session_id, state, snapshot):
        states.append(state)
        started.set()
        await finish.wait()
    def create(lease, coroutine):
        assert lease is sentinel
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task
    sentinel = object()
    monkeypatch.setattr(account_leases, "create_account_task", create)
    monkeypatch.setattr(background, "adopt", lambda task: None)
    monkeypatch.setattr(checkpoint, "build_checkpoint", build)
    async def run():
        state = {"answer": "done", "request_id": "r1", "turn_outcome": {"status": "completed"}, "coverage": {"value": 1}}
        checkpoint.schedule_checkpoint(manager, "s", state, None)
        assert not tasks
        checkpoint.schedule_checkpoint(manager, "s", state, sentinel)
        assert not states  # scheduler returns before the maintenance job runs
        state["coverage"]["value"] = 2
        await started.wait()
        assert states[0]["coverage"]["value"] == 1
        assert not tasks[0].done()
        finish.set()
        await tasks[0]
    asyncio.run(run())


def test_history_append_during_summary_rejects_publication(store, fake_model, monkeypatch):
    manager, _ = store
    from src.core import task_events
    before = manager.get_checkpoint("s")
    async def invoke(*args, **kwargs):
        await manager.save_messages("s", [HumanMessage(content="new active user turn")])
        return AIMessage(content=json.dumps(value()), response_metadata={"finish_reason": "stop"})
    monkeypatch.setattr(task_events, "counted_ainvoke", invoke)
    assert not asyncio.run(checkpoint.build_checkpoint(manager, "s", {
        "answer": "done", "request_id": "r1", "turn_outcome": {"status": "completed"}}, before))
    assert manager.get_checkpoint("s")["summary"] == before["summary"]


def test_legacy_missing_refs_keep_raw_without_model(store, fake_model, monkeypatch):
    manager, _ = store
    from src.core import tool_results
    sources = manager.get_checkpoint_sources("s", 2)
    sources.insert(1, {"role": "ai", "tool_calls": [{"id": "legacy"}]})
    sources.insert(2, {"role": "tool", "tool_call_id": "legacy"})
    monkeypatch.setattr(manager, "get_checkpoint_sources", lambda *a: sources)
    monkeypatch.setattr(tool_results, "pin_references", lambda *a: False)
    before = manager.get_checkpoint("s")
    assert not asyncio.run(checkpoint.build_checkpoint(manager, "s", {
        "answer": "done", "request_id": "r1", "turn_outcome": {"status": "completed"}}, before))
    assert not fake_model
    assert manager.get_checkpoint("s") == before

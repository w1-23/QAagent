"""HTTP request claims with temporary auth/session DBs and no model/network."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
import threading
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from src.test_account_deletion import _configure, _add_user
    _configure(tmp_path, monkeypatch)
    from src import auth, admin_audit
    from src.api import main as api
    from src.core import account_leases as leases, jobs, business_logger, diag, progress_bus
    from src.core.chat_capacity import ChatCapacity
    from src.graph import graph as graph_module
    from src.session import files
    from src.core import llm_pool
    from src.retrieval import runtime as retrieval_runtime

    owner = _add_user(username="claim_owner")
    other = _add_user(username="other_owner")
    state = SimpleNamespace(api=api, auth=auth, jobs=jobs, leases=leases, owner=owner,
                            other=other, user=auth.user_by_id(owner), graph_calls=0,
                            blocked=False, entered=asyncio.Event(), finish=asyncio.Event())
    monkeypatch.setattr(api, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(api.session_manager, "db_path", str(tmp_path / "state/sessions.db"))
    api.session_manager._init_db_sync()
    monkeypatch.setattr(api, "current_user", lambda request: state.user)
    monkeypatch.setattr(api, "_running_graph_tasks", {})
    monkeypatch.setattr(api, "_job_tokens", {})
    monkeypatch.setattr(api, "_pending_graph_starts", set())
    monkeypatch.setattr(auth, "check_rate_limit", lambda *a, **k: True)
    state.reserve = Mock(wraps=auth.reserve_chat_request)
    state.refund = Mock(wraps=auth.release_chat_request)
    monkeypatch.setattr(auth, "reserve_chat_request", state.reserve)
    monkeypatch.setattr(auth, "release_chat_request", state.refund)
    state.upload = Mock(return_value=["offline-upload"])
    state.link = Mock()
    state.parse = Mock(return_value=("", [], []))
    monkeypatch.setattr(api, "_register_session_files", state.upload)
    monkeypatch.setattr(files, "link_turn_files", state.link)
    monkeypatch.setattr(api, "_prepare_chat_attachments", state.parse)
    monkeypatch.setattr(admin_audit, "start_request", lambda **k: None)
    monkeypatch.setattr(admin_audit, "finish_request", lambda **k: None)
    monkeypatch.setattr(business_logger, "blog", lambda *a, **k: None)
    monkeypatch.setattr(diag, "diag", lambda *a, **k: None)
    monkeypatch.setattr(progress_bus, "log_sse_frame", lambda *a, **k: None)
    monkeypatch.setattr(api, "chat_capacity", ChatCapacity())
    monkeypatch.setattr(llm_pool, "get_llm", Mock(side_effect=AssertionError("live model forbidden")))
    monkeypatch.setattr(retrieval_runtime, "get_rag", Mock(side_effect=AssertionError("live retrieval forbidden")))

    class Graph:
        async def astream(self, *args, **kwargs):
            state.graph_calls += 1
            state.graph_lease = leases.current_account_lease()
            state.entered.set()
            if state.blocked:
                await state.finish.wait()
            yield {"supervisor": {"answer": "offline result", "tool_status": []}}
            yield {"session_save": {"_trace": {"history_saved": False, "generation_ms": 1}}}

    state.build = Mock(return_value=Graph())
    monkeypatch.setattr(graph_module, "build_graph", state.build)
    return state


def _payload(**changes):
    return {"query": "offline request", "session_id": None, "client_request_id": "stable-client-key",
            "attachments": [{"name": "input.txt", "type": "text/plain", "data": "data:text/plain;base64,QQ=="}],
            "file_refs": [], "visualization_enabled": True, **changes}


async def _post(rt, payload=None):
    return await rt.api.chat_v2(rt.api.ChatRequest(**copy.deepcopy(payload or _payload())), object())


async def _drain(response):
    async def collect():
        return [event async for event in response.body_iterator]
    events = await asyncio.wait_for(collect(), 5)
    await asyncio.sleep(0)  # Let the graph done callback release its lease.
    return events


def _json(response):
    return json.loads(response.body)


def _jobs(rt):
    with closing(sqlite3.connect(rt.jobs._DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT * FROM task_jobs")]


def _usage(rt):
    with closing(sqlite3.connect(rt.auth.DB_PATH)) as conn:
        return conn.execute("SELECT COALESCE(sum(request_count),0) FROM daily_usage WHERE user_id=?",
                            (rt.owner,)).fetchone()[0]


def test_concurrent_duplicate_only_reserves_uploads_parses_and_executes_once(runtime):
    rt = runtime

    async def scenario():
        responses = await asyncio.gather(_post(rt), _post(rt))
        stream = next(response for response in responses if hasattr(response, "body_iterator"))
        replay = _json(next(response for response in responses if not hasattr(response, "body_iterator")))
        events = await _drain(stream)
        job_event = json.loads(next(e["data"] for e in events if e["event"] == "job"))
        assert replay["code"] == "existing_job" and replay["replayed"]
        assert replay["job_id"] == job_event["job_id"]
        assert replay["session_id"] == job_event["session_id"]
        assert rt.reserve.call_count == rt.upload.call_count == rt.link.call_count == 1
        assert rt.parse.call_count == rt.graph_calls == rt.build.call_count == 1
        assert _usage(rt) == 1 and len(_jobs(rt)) == 1
        assert rt.graph_lease.owner == rt.owner
        assert rt.leases.active_count(rt.owner) == 0
        with closing(sqlite3.connect(rt.jobs._DB_PATH)) as conn:
            assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1

    asyncio.run(scenario())


def test_running_duplicate_returns_pointer_without_cancelling_or_faking_done(runtime):
    rt = runtime
    rt.blocked = True

    async def scenario():
        response = await _post(rt)
        await asyncio.wait_for(rt.entered.wait(), 5)
        original = next(iter(rt.api._running_graph_tasks.values()))
        replay = _json(await _post(rt))
        assert replay["status"] == "running" and replay["terminal_snapshot"] == {}
        assert "answer" not in replay and "turn_status" not in replay
        assert not original.done()
        status = await rt.api.get_job_status(replay["job_id"], object())
        assert status["status"] == "running"
        assert rt.reserve.call_count == rt.upload.call_count == 1
        rt.finish.set()
        await _drain(response)
        assert rt.leases.active_count(rt.owner) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"query": "offline request changed"}, {"visualization_enabled": False},
    {"file_refs": ["different-ref"]}, {"session_id": "different-session"},
    {"attachments": [{"name": "input.txt", "type": "text/plain", "data": "data:text/plain;base64,Qg=="}]},
])
def test_payload_conflict_is_409_before_quota_or_upload(runtime, change):
    rt = runtime

    async def scenario():
        await _drain(await _post(rt))
        with pytest.raises(rt.api.HTTPException) as error:
            await _post(rt, _payload(**change))
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "idempotency_conflict"
        assert rt.reserve.call_count == rt.upload.call_count == rt.graph_calls == 1
        assert len(_jobs(rt)) == 1 and rt.leases.active_count(rt.owner) == 0

    asyncio.run(scenario())


def test_completed_replay_uses_original_sid_and_stored_terminal_snapshot(runtime):
    rt = runtime

    async def scenario():
        await _drain(await _post(rt))
        original = _jobs(rt)[0]
        replay = _json(await _post(rt))
        assert replay["session_id"] == original["session_id"]
        assert replay["request_id"] == original["request_id"]
        assert replay["terminal_snapshot"] == rt.jobs.decode_terminal_snapshot(original)
        assert replay["status"] == "completed" and "answer" not in replay
        assert rt.reserve.call_count == rt.upload.call_count == rt.graph_calls == 1

    asyncio.run(scenario())


def test_owner_isolation_and_recovery_pointer_ownership(runtime):
    rt = runtime

    async def scenario():
        await _drain(await _post(rt))
        first = _jobs(rt)[0]
        rt.user = rt.auth.user_by_id(rt.other)
        with pytest.raises(rt.api.HTTPException) as error:
            await rt.api.get_job_status(first["job_id"], object())
        assert error.value.status_code == 404
        with pytest.raises(rt.api.HTTPException):
            await rt.api.cancel_chat_job(first["job_id"], object())
        await _drain(await _post(rt))
        rows = _jobs(rt)
        assert len(rows) == 2 and rows[0]["job_id"] != rows[1]["job_id"]
        assert {row["owner_user_id"] for row in rows} == {rt.owner, rt.other}
        assert rt.graph_calls == rt.reserve.call_count == 2

    asyncio.run(scenario())


def test_quota_rejection_is_terminal_and_replay_does_not_reserve_again(runtime):
    rt = runtime
    rt.reserve.side_effect = lambda user: (False, 0)

    async def scenario():
        with pytest.raises(rt.api.HTTPException) as error:
            await _post(rt)
        assert error.value.status_code == 429
        replay = _json(await _post(rt))
        assert replay["status"] == "failed" and replay["current_step"] == "admission_failed"
        # No session exists yet, but the persistent owner still authorizes this pointer.
        fact = await rt.api.get_job_status(replay["job_id"], object())
        assert fact["status"] == "failed"
        assert fact["terminal_snapshot"]["history_saved"] is False
        assert rt.reserve.call_count == 1 and rt.refund.call_count == 0
        rt.upload.assert_not_called()
        assert rt.leases.active_count(rt.owner) == 0
        assert not rt.api._running_graph_tasks and not rt.api._job_tokens

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["upload", "parse"])
def test_attachment_failure_is_terminal_and_refunded_once(runtime, stage):
    rt = runtime
    target = rt.upload if stage == "upload" else rt.parse
    target.side_effect = rt.api.HTTPException(status_code=400, detail="offline attachment rejection")

    async def scenario():
        if stage == "upload":
            with pytest.raises(rt.api.HTTPException):
                await _post(rt)
        else:
            events = await _drain(await _post(rt))
            assert any(e["event"] == "error" for e in events)
        replay = _json(await _post(rt))
        assert replay["status"] == "failed"
        assert rt.reserve.call_count == rt.refund.call_count == 1
        assert rt.upload.call_count == 1 and rt.graph_calls == 0 and _usage(rt) == 0
        assert rt.leases.active_count(rt.owner) == 0
        assert not rt.api._running_graph_tasks and not rt.api._job_tokens

    asyncio.run(scenario())


def test_cancel_during_upload_marks_claim_and_retains_thread_lease(runtime):
    rt = runtime
    entered, finish, finished = threading.Event(), threading.Event(), threading.Event()

    def upload(*args):
        entered.set()
        try:
            assert finish.wait(5)
            return ["late-upload"]
        finally:
            finished.set()

    rt.upload.side_effect = upload

    async def scenario():
        task = asyncio.create_task(_post(rt))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            replay = _json(await _post(rt))
            await rt.api.cancel_chat_job(replay["job_id"], object())
            with pytest.raises(asyncio.CancelledError):
                await task
            assert _jobs(rt)[0]["status"] == "cancelled"
            assert rt.leases.active_count(rt.owner) > 0
            assert rt.refund.call_count == 1 and rt.graph_calls == 0
            assert _json(await _post(rt))["status"] == "cancelled"
            assert rt.upload.call_count == 1
        finally:
            finish.set()
            assert await asyncio.to_thread(finished.wait, 3)
            for _ in range(10):
                await asyncio.sleep(0)

    asyncio.run(scenario())
    assert rt.leases.active_count(rt.owner) == 0


def test_http_replay_content_type_pointer_and_conflict_contract(runtime):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    rt = runtime
    app = FastAPI()  # No production lifespan, server or network.
    app.add_api_route("/api/v2/chat", rt.api.chat_v2, methods=["POST"])
    app.add_api_route("/jobs/{job_id}", rt.api.get_job_status, methods=["GET"])
    with TestClient(app) as client:
        first = client.post("/api/v2/chat", json=_payload())
        assert first.status_code == 200 and "text/event-stream" in first.headers["content-type"]
        assert "event: done" in first.text
        replay = client.post("/api/v2/chat", json=_payload())
        assert replay.status_code == 200 and "application/json" in replay.headers["content-type"]
        fact = client.get(replay.json()["status_url"])
        assert fact.status_code == 200 and fact.json()["status"] == "completed"
        conflict = client.post("/api/v2/chat", json=_payload(query="changed"))
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert rt.graph_calls == rt.reserve.call_count == 1
    assert rt.leases.active_count(rt.owner) == 0

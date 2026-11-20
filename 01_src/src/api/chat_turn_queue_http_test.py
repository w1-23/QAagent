"""Queue HTTP and execution lifecycle with temporary stores and a controlled graph."""
import asyncio
from contextlib import asynccontextmanager
from time import monotonic, sleep
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
import pytest

from src import auth
from src.api import main
from src.core import chat_turn_queue as queue, jobs
from src.core.chat_capacity import ChatCapacity
from src.graph import graph
from src.session import files, manager


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(jobs, "_DB_PATH", db)
    monkeypatch.setattr(manager, "DB_PATH", db)
    monkeypatch.setattr(manager, "STATE_DIR", tmp_path)
    monkeypatch.setattr(manager.SessionManager, "_instance", None)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "files")
    monkeypatch.setattr(main, "chat_capacity", ChatCapacity())
    sessions = manager.SessionManager()
    monkeypatch.setattr(main, "session_manager", sessions)
    auth.init_store()
    files.init_store()
    password = "Fixture-only-8043!"
    owner = auth.create_local_account("queue-owner", password)
    auth.create_local_account("queue-other", password)
    sid = "queue-http-session"
    asyncio.run(sessions.get_or_create_session(sid, owner_user_id=owner))
    queue.ensure_schema()

    started = []
    released = {"legacy", "last"}

    class ControlledGraph:
        async def astream(self, state, **_):
            cid = state["client_request_id"]
            started.append(cid)
            deadline = monotonic() + 8
            while cid not in released:
                if monotonic() > deadline:
                    raise TimeoutError("fixture release missing")
                await asyncio.sleep(0.01)
            answer = "answer to " + state["query"]
            yield {"supervisor": {"answer": answer, "turn_outcome": {"status": "completed"}}}
            await sessions.save_messages(state["session_id"],
                [HumanMessage(content=state["query"]), AIMessage(content=answer)],
                idempotency_key=state["idempotency_key"])
            yield {"session_save": {"_trace": {"history_saved": True}}}

    monkeypatch.setattr(graph, "build_graph", lambda _: ControlledGraph())

    @asynccontextmanager
    async def lifespan(app):
        await main._reconcile_turn_queue_startup()
        app.state.turn_queue_wake = asyncio.Event()
        app.state.turn_queue_consumer = asyncio.create_task(main._consume_chat_turns(app.state.turn_queue_wake))
        try:
            yield
        finally:
            app.state.turn_queue_consumer.cancel()
            await asyncio.gather(app.state.turn_queue_consumer, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)
    app.middleware("http")(main.account_auth_guard)
    root = "/api/v2/session/{session_id}"
    for path, handler, method in (
        (root + "/turn-queue", main.enqueue_chat_turn, "POST"),
        (root + "/turn-queue", main.list_chat_turns, "GET"),
        (root + "/turn-queue/{client_request_id}", main.cancel_chat_turn, "DELETE"),
        (root + "/task-by-request/{client_request_id}", main.discover_chat_job, "GET"),
        ("/api/v2/chat", main.chat_v2, "POST"),
        ("/jobs/{job_id}/events", main.get_job_events, "GET"),
    ):
        app.add_api_route(path, handler, methods=[method])
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _, session_token = auth.login("queue-owner", password)
        client.cookies.set("citrus_auth", session_token)
        yield SimpleNamespace(client=client, sid=sid, started=started, released=released,
                              owner=owner, path=f"/api/v2/session/{sid}/turn-queue",
                              other=auth.login("queue-other", password)[1])


def wait_for(predicate):
    deadline = monotonic() + 6
    while not predicate():
        if monotonic() >= deadline:
            pytest.fail("expected queue transition did not happen")
        sleep(0.025)


def send(runtime, cid, query):
    response = runtime.client.post(runtime.path, json={"query": query, "client_request_id": cid})
    assert response.status_code == 202, response.text
    return response.json()


def state(runtime, cid):
    row = queue.get_turn(owner_user_id=runtime.owner, session_id=runtime.sid, client_request_id=cid)
    return row["state"] if row else None


def test_background_fifo_cancel_and_replay(runtime):
    first = send(runtime, "first", "first message")
    wait_for(lambda: runtime.started == ["first"])
    middle = send(runtime, "middle", "middle message")
    last = send(runtime, "last", "last message")
    assert middle["queue_seq"] + 1 == last["queue_seq"]
    assert state(runtime, "last") == "queued"
    cancelled = runtime.client.delete(runtime.path + "/middle")
    assert cancelled.status_code == 200, cancelled.text
    assert state(runtime, "first") == "running"
    runtime.released.add("first")
    wait_for(lambda: state(runtime, "last") == "terminal")
    assert runtime.started == ["first", "last"]
    for turn in (first, last):
        events = runtime.client.get(f'/jobs/{turn["job_id"]}/events').json()["events"]
        terminal = [row for row in events if row["event"] == "done"]
        assert len(terminal) == 1
        assert terminal[0]["data"]["history_saved"] is True


def test_rediscovery_idempotency_and_owner_scope(runtime):
    first = send(runtime, "last", "original")
    assert send(runtime, "last", "original")["job_id"] == first["job_id"]
    conflict = runtime.client.post(runtime.path, json={"query": "changed", "client_request_id": "last"})
    assert conflict.status_code == 409, conflict.text
    discover = runtime.path.replace("turn-queue", "task-by-request") + "/last"
    assert runtime.client.get(discover).json()["job_id"] == first["job_id"]
    runtime.client.cookies.set("citrus_auth", runtime.other)
    assert runtime.client.get(discover).status_code == 404
    assert runtime.client.get(runtime.path).status_code in {403, 404}
    assert runtime.client.get(f'/jobs/{first["job_id"]}/events').status_code == 404


def test_legacy_stream_observes_one_persistent_job(runtime):
    body = {"query": "legacy question", "session_id": runtime.sid, "client_request_id": "legacy"}
    response = runtime.client.post("/api/v2/chat", json=body)
    assert response.status_code == 200, response.text
    assert "event: done" in response.text
    replay = runtime.client.post("/api/v2/chat", json=body)
    assert replay.json()["replayed"] is True
    assert runtime.started == ["legacy"]
    assert replay.json()["job_id"] in response.text

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
from src.core import chat_turn_queue as queue, jobs, user_questions
from src.core.account_leases import current_account_lease
from src.core.tracing import get_job_id
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
    asking, hold_cleanup, held = set(), set(), {}

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
            output = {"answer": answer, "turn_outcome": {"status": "completed"}}
            if cid in asking:
                question = user_questions.create_question(get_job_id(), owner_user_id=owner, session_id=sid,
                    prompt="Which option?", options=[], allow_text=True, context={"query": state["query"]})
                output.update(awaiting_input=True, pending_question=question)
                if cid in hold_cleanup:
                    held[cid] = current_account_lease().retain()
            yield {"supervisor": output}
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
        ("/api/v2/chat/jobs/{job_id}/questions", main.get_task_questions, "GET"),
        ("/api/v2/chat/jobs/{job_id}/questions/{question_id}/answer", main.answer_task_question, "POST"),
    ):
        app.add_api_route(path, handler, methods=[method])
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        _, session_token = auth.login("queue-owner", password)
        client.cookies.set("citrus_auth", session_token)
        try:
            yield SimpleNamespace(client=client, sid=sid, started=started, released=released,
                                  owner=owner, path=f"/api/v2/session/{sid}/turn-queue",
                                  asking=asking, held=held, hold_cleanup=hold_cleanup,
                                  other=auth.login("queue-other", password)[1])
        finally:
            for lease in held.values():
                lease.release()


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



def answer_question(runtime, job_id, *, cancelled=False):
    url = f"/api/v2/chat/jobs/{job_id}/questions"
    questions = runtime.client.get(url).json()["questions"]
    assert len(questions) == 1
    question = questions[0]
    response = runtime.client.post(f'{url}/{question["id"]}/answer', json={
        "version": question["version"], "client_request_id": "fixture-answer",
        "text": "a clarification" if not cancelled else "", "cancelled": cancelled,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_two_questions_preserve_original_queue_position_through_cleanup(runtime):
    runtime.asking.add("first")
    runtime.hold_cleanup.add("first")
    root = send(runtime, "first", "question task")
    follower = send(runtime, "last", "independent task")
    runtime.released.add("first")
    wait_for(lambda: jobs.get_job(root["job_id"])["status"] == "awaiting_input")
    assert state(runtime, "first") == "running"
    answer = answer_question(runtime, root["job_id"])
    assert answer["dispatch_pending"] is True
    assert answer_question(runtime, root["job_id"])["child_job_id"] == answer["child_job_id"]
    child = answer["child_job_id"]
    child_cid = jobs.get_job(child)["client_request_id"]
    runtime.asking.add(child_cid)
    assert jobs.get_job(child)["status"] == "queued"
    assert runtime.started == ["first"]
    runtime.held.pop("first").release()
    wait_for(lambda: runtime.started == ["first", child_cid])
    assert state(runtime, "last") == "queued"
    runtime.released.add(child_cid)
    wait_for(lambda: state(runtime, "first") == "awaiting_input")
    reply = answer_question(runtime, child)
    grandchild = reply["child_job_id"]
    grandchild_cid = jobs.get_job(grandchild)["client_request_id"]
    runtime.released.add(grandchild_cid)
    wait_for(lambda: state(runtime, "last") == "terminal")
    assert runtime.started == ["first", child_cid, grandchild_cid, "last"]
    turns = {t["job_id"]: t for t in runtime.client.get(runtime.path).json()["turns"]}
    assert turns[root["job_id"]]["state"] == turns[follower["job_id"]]["state"] == "terminal"
    assert turns[root["job_id"]]["active_child_job_id"] == grandchild
    events = runtime.client.get(f"/jobs/{grandchild}/events").json()["events"]
    terminal = [row for row in events if row["event"] == "done"]
    assert len(terminal) == 1 and terminal[0]["data"]["history_saved"] is True


def test_cancel_question_releases_head_without_creating_child(runtime):
    runtime.asking.add("first")
    root = send(runtime, "first", "question task")
    send(runtime, "last", "independent task")
    runtime.released.add("first")
    wait_for(lambda: state(runtime, "first") == "awaiting_input")
    assert not answer_question(runtime, root["job_id"], cancelled=True)["child_job_id"]
    wait_for(lambda: state(runtime, "last") == "terminal")
    assert runtime.started == ["first", "last"]
    settled = queue.get_turn(owner_user_id=runtime.owner, session_id=runtime.sid, client_request_id="first")
    assert settled["state"] == "terminal" and settled["clear_reason"] == "question_cancelled"


@pytest.mark.parametrize("claim_before_restart", [False, True])
def test_startup_recovers_durable_answer_or_fences_unknown_dispatch(runtime, monkeypatch, claim_before_restart):
    from time import time

    advance = main._advance_question_turn

    async def pause_dispatch(*_):
        return

    monkeypatch.setattr(main, "_advance_question_turn", pause_dispatch)
    runtime.asking.add("first")
    root = send(runtime, "first", "question task")
    runtime.released.add("first")
    wait_for(lambda: state(runtime, "first") == "awaiting_input")
    send(runtime, "last", "after restart")
    child = answer_question(runtime, root["job_id"])["child_job_id"]
    child_cid = jobs.get_job(child)["client_request_id"]
    runtime.released.add(child_cid)
    assert jobs.get_job(child)["status"] == "queued"
    if claim_before_restart:
        waiting = queue.find_turn_by_job(owner_user_id=runtime.owner, session_id=runtime.sid, job_id=root["job_id"])
        queue.resume_continuation(**main._turn_fence(waiting), active_child_job_id=child, deadline=time() + 60)
        jobs.mark_interrupted_jobs()
    runtime.client.portal.call(main._reconcile_turn_queue_startup)
    monkeypatch.setattr(main, "_advance_question_turn", advance)
    if claim_before_restart:
        assert state(runtime, "first") == "reconcile_required"
        assert state(runtime, "last") == "queued"
        assert runtime.started == ["first"]
    else:
        wait_for(lambda: state(runtime, "last") == "terminal")
        assert runtime.started == ["first", child_cid, "last"]


def test_startup_settles_completed_child(runtime, monkeypatch):
    settled_child = []
    settle = main._settle_dispatched_turn

    def interrupt_settlement(turn, job_id):
        if turn["active_child_job_id"] == job_id:
            settled_child.append(job_id)
        else:
            settle(turn, job_id)

    monkeypatch.setattr(main, "_settle_dispatched_turn", interrupt_settlement)
    runtime.asking.add("first")
    root = send(runtime, "first", "question task")
    runtime.released.add("first")
    wait_for(lambda: state(runtime, "first") == "awaiting_input")
    follower = send(runtime, "last", "after restart")
    child = answer_question(runtime, root["job_id"])["child_job_id"]
    child_cid = jobs.get_job(child)["client_request_id"]
    runtime.released.add(child_cid)
    wait_for(lambda: settled_child == [child])
    assert state(runtime, "first") == "running"
    assert state(runtime, "last") == "queued"
    assert jobs.decode_terminal_snapshot(jobs.get_job(child))["history_saved"] is True
    monkeypatch.setattr(main, "_settle_dispatched_turn", settle)
    runtime.client.portal.call(main._reconcile_turn_queue_startup)
    wait_for(lambda: state(runtime, "last") == "terminal")
    assert state(runtime, "first") == "terminal"
    assert runtime.started == ["first", child_cid, "last"]
    for job_id in (child, follower["job_id"]):
        events = runtime.client.get(f"/jobs/{job_id}/events").json()["events"]
        assert sum(row["event"] == "done" for row in events) == 1

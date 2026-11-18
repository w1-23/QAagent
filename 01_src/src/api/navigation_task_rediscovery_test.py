"""Rediscover tasks after navigation without fabricating or duplicating history."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from src.api.chat_idempotency_test import runtime, _post, _drain  # noqa: F401
from src.api.question_continuation_test import pending
from src.core import task_events, user_questions


async def session(rt, sid="navigation-session", owner=None):
    await rt.api.session_manager.get_or_create_session(
        sid, owner_user_id=rt.owner if owner is None else owner)
    return sid


def claim(rt, sid, key="navigation-key", *, owner=None, status="running", rid="server-request"):
    job = rt.jobs.claim_job(sid, rid, owner_user_id=rt.owner if owner is None else owner,
                            client_request_id=key, payload={})
    assert rt.jobs.update_job(job.job_id, status=status)
    return job.job_id


async def save_turn(rt, sid, key):
    assert await rt.api.session_manager.save_messages(
        sid, [HumanMessage(content="same actual question"), AIMessage(content="saved answer")],
        idempotency_key=f"crid:{key}")


def navigation_app(rt):
    app = FastAPI()
    app.add_api_route("/api/v2/sessions", rt.api.list_sessions, methods=["GET"])
    app.add_api_route("/api/v2/session/{session_id}/messages", rt.api.session_messages, methods=["GET"])
    return app


@pytest.mark.parametrize("status", [
    "running", "queued", "detached", "cancel_requested", "awaiting_input",
    "completed", "failed", "cancelled",
])
def test_empty_history_returns_exact_task_identity_without_invented_query(runtime, status):
    rt = runtime
    sid = asyncio.run(session(rt))
    job_id = claim(rt, sid, status=status)
    snapshot = {"display_status": "failed", "history_saved": False, "notices": ["persisted notice"]}
    assert rt.jobs.save_terminal_snapshot(job_id, snapshot)
    context = {"logical_call_id": "observed-call", "estimated_tokens": 1234}
    event = task_events.append_event(job_id, "context_snapshot", context)
    context = {**context, "seq": event["seq"]}
    claim(rt, sid, owner=rt.other, key="foreign-key")
    claim(rt, "other-session", key="other-session-key")
    rt.jobs.create_job(sid, "unowned-legacy-request")

    with TestClient(navigation_app(rt)) as client:
        response = client.get(f"/api/v2/session/{sid}/messages")
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["messages"] == [] and view["count"] == 0
        assert not view["has_more"] and view["next_before"] == 0
        assert len(view["turns"]) == 1
        turn = view["turns"][0]
        assert turn["turn_request_id"] == "navigation-key"
        assert turn["job_id"] == job_id and turn["job_status"] == status
        assert turn["history_pending"] and turn["replay_cursor"] == 0
        assert turn["user"] is None and turn["answer"] is None
        assert turn["message_ids"] == [] and turn["trace"] == []
        assert turn["terminal_snapshot"] == rt.jobs.decode_terminal_snapshot(rt.jobs.get_job(job_id))
        assert turn["context_snapshot"] == context
        assert view["context"] == context
        rt.user = rt.auth.user_by_id(rt.other)
        assert client.get(f"/api/v2/session/{sid}/messages").status_code == 404


def test_running_stream_survives_navigation_and_is_discoverable_before_history_save(runtime):
    rt = runtime
    rt.blocked = True

    async def scenario():
        response = await _post(rt)
        await asyncio.wait_for(rt.entered.wait(), 5)
        job_id = next(iter(rt.api._running_graph_tasks))
        running = rt.api._running_graph_tasks[job_id]
        sid = rt.jobs.get_job(job_id)["session_id"]
        try:
            view = await rt.api.session_messages(sid, object())
            assert view["messages"] == []
            assert [t["job_id"] for t in view["turns"]] == [job_id]
            assert view["turns"][0]["job_status"] == "running"
            assert not running.done() and rt.graph_calls == 1
            sessions = await rt.api.list_sessions(object())
            listed = next(s for s in sessions["sessions"] if s["session_id"] == sid)
            assert listed["msg_count"] == 0 and listed["has_tasks"] is True
        finally:
            rt.finish.set()
            await _drain(response)
        view = await rt.api.session_messages(sid, object())
        assert len(view["turns"]) == 1 and view["turns"][0]["job_status"] == "completed"
        assert rt.graph_calls == 1

    asyncio.run(scenario())


def test_saved_requests_anywhere_in_history_never_become_pending_pagination_duplicates(runtime):
    rt = runtime

    async def scenario():
        sid = await session(rt)
        old = claim(rt, sid, key="old-client", rid="old-server", status="completed")
        await save_turn(rt, sid, "old-client")
        latest = claim(rt, sid, key="latest-client", rid="latest-server", status="completed")
        await save_turn(rt, sid, "latest-client")
        # Legacy saved turns can identify the server request rather than client key.
        legacy = claim(rt, sid, key="legacy-client", rid="legacy-server", status="failed")
        await save_turn(rt, sid, "legacy-server")
        waiting = claim(rt, sid, key="unsaved-key", rid="unsaved-server", status="queued")
        request_ids = await rt.api.session_manager.get_user_message_request_ids(sid)
        assert request_ids == ["old-client", "latest-client", "legacy-server"]
        assert all(isinstance(key, str) for key in request_ids)
        newest = await rt.api.session_messages(sid, object(), limit=1)
        assert newest["has_more"] and newest["next_before"] > 0
        assert [t["job_id"] for t in newest["turns"]] == [legacy, waiting]
        assert newest["turns"][0]["answer"]["content"] == "saved answer"
        older = await rt.api.session_messages(sid, object(), limit=2, before=newest["next_before"])
        assert [t["job_id"] for t in older["turns"]] == [latest]
        assert not any(t.get("history_pending") for t in older["turns"])
        oldest = await rt.api.session_messages(sid, object(), before=older["next_before"])
        assert [t["job_id"] for t in oldest["turns"]] == [old]
        assert not any(t.get("history_pending") for t in oldest["turns"])
        # Once the waiting task saves its human row, the real turn replaces it.
        await save_turn(rt, sid, "unsaved-key")
        restored = await rt.api.session_messages(sid, object(), limit=1)
        assert [t["job_id"] for t in restored["turns"]] == [waiting]
        assert not restored["turns"][0].get("history_pending")

    asyncio.run(scenario())


def test_unsaved_awaiting_input_restores_question_and_continuation_stays_unique(runtime):
    rt = runtime

    async def scenario():
        sid = await session(rt, "question-session")
        parent, question = pending(rt)
        view = await rt.api.session_messages(sid, object())
        assert view["turns"][0]["job_id"] == parent
        assert view["turns"][0]["question"]["id"] == question["id"]
        assert view["turns"][0]["user"] is None
        await save_turn(rt, sid, "parent-request")
        answered = user_questions.answer_question(parent, question["id"], owner_user_id=rt.owner,
            session_id=sid, version=1, text="actual clarification reply", client_request_id="answer-key")
        child = answered["child_job_id"]
        view = await rt.api.session_messages(sid, object())
        assert [t["job_id"] for t in view["turns"]] == [parent, child]
        continuation = view["turns"][1]
        assert continuation["history_pending"] and "actual clarification reply" in continuation["user"]["content"]
        assert continuation["parent_job_id"] == parent
        await save_turn(rt, sid, rt.jobs.get_job(child)["client_request_id"])
        # Child outside the page must not be appended when parent is paged back.
        older = await rt.api.session_messages(sid, object(), before=view["messages"][-1]["message_id"] + 1)
        assert [t["job_id"] for t in older["turns"]] == [parent]
        assert not any(t.get("history_pending") for t in older["turns"])
        full = await rt.api.session_messages(sid, object())
        assert [t["job_id"] for t in full["turns"]] == [parent, child]
        assert not any(t.get("history_pending") for t in full["turns"])
        # Full-history suppression also covers a saved child outside a latest
        # page containing its parent (e.g. imported/reordered historical turns).
        parent_turn = rt.api._new_history_turn("parent-request")
        scoped = rt.api._history_with_jobs([parent_turn], sid, rt.owner,
            saved_request_ids=[rt.jobs.get_job(child)["client_request_id"], "parent-request"])
        assert [t["job_id"] for t in scoped] == [parent]

    asyncio.run(scenario())


def test_session_list_marks_empty_task_sessions_without_foreign_task_leakage(runtime):
    rt = runtime
    for sid in ("has-task", "no-task", "foreign-job-only"):
        asyncio.run(session(rt, sid))
    asyncio.run(session(rt, "foreign-session", rt.other))
    claim(rt, "has-task", status="failed")
    claim(rt, "foreign-job-only", key="foreign-job", owner=rt.other)
    claim(rt, "foreign-session", key="outside-owned-list")
    with TestClient(navigation_app(rt)) as client:
        response = client.get("/api/v2/sessions")
        assert response.status_code == 200, response.text
        sessions = {s["session_id"]: s for s in response.json()["sessions"]}
        assert set(sessions) == {"has-task", "no-task", "foreign-job-only"}
        assert sessions["has-task"]["has_tasks"] is True
        assert sessions["no-task"]["has_tasks"] is False
        assert sessions["foreign-job-only"]["has_tasks"] is False
        assert all(s["msg_count"] == 0 for s in sessions.values())


@pytest.mark.parametrize("values", [{}, {"AGENT_DETACH_GRACE_SEC": 0}])
def test_missing_or_zero_detach_grace_does_not_schedule_cancellation(monkeypatch, values):
    from src.api import main as api
    monkeypatch.setattr(api, "settings", SimpleNamespace(**values))
    scheduled = Mock()
    monkeypatch.setattr(api.asyncio, "create_task", scheduled)
    api._schedule_detach_grace("navigation-task", object())
    scheduled.assert_not_called()

"""Offline deletion/admission races; every identity and database is temporary."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src import auth
from src.core import account_leases as leases
from src.test_account_deletion import _configure


@pytest.fixture
def account(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    user, token, error = auth.register("lease_account", "offline-password")
    assert not error
    return SimpleNamespace(user=user, owner=user["user_id"], token=token, root=tmp_path)


@pytest.fixture
def api(account, monkeypatch):
    # Import only after config/auth/files/jobs/figures have been redirected.
    from src.api import main
    monkeypatch.setattr(main.session_manager, "db_path", str(account.root / "state/sessions.db"))
    main.session_manager._init_db_sync()
    return main


def test_resource_finalizer_waits_for_retained_children_and_keeps_admission_barrier(account):
    parent = leases.acquire_account_lease(account.owner)
    child = parent.retain()
    called = []
    parent.add_finalizer(lambda: called.append(leases.active_count(account.owner)))
    parent.release()
    assert called == []
    assert leases.active_count(account.owner) == 1
    child.release()
    assert called == [1]
    assert leases.active_count(account.owner) == 0
    child.release()
    assert called == [1]


def test_admission_covers_reservation_to_job_gap_and_is_closed_on_delete(account):
    lease = leases.acquire_account_lease(account.owner)
    try:
        with pytest.raises(leases.AccountDeletionPending):
            auth.delete_user_account(account.owner, raise_if_busy=True)
        assert auth.user_by_id(account.owner)["active"] is False
        assert leases.active_count(account.owner) == 1
        with pytest.raises(leases.AccountUnavailable):
            leases.acquire_account_lease(account.owner)
    finally:
        lease.release()
    assert auth.delete_user_account(account.owner)
    with pytest.raises(leases.AccountUnavailable):
        leases.acquire_account_lease(account.owner)


def test_cancelled_thread_observer_does_not_release_running_worker(account):
    started, finish = threading.Event(), threading.Event()

    def worker():
        started.set()
        assert finish.wait(5)
        return "actual completion"

    async def scenario(pool):
        parent = leases.acquire_account_lease(account.owner)
        task = asyncio.create_task(leases.run_account_thread(parent, worker, executor=pool))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            parent.release()
            assert leases.active_count(account.owner) == 1
            assert auth.delete_user_account(account.owner) is False
            assert auth.user_by_id(account.owner)["active"] is False
        finally:
            finish.set()
            parent.release()

    with ThreadPoolExecutor(max_workers=1) as pool:
        asyncio.run(scenario(pool))
    assert leases.active_count(account.owner) == 0
    assert auth.delete_user_account(account.owner)


def test_nested_session_creation_keeps_lease_when_observer_is_cancelled(account):
    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()
        parent = leases.acquire_account_lease(account.owner)

        async def nested():
            entered.set()
            await finish.wait()

        observer = asyncio.create_task(leases.run_account_async(parent, nested()))
        await entered.wait()
        observer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await observer
        parent.release()
        assert leases.active_count(account.owner) == 1
        assert auth.delete_user_account(account.owner) is False
        finish.set()
        for _ in range(4):
            await asyncio.sleep(0)
        assert leases.active_count(account.owner) == 0

    asyncio.run(scenario())
    assert auth.delete_user_account(account.owner)


def test_retry_token_is_deletion_only_and_password_checked(account, api):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.middleware("http")(api.account_auth_guard)
    app.add_api_route("/api/v2/auth/account", api.delete_account, methods=["DELETE"])
    app.add_api_route("/api/v2/protected", lambda: {"private": True}, methods=["GET", "POST"])
    lease = leases.acquire_account_lease(account.owner)
    with TestClient(app) as client:
        client.cookies.set("citrus_auth", account.token)
        headers = {"Origin": "http://testserver"}
        try:
            pending = client.request("DELETE", "/api/v2/auth/account", headers=headers,
                                     json={"password": "offline-password"})
            assert pending.status_code == 202
            assert pending.json()["status"] == "deletion_pending"
            assert pending.headers["retry-after"] == "2"
            assert auth.user_for_token(account.token) is None
            assert auth.user_for_deletion_retry(account.token)["user_id"] == account.owner
            assert client.get("/api/v2/protected").status_code == 401
            assert client.post("/api/v2/protected", headers=headers).status_code == 401
            bad = client.request("DELETE", "/api/v2/auth/account", headers=headers,
                                 json={"password": "wrong-password"})
            assert bad.status_code == 401
        finally:
            lease.release()
        complete = client.request("DELETE", "/api/v2/auth/account", headers=headers,
                                  json={"password": "offline-password"})
        assert complete.status_code == 200
        assert complete.json()["status"] == "deleted"
        assert auth.user_by_id(account.owner) is None
        assert auth.user_for_deletion_retry(account.token) is None


def test_chat_rejects_stale_authenticated_user_before_quota(account, api, monkeypatch):
    stale_user = dict(account.user)
    monkeypatch.setattr(api, "current_user", lambda _request: stale_user)
    reserve = Mock()
    monkeypatch.setattr(auth, "reserve_chat_request", reserve)
    assert auth.delete_user_account(account.owner)
    with pytest.raises(Exception) as error:
        asyncio.run(api.chat_v2(api.ChatRequest(query="late"), object()))
    assert error.value.status_code == 409
    reserve.assert_not_called()


def test_chat_early_failure_releases_admission(account, api, monkeypatch):
    monkeypatch.setattr(api, "current_user", lambda _request: account.user)
    monkeypatch.setattr(auth, "check_rate_limit", lambda *args, **kwargs: False)
    with pytest.raises(Exception) as error:
        asyncio.run(api.chat_v2(api.ChatRequest(query="limited"), object()))
    assert error.value.status_code == 429
    assert leases.active_count(account.owner) == 0


def test_chat_lease_survives_terminal_job_until_real_outer_finally(account, api, monkeypatch):
    from src.core.test_cancel_lifecycle import chat_runtime
    from src.core import jobs

    runtime = chat_runtime.__wrapped__(monkeypatch, account.root)
    monkeypatch.setattr(api, "current_user", lambda _request: account.user)
    api.session_manager._create_session_sync("offline-sid", account.owner)

    async def scenario():
        entered, finish = asyncio.Event(), asyncio.Event()
        original_release = runtime.gate.release

        async def slow_cleanup(*, owner_user_id):
            assert owner_user_id == account.owner
            entered.set()
            await finish.wait()
            await original_release(owner_user_id=owner_user_id)

        monkeypatch.setattr(runtime.gate, "release", slow_cleanup)
        runtime.graph_entered = asyncio.Event()
        response = await api.chat_v2(api.ChatRequest(query="offline", session_id="offline-sid"), object())
        await asyncio.wait_for(runtime.graph_entered.wait(), 3)
        job_id, task = next(iter(api._running_graph_tasks.items()))
        # Business terminal state is deliberately earlier than task cleanup.
        assert jobs.update_job(job_id, status="completed")
        task.cancel()
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert leases.active_count(account.owner) > 0
            with pytest.raises(leases.AccountDeletionPending):
                auth.delete_user_account(account.owner, raise_if_busy=True)
        finally:
            finish.set()
            await task
            await response.body_iterator.aclose()
            await asyncio.sleep(0)
        assert leases.active_count(account.owner) == 0

    asyncio.run(scenario())
    assert auth.delete_user_account(account.owner)


def test_figure_write_thread_remains_owned_after_request_cancellation(account, api, monkeypatch):
    from src.api import figure_router
    from src.figures import projects

    entered, finish, finished = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(figure_router, "_resolve_session", AsyncMock(return_value="figure-lease-session"))

    def save(*args, **kwargs):
        entered.set()
        try:
            assert finish.wait(5)
            return {"render_version": 2}
        finally:
            finished.set()

    monkeypatch.setattr(projects, "save_overrides", save)
    request = SimpleNamespace(state=SimpleNamespace(user=account.user))

    async def scenario():
        task = asyncio.create_task(figure_router.save_overrides(
            "figure", figure_router.OverrideBatch(overrides=[]), request))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert leases.active_count(account.owner) == 1
            assert auth.delete_user_account(account.owner) is False
        finally:
            finish.set()
            assert await asyncio.to_thread(finished.wait, 3)
            for _ in range(8):
                await asyncio.sleep(0)

    asyncio.run(scenario())
    assert leases.active_count(account.owner) == 0
    assert auth.delete_user_account(account.owner)


@pytest.mark.parametrize("operation", ["save", "delete", "import"])
def test_figure_writes_reject_late_authenticated_request(account, api, monkeypatch, operation):
    from src.api import figure_router
    from src.figures import projects

    request = SimpleNamespace(state=SimpleNamespace(user=dict(account.user)))
    handler = {"save": figure_router.save_overrides, "delete": figure_router.delete_project,
               "import": figure_router.import_project}[operation]
    write = Mock()
    monkeypatch.setattr(projects, {"save": "save_overrides", "delete": "delete_for_owner",
                                  "import": "import_from_library"}[operation], write)
    assert auth.delete_user_account(account.owner)
    args = (("figure", figure_router.OverrideBatch(overrides=[]), request) if operation == "save"
            else ("figure", request) if operation == "delete"
            else (figure_router.ImportRequest(file_id="file"), request))
    with pytest.raises(Exception) as error:
        asyncio.run(handler(*args))
    assert error.value.status_code == 409
    write.assert_not_called()


def test_publisher_holds_admission_and_rejects_late_thread_publication(account, monkeypatch):
    from src.core import tracing
    from src.research import tools

    entered, finish = threading.Event(), threading.Event()

    def publish(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return "published-before-delete"

    monkeypatch.setattr(tools, "_publish_artifact", publish)

    def worker():
        tracing.set_user_id(account.owner)
        return tools._save_artifact("test.txt", b"test", "text/plain")

    with ThreadPoolExecutor(max_workers=1) as pool:
        work = pool.submit(worker)
        try:
            assert entered.wait(3)
            assert auth.delete_user_account(account.owner) is False
        finally:
            finish.set()
        assert work.result(timeout=3) == "published-before-delete"
    assert auth.delete_user_account(account.owner)
    tracing.set_user_id(account.owner)
    try:
        with pytest.raises(leases.AccountUnavailable):
            tools._save_artifact("late.txt", b"late", "text/plain")
    finally:
        tracing.set_user_id(None)


def test_ownerless_local_publication_keeps_existing_behavior(account, monkeypatch):
    from src.core import tracing
    from src.research import tools

    tracing.set_user_id(None)
    publish = Mock(return_value="local")
    monkeypatch.setattr(tools, "_publish_artifact", publish)
    assert tools._save_artifact("local.txt", b"local", "text/plain") == "local"
    publish.assert_called_once()


@pytest.mark.parametrize("cancel_before_start", [False, True])
def test_background_lease_is_retained_before_first_task_step(account, cancel_before_start):
    async def scenario():
        parent = leases.acquire_account_lease(account.owner)
        ran = []

        async def nested():
            assert leases.current_account_lease() is not parent
            ran.append(True)

        task = leases.create_account_task(parent, nested())
        parent.release()
        assert leases.active_count(account.owner) == 1
        assert not auth.delete_user_account(account.owner)
        if cancel_before_start:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await task
        await asyncio.sleep(0)
        assert ran == ([] if cancel_before_start else [True])
        assert leases.active_count(account.owner) == 0

    asyncio.run(scenario())
    assert auth.delete_user_account(account.owner)


@pytest.mark.parametrize("path", ["plain", "artifact", "inline_artifact"])
def test_tool_executor_retains_late_writer_after_observer_cancel(account, monkeypatch, path):
    from langchain_core.tools import StructuredTool
    from src.core import executors
    from src.tools import registry

    entered, finish = threading.Event(), threading.Event()
    target = account.root / "late-tool-write.txt"
    monkeypatch.setattr(registry, "_check_tool_sandbox", AsyncMock(return_value=None))

    def write():
        """Write to an isolated temporary file after the observer has left."""
        entered.set()
        assert finish.wait(5)
        # The worker context must refer to its own retained child, not the
        # released graph lease. Nested work still belongs to the admitted turn.
        child = leases.current_account_lease().retain()
        try:
            target.write_text("finished", encoding="utf-8")
        finally:
            child.release()
        return ("written", {}) if path != "plain" else "written"

    tool = StructuredTool.from_function(
        write, name="offline_lease_writer",
        response_format="content" if path == "plain" else "content_and_artifact")

    async def scenario():
        parent = leases.acquire_account_lease(account.owner)
        with leases.bound_account_lease(parent):
            invocation = (registry.run_tool_checked(tool, {}) if path == "inline_artifact"
                          else registry._run_single_tool(tool, {
                              "name": tool.name, "args": {}, "id": "offline-call"}))
            observer = asyncio.create_task(invocation)
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            observer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await observer
            parent.release()
            assert leases.active_count(account.owner) == 1
            assert not auth.delete_user_account(account.owner)
            assert not target.exists()
        finally:
            finish.set()
            parent.release()

    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(executors, "TOOL_EXECUTOR", pool)
        asyncio.run(scenario())
    assert target.read_text(encoding="utf-8") == "finished"
    assert leases.active_count(account.owner) == 0
    assert auth.delete_user_account(account.owner)


def test_snapshot_timeout_keeps_actual_recorder_owned(account, monkeypatch):
    from langchain_core.messages import AIMessage
    from src.core import executors, progress_bus, turn_context
    from src.graph import general_agent_graph as graph

    entered, finish = threading.Event(), threading.Event()
    target = account.root / "late-snapshot.txt"

    def record(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        target.write_text("snapshot", encoding="utf-8")

    monkeypatch.setattr(turn_context, "record", record)
    monkeypatch.setattr(progress_bus, "get_turn_scope", lambda: ("temporary-session", "request"))
    monkeypatch.setattr(graph, "stream_llm_response", AsyncMock(return_value=AIMessage(content="offline")))

    async def scenario():
        parent = leases.acquire_account_lease(account.owner)
        try:
            with leases.bound_account_lease(parent):
                result = await graph._call_llm(object(), [], timeout=0.1)
            assert result.content == "offline"
            assert entered.is_set()
            parent.release()
            assert leases.active_count(account.owner) == 1
            assert not auth.delete_user_account(account.owner)
            assert not target.exists()
        finally:
            finish.set()
            parent.release()

    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(executors, "TOOL_EXECUTOR", pool)
        asyncio.run(scenario())
    assert target.read_text(encoding="utf-8") == "snapshot"
    assert leases.active_count(account.owner) == 0
    assert auth.delete_user_account(account.owner)


@pytest.mark.parametrize("stage", ["history", "evidence"])
def test_persistence_retains_late_writer_after_graph_leaves(account, api, monkeypatch, stage):
    from src.core import background
    from src.graph.session_persistence import persist_agent_turn

    target = account.root / f"late-{stage}.txt"

    async def scenario():
        entered, finish, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        parent = leases.acquire_account_lease(account.owner)

        async def slow_write(*args, **kwargs):
            entered.set()
            await finish.wait()
            child = leases.current_account_lease().retain()
            try:
                target.write_text(stage, encoding="utf-8")
            finally:
                child.release()
                completed.set()
            return True

        monkeypatch.setattr(api.session_manager, "save_messages",
                            slow_write if stage == "history" else AsyncMock(return_value=True))
        monkeypatch.setattr(api.session_manager, "save_evidence", slow_write)
        with leases.bound_account_lease(parent):
            observer = asyncio.create_task(persist_agent_turn({
                "query": "offline", "answer": "offline answer", "session_id": "temporary-session",
                "web_results": [{"url": "https://example.invalid", "title": "offline"}],
            }))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            if stage == "history":
                observer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await observer
            else:
                assert (await observer)["_trace"]["history_saved"] is True
                assert background.pending_count() > 0
            parent.release()
            assert leases.active_count(account.owner) == 1
            assert not auth.delete_user_account(account.owner)
            assert not target.exists()
        finally:
            finish.set()
            parent.release()
            await asyncio.wait_for(completed.wait(), 3)
            for _ in range(8):
                await asyncio.sleep(0)
        assert leases.active_count(account.owner) == 0

    asyncio.run(scenario())
    assert target.read_text(encoding="utf-8") == stage
    assert auth.delete_user_account(account.owner)

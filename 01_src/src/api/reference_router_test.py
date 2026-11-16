"""Offline P7 router contracts; run with the final unified regression only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
import socket
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request

from src.api import reference_router as api
from src.core import account_leases as leases

OWNER = 991882
BASE = "/api/v2/session/reference_session/reference-audits"
AUDIT = "a" * 32
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def wired(monkeypatch):
    from src import auth
    from src.session import manager
    from src.core import executors, tracing

    original_connect = socket.socket.connect

    def forbidden(sock, address):
        # Windows asyncio builds its private wakeup socketpair over loopback.
        # Permit only that local mechanism; external network remains forbidden.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        raise AssertionError("No live network in router wiring tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"user_id": owner, "active": True})
    monkeypatch.setattr(auth, "user_for_token", lambda token: {"user_id": OWNER} if token == "synthetic" else None)
    monkeypatch.setattr(auth, "check_rate_limit", lambda *args: True)
    monkeypatch.setattr(leases, "_states", {})
    monkeypatch.setattr(manager.session_manager, "session_belongs_to",
                        lambda sid, owner: sid == "reference_session" and owner == OWNER)
    state = SimpleNamespace(calls=[], error=None, reason="batch_finished", worker=None)

    def operation(name):
        def call(owner, session_id, *args, **kwargs):
            admission = leases.current_account_lease()
            assert admission is not None and admission.owner == owner == OWNER
            assert leases.active_count(owner) >= 1
            assert session_id == "reference_session"
            assert tracing.get_session_id() == session_id
            state.calls.append((name, args, kwargs))
            if state.error:
                raise state.error
            if state.worker:
                return state.worker()
            view = {"audit_id": AUDIT, "rows": [], "coverage": {"total": 151, "processed": 1, "pending": 150},
                    "run_reason": state.reason}
            if name == "export_audit":
                return b"synthetic export", "application/json", "reference_audit_" + AUDIT + ".json"
            return [view] if name == "list_audits" else view
        return call

    service = SimpleNamespace(**{name: operation(name) for name in
        ("create_audit", "list_audits", "get_audit", "run_audit", "cancel_audit", "export_audit")})
    monkeypatch.setattr(api, "_service", lambda: service)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        if request.headers.get("x-user", "owner") == "owner":
            request.state.user = {"user_id": OWNER, "role": "admin"}
        return await call_next(request)

    app.include_router(api.router)
    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(executors, "TOOL_EXECUTOR", pool)
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, state=state, service=service)
    assert leases.active_count(OWNER) == 0


def test_all_operations_scope_admission_and_private_responses(wired):
    client, calls = wired.client, wired.state.calls
    assert client.post(BASE, json={"references": [{"title": "Synthetic"}]}, headers=ORIGIN).status_code == 200
    assert client.post(BASE, json={"text": "Synthetic citation"}, headers=ORIGIN).status_code == 200
    assert client.post(BASE, json={"data_url": "data:text/plain;base64,QQ=="}, headers=ORIGIN).status_code == 200
    assert calls[-1][2]["content"] == b"A"
    assert client.get(BASE + "?limit=5").status_code == 200
    detail = client.get(BASE + "/" + AUDIT + "?offset=100&limit=5000")
    assert detail.status_code == 200 and calls[-1][2] == {"offset": 100, "limit": 5000}
    run = client.post(BASE + "/" + AUDIT + "/run", json={"resume": True, "max_rows": 31, "time_budget": 2}, headers=ORIGIN)
    assert run.status_code == 200
    assert calls[-1][2] == {"resume": True, "max_rows": 31, "time_budget": 2}
    assert client.post(BASE + "/" + AUDIT + "/cancel", headers=ORIGIN).status_code == 200
    exported = client.get(BASE + "/" + AUDIT + "/export?format=JSON")
    assert exported.content == b"synthetic export"
    assert exported.headers["content-disposition"].startswith('attachment; filename="reference_audit_')
    for response in (detail, run, exported):
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
    assert {name for name, _, _ in calls} == {"create_audit", "list_audits", "get_audit", "run_audit", "cancel_audit", "export_audit"}


@pytest.mark.parametrize("suffix,body", [("", {"text": "Synthetic"}), ("/" + AUDIT + "/run", {}), ("/" + AUDIT + "/cancel", {})])
@pytest.mark.parametrize("origin", [None, "null", "http://other", "https://testserver", "http://testserver.evil",
                                    "http://testserver/path", "http://user@testserver", "http://testserver:bad"])
def test_writes_require_an_exact_origin_when_mounted_alone(wired, suffix, body, origin):
    response = wired.client.post(BASE + suffix, json=body, headers={} if origin is None else {"Origin": origin})
    assert response.status_code == 403
    assert not wired.state.calls


def test_default_origin_port_is_equivalent(wired):
    assert wired.client.post(BASE, json={"text": "Synthetic"}, headers={"Origin": "http://testserver:80"}).status_code == 200


@pytest.mark.parametrize("method,suffix,body", [("get", "", None), ("get", "/" + AUDIT, None),
    ("post", "", {"text": "Synthetic"}), ("post", "/" + AUDIT + "/run", {}),
    ("post", "/" + AUDIT + "/cancel", {}), ("get", "/" + AUDIT + "/export", None)])
def test_auth_session_and_closed_account_boundaries(wired, monkeypatch, method, suffix, body):
    kwargs = {"headers": ORIGIN}
    if body is not None:
        kwargs["json"] = body
    request = getattr(wired.client, method)
    assert request(BASE + suffix, **{**kwargs, "headers": {**ORIGIN, "x-user": "none"}}).status_code == 401
    assert request(BASE.replace("reference_session", "other_session") + suffix, **kwargs).status_code == 404
    assert not wired.state.calls
    leases.close_admission(OWNER)
    assert request(BASE + suffix, **kwargs).status_code == 409
    assert not wired.state.calls


def test_cookie_auth_fallback(wired):
    wired.client.cookies.set("citrus_auth", "synthetic")
    assert wired.client.get(BASE, headers={"x-user": "cookie"}).status_code == 200


@pytest.mark.parametrize("body", [{}, {"text": "   "}, {"references": []},
    {"text": "Synthetic", "references": [{}]}, {"text": "Synthetic", "data_url": "QQ=="},
    {"data_url": "data:text/plain,QQ=="}, {"data_url": "%%%"}, {"data_url": "data:;base64,"},
    {"text": "Synthetic", "unknown": 1}, {"references": [{}] * 5001},
    {"references": [{"claims": "invalid"}]}, {"references": [{"citation": "x" * 2_000_001}]},
    {"text": "x" * 2_000_001}, {"name": "\n", "text": "Synthetic"}])
def test_create_rejects_ambiguous_empty_malformed_or_unbounded_input(wired, body):
    assert wired.client.post(BASE, json=body, headers=ORIGIN).status_code == 400
    assert not wired.state.calls


@pytest.mark.parametrize("suffix", ["?limit=0", "?limit=101", "?limit=no", "/" + AUDIT + "?offset=-1",
                                      "/" + AUDIT + "?limit=5001", "/" + AUDIT + "/export?format=html"])
def test_invalid_query_is_400(wired, suffix):
    assert wired.client.get(BASE + suffix).status_code == 400
    assert not wired.state.calls


@pytest.mark.parametrize("body", [{"max_rows": 101}, {"max_rows": True}, {"time_budget": 0},
                                  {"time_budget": 121}, {"resume": "yes"}, {"unexpected": True}])
def test_run_options_are_bounded(wired, body):
    assert wired.client.post(BASE + "/" + AUDIT + "/run", json=body, headers=ORIGIN).status_code == 400
    assert not wired.state.calls


@pytest.mark.parametrize("error,status", [(ValueError("bad input"), 400), (LookupError("missing"), 404),
    (PermissionError("unowned"), 404), (leases.AccountUnavailable("closed"), 409),
    (api.ExecutorSaturatedError("full"), 409)])
def test_worker_error_mapping(wired, error, status):
    wired.state.error = error
    assert wired.client.get(BASE + "/" + AUDIT).status_code == status


@pytest.mark.parametrize("reason", ["already_running", "lease_lost"])
def test_run_conflict_preserves_progress_in_409(wired, reason):
    wired.state.reason = reason
    response = wired.client.post(BASE + "/" + AUDIT + "/run", json={}, headers=ORIGIN)
    assert response.status_code == 409
    assert response.json()["run_reason"] == reason
    assert response.json()["coverage"]["processed"] == 1


def test_cancelled_http_observer_retains_real_thread_admission(wired):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked():
        started.set()
        try:
            assert release.wait(5), "Test must release synthetic worker"
            assert leases.active_count(OWNER) > 0
            return {"audit_id": AUDIT}
        finally:
            finished.set()

    wired.state.worker = blocked
    request = Request({"type": "http", "method": "GET", "scheme": "http", "path": "/", "headers": [],
                       "state": {"user": {"user_id": OWNER}}})

    async def scenario():
        task = asyncio.create_task(api.get_audit("reference_session", AUDIT, request))
        try:
            for _ in range(200):
                if started.is_set():
                    break
                await asyncio.sleep(.005)
            assert started.is_set()
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            assert leases.active_count(OWNER) > 0
            leases.close_admission(OWNER)
            with pytest.raises(leases.AccountDeletionPending):
                leases.assert_idle(OWNER)
        finally:
            release.set()
            with suppress(asyncio.CancelledError):
                await task
        for _ in range(200):
            if finished.is_set() and leases.active_count(OWNER) == 0:
                break
            await asyncio.sleep(.005)
        assert finished.is_set() and leases.active_count(OWNER) == 0

    asyncio.run(scenario())

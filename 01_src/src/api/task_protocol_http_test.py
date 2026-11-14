"""Owner-scoped HTTP recovery protocol using temporary stores and fake graph."""
import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.chat_idempotency_test import runtime, _post, _drain, _payload, _jobs  # noqa: F401
from src.core import task_events


def protocol_app(rt, *, middleware=False):
    app = FastAPI()
    for path, handler, methods in (
        ("/jobs/{job_id}", rt.api.get_job_status, ["GET"]),
        ("/jobs/{job_id}/events", rt.api.get_job_events, ["GET"]),
        ("/jobs/{job_id}/operations/{operation_id}/result", rt.api.get_operation_result, ["GET"]),
        ("/api/v2/chat/jobs/{job_id}/updates", rt.api.update_chat_task, ["POST"]),
        ("/api/v2/chat/jobs/{job_id}/approvals/{approval_id}", rt.api.decide_task_approval, ["POST"]),
        ("/api/v2/chat/jobs/{job_id}/resume", rt.api.resume_chat_operation, ["POST"]),
        ("/ready", rt.api.readiness_check, ["GET"]),
    ):
        app.add_api_route(path, handler, methods=methods)
    if middleware:
        app.middleware("http")(rt.api.account_auth_guard)
    return app


def test_chat_owner_capacity_covers_graph_lifetime_and_cancelled_pending_request(runtime):
    rt = runtime
    rt.blocked = True
    rt.api.chat_capacity.max_active = 3
    rt.api.chat_capacity.max_active_per_owner = 1
    rt.api.chat_capacity.max_pending_per_owner = 1
    async def scenario():
        first = await _post(rt)
        await asyncio.wait_for(rt.entered.wait(), 2)
        assert (await rt.api.chat_capacity.snapshot(owner_user_id=rt.owner))["owner_active"] == 1
        second = await _post(rt, _payload(client_request_id="owner-pending"))
        for _ in range(1000):
            if (await rt.api.chat_capacity.snapshot(owner_user_id=rt.owner))["owner_queued"] == 1:
                break
            await asyncio.sleep(0.001)
        assert rt.graph_calls == 1
        assert (await rt.api.chat_capacity.snapshot(owner_user_id=rt.owner))["owner_queued"] == 1
        pending_job = next(job for job in _jobs(rt) if job["client_request_id"] == "owner-pending")
        assert rt.api._cancel_one_job(pending_job["job_id"])["accepted"]
        await _drain(second)
        counts = await rt.api.chat_capacity.snapshot(owner_user_id=rt.owner)
        assert counts["owner_active"] == counts["active"] == 1
        assert counts["owner_queued"] == counts["queued"] == 0
        assert rt.graph_calls == 1
        rt.finish.set()
        await _drain(first)
        counts = await rt.api.chat_capacity.snapshot(owner_user_id=rt.owner)
        assert counts["owner_active"] == counts["active"] == counts["owner_queued"] == 0
        assert rt.api.chat_capacity._active_by_owner == rt.api.chat_capacity._pending_by_owner == {}
    asyncio.run(scenario())


def test_stream_and_http_replay_have_same_sequence_and_terminal_snapshot(runtime):
    rt = runtime
    async def scenario():
        response = await _post(rt)
        live = await _drain(response)
        job_id = json.loads(live[0]["data"])["job_id"]
        page = task_events.replay(job_id)
        assert [int(frame["id"]) for frame in live] == [e["seq"] for e in page["events"]]
        terminal = next(e["data"]["turn_envelope"] for e in page["events"] if e["event"] == "done")
        assert terminal == page["terminal_snapshot"]
        return job_id
    job_id = asyncio.run(scenario())
    with TestClient(protocol_app(rt)) as client:
        head = client.get(f"/jobs/{job_id}/events?after=0&limit=1")
        assert head.status_code == 200 and head.json()["has_more"]
        assert head.headers["cache-control"] == "no-store"
        tail = client.get(f"/jobs/{job_id}/events?after={head.json()['next_cursor']}")
        assert tail.status_code == 200 and not tail.json()["has_more"]
        assert client.get(f"/jobs/{job_id}/events?after=-1").status_code == 400
        rt.user = rt.auth.user_by_id(rt.other)
        assert client.get(f"/jobs/{job_id}/events").status_code == 404


def test_http_update_cas_decision_and_unsafe_resume_are_operational(runtime):
    rt = runtime
    job_id = rt.jobs.claim_job("protocol-session", "request", owner_user_id=rt.owner).job_id
    approval = task_events.create_approval(job_id, "fixture-write", {"value": "fixture"})
    operation_id, _ = task_events.start_operation(job_id, rt.owner, "external-write", {})
    with TestClient(protocol_app(rt)) as client:
        url = f"/api/v2/chat/jobs/{job_id}"
        assert client.post(url + "/updates", json={"expected_version": 0, "instruction": "use this revision"}).status_code == 200
        assert client.post(url + "/updates", json={"expected_version": 0, "instruction": "stale"}).status_code == 409
        assert client.get(f"/jobs/{job_id}").json()["pending_updates"][0]["version"] == 1
        rejected = client.post(url + f"/approvals/{approval}", json={"decision": "rejected"})
        assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
        assert client.post(url + f"/approvals/{approval}", json={"decision": "granted"}).status_code == 409
        rt.jobs.mark_interrupted_jobs()
        assert client.post(url + "/resume", json={"operation_id": operation_id}).status_code == 409
        rt.user = rt.auth.user_by_id(rt.other)
        assert client.post(url + "/updates", json={"expected_version": 1, "instruction": "foreign"}).status_code == 404
        assert client.post(url + f"/approvals/{approval}", json={"decision": "rejected"}).status_code == 404


def test_events_nested_path_passes_real_job_middleware_owner_and_same_origin(runtime, monkeypatch):
    rt = runtime
    owner_user = rt.auth.user_by_id(rt.owner)
    other_user = rt.auth.user_by_id(rt.other)
    monkeypatch.setattr(rt.auth, "user_for_token", lambda token: {"owner": owner_user, "other": other_user}.get(token))
    job_id = rt.jobs.claim_job("pre-session-claim", "request", owner_user_id=rt.owner).job_id
    task_events.append_event(job_id, "status", {"stage": "queued"})
    with TestClient(protocol_app(rt, middleware=True)) as client:
        assert client.get(f"/jobs/{job_id}/events").status_code == 401
        client.cookies.set("citrus_auth", "owner")
        assert client.get(f"/jobs/{job_id}/events").status_code == 200
        url = f"/api/v2/chat/jobs/{job_id}/updates"
        assert client.post(url, json={"expected_version": 0, "instruction": "fixture"}).status_code == 403
        assert client.post(url, json={"expected_version": 0, "instruction": "fixture"}, headers={"origin": "http://testserver"}).status_code == 200
        client.cookies.set("citrus_auth", "other")
        assert client.get(f"/jobs/{job_id}/events").status_code == 404


def test_http_safe_resume_and_refresh_read_back_verified_operation_result(runtime, monkeypatch):
    rt = runtime
    from src.session import files
    from src.tools import registry
    from src.tools.account_files import read_account_file
    monkeypatch.setattr(registry, "_TOOL_SPECS", dict(registry._TOOL_SPECS))
    registry.register_tool(read_account_file, is_readonly=True)
    job_id = rt.jobs.claim_job("resume-http", "request", owner_user_id=rt.owner).job_id
    item = files.register_file(session_id="resume-http", owner_user_id=rt.owner,
        filename="fixture.txt", content=b"persisted recovery fixture", mime="text/plain")
    args = {"file_id": item["file_id"]}
    mode, refs = registry._recovery_inputs("read_account_file", args, rt.owner)
    operation_id, _ = task_events.start_operation(job_id, rt.owner, "read_account_file", args,
        recovery_mode=mode, input_refs=refs)
    rt.jobs.mark_interrupted_jobs()
    original_snapshot = rt.jobs.decode_terminal_snapshot(rt.jobs.get_job(job_id))
    with TestClient(protocol_app(rt)) as client:
        resumed = client.post(f"/api/v2/chat/jobs/{job_id}/resume", json={"operation_id": operation_id})
        assert resumed.status_code == 200
        assert resumed.json()["operation_status"] == "complete"
        view = client.get(f"/jobs/{job_id}").json()
        assert view["status"] == "failed" and view["terminal_snapshot"] == original_snapshot
        stored = client.get(view["operations"][0]["result_url"])
        assert "persisted recovery fixture" in stored.json()["result"]["content"]
        rt.user = rt.auth.user_by_id(rt.other)
        assert client.get(view["operations"][0]["result_url"]).status_code == 404


def test_ready_is_read_only_and_gates_stores_models_and_required_worker(runtime, monkeypatch):
    rt = runtime
    from src.engine import hardware
    monkeypatch.setattr(hardware, "runtime_readiness", lambda: {"ready": False, "require_gpu": True, "errors": ["inference unverified"]})
    monkeypatch.setattr(rt.api.app.state, "task_stores_ready", True, raising=False)
    monkeypatch.setattr(rt.api.app.state, "retrieval_ready", True, raising=False)
    monkeypatch.setattr(rt.api.app.state, "worker_readiness", {"status": "disabled", "verified": False}, raising=False)
    with TestClient(protocol_app(rt)) as client:
        assert client.get("/ready").status_code == 503
        monkeypatch.setattr(hardware, "runtime_readiness", lambda: {"ready": True, "require_gpu": False, "errors": []})
        monkeypatch.delenv("CITRUS_REQUIRE_FIGURE_WORKER", raising=False)
        assert client.get("/ready").json()["status"] == "degraded"
        monkeypatch.setenv("CITRUS_REQUIRE_FIGURE_WORKER", "1")
        assert client.get("/ready").status_code == 503
        rt.api.app.state.worker_readiness = {"status": "ready", "verified": True}
        assert client.get("/ready").status_code == 200
        rt.api.app.state.task_stores_ready = False
        assert client.get("/ready").status_code == 503

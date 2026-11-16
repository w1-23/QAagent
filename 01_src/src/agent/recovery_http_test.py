"""Actual FastAPI/SSE recovery route with synthetic auth/model and real temp stores."""
import asyncio

import httpx
from langchain_core.messages import AIMessage

from src.agent.host_test import environment, install
from src.agent import host
from src.core import jobs, task_events


def test_actual_http_recovery_auth_and_save_only_lifecycle(environment, monkeypatch):
    state, job_id = environment
    import src.logger
    monkeypatch.setattr(src.logger, "setup_logging", lambda: None)
    from src.api import main
    from src import auth
    user = {"user_id": 17, "username": "synthetic-owner", "role": "user", "active": True}
    monkeypatch.setattr(auth, "user_for_token", lambda token: user if token == "synthetic" else None)
    monkeypatch.setattr(auth, "user_by_id", lambda owner: user if owner == 17 else None)
    monkeypatch.setattr(auth, "release_chat_request", lambda *_: (_ for _ in ()).throw(AssertionError("recovery must not refund a new quota")))
    main.app.state.task_stores_ready = True
    state.update(client_request_id="synthetic-client", idempotency_key="synthetic-idempotency")
    sent = install(monkeypatch, [AIMessage(content="durably recovered answer")])
    async def scenario():
        first = await host.general_agent_node(state)
        assert first["turn_outcome"]["status"] == "completed"
        # Process stopped after accepting answer, before outer session_save/done.
        jobs.mark_interrupted_jobs()
        prior = task_events.replay(job_id)["events"]
        assert any(e["event"] == "done" for e in prior)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://testserver", timeout=20) as client:
            url = f"/api/v2/chat/jobs/{job_id}/recover-agent"
            denied = await client.post(url, json={})
            assert denied.status_code == 401
            client.cookies.set("citrus_auth", "synthetic")
            csrf = await client.post(url, json={})
            assert csrf.status_code == 403
            invalid = await client.post(url, json={"query": "replace original"}, headers={"origin": "http://testserver"})
            assert invalid.status_code == 422
            response = await client.post(url, json={}, headers={"origin": "http://testserver"})
            assert response.status_code == 200, response.text
            assert "text/event-stream" in response.headers["content-type"]
            assert "durably recovered answer" in response.text
            assert response.text.count("event: done") == 1
            assert '"history_saved": true' in response.text or '"history_saved":true' in response.text
            repeated = await client.post(url, json={}, headers={"origin": "http://testserver"})
            assert repeated.status_code == 409
        assert len(sent) == 1  # save-only recovery made no new model/tool request
        assert jobs.get_job(job_id)["status"] == "completed"
        events = task_events.replay(job_id)["events"]
        assert events[:len(prior)] == prior
        assert sum(e["event"] == "done" for e in events) == 2
    asyncio.run(scenario())

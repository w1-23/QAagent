"""Actual authenticated Skill catalog and explicit-selection admission routes."""
import asyncio

import httpx
from fastapi.responses import JSONResponse

from src.agent.host_test import environment


def test_catalog_and_selection_are_authenticated_versioned_and_separate_from_query(environment, monkeypatch):
    import src.logger
    monkeypatch.setattr(src.logger, "setup_logging", lambda: None)
    from src.api import main
    from src import auth
    from src.agent.skill_adapter import skill_catalog
    user = {"user_id": 17, "username": "synthetic-owner", "role": "user", "active": True}
    monkeypatch.setattr(auth, "user_for_token", lambda token: user if token == "synthetic" else None)
    monkeypatch.setattr(auth, "user_by_id", lambda owner: user if owner == 17 else None)
    monkeypatch.setattr(auth, "check_rate_limit", lambda *a, **kw: True)
    reservations = []
    monkeypatch.setattr(auth, "reserve_chat_request", lambda user: (reservations.append(1) or True, 100))
    main.app.state.task_stores_ready = True
    captured = []
    async def start(req, request, lease, user, sid, rid, job_id, *args, **kwargs):
        captured.append(req.model_dump())
        main._job_tokens.pop(job_id, None)
        main._running_graph_tasks.pop(job_id, None)
        return JSONResponse({"accepted": True})
    monkeypatch.setattr(main, "_start_claimed_chat", start)
    entry = next(row for row in skill_catalog() if row['id'] == 'word-export')
    choice = {key: entry[key] for key in ("id", "version", "sha256")}
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as client:
            assert (await client.get("/api/v2/skills")).status_code == 401
            client.cookies.set("citrus_auth", "synthetic")
            catalog = await client.get("/api/v2/skills")
            assert catalog.status_code == 200 and catalog.headers["cache-control"] == "no-store"
            assert {row['id'] for row in catalog.json()['skills']} == {
                'candidate-search', 'reference-verification', 'data-audit', 'figure-initial'}
            assert "text" not in catalog.json()["skills"][0]
            headers = {"origin": "http://testserver"}
            body = {"query": "original request", "session_id": "host-session", "selected_skills": [{**choice, "sha256": "0" * 64}]}
            denied = await client.post("/api/v2/chat", json=body, headers=headers)
            assert denied.status_code == 409 and denied.json()["detail"]["code"] == "skill_selection_changed"
            assert not reservations and not captured
            body["selected_skills"] = [{**choice, "body": "client instructions"}]
            assert (await client.post("/api/v2/chat", json=body, headers=headers)).status_code == 422
            body["selected_skills"] = [choice, choice]
            assert (await client.post("/api/v2/chat", json=body, headers=headers)).status_code == 409
            assert not reservations
            unavailable = next(row for row in skill_catalog() if row['id'] == 'data-audit')
            body["selected_skills"] = [{key: unavailable[key] for key in ('id', 'version', 'sha256')}]
            assert (await client.post("/api/v2/chat", json=body, headers=headers)).status_code == 409
            assert not reservations and not captured
            body["selected_skills"] = [choice]
            accepted = await client.post("/api/v2/chat", json=body, headers=headers)
            assert accepted.status_code == 200 and len(reservations) == 1
            assert captured[0]["query"] == "original request"
            assert captured[0]["selected_skills"] == [choice]
    asyncio.run(scenario())

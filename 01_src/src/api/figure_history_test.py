"""Retired figure history routes stay absent; current detail remains owner-scoped."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.figures import store
from src.figures.history_store_test import isolated_stores, OWNER, OTHER, SESSION  # noqa: F401


@pytest.fixture
def client(monkeypatch):
    from src.api import figure_router

    monkeypatch.setattr(figure_router, "_owner", lambda request: int(request.headers.get("x-owner", OWNER)))
    app = FastAPI()
    app.include_router(figure_router.router)
    with TestClient(app) as value:
        yield value


@pytest.mark.parametrize("method,suffix", [
    ("get", "/revisions"), ("get", "/revisions/1"),
    ("get", "/revisions/1/export/svg"), ("get", "/revisions/1/preview.svg"),
    ("post", "/restore"),
])
def test_saved_history_routes_are_not_registered(client, method, suffix):
    fid = store.create_project(owner_user_id=OWNER, session_id=SESSION)["figure_id"]
    response = getattr(client, method)(f"/api/v2/figures/projects/{fid}{suffix}")
    assert response.status_code == 404
    schema = client.get("/openapi.json").json()
    assert not any("/revisions" in path or path.endswith("/restore") for path in schema["paths"])
    assert "RestoreRequest" not in schema.get("components", {}).get("schemas", {})


def test_current_detail_has_no_history_and_preserves_owner_scope(client):
    fid = store.create_project(owner_user_id=OWNER, session_id=SESSION)["figure_id"]
    from src.figures.manifest import MANIFEST_VERSION
    assert store.update_project(fid, OWNER, expected_revision=0, render_version=1,
                                manifest_json={"version": MANIFEST_VERSION, "objects": []})
    url = f"/api/v2/figures/projects/{fid}"
    response = client.get(url)
    assert response.status_code == 200
    assert response.json()["render_version"] == 1
    assert "revisions" not in response.json()
    assert client.get(url, headers={"x-owner": str(OTHER)}).status_code == 404

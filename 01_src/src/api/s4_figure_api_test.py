"""Offline ASGI acceptance of S4 panels and input-backed script projects."""
import json

import pytest

from src.api.test_figure_router import _workspace
from src.figures.s4_capabilities_test import _multi, INPUT_SCRIPT


@pytest.mark.parametrize("source_kind", ["spec", "script"])
def test_s4_create_edit_save_reopen_download_api(tmp_path, monkeypatch, source_kind):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.config import settings
    from src.core import tracing
    from src.session import files
    from src.session.manager import session_manager
    session_manager._create_session_sync("s4-api-session", 101)
    assert session_manager.session_belongs_to("s4-api-session", 101)
    from src.research.figure_tools import create_research_figure, run_research_figure_script
    monkeypatch.setattr(settings, "FIGURES_SANDBOX_BACKEND", "local")
    monkeypatch.setattr(settings, "FIGURES_SANDBOX_ALLOW_LOCAL", True)
    tracing.set_user_id(101)
    tracing.set_session_id("s4-api-session")
    try:
        if source_kind == "spec":
            _, result = create_research_figure.func(spec_json=json.dumps(_multi()))
            target = "panel_yield.title"
        else:
            entry = files.register_file(session_id="s4-api-session", owner_user_id=101,
                filename="measurements.csv", content=b"x,y\n1,11\n2,22\n", mime="text/csv")
            _, result = run_research_figure_script.func(script=INPUT_SCRIPT,
                inputs_json={"data": {"file_id": entry["file_id"]}},
                qa_profile_json={"caption": "Two observed measurements."})
            target = "axes_0.title"
    finally:
        tracing.set_user_id(None)
        tracing.set_session_id("")
    assert result.get("editable"), result
    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.include_router(main.figure_router.router)
    base = f"/api/v2/figures/projects/{result['figure_id']}"
    edit = {"overrides": [{"gid": target, "prop": "fontsize", "value": 15}], "expected_revision": 1}
    with TestClient(app) as client:
        draft = client.post(base + "/preview", json=edit)
        assert draft.status_code == 200, draft.text
        saved = client.post(base + "/overrides", json=edit)
        assert saved.status_code == 200, saved.text
        reopened = client.get(base)
        assert reopened.status_code == 200
        payload = reopened.json()
        assert payload["render_version"] == 2
        title = next(item for item in payload["manifest"]["objects"] if item["gid"] == target)
        assert title["fontsize"] == 15
        assert "qa_receipt" in payload["manifest"]
        preview = client.get(base + "/preview.svg")
        download = client.get(base + "/export/svg")
        assert preview.content == download.content
        assert preview.content.decode("utf-8") == draft.json()["svg"]
        assert client.get(base + "/export/png").content.startswith(b"\x89PNG")
        assert client.get(base + "/export/pdf").content.startswith(b"%PDF-")
        identity["user_id"] = 202
        assert client.get(base).status_code == 404
        assert client.post(base + "/preview", json=edit).status_code == 404

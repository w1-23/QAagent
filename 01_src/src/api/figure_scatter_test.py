"""Synthetic scatter creation followed by real ASGI workspace HTTP round trips.

The production POST /projects imports a bar only; it cannot create a scatter.
Creation therefore uses the real create_research_figure tool, with no model or
mock project. All open/edit/read/download requests use the production router.
Auth identity is injected; accounts, sessions, files and revisions use isolated
real temporary stores. Render spies observe the actual edited PathCollections.
"""
from __future__ import annotations

from contextvars import copy_context
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from xml.etree import ElementTree

import numpy as np
import pytest


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import src.config as config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)

    def no_network(*args, **kwargs):
        raise AssertionError("Synthetic scatter fixtures cannot access the network")

    from src import auth
    from src.core import account_leases, tracing
    from src.figures import trusted_render, store
    from src.research import tools
    from src.session import files, manager

    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state/accounts.sqlite3")
    monkeypatch.setattr(account_leases, "_states", {})
    auth.init_store()
    with auth._connect() as conn:
        for owner in (101, 202):
            conn.execute(
                "INSERT INTO users(user_id,username,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
                (owner, f"SYNTHETIC_scatter_{owner}", b"synthetic-salt", b"synthetic-hash", "2026-01-01"))
    monkeypatch.setattr(manager, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manager, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(manager, "DB_PATH", tmp_path / "state/sessions.db")
    monkeypatch.setattr(manager.session_manager, "db_path", str(tmp_path / "state/sessions.db"))
    manager.session_manager._init_db_sync()
    session_id = "synthetic-scatter-session"
    manager.session_manager._create_session_sync(session_id, 101)
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(store, "_STATE_DIR", tmp_path / "state")
    files.init_store()

    observed = []
    real_render = trusted_render.render_resolved

    def observe_render(*args, **kwargs):
        result = real_render(*args, **kwargs)
        observed.append(result)
        return result

    monkeypatch.setattr(trusted_render, "render_resolved", observe_render)

    def create(grouped=False):
        from src.core import figures
        from src.research.figure_tools import create_research_figure
        entry = files.register_file(
            session_id=session_id, owner_user_id=101, filename="SYNTHETIC_scatter.csv",
            content=b"x,y,group\n2.5,9,A\n8,-4,B\n19,15,A\n24,6,B\n")
        ref = {"kind": "account_file", "file_id": entry["file_id"]}
        spec = {"chart": "scatter", "title": "SYNTHETIC scatter", "error": "none",
                "x": {**ref, "column": "x"}, "y": {**ref, "column": "y"}}
        if grouped:
            spec["group"] = {**ref, "column": "group"}

        def invoke():
            figures.begin_request()
            tracing.set_user_id(101)
            tracing.set_session_id(session_id)
            tracing.set_client_request_id("synthetic-scatter-create")
            tracing.set_current_tool_name("create_research_figure")
            return create_research_figure.func(spec_json=json.dumps(spec), formats_json=["svg"])

        text, result = copy_context().run(invoke)
        assert result["_tool_outcome"]["status"] == "complete", text
        assert result["chart"] == "scatter" and result["editable"]
        json.dumps(result, allow_nan=False)
        return result["figure_id"]

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.figure_router import router
    app = FastAPI()
    identity = {"user_id": 101}

    @app.middleware("http")
    async def synthetic_identity(request, call_next):
        request.state.user = dict(identity)
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        # Windows asyncio creates an internal socketpair at loop startup.
        # Once that exists, block every network connect during actual tests.
        monkeypatch.setattr(socket.socket, "connect", no_network)
        yield SimpleNamespace(client=client, create=create, observed=observed,
                              session_id=session_id, store=store, files=files,
                              root=tmp_path, identity=identity)


def _strict_response(response):
    assert response.status_code == 200, response.text

    def reject_constant(value):
        raise AssertionError(f"Non-JSON number in HTTP body: {value}")

    data = json.loads(response.text, parse_constant=reject_constant)
    json.dumps(data, allow_nan=False)
    return data


def _series(manifest):
    return [item for item in manifest["objects"] if item["role"] == "series"]


def _coordinates(rendered):
    # Actual child Artist measurements cross the process boundary as JSON.
    return [item['offsets'] for item in rendered.observations['series'] if 'offsets' in item]


def _assert_points(rendered, grouped):
    expected = ([[[2.5, 9], [19, 15]], [[8, -4], [24, 6]]] if grouped else
                [[[2.5, 9], [8, -4], [19, 15], [24, 6]]])
    actual = _coordinates(rendered)
    assert len(actual) == len(expected)
    for points, wanted in zip(actual, expected):
        np.testing.assert_array_equal(points, wanted)


@pytest.mark.parametrize("grouped", [False, True])
def test_tool_created_scatter_http_open_edit_read_and_download_preserve_scientific_points(workspace, grouped):
    w = workspace
    figure_id = w.create(grouped)
    url = f"/api/v2/figures/projects/{figure_id}"
    original = w.store.get_project(figure_id, 101)
    original_source = deepcopy(original["source_json"])
    assert original["data_refs_json"]
    original_svg_entry = w.files.get_account_file(original["exports_json"]["svg"]["file_id"], 101)
    original_svg_path = Path(original_svg_entry["path"])
    assert original_svg_path.is_relative_to(w.root)
    original_svg = original_svg_path.read_bytes()
    _assert_points(w.observed[0], grouped)

    opened = _strict_response(w.client.get(url))
    items = _series(opened["manifest"])
    assert len(items) == (2 if grouped else 1)
    assert all(item.get("bbox") and item.get("color") for item in items)
    for item in items:
        assert all(np.isfinite(list(item["bbox"].values())))
    _assert_points(w.observed[0], grouped)
    edits = [{"gid": items[0]["gid"], "prop": "color", "value": "#d55e00"},
             {"gid": items[0]["gid"], "prop": "linewidth", "value": 2.5}]
    edited = _strict_response(w.client.post(
        url + "/overrides", params={"session_id": w.session_id},
        json={"overrides": edits, "expected_revision": opened["render_version"]}))
    assert edited["render_version"] == opened["render_version"] + 1
    assert _series(edited["manifest"])[0]["color"] == "#d55e00"
    assert len(w.observed) == 2  # Observe real tool and edit renders, not a fresh proxy render.
    _assert_points(w.observed[1], grouped)
    np.testing.assert_array_equal(w.observed[1].observations['series'][0]['linewidths'], [2.5])
    reopened = _strict_response(w.client.get(url))
    assert reopened["manifest"] == edited["manifest"]
    assert reopened["overrides"] == edits
    assert reopened["render_version"] == edited["render_version"]
    assert w.store.get_project(figure_id, 101)["source_json"] == original_source
    assert reopened["data_refs"] == opened["data_refs"]
    assert original_svg_path.read_bytes() == original_svg  # Published prior version is immutable.
    assert reopened["exports"]["svg"]["file_id"] != original["exports_json"]["svg"]["file_id"]

    preview = w.client.get(url + "/preview.svg")
    download = w.client.get(url + "/export/svg")
    assert preview.status_code == download.status_code == 200
    assert preview.content == download.content
    svg = ElementTree.fromstring(download.content)
    assert svg.tag.endswith("svg")
    assert any(node.get("id") == items[0]["gid"] for node in svg.iter())
    assert b"#d55e00" in download.content
    assert "sandbox" in preview.headers["content-security-policy"]
    _assert_points(w.observed[1], grouped)


@pytest.mark.parametrize("geometry", [
    {"bbox": {"x0": float("inf"), "y0": float("-inf"), "x1": float("-inf"), "y1": float("inf")},
     "anchor": [float("inf"), float("nan")]},
    {"bbox": {"x0": 1, "y0": 2, "x1": 3, "y1": float("nan")}},
    {"bbox": [float("inf"), 0, 1, 2]},
    {"bbox": {"x0": 3, "y0": 2, "x1": 1, "y1": 4}},
    {"bbox": None, "anchor": [float("nan"), 2]},
])
def test_legacy_invalid_scatter_geometry_get_is_strict_json_and_selector_still_edits(workspace, geometry):
    w = workspace
    figure_id = w.create()
    url = f"/api/v2/figures/projects/{figure_id}"
    project = w.store.get_project(figure_id, 101)
    legacy = deepcopy(project["manifest_json"])
    item = _series(legacy)[0]
    item.update(geometry)
    identity = {key: deepcopy(item[key]) for key in ("gid", "locator", "editable")}
    w.store.update_project(figure_id, 101, manifest_json=legacy,
                           render_version=project["render_version"] + 1)
    # Deliberately persisted old Infinity/NaN; GET must sanitize without a rewrite.
    before_get = json.dumps(w.store.get_project(figure_id, 101)["manifest_json"], sort_keys=True)
    opened = _strict_response(w.client.get(url))
    cleaned = _series(opened["manifest"])[0]
    assert cleaned["bbox"] is None and cleaned["anchor"] is None
    assert cleaned["geometry_warning"] == "legacy_bounds_unavailable"
    assert {key: cleaned[key] for key in identity} == identity
    assert json.dumps(w.store.get_project(figure_id, 101)["manifest_json"], sort_keys=True) == before_get
    _assert_points(w.observed[0], False)
    edited = _strict_response(w.client.post(
        url + "/overrides", params={"session_id": w.session_id},
        json={"overrides": [{"gid": identity["gid"], "prop": "color", "value": "#d55e00"}],
              "expected_revision": opened["render_version"]}))
    repaired = _series(edited["manifest"])[0]
    assert repaired["bbox"] and "geometry_warning" not in repaired
    assert repaired["color"] == "#d55e00"
    assert _strict_response(w.client.get(url))["manifest"] == edited["manifest"]
    _assert_points(w.observed[1], False)


def test_scatter_http_access_is_scoped_to_real_temporary_owner(workspace):
    w = workspace
    figure_id = w.create()
    w.identity["user_id"] = 202
    url = f"/api/v2/figures/projects/{figure_id}"
    assert w.client.get(url).status_code == 404
    assert w.client.get(url + "/preview.svg").status_code == 404
    assert w.client.post(url + "/overrides", json={"overrides": []}).status_code == 404

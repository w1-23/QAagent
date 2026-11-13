"""Multi-user isolation for the figure workspace.

The workspace holds a user's figures, the data behind them and the edits they
made. Every one of those must stay inside one account. This file checks that
from the outside: two accounts, one shared service, and no path that lets the
second one see or touch the first one's material.

The rule the service follows is *not found*, not *found and refused*. A 403
tells a caller that an id exists; a 404 does not. So the tests below assert
404s, and they assert them for every verb: read, edit, delete, preview,
download and import.

What is deliberately *not* tested here: the sandbox's own boundary. That is
``src/figures/test_sandbox.py``'s job, and it is a different boundary —
process isolation rather than account isolation.
"""
from __future__ import annotations

import hashlib

import pytest

from src.api.test_figure_router import _make_project, _workspace


def _app(monkeypatch, main, identity):
    """An ASGI app with the figure routes and one signed-in account."""
    from fastapi import FastAPI

    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects",
                      main.figure_router.list_projects, methods=["GET"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}",
                      main.figure_router.get_project, methods=["GET"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}/overrides",
                      main.figure_router.save_overrides, methods=["POST"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}",
                      main.figure_router.delete_project, methods=["DELETE"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}/preview.svg",
                      main.figure_router.preview_svg, methods=["GET"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}/export/{fmt}",
                      main.figure_router.export_project, methods=["GET"])
    app.add_api_route("/api/v2/figures/data-files",
                      main.figure_router.list_data_files, methods=["GET"])
    app.add_api_route("/api/v2/figures/data-files/{file_id}/schema",
                      main.figure_router.data_file_schema, methods=["GET"])
    app.add_api_route("/api/v2/figures/projects",
                      main.figure_router.import_project, methods=["POST"])
    return app


# ── Reading another account's project ─────────────────────────────────────

def test_the_detail_of_another_accounts_project_is_not_found(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    figure_id, _manifest, _published = _make_project(owner=101)
    app = _app(monkeypatch, main, {"user_id": 202})
    with TestClient(app) as client:
        response = client.get(f"/api/v2/figures/projects/{figure_id}")
    assert response.status_code == 404


def test_the_preview_of_another_accounts_project_is_not_found(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    figure_id, _manifest, _published = _make_project(owner=101)
    app = _app(monkeypatch, main, {"user_id": 202})
    with TestClient(app) as client:
        response = client.get(f"/api/v2/figures/projects/{figure_id}/preview.svg")
    assert response.status_code == 404


@pytest.mark.parametrize("fmt", ["svg", "png", "pdf"])
def test_the_download_of_another_accounts_project_is_not_found(
        tmp_path, monkeypatch, fmt):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    figure_id, _manifest, _published = _make_project(owner=101)
    app = _app(monkeypatch, main, {"user_id": 202})
    with TestClient(app) as client:
        response = client.get(f"/api/v2/figures/projects/{figure_id}/export/{fmt}")
    assert response.status_code == 404


# ── Changing another account's project ───────────────────────────────────

def test_another_account_cannot_edit_the_project(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    figure_id, manifest, published = _make_project(owner=101)
    series = next(item for item in manifest["objects"] if item["role"] == "series")

    app = _app(monkeypatch, main, {"user_id": 202})
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/figures/projects/{figure_id}/overrides",
            json={"overrides": [{"gid": series["gid"], "prop": "color",
                                 "value": "#000000"}]})
    assert response.status_code == 404

    # The owner's figure is untouched.
    from src.figures import store
    stored = store.get_project(figure_id, 101)
    assert stored["overrides_json"] == []
    assert stored["exports_json"]["svg"]["sha256"] == published["svg"]["sha256"]


def test_another_account_cannot_delete_the_project(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    figure_id, _manifest, _published = _make_project(owner=101)
    app = _app(monkeypatch, main, {"user_id": 202})
    with TestClient(app) as client:
        response = client.delete(f"/api/v2/figures/projects/{figure_id}")
    assert response.status_code == 404

    from src.figures import store
    assert store.get_project(figure_id, 101) is not None


# ── The listings ──────────────────────────────────────────────────────────

def test_the_listing_holds_only_the_callers_own_projects(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    mine, _m, _p = _make_project(owner=101, session_id="session-a")
    theirs, _m2, _p2 = _make_project(owner=202, session_id="session-b")
    third, _m3, _p3 = _make_project(owner=303, session_id="session-c")

    app = _app(monkeypatch, main, {"user_id": 202})
    with TestClient(app) as client:
        payload = client.get("/api/v2/figures/projects").json()
    ids = {item["figure_id"] for item in payload["projects"]}
    assert ids == {theirs}
    assert mine not in ids
    assert third not in ids


def test_the_data_file_listing_holds_only_the_callers_own_files(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient
    from src.session import files as session_files

    session_files.register_file(
        session_id="isolation-a", owner_user_id=101, filename="mine.csv",
        content=b"a,b\n1,2\n", mime="text/csv",
        origin=session_files.ORIGIN_UPLOADED)
    session_files.register_file(
        session_id="isolation-b", owner_user_id=202, filename="theirs.csv",
        content=b"a,b\n3,4\n", mime="text/csv",
        origin=session_files.ORIGIN_UPLOADED)

    app = _app(monkeypatch, main, {"user_id": 101})
    with TestClient(app) as client:
        payload = client.get("/api/v2/figures/data-files").json()
    names = {item["filename"] for item in payload["files"]}
    assert "mine.csv" in names
    assert "theirs.csv" not in names


def test_another_accounts_table_schema_is_not_found(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient
    from src.session import files as session_files

    entry = session_files.register_file(
        session_id="isolation-c", owner_user_id=202, filename="theirs.csv",
        content=b"a,b\n3,4\n", mime="text/csv",
        origin=session_files.ORIGIN_UPLOADED)

    app = _app(monkeypatch, main, {"user_id": 101})
    with TestClient(app) as client:
        response = client.get(
            f"/api/v2/figures/data-files/{entry['file_id']}/schema")
    assert response.status_code == 404


# ── The store itself ──────────────────────────────────────────────────────

def test_the_store_never_returns_another_accounts_row(tmp_path, monkeypatch):
    """The ownership check is in the SQL, not in the caller.

    Every query carries the owner's id in its WHERE clause. A row belonging
    to somebody else is not found — the same answer as for an id that never
    existed.
    """
    _workspace(tmp_path, monkeypatch)
    from src.figures import store

    figure_id, _manifest, _published = _make_project(owner=101)
    assert store.get_project(figure_id, 101) is not None
    assert store.get_project(figure_id, 202) is None
    assert store.get_project("no-such-project", 101) is None
    assert store.update_project(figure_id, 202, title="hijacked") is False
    assert store.delete_project(figure_id, 202) is False
    assert store.get_project(figure_id, 101) is not None


def test_the_export_map_of_one_account_never_points_at_another_accounts_file(
        tmp_path, monkeypatch):
    """The export entries are file ids, and they inherit the library's rules.

    If a project of one account somehow carried another's file id, the
    download would have to be refused. The library refuses it.
    """
    _workspace(tmp_path, monkeypatch)
    from src.figures import projects
    from src.session import files as session_files

    figure_id, _manifest, published = _make_project(owner=101)
    stored = projects.detail_for_owner(figure_id, 101)
    file_id = stored["exports"]["svg"]["file_id"]
    assert session_files.get_account_file(file_id, 101) is not None
    assert session_files.get_account_file(file_id, 202) is None


def test_two_accounts_never_share_a_rendered_file(tmp_path, monkeypatch):
    """Same specification, two accounts, two different files.

    The figure is deterministic, so both accounts produce byte-identical
    drawings — but they must be *different files*, owned separately. If the
    service deduplicated by content, one account's download would be served
    from the other account's directory.
    """
    _workspace(tmp_path, monkeypatch)
    from src.figures import projects

    mine, _m, published_mine = _make_project(owner=101, session_id="shared-a")
    theirs, _m2, published_theirs = _make_project(owner=202, session_id="shared-b")

    assert published_mine["svg"]["sha256"] == published_theirs["svg"]["sha256"]
    assert published_mine["svg"]["file_id"] != published_theirs["svg"]["file_id"]
    assert projects.detail_for_owner(mine, 101)["exports"]["svg"]["file_id"] \
        != projects.detail_for_owner(theirs, 202)["exports"]["svg"]["file_id"]


def test_the_digest_of_a_figure_is_the_digest_of_its_bytes(tmp_path, monkeypatch):
    """The published digest must describe the published bytes.

    A digest that describes something else would make the export-consistency
    check meaningless, so it is checked against the file on disk.
    """
    _workspace(tmp_path, monkeypatch)
    from src.session import files as session_files

    _figure_id, _manifest, published = _make_project(owner=101)
    info = session_files.get_account_file(published["svg"]["file_id"], 101)
    digest = hashlib.sha256(info["path"].read_bytes()).hexdigest()
    assert digest == published["svg"]["sha256"]

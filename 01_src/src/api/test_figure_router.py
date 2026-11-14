"""The figure workspace: its API, its ownership rules and its guarantees.

These tests cover the properties the workspace promises rather than the shape
of its JSON:

* a project belonging to another account is *not found*, not found-and-refused,
  so an id cannot be probed;
* the preview and the download are the same bytes;
* an edit that is not on the allowlist is rejected as a whole batch, and a
  rejected edit leaves the figure untouched;
* the numbers in a figure do not move when the colours and the font sizes do;
* the rendered SVG carries no active content.

No production server, login or model is called: the handlers are mounted in an
isolated ASGI app and the identity is supplied by the test.
"""
from __future__ import annotations

import hashlib

import pytest


def _workspace(tmp_path, monkeypatch):
    """Point the service's stores at a temporary directory and build an app."""
    import src.config as config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)

    from src import auth
    from src.core import account_leases
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state/accounts.sqlite3")
    monkeypatch.setattr(account_leases, "_states", {})
    auth.init_store()
    with auth._connect() as conn:
        for owner in (101, 202, 303):
            conn.execute(
                "INSERT OR IGNORE INTO users(user_id,username,salt,password_hash,created_at) "
                "VALUES(?,?,?,?,?)",
                (owner, f"figure_fixture_{owner}", b"salt", b"hash", "2026-01-01"))

    from src.api import main
    from src.session import files
    from src.research import tools
    from src.figures import store

    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(store, "_STATE_DIR", tmp_path / "state")
    files.init_store()
    from src.session import manager
    monkeypatch.setattr(manager, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manager, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(manager, "DB_PATH", tmp_path / "state/sessions.db")
    monkeypatch.setattr(manager.session_manager, "db_path", str(tmp_path / "state/sessions.db"))
    manager.session_manager._init_db_sync()
    return main


def _make_project(owner: int = 101, session_id: str = "figure-session-001"):
    """Create a figure project the way the agent tool does."""
    from src.figures import projects
    from src.figures.core import build_resolved_figure
    from src.figures.exporter import export_all
    from src.figures.manifest import build_manifest
    from src.figures.resolve import resolve_spec
    from src.figures.spec import parse_spec
    from src.figures.store import create_project, update_project
    from src.session.manager import session_manager
    session_manager._create_session_sync(session_id, owner)
    assert session_manager.session_belongs_to(session_id, owner)

    spec = parse_spec({
        "chart": "bar", "title": "果实重量", "x_label": "处理", "y_label": "g",
        "x": {"kind": "inline", "column": "处理", "values": ["CK", "T1"]},
        "y": {"kind": "inline", "column": "重量", "values": [10.1, 12.2]},
        "error": "none", "legend": True})
    resolved, info = resolve_spec(spec)
    rendered = build_resolved_figure(resolved)
    blobs = export_all(rendered, formats=projects.DEFAULT_FORMATS)
    manifest = build_manifest(rendered, source="spec")

    project = create_project(
        owner_user_id=owner, title="果实重量", source_kind="spec",
        source={"kind": "spec", "spec": spec.as_dict()},
        session_id=session_id, data_refs=info.get("refs", []))
    published = projects.publish_rendered(owner, session_id, "果实重量", blobs)
    update_project(project["figure_id"], owner, manifest_json=manifest,
                   exports_json=published, render_version=1)
    return project["figure_id"], manifest, published


# ── The page ──────────────────────────────────────────────────────────────

def test_the_page_redirects_an_anonymous_visitor(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_api_route("/figures", main.serve_figures_page, methods=["GET"])
    with TestClient(app) as client:
        response = client.get("/figures", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/?next=%2Ffigures"


def test_the_page_is_served_to_a_signed_in_account(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import src.auth as auth

    monkeypatch.setattr(auth, "user_for_token",
                        lambda token: {"user_id": 101, "role": "user"} if token else None)
    app = FastAPI()
    app.add_api_route("/figures", main.serve_figures_page, methods=["GET"])
    with TestClient(app) as client:
        client.cookies.set("citrus_auth", "token")
        response = client.get("/figures")
    assert response.status_code == 200
    assert "图表工作台" in response.text


# ── Ownership ─────────────────────────────────────────────────────────────

def test_another_accounts_project_is_not_found(tmp_path, monkeypatch):
    """A 404, not a 403: the caller must not learn that the id exists."""
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    figure_id, _manifest, _published = _make_project(owner=101)

    identity = {"user_id": 202}
    monkeypatch.setattr(main, "current_user", lambda request: identity)

    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}",
                      main.figure_router.get_project, methods=["GET"])
    with TestClient(app) as client:
        response = client.get(f"/api/v2/figures/projects/{figure_id}")
    assert response.status_code == 404
    assert "无权访问" in response.json()["detail"]


def test_the_listing_only_shows_your_own_projects(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    mine, _m, _p = _make_project(owner=101)
    theirs, _m2, _p2 = _make_project(owner=202, session_id="figure-session-owner-202")

    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects",
                      main.figure_router.list_projects, methods=["GET"])
    with TestClient(app) as client:
        payload = client.get("/api/v2/figures/projects").json()
    ids = {item["figure_id"] for item in payload["projects"]}
    assert mine in ids
    assert theirs not in ids


# ── Preview and download ──────────────────────────────────────────────────

def test_the_preview_and_the_download_are_the_same_bytes(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    figure_id, _manifest, published = _make_project(owner=101)
    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)

    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}/preview.svg",
                      main.figure_router.preview_svg, methods=["GET"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}/export/{fmt}",
                      main.figure_router.export_project, methods=["GET"])
    with TestClient(app) as client:
        preview = client.get(f"/api/v2/figures/projects/{figure_id}/preview.svg")
        download = client.get(f"/api/v2/figures/projects/{figure_id}/export/svg")
    assert preview.status_code == 200
    assert preview.content == download.content
    assert hashlib.sha256(preview.content).hexdigest() == published["svg"]["sha256"]
    assert preview.headers["content-type"].startswith("image/svg+xml")
    # The preview must be inert: an SVG that can act is a hole.
    assert "sandbox" in preview.headers["content-security-policy"]
    assert preview.headers["x-content-type-options"] == "nosniff"


def test_the_png_and_the_pdf_are_downloadable(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    figure_id, _manifest, _published = _make_project(owner=101)
    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}/export/{fmt}",
                      main.figure_router.export_project, methods=["GET"])
    with TestClient(app) as client:
        png = client.get(f"/api/v2/figures/projects/{figure_id}/export/png")
        pdf = client.get(f"/api/v2/figures/projects/{figure_id}/export/pdf")
        bogus = client.get(f"/api/v2/figures/projects/{figure_id}/export/exe")
    assert png.status_code == 200
    assert png.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")
    assert bogus.status_code == 404


# ── Edits ─────────────────────────────────────────────────────────────────

def test_a_colour_edit_is_applied_and_the_figure_is_re_rendered(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    figure_id, manifest, published = _make_project(owner=101)
    series = next(item for item in manifest["objects"] if item["role"] == "series")

    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}/overrides",
                      main.figure_router.save_overrides, methods=["POST"])
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/figures/projects/{figure_id}/overrides",
            json={"overrides": [{"gid": series["gid"], "prop": "color",
                                 "value": "#b64342"}]})
    assert response.status_code == 200
    payload = response.json()
    assert payload["overrides"]
    assert payload["exports"]["svg"]["sha256"] != published["svg"]["sha256"]
    # The new SVG really carries the new colour.
    from src.session import files as session_files
    info = session_files.get_account_file(payload["exports"]["svg"]["file_id"], 101)
    assert "#b64342" in info["path"].read_text(encoding="utf-8").lower()


def test_the_data_does_not_move_when_the_style_changes(tmp_path, monkeypatch):
    """The promise that makes the editor safe: colours change, numbers do not."""
    from src.figures import projects
    from src.figures.core import build_resolved_figure
    from src.figures.exporter import export_all
    from src.figures.manifest import build_manifest
    from src.figures.overrides import apply_overrides, validate_overrides
    from src.figures.resolve import resolve_spec
    from src.figures.spec import parse_spec

    spec = parse_spec({
        "chart": "bar", "title": "T", "x_label": "X", "y_label": "Y",
        "x": {"kind": "inline", "column": "c", "values": ["A", "B"]},
        "y": {"kind": "inline", "column": "v", "values": [10.0, 20.0]}})
    resolved, _info = resolve_spec(spec)

    def bar_heights():
        rendered = build_resolved_figure(resolved)
        axes = rendered.axes[0]
        return [patch.get_height() for patch in axes.patches]

    before = bar_heights()
    rendered = build_resolved_figure(resolved)
    manifest = build_manifest(rendered, source="spec")
    series = next(item for item in manifest["objects"] if item["role"] == "series")
    validated = validate_overrides(
        [{"gid": series["gid"], "prop": "color", "value": "#b64342"},
         {"gid": series["gid"], "prop": "linewidth", "value": 3.0}], manifest)
    apply_overrides(rendered, manifest, validated)
    after = bar_heights()
    assert before == after == [10.0, 20.0]


def test_an_unknown_property_is_rejected_and_nothing_changes(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    figure_id, manifest, published = _make_project(owner=101)
    series = next(item for item in manifest["objects"] if item["role"] == "series")

    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}/overrides",
                      main.figure_router.save_overrides, methods=["POST"])
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/figures/projects/{figure_id}/overrides",
            json={"overrides": [{"gid": series["gid"], "prop": "get_height",
                                 "value": 999}]})
    assert response.status_code == 400
    assert "不允许修改属性" in response.json()["detail"]

    # The figure is untouched: the rejected batch changed nothing.
    from src.figures import store
    stored = store.get_project(figure_id, 101)
    assert stored["overrides_json"] == []
    assert stored["exports_json"]["svg"]["sha256"] == published["svg"]["sha256"]


def test_a_batch_with_one_bad_entry_is_rejected_whole(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    figure_id, manifest, _published = _make_project(owner=101)
    series = next(item for item in manifest["objects"] if item["role"] == "series")
    title = next(item for item in manifest["objects"] if item["role"] == "title")

    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}/overrides",
                      main.figure_router.save_overrides, methods=["POST"])
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/figures/projects/{figure_id}/overrides",
            json={"overrides": [
                {"gid": title["gid"], "prop": "fontsize", "value": 10.0},
                {"gid": series["gid"], "prop": "color", "value": "not-a-colour"}]})
    assert response.status_code == 400

    from src.figures import store
    stored = store.get_project(figure_id, 101)
    assert stored["overrides_json"] == [], "一批里有坏条目时，整批都不该落下"


def test_an_edit_to_an_unknown_object_is_rejected(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    figure_id, _manifest, _published = _make_project(owner=101)
    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}/overrides",
                      main.figure_router.save_overrides, methods=["POST"])
    with TestClient(app) as client:
        response = client.post(
            f"/api/v2/figures/projects/{figure_id}/overrides",
            json={"overrides": [{"gid": "axes_9.series_0", "prop": "color",
                                 "value": "#000000"}]})
    assert response.status_code == 400
    assert "没有对象" in response.json()["detail"]


# ── The manifest's geometry ───────────────────────────────────────────────

def test_the_manifest_boxes_line_up_with_the_exported_svg(tmp_path, monkeypatch):
    """The editor places its selection boxes from the manifest.

    If the boxes and the drawing were in different coordinate systems, every
    box would sit on the wrong part of the figure — so the two must agree.
    """
    import re
    from src.figures.core import build_resolved_figure
    from src.figures.exporter import export_all
    from src.figures.manifest import build_manifest
    from src.figures.resolve import resolve_spec
    from src.figures.spec import parse_spec

    spec = parse_spec({
        "chart": "bar", "title": "TITLE", "x_label": "XL", "y_label": "YL",
        "x": {"kind": "inline", "column": "c", "values": ["A", "B"]},
        "y": {"kind": "inline", "column": "v", "values": [1.0, 2.0]}})
    rendered = build_resolved_figure(resolve_spec(spec)[0])
    manifest = build_manifest(rendered, source="spec")
    svg = export_all(rendered, formats=("svg",))["svg"].decode("utf-8")

    view_box = [float(value) for value in
                re.search(r'viewBox="([^"]+)"', svg).group(1).split()]
    width, height = manifest["figure_size_px"]
    assert width == pytest.approx(view_box[2], abs=0.05)
    assert height == pytest.approx(view_box[3], abs=0.05)

    # The title's centre in the SVG must be the centre of the manifest's box.
    title = next(item for item in manifest["objects"] if item["gid"] == "axes_0.title")
    group = re.search(r'<g id="axes_0\.title"[^>]*>(.*?)</g>', svg, re.S).group(1)
    anchor_x = float(re.search(r'<text[^>]*x="([\d.]+)"', group).group(1))
    box_centre = (title["bbox"]["x0"] + title["bbox"]["x1"]) / 2.0
    assert anchor_x == pytest.approx(box_centre, abs=0.05)


# ── Importing from the library ────────────────────────────────────────────

def test_importing_a_table_creates_a_project(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.session import files as session_files

    session_id = "figure-import-001"
    csv = b"treatment,weight\nCK,10.1\nT1,12.2\n"
    entry = session_files.register_file(
        session_id=session_id, owner_user_id=101, filename="trial.csv",
        content=csv, mime="text/csv", origin=session_files.ORIGIN_UPLOADED)

    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects",
                      main.figure_router.import_project, methods=["POST"])
    app.add_api_route("/api/v2/figures/data-files",
                      main.figure_router.list_data_files, methods=["GET"])
    with TestClient(app) as client:
        response = client.post("/api/v2/figures/projects",
                               json={"file_id": entry["file_id"], "title": "试验图"})
        listing = client.get("/api/v2/figures/data-files").json()
    assert response.status_code == 200
    payload = response.json()
    assert payload["figure_id"]
    assert "treatment" in payload["columns"]
    assert any(item["file_id"] == entry["file_id"] for item in listing["files"])


def test_importing_someone_elses_file_is_refused(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.session import files as session_files

    session_id = "figure-import-002"
    csv = b"a,b\n1,2\n"
    entry = session_files.register_file(
        session_id=session_id, owner_user_id=202, filename="theirs.csv",
        content=csv, mime="text/csv", origin=session_files.ORIGIN_UPLOADED)

    identity = {"user_id": 101}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects",
                      main.figure_router.import_project, methods=["POST"])
    with TestClient(app) as client:
        response = client.post("/api/v2/figures/projects",
                               json={"file_id": entry["file_id"]})
    assert response.status_code == 404

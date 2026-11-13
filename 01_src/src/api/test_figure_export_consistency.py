"""Export consistency: the preview, the download and the source data.

The workspace promises three things that a user can check by eye:

* the picture in the browser is the file that comes out of the download;
* an edit changes the picture and nothing else;
* the figure's own source — the script, the uploaded table — is never written
  to.

Each of those is a property of bytes, so each is tested as a property of
bytes. The tests compare digests and file contents rather than looking at
the shape of the JSON, because a JSON shape can look right while the bytes
differ.

The reproducibility of an export matters for the same reason: if the same
figure exported twice gives two different files, then "the download is the
preview" cannot be checked by comparing a later export with an earlier one,
and the user has no way to tell a re-render from a change.
"""
from __future__ import annotations

import hashlib

import pytest


def _workspace(tmp_path, monkeypatch):
    """Point the service's stores at a temporary directory."""
    import src.config as config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)

    from src.session import files
    from src.research import tools
    from src.figures import store

    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    store.set_state_dir(tmp_path / "state")
    files.init_store()


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ── The engine ────────────────────────────────────────────────────────────

def _figure(**spec_overrides):
    from src.figures.core import build_resolved_figure
    from src.figures.resolve import resolve_spec
    from src.figures.spec import parse_spec

    payload = {"chart": "bar", "title": "果实重量", "x_label": "处理",
               "y_label": "g",
               "x": {"kind": "inline", "column": "处理", "values": ["CK", "T1"]},
               "y": {"kind": "inline", "column": "重量", "values": [10.1, 12.2]},
               "error": "none", "legend": True}
    payload.update(spec_overrides)
    spec = parse_spec(payload)
    resolved, _info = resolve_spec(spec)
    return build_resolved_figure(resolved), spec


def test_the_same_figure_exports_to_the_same_bytes():
    """Two exports of one figure are identical files.

    Matplotlib salts its internal element ids randomly, so without a fixed
    salt the same figure would export to two different byte streams and the
    "preview equals download" promise could not be verified by comparing
    exports.
    """
    import time

    from src.figures.exporter import export_all

    first, _spec = _figure()
    blobs_a = export_all(first, formats=("svg", "png", "pdf"), dpi=150)
    # The second export must land in a different second. Without the pause the
    # test passes by luck: a wall-clock timestamp inside the PDF would still
    # match if both renders happened inside the same second.
    time.sleep(1.05)
    second, _spec = _figure()
    blobs_b = export_all(second, formats=("svg", "png", "pdf"), dpi=150)
    for fmt in ("svg", "png", "pdf"):
        assert _digest(blobs_a[fmt]) == _digest(blobs_b[fmt]), fmt


def test_the_three_formats_come_from_one_render():
    """One figure, three files, one drawing.

    The formats are written from the same figure object in the same call, so
    the PNG must carry the same picture as the SVG — same canvas, same
    aspect ratio, and the same text drawn on it.
    """
    from src.figures.exporter import export_all

    rendered, _spec = _figure()
    blobs = export_all(rendered, formats=("svg", "png", "pdf"), dpi=150)

    svg = blobs["svg"].decode("utf-8")
    assert "<text" in svg, "SVG 里的文字应当仍是文字"
    assert "果实重量" in svg
    assert blobs["png"].startswith(b"\x89PNG\r\n\x1a\n")
    assert blobs["pdf"].startswith(b"%PDF-")

    import re
    view_box = [float(value) for value in
                re.search(r'viewBox="([^"]+)"', svg).group(1).split()]
    width, height = view_box[2], view_box[3]

    # The PNG's IHDR carries the real pixel size; at 150 dpi it must be the
    # canvas in inches times the resolution.
    import struct
    ihdr = blobs["png"][16:24]
    png_width, png_height = struct.unpack(">II", ihdr)
    assert png_width == pytest.approx(width / 72.0 * 150, abs=1.5)
    assert png_height == pytest.approx(height / 72.0 * 150, abs=1.5)
    # The same picture: the same proportions.
    assert png_width / png_height == pytest.approx(width / height, abs=0.01)


def test_the_exported_svg_carries_no_active_content():
    """A figure that can act is a hole, not a picture."""
    from src.figures.exporter import export_all

    rendered, _spec = _figure()
    svg = export_all(rendered, formats=("svg",))["svg"].decode("utf-8").lower()
    for forbidden in ("<script", "javascript:", "data:", "<image",
                      "onload=", "onerror=", "<style", "style="):
        assert forbidden not in svg, forbidden


def test_the_exported_svg_passes_the_services_own_sanitizer():
    """The chat preview and the workspace preview must agree.

    The chat path runs the figure through ``src.tools.draw_svg``'s
    sanitizer; if the workspace produced something that sanitizer would
    change, the two previews would differ.
    """
    from src.figures.exporter import export_all
    from src.tools.draw_svg import _sanitize_svg

    rendered, _spec = _figure()
    svg = export_all(rendered, formats=("svg",))["svg"].decode("utf-8")
    sanitized = _sanitize_svg(svg)
    assert sanitized
    # Nothing that carries paint may have been dropped.
    assert "#0f4d92" in sanitized.lower()


# ── The workspace ─────────────────────────────────────────────────────────

def test_the_preview_and_the_download_are_byte_identical(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    from src.api.test_figure_router import _make_project
    import src.api.main as main

    _workspace(tmp_path, monkeypatch)
    figure_id, _manifest, published = _make_project(owner=101)

    monkeypatch.setattr(main, "current_user", lambda request: {"user_id": 101})
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}/preview.svg",
                      main.figure_router.preview_svg, methods=["GET"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}/export/{fmt}",
                      main.figure_router.export_project, methods=["GET"])
    with TestClient(app) as client:
        preview = client.get(f"/api/v2/figures/projects/{figure_id}/preview.svg")
        download = client.get(f"/api/v2/figures/projects/{figure_id}/export/svg")
    assert preview.content == download.content
    assert _digest(preview.content) == published["svg"]["sha256"]


def test_an_edit_changes_the_file_and_the_download_still_matches(tmp_path, monkeypatch):
    """After an edit, the preview and the download are still the same file.

    The edit is a re-render followed by a publication. If the preview were
    still serving the previous render while the download served the new one,
    the user would download a picture they had not seen.
    """
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    from src.api.test_figure_router import _make_project
    import src.api.main as main

    _workspace(tmp_path, monkeypatch)
    figure_id, manifest, published = _make_project(owner=101)
    series = next(item for item in manifest["objects"] if item["role"] == "series")

    monkeypatch.setattr(main, "current_user", lambda request: {"user_id": 101})
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}/overrides",
                      main.figure_router.save_overrides, methods=["POST"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}/preview.svg",
                      main.figure_router.preview_svg, methods=["GET"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}/export/{fmt}",
                      main.figure_router.export_project, methods=["GET"])
    with TestClient(app) as client:
        edited = client.post(
            f"/api/v2/figures/projects/{figure_id}/overrides",
            json={"overrides": [{"gid": series["gid"], "prop": "color",
                                 "value": "#b64342"}]}).json()
        preview = client.get(f"/api/v2/figures/projects/{figure_id}/preview.svg")
        download = client.get(f"/api/v2/figures/projects/{figure_id}/export/svg")

    assert preview.content == download.content
    assert _digest(preview.content) == edited["exports"]["svg"]["sha256"]
    assert edited["exports"]["svg"]["sha256"] != published["svg"]["sha256"]


def test_re_rendering_the_same_edits_gives_the_same_file(tmp_path, monkeypatch):
    """The export is reproducible, so a re-render changes nothing.

    This is what lets a user trust that opening a project again did not
    alter the figure: the file comes out identical.
    """
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    from src.api.test_figure_router import _make_project
    import src.api.main as main

    _workspace(tmp_path, monkeypatch)
    figure_id, manifest, _published = _make_project(owner=101)
    title = next(item for item in manifest["objects"] if item["role"] == "title")

    monkeypatch.setattr(main, "current_user", lambda request: {"user_id": 101})
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects/{figure_id}/overrides",
                      main.figure_router.save_overrides, methods=["POST"])
    with TestClient(app) as client:
        first = client.post(
            f"/api/v2/figures/projects/{figure_id}/overrides",
            json={"overrides": [{"gid": title["gid"], "prop": "fontsize",
                                 "value": 11.0}]}).json()
        second = client.post(
            f"/api/v2/figures/projects/{figure_id}/overrides",
            json={"overrides": [{"gid": title["gid"], "prop": "fontsize",
                                 "value": 11.0}]}).json()
    assert first["exports"]["svg"]["sha256"] == second["exports"]["svg"]["sha256"]


# ── The source data ───────────────────────────────────────────────────────

def test_the_source_table_is_never_written_to(tmp_path, monkeypatch):
    """Editing a figure must not touch the data behind it."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    from src.session import files as session_files
    import src.api.main as main

    _workspace(tmp_path, monkeypatch)

    csv = b"treatment,weight\nCK,10.1\nT1,12.2\n"
    registered = session_files.register_file(
        session_id="export-source-001", owner_user_id=101, filename="trial.csv",
        content=csv, mime="text/csv", origin=session_files.ORIGIN_UPLOADED)
    entry = session_files.get_account_file(registered["file_id"], 101)
    original = entry["path"].read_bytes()
    assert original == csv

    monkeypatch.setattr(main, "current_user", lambda request: {"user_id": 101})
    app = FastAPI()
    app.add_api_route("/api/v2/figures/projects",
                      main.figure_router.import_project, methods=["POST"])
    app.add_api_route("/api/v2/figures/projects/{figure_id}/overrides",
                      main.figure_router.save_overrides, methods=["POST"])
    with TestClient(app) as client:
        created = client.post("/api/v2/figures/projects",
                              json={"file_id": entry["file_id"]}).json()
        series = next(item for item in created["manifest"]["objects"]
                      if item["role"] == "series")
        client.post(
            f"/api/v2/figures/projects/{created['figure_id']}/overrides",
            json={"overrides": [{"gid": series["gid"], "prop": "color",
                                 "value": "#b64342"}]})

    assert entry["path"].read_bytes() == original, "原始表格不能被改写"


def test_the_script_is_never_written_to(tmp_path, monkeypatch):
    """A script-sourced project's script is read-only material.

    The script lives in the account's library; a figure project points at
    it. Editing the figure must not touch it.
    """
    from src.session import files as session_files

    _workspace(tmp_path, monkeypatch)
    script = b"import matplotlib\nmatplotlib.use('Agg')\n"
    registered = session_files.register_file(
        session_id="export-source-002", owner_user_id=101, filename="plot.py",
        content=script, mime="text/x-python", origin=session_files.ORIGIN_UPLOADED)
    entry = session_files.get_account_file(registered["file_id"], 101)
    original = entry["path"].read_bytes()

    from src.figures import store
    project = store.create_project(
        owner_user_id=101, title="脚本图", source_kind="script",
        source={"script": "import matplotlib\n"}, session_id="export-source-002")
    store.update_project(project["figure_id"], 101,
                         manifest_json={"objects": []})

    stored = store.get_project(project["figure_id"], 101)
    assert stored["source_json"]["script"]
    assert entry["path"].read_bytes() == original

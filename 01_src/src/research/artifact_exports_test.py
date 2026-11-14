"""Existing-byte export contracts, real temporary library and offline HTTP routes."""
import io
import pytest

from src.core.test_publication_reconcile import isolated, OWNER, OTHER, SID  # noqa: F401
from src.figures import store
from src.research import artifact_exports as exports
from src.research.artifacts import publish_artifact
from src.session import files

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><title>Synthetic</title><rect width="10" height="10"/></svg>'


@pytest.fixture(autouse=True)
def figure_store(tmp_path):
    store.set_state_dir(tmp_path / "figures")
    yield
    store.set_state_dir(None)


def upload(name="data.csv", content=b"A,B\n1,2\n", **kwargs):
    return files.register_file(session_id=SID, owner_user_id=OWNER, filename=name, content=content, **kwargs)


def publish(name, content, mime):
    return publish_artifact(owner=OWNER, session_id=SID, filename=name, content=content, mime=mime,
                            producer_tool="figure_workspace")


def project_with_exports(mapping, owner=OWNER):
    project = store.create_project(owner_user_id=owner, session_id=SID, source_kind="script",
                                   source={"script": "raise RuntimeError('must never execute')"})
    assert store.update_project(project["figure_id"], owner, exports_json=mapping, render_version=1)
    return project


def generated_pair():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
    svg = publish("synthetic.svg", SVG, "image/svg+xml")
    png = publish("synthetic.png", buffer.getvalue(), "image/png")
    project = project_with_exports({"svg": svg, "png": png})
    return svg, png, project


@pytest.mark.parametrize("name,blob", [
    ("synthetic.csv", b"A,B\n1,2\n"), ("synthetic.tsv", b"A\tB\n1\t2\n"),
    ("synthetic.html", b"<!doctype html><html><body>Synthetic</body></html>"),
    ("synthetic.pdf", b"%PDF-existing opaque uploaded bytes"),
    ("synthetic.bib", b"@misc{synthetic,title={Synthetic}}"),
    ("synthetic.bin", b"\x00\x01\x02"), ("no-extension", b"original"),
])
def test_every_existing_format_returns_original_identity_without_copy(isolated, name, blob):
    entry = upload(name, blob)
    payload = exports.export_options(OWNER, entry["file_id"])
    assert len(payload["options"]) == 1
    option = payload["options"][0]
    first = exports.export_file(OWNER, entry["file_id"], option["target_format"], source_sha256=entry["sha256"])
    second = exports.export_file(OWNER, entry["file_id"], option["target_format"])
    assert first == second
    assert first["file_id"] == entry["file_id"]
    assert first["download_url"] == entry["download_url"]
    assert len(files.list_files(SID, OWNER)) == 1
    assert files.get_account_file(entry["file_id"], OWNER)["path"].read_bytes() == blob
    assert payload["conversion_available"] is False


def test_annotated_multisheet_workbook_is_never_flattened(isolated):
    from openpyxl import Workbook, load_workbook
    from openpyxl.comments import Comment
    book = Workbook()
    book.active["A1"] = "Synthetic annotation"
    book.active["A1"].comment = Comment("Preserve this note", "fixture")
    book.create_sheet("Evidence")["B2"] = "Synthetic evidence"
    buffer = io.BytesIO()
    book.save(buffer)
    entry = upload("annotated.xlsx", buffer.getvalue())
    payload = exports.export_options(OWNER, entry["file_id"])
    assert [item["target_format"] for item in payload["options"]] == ["xlsx"]
    with pytest.raises(exports.ExportError, match="所选格式") as error:
        exports.export_file(OWNER, entry["file_id"], "csv")
    assert error.value.status == 422
    output = exports.export_file(OWNER, entry["file_id"], "xlsx")
    saved = files.get_account_file(output["file_id"], OWNER)["path"].read_bytes()
    assert saved == buffer.getvalue()
    reopened = load_workbook(io.BytesIO(saved))
    assert reopened.sheetnames == ["Sheet", "Evidence"]
    assert reopened.active["A1"].comment.text == "Preserve this note"
    reopened.close()


def test_saved_figure_formats_are_selected_without_render_or_model(isolated, monkeypatch):
    svg, png, _ = generated_pair()
    from src.figures import projects
    from src.research import tools
    monkeypatch.setattr(projects, "_render_edits", lambda *a, **kw: pytest.fail("render invoked"))
    monkeypatch.setattr(projects, "publish_rendered", lambda *a, **kw: pytest.fail("figure regeneration invoked"))
    monkeypatch.setattr(tools.export_paper_analysis, "func", lambda *a, **kw: pytest.fail("model delivery invoked"))
    payload = exports.export_options(OWNER, svg["file_id"])
    assert [item["target_format"] for item in payload["options"]] == ["svg", "png"]
    selected = exports.export_file(OWNER, svg["file_id"], "png", source_sha256=svg["sha256"])
    assert selected["file_id"] == png["file_id"]
    assert selected["source_file_id"] == svg["file_id"]
    assert len(files.list_files(SID, OWNER)) == 2
    with pytest.raises(exports.ExportError) as error:
        exports.export_file(OWNER, svg["file_id"], "pdf")
    assert error.value.code == "unsupported_format"


def test_target_version_is_pinned_when_a_figure_reuses_unchanged_source_bytes(isolated):
    svg, png, project = generated_pair()
    # A different saved PNG can coexist with identical SVG bytes. The option
    # selected by the UI must not silently switch to that newer target.
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (3, 3), "white").save(buffer, format="PNG")
    new_png = publish("synthetic.png", buffer.getvalue(), "image/png")
    assert store.update_project(project["figure_id"], OWNER, exports_json={"svg": svg, "png": new_png}, render_version=2)
    with pytest.raises(exports.ExportError) as error:
        exports.export_file(OWNER, svg["file_id"], "png", source_sha256=svg["sha256"],
                            target_file_id=png["file_id"], target_sha256=png["sha256"])
    assert error.value.code == "target_changed" and error.value.status == 409


def test_unrelated_files_never_become_export_siblings(isolated):
    first = publish("same-name.svg", SVG, "image/svg+xml")
    other = publish("same-name.csv", b"A\n1\n", "text/csv")
    project_with_exports({"csv": other})
    payload = exports.export_options(OWNER, first["file_id"])
    assert len(payload["options"]) == 1


def test_updated_figure_does_not_redirect_old_source_to_new_render(isolated):
    svg, png, project = generated_pair()
    newer_svg = publish("synthetic.svg", SVG.replace(b"Synthetic", b"New synthetic"), "image/svg+xml")
    assert store.update_project(project["figure_id"], OWNER, exports_json={"svg": newer_svg}, render_version=2)
    assert [item["file_id"] for item in exports.export_options(OWNER, svg["file_id"])["options"]] == [svg["file_id"]]
    with pytest.raises(exports.ExportError):
        exports.export_file(OWNER, svg["file_id"], "png")


def test_private_and_internal_files_are_not_exportable(isolated):
    public = upload()
    internal = upload("private.csv", b"A\n3\n", visibility="internal")
    for owner, fid in ((OTHER, public["file_id"]), (OWNER, internal["file_id"]), (0, public["file_id"])):
        with pytest.raises(exports.ExportError) as error:
            exports.export_options(owner, fid)
        assert error.value.status == 404


def test_source_hash_and_pinned_expected_hash_are_enforced(isolated):
    entry = upload()
    with pytest.raises(exports.ExportError) as mismatch:
        exports.export_file(OWNER, entry["file_id"], "csv", source_sha256="a" * 64)
    assert mismatch.value.status == 409
    path = files.get_account_file(entry["file_id"], OWNER)["path"]
    path.write_bytes(b"A,B\n9,8\n")
    with pytest.raises(exports.ExportError) as changed:
        exports.export_options(OWNER, entry["file_id"])
    assert changed.value.code == "source_changed"


def test_invalid_peer_hash_is_omitted_from_options(isolated):
    svg, png, _ = generated_pair()
    files.get_account_file(png["file_id"], OWNER)["path"].write_bytes(b"tampered")
    assert [item["target_format"] for item in exports.export_options(OWNER, svg["file_id"])["options"]] == ["svg"]


def test_http_routes_authenticate_select_and_download_existing_bytes(isolated, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import main
    identity = {"user_id": OWNER}
    monkeypatch.setattr(main, "current_user", lambda request: identity)
    app = FastAPI()
    paths = {"/api/v2/files/{file_id}/export-options", "/api/v2/files/{file_id}/export", "/api/v2/files/{file_id}/download"}
    app.router.routes.extend(route for route in main.app.routes if getattr(route, "path", "") in paths)
    svg, png, _ = generated_pair()
    base = "/api/v2/files/" + svg["file_id"]
    with TestClient(app) as client:
        options = client.get(base + "/export-options")
        assert options.status_code == 200, options.text
        assert len(options.json()["options"]) == 2
        result = client.post(base + "/export", json={"target_format": "png", "source_sha256": svg["sha256"]})
        assert result.status_code == 200, result.text
        assert result.json()["file_id"] == png["file_id"]
        assert client.get(result.json()["download_url"]).content.startswith(b"\x89PNG")
        assert client.post(base + "/export", json={"target_format": "csv"}).status_code == 422
        assert client.post(base + "/export", json={"target_format": "svg", "owner": OWNER}).status_code == 422
        identity["user_id"] = OTHER
        assert client.get(base + "/export-options").status_code == 404
        assert client.post(base + "/export", json={"target_format": "svg"}).status_code == 404
        assert client.get(result.json()["download_url"]).status_code == 404

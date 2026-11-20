"""F5 acceptance: authorized SVG projects, edits, QA receipts and rejection.

Every diagram here is a synthetic architecture or research-workflow schematic.
The suite uses a temporary figure store and a fake account file registry (the
same pattern as the F4 contract tests). It never touches a production runtime,
database, key, deployed port, network API, vendor source, model or browser.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("citrus_figures")

from src.figures import projects, store, svg_projects
from src.figures.nature_runtime import current_vendor_root

OWNER = 7
SESSION = "session1234"


def _architecture_svg(extra: str = "") -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120mm" height="70mm" '
        'viewBox="0 0 340 200">'
        '<title id="title">Synthetic architecture</title>'
        '<text id="label" x="20" y="24" font-size="7" fill="#222222">'
        "Synthetic architecture</text>"
        '<rect id="input" x="20" y="50" width="72" height="36" fill="#eef3ff" '
        'stroke="#333333" stroke-width="0.7"/>'
        '<text id="input_label" x="34" y="72" font-size="7" fill="#222222">Input</text>'
        '<line id="edge" x1="92" y1="68" x2="132" y2="68" stroke="#444444" '
        'stroke-width="0.7" marker-end="url(#arrow)"/>'
        '<rect id="process" x="132" y="50" width="82" height="36" fill="#ffffff" '
        'stroke="#333333" stroke-width="0.7"/>'
        '<text id="process_label" x="148" y="72" font-size="7" fill="#222222">Process</text>'
        '<rect id="output" x="244" y="50" width="72" height="36" fill="#f6f6f6" '
        'stroke="#333333" stroke-width="0.7"/>'
        '<text id="output_label" x="258" y="72" font-size="7" fill="#222222">Output</text>'
        '<defs><marker id="arrow" markerWidth="4" markerHeight="4" markerUnits="strokeWidth">'
        '<path d="M0,0 L4,2 L0,4 Z" fill="#444444"/></marker></defs>'
        f"{extra}"
        "</svg>"
    )


def _workflow_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="110mm" height="60mm" '
        'viewBox="0 0 300 160">'
        '<text id="workflow_title" x="16" y="22" font-size="7" fill="#222222">'
        "Research workflow</text>"
        '<rect id="step_a" x="16" y="44" width="64" height="30" fill="#eef3ff" '
        'stroke="#333333" stroke-width="0.7"/>'
        '<text id="step_a_label" x="28" y="63" font-size="7" fill="#222222">Collect</text>'
        '<rect id="step_b" x="112" y="44" width="64" height="30" fill="#eef3ff" '
        'stroke="#333333" stroke-width="0.7"/>'
        '<text id="step_b_label" x="124" y="63" font-size="7" fill="#222222">Check</text>'
        '<rect id="step_c" x="208" y="44" width="64" height="30" fill="#eef3ff" '
        'stroke="#333333" stroke-width="0.7"/>'
        '<text id="step_c_label" x="224" y="63" font-size="7" fill="#222222">Report</text>'
        "</svg>"
    )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    previous = store._STATE_DIR
    store.set_state_dir(tmp_path / "state")
    entries: dict[str, dict] = {}
    counter = [0]
    state = SimpleNamespace(entries=entries, published=[], fail=False)

    def add(blob: bytes, *, producer: str = "svg_project_test",
            origin: str = "generated", sid: str = SESSION) -> dict:
        counter[0] += 1
        file_id = f"{counter[0]:032x}"
        path = tmp_path / f"{file_id}.svg"
        path.write_bytes(blob)
        entries[file_id] = {
            "file_id": file_id, "path": path,
            "sha256": hashlib.sha256(blob).hexdigest(),
            "size_bytes": len(blob), "producer_tool": producer,
            "origin": origin, "session_id": sid,
            "download_url": f"/api/v2/files/{file_id}/download",
            "filename": file_id,
        }
        return entries[file_id]

    def publish(*, owner, session_id, filename, content, mime, producer_tool,
                request_id="", **kwargs):
        if state.fail:
            raise OSError("storage unavailable")
        entry = add(content, producer=producer_tool, sid=session_id)
        state.published.append(entry)
        return {
            "file_id": entry["file_id"], "filename": filename,
            "sha256": entry["sha256"], "size": len(content),
            "download_url": entry["download_url"],
            "artifact_url": f"/api/v2/artifacts/{entry['file_id']}",
            "artifact_id": entry["file_id"],
        }

    @contextlib.contextmanager
    def lease(*args, **kwargs):
        yield SimpleNamespace(owner=OWNER)

    from src.core import account_leases
    from src.research import artifacts as artifacts_module
    from src.session import files as files_module
    from src.session.manager import session_manager

    monkeypatch.setattr(account_leases, "account_lease", lease)
    monkeypatch.setattr(store, "_pin_files", lambda *args, **kwargs: ("", []))
    monkeypatch.setattr(
        session_manager, "session_belongs_to",
        lambda sid, owner: sid == SESSION and int(owner) == OWNER)
    monkeypatch.setattr(
        files_module, "get_account_file",
        lambda file_id, owner, **kwargs: entries.get(str(file_id)))
    monkeypatch.setattr(artifacts_module, "publish_artifact", publish)
    state.add = add
    try:
        yield state
    finally:
        store.set_state_dir(previous)


def _create(isolated, svg: str, *, title: str = "arch"):
    return svg_projects.create_svg_project(
        owner=OWNER, session_id=SESSION, svg=svg,
        alt_text="Synthetic architecture", title=title,
        vendor_root=current_vendor_root())


def test_create_publishes_hash_bound_project_with_initial_and_current_receipt(isolated):
    result = _create(isolated, _architecture_svg())
    figure_id = result["figure_id"]
    assert len(figure_id) == 32 and result["render_version"] == 1
    project = store.get_project(figure_id, OWNER)
    assert project is not None and project["source_kind"] == "svg"
    source, manifest = project["source_json"], project["manifest_json"]
    blob = source["svg"].encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    assert result["svg_sha256"] == source["svg_sha256"] == digest
    export = project["exports_json"]["svg"]
    assert export["sha256"] == digest and export["file_id"] in isolated.entries
    assert isolated.entries[export["file_id"]]["path"].read_bytes() == blob
    assert result["links"]["file"]["download_url"] == export["download_url"]
    assert result["links"]["workbench_url"] == f"/figures?project={figure_id}"
    assert manifest["initial_svg_receipt"]["receipt_id"] == \
        manifest["current_qa_receipt"]["receipt_id"]
    assert manifest["initial_svg_receipt"]["artifacts"][0]["sha256"] == digest
    assert manifest["current_qa_receipt"]["mode"] == "initial"
    assert manifest["edit_provenance"]["mode"] == "initial"
    assert manifest["figure_kind"] == "svg_schematic"
    assert source["edit_contract"]["operations"]["set_text"]["tags"]


def test_edit_preserves_initial_receipt_and_rejects_stale_revision(isolated):
    created = _create(isolated, _workflow_svg(), title="workflow")
    figure_id = created["figure_id"]
    edits = [
        {"op": "set_label", "target": "workflow_title", "value": "Research workflow v2"},
        {"op": "set_color", "target": "step_a", "value": "#e8f0ff"},
        {"op": "set_stroke", "target": "step_a", "attribute": "stroke-width", "value": "0.9"},
        {"op": "set_font", "target": "workflow_title", "attribute": "font-size", "value": "8"},
    ]
    edited = svg_projects.apply_svg_project_edits(
        figure_id=figure_id, owner=OWNER, edits=edits, expected_revision=1,
        vendor_root=current_vendor_root())
    assert edited["render_version"] == 2
    project = store.get_project(figure_id, OWNER)
    source, manifest = project["source_json"], project["manifest_json"]
    assert "Research workflow v2" in source["svg"]
    assert 'fill="#e8f0ff"' in source["svg"]
    assert 'stroke-width="0.9"' in source["svg"]
    assert 'font-size="8"' in source["svg"]
    assert [item["target"] for item in source["accepted_edits"]] == \
        ["workflow_title", "step_a", "step_a", "workflow_title"]
    assert manifest["initial_svg_receipt"]["receipt_id"] == created["initial_receipt_id"]
    assert manifest["current_qa_receipt"]["receipt_id"] == edited["current_receipt_id"]
    assert manifest["current_qa_receipt"]["supersedes"] == created["current_receipt_id"]
    assert manifest["initial_svg_receipt"]["receipt_id"] != \
        manifest["current_qa_receipt"]["receipt_id"]
    assert project["exports_json"]["svg"]["sha256"] == \
        hashlib.sha256(source["svg"].encode("utf-8")).hexdigest()
    with pytest.raises(projects.ProjectError) as conflict:
        svg_projects.apply_svg_project_edits(
            figure_id=figure_id, owner=OWNER, edits=edits, expected_revision=1,
            vendor_root=current_vendor_root())
    assert conflict.value.code == "revision_conflict"
    current = store.get_project(figure_id, OWNER)
    assert current["render_version"] == 2 and current["source_json"]["svg"] == source["svg"]


def test_reaudit_uses_current_export_bytes_and_publishes_real_receipt_link(isolated):
    created = _create(isolated, _architecture_svg())
    figure_id = created["figure_id"]
    result = svg_projects.reaudit_svg_project(
        figure_id=figure_id, owner=OWNER, expected_revision=1,
        publish_receipt=True, vendor_root=current_vendor_root())
    receipt = result["receipt"]
    assert receipt["mode"] == "reaudit"
    assert receipt["supersedes"] == created["current_receipt_id"]
    assert receipt["artifacts"][0]["sha256"] == created["svg_sha256"]
    assert result["receipt_id"] == receipt["receipt_id"]
    receipt_file = result["receipt_file"]
    assert receipt_file["download_url"] and receipt_file["file_id"] in isolated.entries
    stored = json.loads(isolated.entries[receipt_file["file_id"]]["path"].read_text("utf-8"))
    assert stored["receipt_id"] == receipt["receipt_id"]
    assert stored["artifacts"][0]["sha256"] == created["svg_sha256"]
    # Reauditing certifies the stored bytes; it never rewrites the project.
    assert store.get_project(figure_id, OWNER)["render_version"] == 1


@pytest.mark.parametrize("unsafe", [
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" viewBox="0 0 10 10">'
    '<script>alert(1)</script></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" viewBox="0 0 10 10">'
    '<foreignObject><body xmlns="http://www.w3.org/1999/xhtml">x</body></foreignObject></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" viewBox="0 0 10 10">'
    '<rect href="https://evil.example/x"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" viewBox="0 0 10 10">'
    '<rect onclick="alert(1)"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" viewBox="0 0 10 10">'
    '<rect fill="url(https://evil.example/x)"/></svg>',
    '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" viewBox="0 0 10 10"/>',
])
def test_unsafe_svg_is_rejected_before_any_project_exists(isolated, unsafe):
    with pytest.raises(projects.ProjectError) as exc:
        _create(isolated, unsafe)
    assert exc.value.code == "svg_invalid"
    assert store.list_projects(OWNER) == []


def test_sanitizer_allows_safe_local_refs_only():
    safe = svg_projects.sanitize_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20" viewBox="0 0 20 20">'
        '<defs><path id="shape" d="M0,0 L4,0 L0,4 Z"/></defs>'
        '<rect id="box" href="#shape" clip-path="url(#clip)"/>'
        '<clipPath id="clip"><rect width="4" height="4"/></clipPath></svg>')
    assert 'href="#shape"' in safe and 'url(#clip)' in safe
    with pytest.raises(ValueError):
        svg_projects.sanitize_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" width="20" viewBox="0 0 20 20">'
            '<rect href="//evil.example/x"/></svg>')


@pytest.mark.parametrize(("edits", "code"), [
    ([{"op": "set_attribute", "target": "input", "attribute": "x", "value": "30"}],
     "invalid_edit"),
    ([{"op": "set_attribute", "target": "input", "attribute": "href", "value": "#x"}],
     "invalid_edit"),
    ([{"op": "set_text", "target": "missing", "value": "X"}], "unknown_target"),
    ([{"op": "set_color", "target": "input", "value": "url(https://evil.example/x)"}],
     "invalid_edit_value"),
    ([{"op": "set_font", "target": "input", "attribute": "font-size", "value": "8"}],
     "unsupported_edit"),
    ([{"op": "set_text", "target": "input", "value": "X"}], "unsupported_edit"),
])
def test_edit_vocabulary_rejects_geometry_external_values_and_wrong_targets(
        isolated, edits, code):
    created = _create(isolated, _architecture_svg())
    with pytest.raises(projects.ProjectError) as exc:
        svg_projects.apply_svg_project_edits(
            figure_id=created["figure_id"], owner=OWNER, edits=edits,
            expected_revision=1, vendor_root=current_vendor_root())
    assert exc.value.code == code
    assert store.get_project(created["figure_id"], OWNER)["render_version"] == 1


def test_ambiguous_ids_are_refused_and_text_is_escaped():
    duplicate = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" viewBox="0 0 40 20">'
        '<text id="dup" x="2" y="8" font-size="6">A</text>'
        '<text id="dup" x="2" y="16" font-size="6">B</text></svg>')
    with pytest.raises(projects.ProjectError) as exc:
        svg_projects.apply_svg_edits(
            duplicate, [{"op": "set_text", "target": "dup", "value": "C"}])
    assert exc.value.code == "ambiguous_target"
    edited = svg_projects.apply_svg_edits(
        _architecture_svg(),
        [{"op": "set_text", "target": "label", "value": "A < B & C"}])
    assert "A &lt; B &amp; C" in edited
    assert "<script" not in edited


def test_publish_failure_leaves_no_project_or_link(isolated):
    isolated.fail = True
    with pytest.raises(projects.ProjectError) as exc:
        _create(isolated, _architecture_svg())
    assert exc.value.code == "publish_failed"
    assert store.list_projects(OWNER) == []


def test_edit_publish_failure_keeps_previous_verified_revision(isolated):
    created = _create(isolated, _architecture_svg())
    figure_id = created["figure_id"]
    before = store.get_project(figure_id, OWNER)
    isolated.fail = True
    with pytest.raises(projects.ProjectError) as exc:
        svg_projects.apply_svg_project_edits(
            figure_id=figure_id, owner=OWNER,
            edits=[{"op": "set_text", "target": "label", "value": "Changed"}],
            expected_revision=1, vendor_root=current_vendor_root())
    assert exc.value.code == "publish_failed"
    after = store.get_project(figure_id, OWNER)
    assert after["render_version"] == 1
    assert after["source_json"]["svg"] == before["source_json"]["svg"]
    assert after["exports_json"] == before["exports_json"]


def test_draw_svg_saves_an_authorized_project_and_returns_links(isolated):
    pytest.importorskip("langchain_core")
    from src.core import figures, tracing
    from src.tools import draw_svg as draw_svg_module

    figures.begin_request()
    tracing.set_user_id(OWNER)
    tracing.set_session_id(SESSION)
    try:
        call = getattr(draw_svg_module.draw_svg, "func", None)
        if call is None:
            text = draw_svg_module.draw_svg.invoke(
                {"svg": _architecture_svg(), "alt_text": "Synthetic architecture"})
        else:
            text = call(_architecture_svg(), "Synthetic architecture")
    finally:
        tracing.set_user_id(None)
        tracing.set_session_id("")
    assert "[[FIGURE:1]]" in text and "工作台：" in text
    saved = store.list_projects(OWNER)
    assert len(saved) == 1
    figure_id = saved[0]["figure_id"]
    assert f"/figures?project={figure_id}" in text
    export = saved[0]["exports_json"]["svg"]
    assert f"下载：{export['download_url']}" in text
    assert export["sha256"] == saved[0]["source_json"]["svg_sha256"]


def test_draw_svg_keeps_the_existing_chat_placeholder_without_auth_context():
    pytest.importorskip("langchain_core")
    from src.core import figures
    from src.tools import draw_svg as draw_svg_module

    assert draw_svg_module._sanitize_svg is svg_projects.sanitize_svg
    figures.begin_request()
    call = getattr(draw_svg_module.draw_svg, "func", None)
    if call is None:
        text = draw_svg_module.draw_svg.invoke(
            {"svg": _architecture_svg(), "alt_text": "Synthetic architecture"})
    else:
        text = call(_architecture_svg(), "Synthetic architecture")
    assert text.startswith("SVG 已通过安全校验")
    assert "[[FIGURE:1]]" in text
    assert "（图示说明：Synthetic architecture）" in text
    assert "工作台：" not in text

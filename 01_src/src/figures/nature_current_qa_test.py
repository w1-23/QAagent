"""F4 acceptance: current-byte QA over real exports and stored bytes.

The tests execute the real Matplotlib export, the pinned 44-file vendor checks
and the real worker protocol. ``LocalBackend`` runs the actual ``worker.py`` in
a native child process; it is the contained development runner, not the
production Docker/Apptainer security boundary. No network, model, deployed
service or production database is used. Every input here is synthetic.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("citrus_figures")

from citrus_figures import render_figure, write_bundle
from citrus_figures.bundle import verify_saved_bundle
from citrus_figures.render_policy import export_with_layout
from citrus_figures.verification import verify_current_figure

from src.figures import core, current_artifact_api, exporter, projects, store
from src.figures.nature_runtime import (current_vendor_root, layout_evidence,
                                        run_current_qa, verify_svg_bytes)
from src.figures.sandbox import LocalBackend, SandboxJob
from src.figures.script_outputs import output_slots

ROOT = Path(__file__).resolve().parents[3]
VENDOR = ROOT / "skills/nature-figure/vendor/nature-skills"

SEVEN = {"format_integrity", "source_preflight", "panel_alignment", "pdf_text",
         "pdf_collisions", "svg_schematic", "visual"}
EXECUTED = {"executed", "attempted", "not_run"}
CONCLUSIONS = {"pass", "warn", "fail", "not_applicable", "undetermined"}

SCRIPT = '''
import matplotlib.pyplot as plt
fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.0))
line, = axes[0].plot([0, 1, 2], [0, 1, 4], label="trend")
axes[0].set_title("A plot")
axes[1].bar(["x", "y"], [3, 5])
axes[1].set_title("B bars")
'''
OUTPUTS = {"svg": "figure.svg", "png": "figure.png",
           "pdf": "figure.pdf", "manifest": "objects.json"}


def _sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _run(script=SCRIPT, *, overrides=None, mode="initial", outputs=None,
         contract=None, output_key=""):
    runner = LocalBackend()
    job = SandboxJob(script=script, job_id=f"f4-{mode}", outputs=outputs or dict(OUTPUTS),
                     overrides=list(overrides or []), replay_overrides=bool(overrides),
                     render_mode=mode, output_contract=dict(contract or {}),
                     output_key=output_key)
    result = runner.run(job)
    assert result.ok, result.describe()
    return result


def _assert_measured(receipt: dict) -> None:
    """Every check states execution, conclusion, coverage and measured bytes."""
    assert receipt["version"] == 2
    for check in receipt["checks"]:
        assert check["executed"] in EXECUTED
        assert check["conclusion"] in CONCLUSIONS
        assert isinstance(check["coverage"], dict)
        assert isinstance(check["diagnostics"], dict)
        if check["executed"] == "executed" and check["artifact"] is not None:
            assert len(check["artifact"]["sha256"]) == 64
            int(check["artifact"]["sha256"], 16)
    assert any(check["executed"] == "executed" for check in receipt["checks"])


def _artifact_sha(receipt: dict, name: str, fmt: str) -> list[str]:
    return [check["artifact"]["sha256"] for check in receipt["checks"]
            if check["check"] == name and (check["artifact"] or {}).get("format") == fmt]


def _render(script: str):
    result = core.capture_figure(script, check_layout=False)
    assert result.error == "" and result.figures_seen == 1
    rendered = core.RenderedFigure(result.figure, list(result.figure.axes))
    pdf, layout = export_with_layout(rendered, exporter=exporter, dpi=300)
    return rendered, pdf, layout


def test_initial_worker_current_qa_reuses_one_audited_pdf():
    result = _run()
    manifest = json.loads(result.outputs["manifest"])
    qa = manifest["current_qa_receipt"]
    layout = manifest["current_qa_layout"]
    pdf_sha = _sha(result.outputs["pdf"])

    _assert_measured(qa)
    assert qa["mode"] == "initial"
    assert {check["check"] for check in qa["checks"]} == SEVEN
    assert {check["check"] for check in qa["checks"] if check["executed"] == "executed"} == {
        "format_integrity", "source_preflight", "panel_alignment",
        "pdf_text", "pdf_collisions", "svg_schematic"}
    visual = next(check for check in qa["checks"] if check["check"] == "visual")
    assert (visual["executed"], visual["conclusion"], visual["reason_code"]) == (
        "not_run", "undetermined", "capability_absent")
    assert qa["policy"]["blocking_checks"] == [
        "format_integrity", "panel_alignment", "pdf_collisions", "pdf_text"]

    # The layout was captured at the single PDF draw and names those exact bytes.
    assert layout["capture"] == "pdf_draw_event" and layout["rerendered"] is False
    assert layout["artifact_sha256"] == pdf_sha
    for name in ("pdf_text", "pdf_collisions"):
        assert _artifact_sha(qa, name, "pdf") == [pdf_sha]
    assert pdf_sha in _artifact_sha(qa, "format_integrity", "pdf")
    assert _artifact_sha(qa, "svg_schematic", "svg") == [_sha(result.outputs["svg"])]

    # The legacy v1 initial receipt and the v2 acknowledgement are preserved.
    legacy = manifest["nature_skill_receipt"]
    assert legacy["version"] == 1 and legacy["commit"] == "7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8"
    assert legacy["pdf_sha256"] == pdf_sha
    ack = manifest["render_receipt"]
    assert (ack["version"], ack["mode"], ack["nature_audit"]) == (2, "initial", "performed")
    assert ack["checks"]["pdf"]["sha256"] == pdf_sha
    assert ack["current_qa_receipt"]["receipt_id"] == qa["receipt_id"]
    assert ack["current_qa_layout"] == layout
    # The requested download is the audited PDF, not a second export.
    assert _sha(result.outputs["pdf"]) == layout["artifact_sha256"]


V2_SCRIPT = '''
import matplotlib.pyplot as plt
fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.0))
axes[0].plot([0, 1, 2], [0, 1, 4]); axes[0].set_title("A")
axes[1].bar(["x", "y"], [3, 5]); axes[1].set_title("B")
emit.figure("main", fig)
'''


def test_svg_only_v2_outputs_keep_one_internal_audit_pdf():
    """New presets declare SVG only; the audited PDF stays the same export."""
    contract = {"version": 2, "tables": [],
                "figures": [{"name": "main", "title": "main", "formats": ["svg"]}]}
    result = _run(script=V2_SCRIPT, contract=contract, outputs=output_slots(contract))
    assert "figure:main:pdf" not in result.outputs
    manifest = json.loads(result.outputs["figure:main:manifest"])
    qa, layout = manifest["current_qa_receipt"], manifest["current_qa_layout"]
    _assert_measured(qa)
    internal = set()
    for check in qa["checks"]:
        if check["check"] in {"pdf_text", "pdf_collisions"}:
            assert check["executed"] == "executed"
            assert check["artifact"]["format"] == "pdf"
            internal.add(check["artifact"]["sha256"])
    assert len(internal) == 1
    internal_sha = internal.pop()
    assert layout["artifact_sha256"] == internal_sha
    assert internal_sha in _artifact_sha(qa, "format_integrity", "pdf")
    assert _artifact_sha(qa, "svg_schematic", "svg") == [
        _sha(result.outputs["figure:main:svg"])]
    assert set(manifest["render_receipt"]["checks"]) == {"svg"}
    assert manifest["render_receipt"]["checks"]["svg"]["sha256"] == (
        _sha(result.outputs["figure:main:svg"]))

    # An explicitly requested PDF is the same bytes that were audited.
    contract["figures"][0]["formats"] = ["svg", "pdf"]
    second = _run(script=V2_SCRIPT, contract=contract, outputs=output_slots(contract))
    requested = json.loads(second.outputs["figure:main:manifest"])
    pdf_sha = _sha(second.outputs["figure:main:pdf"])
    assert requested["current_qa_layout"]["artifact_sha256"] == pdf_sha
    assert _artifact_sha(requested["current_qa_receipt"], "pdf_text", "pdf") == [pdf_sha]
    assert _artifact_sha(requested["current_qa_receipt"], "pdf_collisions", "pdf") == [pdf_sha]
    assert requested["render_receipt"]["checks"]["pdf"]["sha256"] == pdf_sha


def test_saved_edit_audits_new_bytes_with_layout_binding():
    first = _run()
    first_pdf = _sha(first.outputs["pdf"])
    manifest = json.loads(first.outputs["manifest"])
    target = next(obj for obj in manifest["objects"] if "color" in (obj.get("editable") or []))
    second = _run(overrides=[{"gid": target["gid"], "prop": "color", "value": "#c0392b"}],
                  mode="edit")
    edited = json.loads(second.outputs["manifest"])
    qa = edited["current_qa_receipt"]
    layout = edited["current_qa_layout"]
    pdf_sha = _sha(second.outputs["pdf"])

    _assert_measured(qa)
    assert qa["mode"] == "edit"
    assert qa["policy"]["blocking_checks"] == ["format_integrity"]
    assert "nature_skill_receipt" not in edited
    assert edited["render_receipt"]["nature_audit"] == "not_repeated_manual_edit"
    assert layout["artifact_sha256"] == pdf_sha != first_pdf
    assert _artifact_sha(qa, "panel_alignment", "pdf") == [pdf_sha]
    assert {check["check"] for check in qa["checks"]} == SEVEN
    assert qa["checks"][0]["conclusion"] == "pass"


def test_current_artifact_reaudit_binds_stored_layout_or_records_gap():
    result = _run()
    manifest = json.loads(result.outputs["manifest"])
    artifacts = [{"format": fmt, "bytes": result.outputs[fmt], "role": "figure"}
                 for fmt in ("pdf", "svg", "png")]
    pdf_sha = _sha(result.outputs["pdf"])

    bound = current_artifact_api.audit_current_bytes(
        artifacts=artifacts, source=SCRIPT, backend="python",
        stored_layout=manifest["current_qa_layout"])
    assert bound["layout"]["binding"] == "bound_to_current_pdf"
    assert bound["layout"]["artifact_sha256"] == pdf_sha
    assert bound["layout"]["layout"]["panels"] == manifest["current_qa_layout"]["layout"]["panels"]
    assert _artifact_sha(bound, "panel_alignment", "pdf") == [pdf_sha]
    assert bound["receipt_id"] == current_artifact_api._receipt_id(bound)

    legacy = current_artifact_api.audit_current_bytes(
        artifacts=artifacts, source=SCRIPT, stored_layout=None)
    assert legacy["layout"]["binding"] == "absent"
    panel = next(c for c in legacy["checks"] if c["check"] == "panel_alignment")
    assert (panel["executed"], panel["conclusion"], panel["reason_code"]) == (
        "not_run", "undetermined", "bound_layout_absent")

    stale = dict(manifest["current_qa_layout"], artifact_sha256="0" * 64)
    unbound = current_artifact_api.audit_current_bytes(
        artifacts=artifacts, source=SCRIPT, stored_layout=stale)
    assert unbound["layout"]["binding"] == "unbound"
    assert unbound["layout"]["layout"] is None

    spec = current_artifact_api.audit_current_bytes(
        artifacts=artifacts, source='{"chart": "bar"}', backend="json",
        stored_layout=manifest["current_qa_layout"])
    source_check = next(c for c in spec["checks"] if c["check"] == "source_preflight")
    assert (source_check["executed"], source_check["conclusion"]) == (
        "not_run", "not_applicable")

    reaudit = current_artifact_api.audit_current_bytes(
        artifacts=artifacts, source=SCRIPT, mode="reaudit",
        supersedes=bound["receipt_id"], stored_layout=manifest["current_qa_layout"])
    assert reaudit["mode"] == "reaudit" and reaudit["supersedes"] == bound["receipt_id"]
    with pytest.raises(ValueError, match="supersedes"):
        current_artifact_api.audit_current_bytes(artifacts=artifacts, mode="reaudit")


def _fake_session_registry(tmp_path, monkeypatch):
    from src.core import account_leases as leases
    from src.research import artifacts as artifacts_module
    from src.session import files as files_module
    from src.session.manager import session_manager

    entries, counter = {}, [0]

    def add(blob, *, producer="figure_test", origin="generated", sid="session1234"):
        counter[0] += 1
        file_id = f"{counter[0]:032x}"
        path = tmp_path / f"{file_id}.bin"
        path.write_bytes(blob)
        entries[file_id] = {"file_id": file_id, "path": path, "sha256": _sha(blob),
                            "size_bytes": len(blob), "producer_tool": producer,
                            "origin": origin, "session_id": sid,
                            "download_url": f"/download/{file_id}", "filename": file_id}
        return entries[file_id]

    @contextlib.contextmanager
    def lease(*args, **kwargs):
        yield

    monkeypatch.setattr(leases, "account_lease", lease)
    monkeypatch.setattr(store, "_pin_files", lambda *a, **k: ("", []))
    monkeypatch.setattr(files_module, "get_account_file",
                        lambda file_id, owner, **kwargs: entries.get(file_id))
    monkeypatch.setattr(session_manager, "session_belongs_to", lambda sid, owner: True)

    def publish(*, owner, session_id, filename, content, mime, producer_tool, request_id=""):
        entry = add(content, producer=producer_tool, sid=session_id)
        return {"file_id": entry["file_id"], "filename": filename,
                "sha256": entry["sha256"], "size": len(content),
                "download_url": entry["download_url"], "artifact_id": entry["file_id"]}

    monkeypatch.setattr(artifacts_module, "publish_artifact", publish)
    return entries, add


def test_verify_project_publishes_real_superseding_receipt(tmp_path, monkeypatch):
    previous = store._STATE_DIR
    store.set_state_dir(tmp_path / "state")
    try:
        entries, _add = _fake_session_registry(tmp_path, monkeypatch)
        result = _run()
        manifest = json.loads(result.outputs["manifest"])
        exports = {}
        for fmt in ("svg", "png", "pdf"):
            entry = _add(result.outputs[fmt])
            exports[fmt] = {key: value for key, value in entry.items() if key != "path"}
        project = projects.create_project(
            owner_user_id=7, title="f4", source_kind="script",
            source={"script": SCRIPT, "parameters": {}}, session_id="session1234")
        figure_id = project["figure_id"]
        assert store.update_project(figure_id, 7, expected_revision=0,
                                    manifest_json=manifest, exports_json=exports,
                                    render_version=1)

        first = current_artifact_api.verify_project(figure_id, 7, expected_revision=1,
                                                    include_bundle=True)
        receipt = first["receipt"]
        pdf_sha = _sha(result.outputs["pdf"])
        assert receipt["mode"] == "reaudit"
        assert receipt["supersedes"] == manifest["current_qa_receipt"]["receipt_id"]
        assert receipt["layout"]["binding"] == "bound_to_current_pdf"
        assert receipt["layout"]["artifact_sha256"] == pdf_sha
        assert receipt["capabilities"]["vendor_root"] == "verified"
        assert receipt["capabilities"]["source_execution"] == "not_run"
        assert receipt["receipt_id"] == current_artifact_api._receipt_id(receipt)
        assert first["receipt_file"]["download_url"]
        assert first["bundle"]["download_url"]
        with zipfile.ZipFile(io.BytesIO(entries[first["bundle"]["file_id"]]["path"].read_bytes())) as archive:
            assert "qa/layout.json" in archive.namelist()
            archived = json.loads(archive.read("qa/receipt.json"))
            assert archived["receipt_id"] == receipt["receipt_id"]

        second = current_artifact_api.verify_project(
            figure_id, 7, expected_revision=1,
            supersedes_file_id=first["receipt_file"]["file_id"], include_bundle=False)
        reaudit = second["receipt"]
        assert reaudit["mode"] == "reaudit"
        assert reaudit["supersedes"] == receipt["receipt_id"]
        assert reaudit["layout"]["binding"] == "bound_to_current_pdf"
        assert reaudit["receipt_id"] == current_artifact_api._receipt_id(reaudit)

        with pytest.raises(projects.ProjectError) as conflict:
            current_artifact_api.verify_project(figure_id, 7, expected_revision=0)
        assert conflict.value.code == "revision_conflict"

        # An artifact written before layout capture keeps an explicit gap and
        # is still audited from its stored bytes; nothing is rerendered.
        legacy = json.loads(json.dumps(manifest))
        legacy.pop("current_qa_layout", None)
        legacy["render_receipt"].pop("current_qa_layout", None)
        assert store.update_project(figure_id, 7, expected_revision=1,
                                    manifest_json=legacy, render_version=2)
        old = current_artifact_api.verify_project(figure_id, 7, expected_revision=2)
        assert old["receipt"]["layout"]["binding"] == "absent"
        old_panel = next(c for c in old["receipt"]["checks"]
                         if c["check"] == "panel_alignment")
        assert (old_panel["executed"], old_panel["reason_code"]) == (
            "not_run", "bound_layout_absent")
        assert old["receipt"]["capabilities"]["rendering"] == "not_run"
    finally:
        store.set_state_dir(previous)


def test_host_accepts_bound_exports_and_rejects_changed_bytes():
    from src.figures.nature_receipts import accept_current_render_qa, current_receipt_id

    result = _run()
    manifest = json.loads(result.outputs["manifest"])
    blobs = {fmt: result.outputs[fmt] for fmt in ("svg", "png", "pdf")}
    assert accept_current_render_qa(manifest, blobs, "initial") == manifest["current_qa_receipt"]
    assert "current_qa_receipt" not in manifest["render_receipt"]
    with pytest.raises(ValueError):
        accept_current_render_qa(manifest, {**blobs, "svg": blobs["svg"] + b"\n"}, "initial")
    with pytest.raises(ValueError):
        accept_current_render_qa(manifest, {**blobs, "pdf": blobs["pdf"] + b"\n"}, "initial")
    checks = manifest["current_qa_receipt"]["checks"]
    pdf_collisions = next(c for c in checks if c["check"] == "pdf_collisions")
    pdf_collisions["artifact"] = next(a for a in manifest["current_qa_receipt"]["artifacts"]
                                      if a["format"] == "svg")
    manifest["current_qa_receipt"]["receipt_id"] = current_receipt_id(manifest["current_qa_receipt"])
    with pytest.raises(ValueError):
        accept_current_render_qa(manifest, blobs, "initial")


def test_svg_only_saved_bytes_never_reuse_internal_pdf_audit():
    from src.figures.nature_receipts import accept_current_render_qa

    result = _run(outputs={"svg": "figure.svg", "manifest": "objects.json"})
    manifest = json.loads(result.outputs["manifest"])
    svg = result.outputs["svg"]
    render = accept_current_render_qa(manifest, {"svg": svg}, "initial")
    assert next(a for a in render["artifacts"] if a["format"] == "pdf")["role"] == "internal_qa"
    receipt = current_artifact_api.audit_current_bytes(
        artifacts=[{"format": "svg", "bytes": svg}], source=SCRIPT,
        stored_layout=manifest["current_qa_layout"], mode="reaudit", supersedes=render["receipt_id"])
    assert receipt["layout"]["binding"] == "unbound"
    checks = {c["check"]: c for c in receipt["checks"]}
    assert set(checks) == SEVEN
    for name in ("panel_alignment", "pdf_text", "pdf_collisions", "visual"):
        assert checks[name]["executed"] == "not_run"
    assert checks["svg_schematic"]["artifact"]["sha256"] == _sha(svg)


def test_real_text_misalignment_collision_and_unauditable_cases():
    vendor = current_vendor_root()
    assert vendor is not None and vendor == VENDOR.resolve()

    misaligned = '''
import matplotlib.pyplot as plt
fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.0))
axes[0].plot([0, 1], [0, 1]); axes[1].plot([0, 1], [0, 1])
position = axes[1].get_position()
axes[1].set_position([position.x0, position.y0 + 0.06, position.width, position.height - 0.06])
'''
    rendered, pdf, layout = _render(misaligned)
    receipt = verify_current_figure(
        artifacts={"pdf": pdf}, source=misaligned, layout_manifest=layout,
        mode="initial", vendor_root=vendor)
    panel = next(c for c in receipt["checks"] if c["check"] == "panel_alignment")
    assert panel["executed"] == "executed" and panel["conclusion"] == "fail"
    assert "row-axes-misalignment" in {f["kind"] for f in panel["diagnostics"]["findings"]}
    assert receipt["overall"]["blocking"] == ["panel_alignment"]

    tiny = '''
import matplotlib.pyplot as plt
fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.0))
axes[0].text(0.5, 0.5, "overlapping label alpha", fontsize=3)
axes[0].text(0.5, 0.5, "overlapping label beta", fontsize=3)
axes[1].plot([0, 1], [0, 1])
'''
    rendered, pdf, layout = _render(tiny)
    svg = exporter.export_all(rendered, formats=("svg",), dpi=300)["svg"]
    receipt = verify_current_figure(
        artifacts={"pdf": pdf, "svg": svg}, source=tiny, layout_manifest=layout,
        mode="initial", vendor_root=vendor)
    text = next(c for c in receipt["checks"] if c["check"] == "pdf_text")
    collisions = next(c for c in receipt["checks"] if c["check"] == "pdf_collisions")
    schematic = next(c for c in receipt["checks"] if c["check"] == "svg_schematic")
    assert text["conclusion"] == "fail" and text["diagnostics"]["below_minimum_count"] >= 1
    assert collisions["conclusion"] == "fail"
    assert schematic["conclusion"] == "fail"
    assert "text.small" in {f["kind"] for f in schematic["diagnostics"]["findings"]}

    freeform = '''
import matplotlib.pyplot as plt
fig = plt.figure(figsize=(7.0, 3.0))
first = fig.add_axes([0.08, 0.15, 0.35, 0.70]); first.plot([0, 1], [0, 1])
second = fig.add_axes([0.55, 0.30, 0.35, 0.55]); second.plot([0, 1], [0, 1])
'''
    rendered, pdf, layout = _render(freeform)
    receipt = verify_current_figure(
        artifacts={"pdf": pdf}, source=freeform, layout_manifest=layout,
        mode="initial", vendor_root=vendor)
    panel = next(c for c in receipt["checks"] if c["check"] == "panel_alignment")
    assert (panel["executed"], panel["conclusion"], panel["reason_code"]) == (
        "attempted", "undetermined", "upstream_not_auditable")

    malformed = verify_svg_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><g></svg>',
                                 mode="edit", vendor_root=vendor)
    assert [(c["executed"], c["conclusion"]) for c in malformed["checks"]
            if c["check"] == "svg_schematic"] == [("attempted", "undetermined")]

    bad_pdf = verify_current_figure(artifacts={"pdf": b"not a pdf"},
                                    checks=["format_integrity"], vendor_root=vendor)
    assert (bad_pdf["checks"][0]["conclusion"], bad_pdf["overall"]["conclusion"]) == (
        "fail", "fail")


def test_saved_bundle_reaudit_and_legacy_compatibility(tmp_path):
    vendor = current_vendor_root()
    session = render_figure(source=SCRIPT)
    output = tmp_path / "bundle"
    written = write_bundle(session, output, vendor_root=vendor, formats=("pdf", "svg", "png"))
    assert (output / "qa" / "layout.json").is_file()
    stored_pdf = (output / "figure.pdf").read_bytes()
    first = verify_saved_bundle(output, vendor_root=vendor, mode="reaudit",
                                supersedes=written["receipt"]["receipt_id"])
    assert first["mode"] == "reaudit"
    assert first["supersedes"] == written["receipt"]["receipt_id"]
    assert first["layout"]["binding"] == "bound_to_current_pdf"
    assert first["bundle"]["layout_binding"] == "bound_to_current_pdf"
    # Stored bytes are certified as-is; nothing was rerendered or rewritten.
    assert (output / "figure.pdf").read_bytes() == stored_pdf
    assert _artifact_sha(first, "format_integrity", "pdf") == [_sha(stored_pdf)]
    assert first["receipt_id"] == current_artifact_api._receipt_id(first)

    legacy = tmp_path / "legacy"
    shutil.copytree(output, legacy)
    (legacy / "qa" / "layout.json").unlink()
    manifest = json.loads((legacy / "figure.bundle.json").read_text(encoding="utf-8"))
    manifest["members"] = [member for member in manifest["members"]
                           if member["path"] != "qa/layout.json"]
    (legacy / "figure.bundle.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    old = verify_saved_bundle(legacy, vendor_root=vendor, mode="initial")
    assert old["layout"]["binding"] == "absent"
    panel = next(c for c in old["checks"] if c["check"] == "panel_alignment")
    assert (panel["executed"], panel["reason_code"]) == ("not_run", "bound_layout_absent")
    assert old["overall"]["conclusion"] == "incomplete"

    tampered = tmp_path / "tampered"
    shutil.copytree(output, tampered)
    (tampered / "figure.svg").write_bytes(b"<svg/>")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_saved_bundle(tampered, vendor_root=vendor, mode="initial")


def test_f5_svg_entry_accepts_sanitized_bytes_only():
    vendor = current_vendor_root()
    good = (b'<svg xmlns="http://www.w3.org/2000/svg" width="89mm" height="60mm" '
            b'viewBox="0 0 252.28 170.08"><text x="14" y="28" font-size="6">A</text></svg>')
    receipt = verify_svg_bytes(good, mode="edit", vendor_root=vendor)
    _assert_measured(receipt)
    schematic = next(c for c in receipt["checks"] if c["check"] == "svg_schematic")
    assert schematic["executed"] == "executed" and schematic["conclusion"] == "pass"
    assert schematic["artifact"]["sha256"] == _sha(good)
    assert receipt["overall"]["blocking"] == []

    unsafe = (b'<svg xmlns="http://www.w3.org/2000/svg" width="89mm" height="60mm" '
              b'viewBox="0 0 252.28 170.08"><script>alert(1)</script>'
              b'<text x="14" y="28" font-size="6">A</text></svg>')
    blocked = verify_svg_bytes(unsafe, mode="edit", vendor_root=vendor)
    assert next(c for c in blocked["checks"] if c["check"] == "svg_schematic")["conclusion"] == "fail"

    with pytest.raises(ValueError, match="bytes"):
        verify_svg_bytes("not bytes", vendor_root=vendor)


def test_runtime_adapter_exposes_verified_vendor_and_layout_evidence():
    vendor = current_vendor_root()
    assert vendor == VENDOR.resolve()
    _, pdf, layout = _render(SCRIPT)
    evidence = layout_evidence(layout, _sha(pdf))
    assert evidence["artifact_sha256"] == _sha(pdf)
    assert evidence["rerendered"] is False
    assert layout_evidence(layout, "0" * 64) is None

    receipt = run_current_qa(artifacts={"pdf": pdf}, mode="initial", source=SCRIPT,
                             layout_manifest=layout, vendor_root=vendor)
    _assert_measured(receipt)
    assert {check["check"] for check in receipt["checks"]} == SEVEN
    svg = next(check for check in receipt["checks"] if check["check"] == "svg_schematic")
    assert (svg["executed"], svg["reason_code"]) == ("not_run", "format_absent")
    assert receipt["overall"]["unexecuted"] == ["svg_schematic", "visual"]
    assert receipt["overall"]["blocking"] == []


def test_categorical_bar_keeps_layout_evidence_from_pdf_export():
    from src.figures.resolve import resolve_spec
    from src.figures.spec import parse_spec
    from src.figures.trusted_render import render_resolved

    resolved, _ = resolve_spec(parse_spec({
        "chart": "bar", "title": "Source measurements",
        "x": {"kind": "inline", "column": "Group", "values": ["A", "A", "B", "B", "C", "C"]},
        "y": {"kind": "inline", "column": "Value", "values": [3.1, 4.2, 4.9, 5.4, 6.0, 7.25]},
    }))
    result = render_resolved(resolved, formats=("svg",))
    assert set(result.blobs) == {"svg"}
    receipt = result.manifest["current_qa_receipt"]
    layout = result.manifest["current_qa_layout"]
    assert len(layout["layout"]["panels"]) == 1
    pdf = next(row for row in receipt["artifacts"] if row["format"] == "pdf")
    assert pdf["role"] == "internal_qa" and layout["artifact_sha256"] == pdf["sha256"]
    alignment = next(check for check in receipt["checks"] if check["check"] == "panel_alignment")
    assert alignment["executed"] == "executed" and alignment["artifact"] == pdf

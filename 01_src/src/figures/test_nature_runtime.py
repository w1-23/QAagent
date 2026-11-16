"""Local runtime contract tests; real_upstream tests execute pinned vendor code.

These do not establish Docker isolation. Run from isolated Python with only the
controlled 01_src directory added to sys.path. Tests use private temporary roots.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.figures import nature_runtime as nature


@dataclasses.dataclass
class Finding:
    check_id: str = "FONT-FAMILY"
    level: str = "FAIL"
    message: str = "No explicit font in user source"
    evidence: tuple = ()


def fake_modules(monkeypatch, *, geometry_code=0, dependency_error=False):
    def collision(_path):
        if dependency_error:
            raise ImportError("PyMuPDF unavailable")
        return {"auditable": True, "summary": {"fail": int(geometry_code == 1), "warn": 0},
                "findings": [{"severity": "FAIL"}] if geometry_code == 1 else []}

    modules = {
        "validate_figure": SimpleNamespace(
            validate_source=lambda source, backend: [Finding()],
            summarize=lambda findings, strict=False: {"counts": {"FAIL": 1, "WARN": 0}, "ready": False}),
        "audit_panel_alignment": SimpleNamespace(
            matplotlib_layout_manifest=lambda figure: {"panels": []},
            audit_layout_manifest=lambda manifest: {"auditable": True, "findings": [], "summary": {}},
            exit_code=lambda report: 0),
        "audit_pdf_text": SimpleNamespace(audit_pdf=lambda data: {
            "auditable": True, "below_minimum_count": 0, "below_minimum": [], "warnings": []}),
        "audit_figure_collisions": SimpleNamespace(audit_pdf=collision, exit_code=lambda report: geometry_code),
    }
    monkeypatch.setattr(nature, "_load", modules.__getitem__)


def test_advisory_source_findings_are_preserved_and_pdf_is_reused(monkeypatch):
    fake_modules(monkeypatch)
    calls = []
    pdf = b"%PDF-fake-test-only"
    receipt, returned = nature.audit_fig(object(), "emit.figure('a', fig)",
                                        pdf_factory=lambda: calls.append(1) or pdf)
    assert calls == [1]
    assert returned is pdf
    assert receipt["pdf_sha256"] == hashlib.sha256(pdf).hexdigest()
    assert receipt["status"] == "review_required"
    source = receipt["checks"]["source_preflight"]
    assert source["findings"][0]["level"] == "FAIL"
    assert source["policy"] == "advisory_source_preflight"
    assert receipt["checks"]["pdf_collisions"]["status"] == "pass"


@pytest.mark.parametrize("code,missing,expected", [(1, False, "fail"), (0, True, "not_auditable")])
def test_rendered_failure_or_missing_dependency_blocks(monkeypatch, code, missing, expected):
    fake_modules(monkeypatch, geometry_code=code, dependency_error=missing)
    with pytest.raises(nature.NatureAuditError) as caught:
        nature.audit_fig(object(), "source", pdf_factory=lambda: b"%PDF-fake-test-only")
    receipt = caught.value.receipt
    assert receipt["checks"]["pdf_collisions"]["status"] == expected
    assert receipt["status"] == expected
    assert set(receipt["checks"]) == {"source_preflight", "panel_alignment", "pdf_text", "pdf_collisions"}


def test_pdf_export_exception_is_not_a_pass(monkeypatch):
    fake_modules(monkeypatch)
    def failed_export():
        raise ValueError("export failed")
    with pytest.raises(nature.NatureAuditError) as caught:
        nature.audit_fig(object(), "source", pdf_factory=failed_export)
    assert caught.value.receipt["checks"]["pdf_text"]["status"] == "not_auditable"
    assert caught.value.receipt["checks"]["pdf_collisions"]["status"] == "not_auditable"


def test_hash_mismatch_is_rejected_before_module_execution(tmp_path, monkeypatch):
    relative = "skills/nature-figure/scripts/figure_safety.py"
    script = tmp_path / relative
    script.parent.mkdir(parents=True)
    script.write_text("raise AssertionError('must not execute')\n", encoding="utf-8")
    (tmp_path / "provenance.json").write_text(json.dumps({
        "source": nature.SOURCE, "commit": nature.COMMIT,
        "files": [{"path": relative, "sha256": "0" * 64}],
    }), encoding="utf-8")
    monkeypatch.setattr(nature, "_VENDOR_ROOT", tmp_path)
    with pytest.raises(ValueError, match="hash mismatch"):
        nature.interp_monotone(1, [1], [1])


def run_worker(tmp_path, script, *, v2=False, figures=(), tables=(), replay=False, overrides=()):
    from src.figures import worker, script_outputs
    incoming = tmp_path / "in"
    incoming.mkdir()
    job = {"script": script, "inputs": {}, "dpi": 300,
           "outputs": {"svg": "figure.svg", "pdf": "figure.pdf", "manifest": "objects.json"}}
    if v2:
        contract = script_outputs.normalize_output_contract({"version": 2, "tables": list(tables), "figures": list(figures)})
        job.update(output_contract=contract, job_id="nature-test", output_key="plot" if replay else "")
        job["outputs"] = script_outputs.output_slots(contract, output_key=job["output_key"])
    if replay:
        job.update(replay_overrides=True, overrides=list(overrides), base_overrides=[])
    path = incoming / "job.json"
    path.write_text(json.dumps(job), encoding="utf-8")
    return worker.run(path), job, tmp_path / "out"


@pytest.mark.parametrize("v2", [False, True])
def test_worker_preserves_audited_pdf_and_replay_receipt(tmp_path, monkeypatch, v2):
    from src.figures import worker
    observed = []
    pdf = b"%PDF-fixed-audited-test-bytes"
    receipt = {"version": 1, "commit": nature.COMMIT, "status": "review_required", "checks": {}}
    def audit(figure, source, *, pdf_factory):
        observed.append(figure.axes[0].get_title())
        return receipt, pdf
    monkeypatch.setattr(nature, "audit_fig", audit)
    script = "fig, ax = plt.subplots()\nax.plot([1, 2], [2, 1])\nax.set_title('Before')\n"
    if v2:
        script += "emit.figure('plot', fig)\n"
    code, job, out = run_worker(tmp_path, script, v2=v2, figures=[{"name": "plot", "formats": ["svg", "pdf"]}],
                                replay=True, overrides=[{"gid": "axes_0.title", "prop": "text", "value": "After"}])
    assert code == 0
    assert observed == ["After"]
    prefix = "figure:plot:" if v2 else ""
    assert (out / job["outputs"][prefix + "pdf"]).read_bytes() == pdf
    manifest = json.loads((out / job["outputs"][prefix + "manifest"]).read_bytes())
    assert manifest["nature_skill_receipt"] == receipt


def test_worker_partial_failure_keeps_successful_figure_and_failure_receipt(tmp_path, monkeypatch):
    from src.figures import worker
    receipt = {"version": 1, "commit": nature.COMMIT, "status": "fail",
               "checks": {"pdf_collisions": {"status": "fail", "findings": ["overlap"]}}}
    def audit(figure, source, *, pdf_factory):
        if figure.axes[0].get_title() == "Bad":
            raise nature.NatureAuditError(receipt)
        return {**receipt, "status": "pass", "checks": {}}, b"%PDF-clean-test-only"
    monkeypatch.setattr(nature, "audit_fig", audit)
    script = ("fig, ax = plt.subplots()\nax.plot([1, 2], [2, 1])\nax.set_title('Good')\n"
              "emit.figure('good', fig)\nfig2, ax2 = plt.subplots()\nax2.plot([1, 2], [2, 1])\n"
              "ax2.set_title('Bad')\nemit.figure('bad', fig2)\n")
    code, job, out = run_worker(tmp_path, script, v2=True,
                                figures=[{"name": name, "formats": ["pdf"]} for name in ("good", "bad")])
    assert code == 0
    result = json.loads((out / "result.json").read_bytes())
    assert result["status"] == "partial"
    assert result["slots"]["figure:good:pdf"]["status"] == "ok"
    assert result["slots"]["figure:bad:pdf"]["status"] == "error"
    assert not (out / job["outputs"]["figure:bad:pdf"]).exists()
    manifest = json.loads((out / job["outputs"]["figure:bad:manifest"]).read_bytes())
    assert manifest["nature_skill_receipt"] == receipt
    assert (tmp_path / "in/job.json").read_text(encoding="utf-8").find("ax2.plot") >= 0


def test_worker_report_only_does_not_load_nature_modules(tmp_path, monkeypatch):
    from src.figures import worker
    monkeypatch.setattr(nature, "audit_fig", lambda *a, **kw: pytest.fail("No figure QA expected"))
    code, _, out = run_worker(tmp_path, "emit.report({'message': 'no plots'})", v2=True)
    assert code == 0
    assert json.loads((out / "result.json").read_bytes())["status"] == "ok"


def test_worker_table_only_does_not_load_nature_modules(tmp_path, monkeypatch):
    from src.figures import worker
    monkeypatch.setattr(nature, "audit_fig", lambda *a, **kw: pytest.fail("No figure QA expected"))
    script = "import pandas as pd\nemit.table('table', pd.DataFrame({'value': [1, 2]}))"
    code, _, out = run_worker(tmp_path, script, v2=True, tables=[{"name": "table"}])
    assert code == 0
    result = json.loads((out / "result.json").read_bytes())
    assert result["slots"]["table:table"]["status"] == "ok"


def test_legacy_failure_retains_receipt_without_publishing(tmp_path, monkeypatch, capsys):
    from src.figures import worker
    receipt = {"version": 1, "commit": nature.COMMIT, "status": "not_auditable",
               "checks": {"pdf_collisions": {"status": "not_auditable", "error": "missing PyMuPDF"}}}
    def audit(*args, **kwargs):
        raise nature.NatureAuditError(receipt)
    monkeypatch.setattr(nature, "audit_fig", audit)
    code, _, out = run_worker(tmp_path, "fig, ax = plt.subplots()\nax.plot([1, 2], [2, 1])")
    assert code == worker.EXIT_SCRIPT_ERROR
    assert not (out / "figure.pdf").exists()
    assert not (out / "figure.svg").exists()
    assert json.loads((out / "objects.json").read_bytes())["nature_skill_receipt"] == receipt
    assert json.loads(capsys.readouterr().out)["nature_skill_receipt"] == receipt


def test_real_upstream_clean_figure_and_helpers():
    """Requires authorization to execute pinned upstream modules."""
    from src.figures import core, exporter
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    with mpl.rc_context():
        nature.configure_style()
        assert mpl.rcParams["pdf.fonttype"] == 42
        assert nature.interp_monotone(1.5, [1, 2], [2, 4]) == pytest.approx(3)
        assert nature.label_y_above([1, 2]) > 2
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.plot([1, 2, 3], [2, 3, 2])
        ax.set_title("Trend")
        rendered = core.RenderedFigure(figure=fig, axes=[ax])
        receipt, pdf = nature.audit_fig(fig, "ax.plot([1, 2, 3], [2, 3, 2])",
                                        pdf_factory=lambda: exporter.export_all(rendered, formats=("pdf",))["pdf"])
        plt.close(fig)
    assert receipt["checks"]["panel_alignment"]["status"] == "not_applicable"
    assert receipt["checks"]["pdf_text"]["status"] == "pass"
    assert receipt["checks"]["pdf_collisions"]["status"] in {"pass", "warn"}
    assert receipt["pdf_sha256"] == hashlib.sha256(pdf).hexdigest()


@pytest.mark.parametrize("explicit", [False, True])
def test_real_colorbar_is_not_a_panel_but_keeps_pdf_audits(explicit):
    from src.figures import core, exporter
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 4))
    mesh = ax.imshow([[1, 2], [3, 4]], aspect="auto")
    if explicit:
        fig.subplots_adjust(right=0.75)
        cax = fig.add_axes([0.82, 0.15, 0.03, 0.7])
        colorbar = fig.colorbar(mesh, cax=cax)
        assert not cax.get_label().startswith("<colorbar")
    else:
        colorbar = fig.colorbar(mesh, ax=ax)
    rendered = core.RenderedFigure(figure=fig, axes=list(fig.axes))
    try:
        receipt, _ = nature.audit_fig(fig, "", pdf_factory=lambda:
            exporter.export_all(rendered, formats=("pdf",))["pdf"])
        alignment = receipt["checks"]["panel_alignment"]
        assert alignment["status"] == "not_applicable"
        assert len(alignment["report"]["layout"]["panels"]) == 1
        assert len(alignment["report"]["excluded_axes"]) == 1
        assert receipt["checks"]["pdf_collisions"]["status"] in {"pass", "warn"}
        # Excluding the colorbar from panel layout must not exclude its text
        # from the actual PDF font floor audit.
        colorbar.set_label("Small colorbar label", fontsize=3)
        with pytest.raises(nature.NatureAuditError) as caught:
            nature.audit_fig(fig, "", pdf_factory=lambda:
                exporter.export_all(rendered, formats=("pdf",))["pdf"])
        assert caught.value.receipt["checks"]["pdf_text"]["status"] == "fail"
    finally:
        plt.close(fig)


def test_colorbar_label_alone_does_not_exempt_a_data_panel():
    from src.figures import core, exporter
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(6, 4))
    fig.add_axes([0.1, 0.2, 0.6, 0.6])
    narrow = fig.add_axes([0.8, 0.2, 0.03, 0.6], label="<colorbar>")
    rendered = core.RenderedFigure(figure=fig, axes=list(fig.axes))
    try:
        with pytest.raises(nature.NatureAuditError) as caught:
            nature.audit_fig(fig, "", pdf_factory=lambda:
                exporter.export_all(rendered, formats=("pdf",))["pdf"])
        alignment = caught.value.receipt["checks"]["panel_alignment"]
        assert alignment["status"] == "not_auditable"
        assert len(alignment["report"]["layout"]["panels"]) == 2
        assert narrow.get_label() == "<colorbar>"
    finally:
        plt.close(fig)


def test_real_upstream_small_glyph_and_collision_fail():
    from src.figures import core, exporter
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.text(0.5, 0.5, "Overlap", fontsize=3)
    ax.text(0.5, 0.5, "Overlap", fontsize=3)
    rendered = core.RenderedFigure(figure=fig, axes=[ax])
    try:
        with pytest.raises(nature.NatureAuditError) as caught:
            nature.audit_fig(fig, "ax.text(0.5, 0.5, 'Overlap', fontsize=3)",
                             pdf_factory=lambda: exporter.export_all(rendered, formats=("pdf",))["pdf"])
        assert caught.value.receipt["checks"]["pdf_text"]["status"] == "fail"
        assert caught.value.receipt["checks"]["pdf_collisions"]["status"] == "fail"
    finally:
        plt.close(fig)

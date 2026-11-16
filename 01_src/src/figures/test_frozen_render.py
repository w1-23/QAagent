"""Separated scientific-worker contract tests with synthetic, trusted inputs.

Direct capture tests validate semantics, not container isolation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.figures import core, worker
from src.figures.dataset_codec import encode_frame, decode_frame
from src.figures.frozen_render import geometry_signature, typed_payload_sha256
from src.figures.script_outputs import normalize_output_contract, output_slots

SOURCE = "fig, ax = plt.subplots()\nax.plot(data['tidy']['x'], data['tidy']['y'])"
ANALYSIS = "import pandas as pd\nf = pd.DataFrame({'x': [1., 2., 3.], 'y': [4., 5., 6.]})\nemit.table('tidy', f)"


def contract(source=SOURCE):
    return normalize_output_contract({"version": 2, "tables": [{"name": "tidy"}],
        "figures": [{"name": "plot", "parents": ["tidy"], "render_script": source, "formats": ["png"]}]})


@pytest.mark.parametrize("change", [{"parents": []}, {"parents": ["raw"]},
    {"parents": ["tidy", "tidy"]}, {"render_script": ""}, {"render_script": 3},
    {"render_script": "if !!!"}, {"render_script": "#" * (256 * 1024 + 1)}])
def test_bad_separated_contract(change):
    value = contract()
    value["figures"][0].update(change)
    with pytest.raises(ValueError):
        normalize_output_contract(value)


@pytest.mark.parametrize("panel_count", [1, 4])
def test_relationship_preset_freezes_coordinates_and_replays_without_raw_input(tmp_path, monkeypatch, panel_count):
    import pandas as pd
    from src.research.script_presets import get_preset
    from src.figures import projects
    from src.config import settings
    preset = get_preset("relationship_panels")
    value = normalize_output_contract(preset["outputs"])
    responses = [f"response_{i}" for i in range(panel_count)]
    path = tmp_path / "synthetic.csv"
    frame = pd.DataFrame({"dose": [1., 2., 3.],
                          **{name: [float(i), None, float(i + 2)] for i, name in enumerate(responses)}})
    frame.to_csv(path, index=False)
    initial = core.capture_outputs(preset["source"], output_contract=value,
        namespace={"data": path, "parameters": {"x": "dose", "ys": responses,
                   "labels": {"dose": "Dose (mg)"}}})
    assert not initial.error, initial.error
    table = initial.tables["relations_data"]
    assert len(table) == 2 * panel_count
    assert initial.report["input_rows"] == 3
    assert all(panel["missing_pair_rows"] == 1 for panel in initial.report["panels"])
    path.unlink()
    replay = core.capture_outputs("raise AssertionError('analysis reran')", output_contract=value,
        output_key="relations", frozen_tables=initial.tables)
    assert not replay.error, replay.error
    receipt = initial.frozen_render_receipts["relations"]
    assert receipt["geometry_status"] == "supported", receipt
    assert replay.frozen_render_receipts["relations"] == receipt
    for index in range(panel_count):
        ax = replay.figures["relations"].axes[index]
        assert ax.get_xlabel() == "Dose (mg)"
        assert ax.collections[0].get_offsets().tolist() == [[1., float(index)], [3., float(index + 2)]]
    monkeypatch.setattr(settings, "FIGURES_SANDBOX_BACKEND", "local")
    monkeypatch.setattr(settings, "FIGURES_SANDBOX_ALLOW_LOCAL", True)
    source = {"script": preset["source"], "output_contract": value, "output_key": "relations",
              "input_snapshots": [], "frozen_render": {"version": 1, "receipt": receipt,
              "input_snapshots": [{"variable": "relations_data",
                  "sha256": receipt["data_sha256"]["relations_data"]}]}}
    assert projects._can_edit({"source_kind": "script", "source_json": source})


def test_initial_snapshots_are_isolated_and_replay_skips_analysis():
    value = contract("assert 'f' not in globals() and 'raw_path' not in globals()\n" + SOURCE)
    initial = core.capture_outputs(ANALYSIS + "\nf.loc[0, 'y'] = 999", output_contract=value,
                                   namespace={"raw_path": Path("never-read.csv")})
    assert not initial.error
    assert initial.tables["tidy"].iloc[0]["y"] == 4
    replay = core.capture_outputs("raise AssertionError('analysis reran')", output_contract=value,
                                  output_key="plot", frozen_tables=initial.tables)
    assert not replay.error
    receipt = initial.frozen_render_receipts["plot"]
    assert receipt["geometry_status"] == "supported", receipt
    assert receipt == replay.frozen_render_receipts["plot"]
    assert receipt["data_sha256"] == {"tidy": typed_payload_sha256(encode_frame(initial.tables["tidy"]))}
    assert replay.tables == {}


def test_renderer_mutation_does_not_change_snapshot_or_other_renderers():
    value = contract("data['tidy'].loc[0, 'y'] = 999\n" + SOURCE)
    value["figures"].append({"name": "second", "parents": ["tidy"], "render_script": SOURCE})
    initial = core.capture_outputs(ANALYSIS, output_contract=value)
    assert not initial.error
    assert initial.tables["tidy"].iloc[0]["y"] == 4
    assert initial.figures["plot"].axes[0].lines[0].get_ydata()[0] == 999
    assert initial.figures["second"].axes[0].lines[0].get_ydata()[0] == 4
    assert initial.frozen_render_receipts["plot"]["data_sha256"] == initial.frozen_render_receipts["second"]["data_sha256"]


@pytest.mark.parametrize("source", ["plt.figure(); plt.figure()", "pass", "import socket\nplt.figure()",
                                      "plt.plot(f['x'], f['y'])"])
def test_invalid_or_leaking_renderer_cannot_publish(source):
    result = core.capture_outputs(ANALYSIS, output_contract=contract(source))
    assert result.error and not result.tables and not result.figures and not result.frozen_render_receipts


def test_emit_figure_conflict_and_missing_tables_rejected():
    for source in (ANALYSIS + "\nemit.figure('plot', plt.figure())", "pass"):
        result = core.capture_outputs(source, output_contract=contract())
        assert result.error and not result.figures


@pytest.mark.parametrize("frozen", [None, {}, {"extra": object()}, {"tidy": object(), "extra": object()}])
def test_replay_requires_exact_frozen_inputs(frozen):
    with pytest.raises(ValueError, match="exactly"):
        core.capture_outputs("pass", output_contract=contract(), output_key="plot", frozen_tables=frozen)


def test_frozen_inputs_rejected_for_initial_or_legacy():
    with pytest.raises(ValueError):
        core.capture_outputs(ANALYSIS, output_contract=contract(), frozen_tables={})
    value = contract()
    del value["figures"][0]["render_script"]
    with pytest.raises(ValueError):
        core.capture_outputs("pass", output_contract=value, output_key="plot", frozen_tables={})


@pytest.mark.parametrize("draw,mutate", [
    ("ax.plot([1,2], [3,4])", "ax.lines[0].set_ydata([3,5])"),
    ("ax.scatter([1,2], [3,4], c=[1.,2.])", "ax.collections[0].set_offsets([[1,3],[2,5]])"),
    ("ax.scatter([1,2], [3,4], c=[1.,2.])", "ax.collections[0].set_array(np.array([1.,3.]))"),
    ("ax.bar([1,2], [3,4])", "ax.patches[0].set_height(7)"),
    ("ax.imshow([[1.,2.],[3.,4.]])", "ax.images[0].set_data([[1.,2.],[3.,5.]])"),
    ("ax.imshow([[1.,2.],[3.,4.]])", "ax.images[0].set_clim(0,9)"),
    ("ax.plot([1,2], [3,4])", "ax.set_xlim(0,9)"),
    ("ax.plot([1,2], [3,4])", "ax.set_xticks([1,1.5,2])"),
    ("ax.errorbar([1,2], [3,4], yerr=[.1,.2])", "ax.collections[0].set_segments([[[1,2],[1,4]], [[2,3],[2,5]]])"),
])
def test_numeric_changes_change_geometry(draw, mutate):
    import numpy as np
    result = core.capture_figure("fig, ax = plt.subplots()\n" + draw, check_layout=False)
    assert not result.error
    figure, ax = result.figure, result.figure.axes[0]
    before = geometry_signature(figure)
    assert before["geometry_status"] == "supported", before
    exec(mutate, {"ax": ax, "np": np})
    after = geometry_signature(figure)
    assert after["geometry_status"] == "supported", after
    assert after["geometry_sha256"] != before["geometry_sha256"]


def test_style_changes_preserve_geometry_and_unknown_artists_are_unsupported():
    result = core.capture_figure("fig, ax = plt.subplots()\nax.plot([1,2], [3,4])", check_layout=False)
    figure, ax = result.figure, result.figure.axes[0]
    before = geometry_signature(figure)
    ax.lines[0].set_linewidth(4)
    ax.lines[0].set_markersize(12)
    ax.set_title("updated")
    ax.set_position([.2, .2, .6, .6])
    assert geometry_signature(figure) == before
    ax.annotate("point", (1, 3), xytext=(2, 4), arrowprops={"arrowstyle": "->"})
    unsupported = geometry_signature(figure)
    assert unsupported["geometry_status"] == "unsupported" and unsupported["geometry_sha256"] is None


@pytest.mark.parametrize("coords", ["ax.transAxes", "ax.transData"])
def test_plain_labels_support_frozen_replay_and_detect_changed_baseline(coords):
    value = contract(SOURCE + f"\nax.text(.8, .1, 'n = 3', transform={coords})")
    initial = core.capture_outputs(ANALYSIS, output_contract=value)
    replay = core.capture_outputs("raise AssertionError('analysis reran')", output_contract=value,
                                  output_key="plot", frozen_tables=initial.tables)
    receipt = initial.frozen_render_receipts["plot"]
    assert receipt["geometry_status"] == "supported", receipt
    assert replay.frozen_render_receipts["plot"] == receipt
    figure = initial.figures["plot"]
    figure.axes[0].texts[0].set_text('n = 999')
    assert geometry_signature(figure)["geometry_sha256"] != receipt["geometry_sha256"]


def test_analysis_rc_changes_do_not_leak_into_replay():
    value = contract()
    initial = core.capture_outputs(ANALYSIS + "\nmatplotlib.rcParams['axes.xmargin'] = .9", output_contract=value)
    replay = core.capture_outputs("raise AssertionError", output_contract=value,
                                  output_key="plot", frozen_tables=initial.tables)
    assert not initial.error and not replay.error
    assert initial.frozen_render_receipts == replay.frozen_render_receipts


def test_random_coordinate_drift_changes_only_geometry_receipt(monkeypatch):
    import numpy as np
    draws = iter([0.2, 0.8])
    monkeypatch.setattr(np.random, "random", lambda: next(draws))
    value = contract("import numpy as np\n" + SOURCE + "\nax.lines[0].set_ydata([np.random.random(), 5, 6])")
    initial = core.capture_outputs(ANALYSIS, output_contract=value)
    replay = core.capture_outputs("pass", output_contract=value, output_key="plot", frozen_tables=initial.tables)
    before, after = initial.frozen_render_receipts["plot"], replay.frozen_render_receipts["plot"]
    assert before["renderer_sha256"] == after["renderer_sha256"]
    assert before["data_sha256"] == after["data_sha256"]
    assert before["geometry_sha256"] != after["geometry_sha256"]


def test_collection_path_geometry_and_large_integer_images():
    import numpy as np
    from matplotlib.collections import PolyCollection
    result = core.capture_figure("fig, ax = plt.subplots()", check_layout=False)
    figure, ax = result.figure, result.figure.axes[0]
    polygons = PolyCollection([[(0, 0), (1, 1), (2, 0)]])
    ax.add_collection(polygons)
    before = geometry_signature(figure)
    assert before["geometry_status"] == "supported", before
    polygons.set_verts([[(0, 0), (1, 2), (2, 0)]])
    assert geometry_signature(figure)["geometry_sha256"] != before["geometry_sha256"]
    image = ax.imshow(np.array([[2**54, 2**54 + 1]], dtype="int64"))
    before = geometry_signature(figure)
    assert before["geometry_status"] == "supported", before
    image.set_data(np.array([[2**54, 2**54 + 2]], dtype="int64"))
    assert geometry_signature(figure)["geometry_sha256"] != before["geometry_sha256"]


@pytest.mark.parametrize("source", [
    "fig.canvas.mpl_connect('draw_event', lambda event: None)",
    "ax.set_xscale('log')", "ax.annotate('data annotation', xy=(1, 1))",
    "from matplotlib.transforms import Bbox\nax.lines[0].set_clip_box(Bbox.from_bounds(0, 0, 10, 10))",
])
def test_unsupported_geometry_is_explicit_without_blocking_capture(source):
    value = contract(SOURCE + "\n" + source)
    result = core.capture_outputs(ANALYSIS, output_contract=value)
    assert not result.error and result.figures
    receipt = result.frozen_render_receipts["plot"]
    assert receipt["geometry_sha256"] is None and receipt["geometry_status"] == "unsupported"
    assert receipt["geometry_unsupported"]


def test_worker_decodes_only_frozen_paths_and_keeps_pre_override_receipt(tmp_path, monkeypatch):
    from src.figures import exporter, manifest, overrides, render_policy
    value = contract()
    initial = core.capture_outputs(ANALYSIS, output_contract=value)
    snapshot = tmp_path / "tidy.json"
    snapshot.write_text(json.dumps(encode_frame(initial.tables["tidy"])), encoding="utf-8")
    monkeypatch.setattr(render_policy, "export_figure", lambda *a, **k: ({"png": b"png"}, None, {}))
    monkeypatch.setattr(overrides, "validate_overrides", lambda values, manifest: values)
    monkeypatch.setattr(overrides, "merge_overrides", lambda base, values: values)
    def apply(rendered, manifest, accepted):
        rendered.figure.axes[0].lines[0].set_linewidth(8)
        return ["style"]
    monkeypatch.setattr(overrides, "apply_overrides", apply)
    job = {"job_id": "frozen", "output_contract": value, "output_key": "plot",
           "replay_overrides": True, "render_mode": "preview", "outputs": output_slots(value, output_key="plot")}
    out = tmp_path / "out"
    code = worker._run_outputs(job, "raise AssertionError('analysis reran')", {"tidy": snapshot},
                               out, core, exporter, manifest)
    assert code == 0
    saved = json.loads((out / "figure_00.objects.json").read_text())
    assert saved["frozen_render_receipt"] == initial.frozen_render_receipts["plot"]
    assert json.loads((out / "result.json").read_text())["applied_count"] == 1
    rejected = worker._run_outputs(job, "pass", {"tidy": snapshot, "original": snapshot},
                                   tmp_path / "rejected", core, exporter, manifest)
    assert rejected == worker.EXIT_FORBIDDEN and not (tmp_path / "rejected").exists()


def test_worker_initial_manifest_receipt(tmp_path, monkeypatch):
    from src.figures import exporter, manifest, render_policy
    monkeypatch.setattr(render_policy, "export_figure", lambda *a, **k: ({"png": b"png"}, None, {}))
    monkeypatch.setattr(core, "_tick_label_problems", lambda *a, **k: [])
    value = contract()
    job = {"job_id": "frozen", "output_contract": value, "outputs": output_slots(value)}
    out = tmp_path / "out"
    assert worker._run_outputs(job, ANALYSIS, {}, out, core, exporter, manifest) == 0
    saved = json.loads((out / "figure_00.objects.json").read_text())
    payload = json.loads((out / "table_00.json").read_text())
    assert saved["frozen_render_receipt"]["data_sha256"]["tidy"] == typed_payload_sha256(payload)
    assert saved["frozen_render_receipt"]["geometry_status"] == "supported"
    assert encode_frame(decode_frame(payload)) == payload


def colorbar_figure(orientation="vertical", **kwargs):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    figure = Figure()
    FigureCanvasAgg(figure)
    axes = figure.subplots()
    image = axes.imshow([[1., 2.], [3., 4.]])
    colorbar = figure.colorbar(image, ax=axes, orientation=orientation, **kwargs)
    return figure, colorbar


@pytest.mark.parametrize("orientation", ["vertical", "horizontal"])
def test_standard_colorbar_style_and_draw_preserve_geometry(orientation):
    figure, colorbar = colorbar_figure(orientation)
    before = geometry_signature(figure)
    assert before["geometry_status"] == "supported", before
    colorbar.set_label("Revised scale label")
    figure.set_layout_engine(None)
    box = colorbar.ax.get_position()
    colorbar.ax.set_position([box.x0 + .02, box.y0 + .01, box.width, box.height])
    colorbar.ax.tick_params(labelsize=13, width=2)
    colorbar.outline.set_linewidth(3)
    assert geometry_signature(figure) == before
    figure.canvas.draw()
    assert geometry_signature(figure) == before


@pytest.mark.parametrize("field", ["norm", "boundaries", "values", "ticks", "outline"])
def test_colorbar_numerical_drift_changes_signature(field):
    figure, colorbar = colorbar_figure()
    before = geometry_signature(figure)
    assert before["geometry_status"] == "supported", before
    if field == "norm":
        colorbar.mappable.set_clim(0, 9)
    elif field == "boundaries":
        colorbar._boundaries = colorbar._boundaries.copy()
        colorbar._boundaries[1] += .001
    elif field == "values":
        colorbar._values = colorbar._values.copy()
        colorbar._values[1] += .001
    elif field == "ticks":
        colorbar.set_ticks([1., 2., 4.])
    else:
        colorbar.outline.set_xy([[0, 0], [0, 1], [.9, 1], [.9, 0], [0, 0]])
    after = geometry_signature(figure)
    assert after["geometry_status"] == "supported", after
    assert after["geometry_sha256"] != before["geometry_sha256"]


@pytest.mark.parametrize("custom", ["locator", "wrapped_locator", "subclass_locator", "outline_transform"])
def test_custom_colorbar_geometry_remains_unsupported(custom):
    from matplotlib.colorbar import _ColorbarAxesLocator
    from matplotlib.transforms import Affine2D
    figure, colorbar = colorbar_figure()
    locator = colorbar.ax.get_axes_locator()
    if custom == "locator":
        colorbar.ax.set_axes_locator(lambda ax, renderer: ax.get_position(original=True))
    elif custom == "wrapped_locator":
        locator._orig_locator = lambda ax, renderer: ax.get_position(original=True)
    elif custom == "subclass_locator":
        class CustomLocator(_ColorbarAxesLocator):
            pass
        colorbar.ax.set_axes_locator(CustomLocator(colorbar))
    else:
        colorbar.outline.set_transform(Affine2D().translate(.1, 0) + colorbar.ax.transAxes)
    receipt = geometry_signature(figure)
    assert receipt["geometry_status"] == "unsupported" and receipt["geometry_sha256"] is None
    assert "unsupported colorbar geometry" in receipt["geometry_unsupported"]


@pytest.mark.parametrize("extend", ["min", "max", "both"])
def test_extended_colorbar_remains_unsupported(extend):
    figure, colorbar = colorbar_figure(extend=extend)
    receipt = geometry_signature(figure)
    assert receipt["geometry_status"] == "unsupported" and receipt["geometry_sha256"] is None
    assert "unsupported colorbar geometry" in receipt["geometry_unsupported"]


def test_separated_colorbar_initial_replay_receipts_match():
    value = contract("fig, ax = plt.subplots()\nimage = ax.scatter(data['tidy']['x'], data['tidy']['y'], c=data['tidy']['y'])\nfig.colorbar(image, ax=ax)")
    initial = core.capture_outputs(ANALYSIS, output_contract=value)
    replay = core.capture_outputs("raise AssertionError('analysis reran')", output_contract=value,
                                  output_key="plot", frozen_tables=initial.tables)
    assert not initial.error and not replay.error
    assert initial.frozen_render_receipts["plot"]["geometry_status"] == "supported"
    assert initial.frozen_render_receipts == replay.frozen_render_receipts

"""Synthetic fine-grained editing: real artists, deterministic replay, no data edits."""
from __future__ import annotations

import json
from copy import deepcopy

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_hex
from matplotlib.figure import Figure

from src.figures.core import RenderedFigure, build_resolved_figure
from src.figures.manifest import assign_locators, available_font_families, build_manifest, find_by_locator
from src.figures.overrides import OverrideError, apply_overrides, merge_overrides, validate_overrides
from src.figures.resolve import resolve_spec
from src.figures.spec import parse_spec


def _figure(chart="scatter", error="none"):
    raw = {"chart": chart, "title": "SYNTHETIC editable figure", "error": error,
           "x": {"kind": "inline", "values": [2, 8, 19, 2, 8, 19]},
           "y": {"kind": "inline", "values": [4, 12, 7, 6, 16, 11]},
           "x_label": "SYNTHETIC x", "y_label": "SYNTHETIC y"}
    resolved, _ = resolve_spec(parse_spec(raw))
    return build_resolved_figure(resolved)


def _items(manifest, role):
    return [item for item in manifest["objects"] if item["role"] == role]


def _item(manifest, gid):
    return next(item for item in manifest["objects"] if item["gid"] == gid)


def _edit(rendered, manifest, gid, **props):
    edits = validate_overrides([{"gid": gid, "prop": key, "value": value} for key, value in props.items()], manifest)
    apply_overrides(rendered, manifest, edits)
    return build_manifest(rendered)


def test_discovery_preserves_legacy_gid_and_exposes_new_objects_deterministically():
    first, second = _figure(), _figure()
    old_gids = set(first.locators)
    manifest = build_manifest(first)
    again = build_manifest(second)
    assert manifest["version"] == 3
    json.dumps(manifest, allow_nan=False)
    assert old_gids.issubset({item["gid"] for item in manifest["objects"]})
    assert len(_items(manifest, "spine")) == 4
    assert len(_items(manifest, "point")) == 6
    assert _items(manifest, "ticklabel")
    assert [(item["gid"], item["locator"]) for item in manifest["objects"]] == [
        (item["gid"], item["locator"]) for item in again["objects"]]
    assert first.axes[0].collections[0].get_gid() == "axes_0.series_0"
    assert manifest["font_families"] == available_font_families()


def test_point_color_hide_restore_changes_only_rgba_never_data_or_sizes():
    rendered = _figure()
    manifest = build_manifest(rendered)
    collection = rendered.axes[0].collections[0]
    offsets, sizes = collection.get_offsets().copy(), collection.get_sizes().copy()
    limits = (rendered.axes[0].get_xlim(), rendered.axes[0].get_ylim())
    rgba = np.repeat(collection.get_facecolors(), len(offsets), axis=0)
    gid = "axes_0.series_0.point_1"
    changed = _edit(rendered, manifest, gid, color="#eb6834")
    actual = collection.get_facecolors()
    np.testing.assert_array_equal(actual[[0, 2, 3, 4, 5]], rgba[[0, 2, 3, 4, 5]])
    assert to_hex(actual[1]) == "#eb6834"
    assert "color" not in _item(changed, "axes_0.series_0")
    visible_pixels = np.asarray(rendered.figure.canvas.buffer_rgba()).copy()
    hidden = _edit(rendered, changed, gid, visible=False)
    assert _item(hidden, gid)["visible"] is False
    assert _item(hidden, gid)["color"] == "#eb6834"
    assert "bbox" not in _item(hidden, gid)
    assert collection.get_facecolors()[1, 3] == 0
    assert not np.array_equal(visible_pixels, np.asarray(rendered.figure.canvas.buffer_rgba()))
    restored = _edit(rendered, hidden, gid, visible=True)
    assert _item(restored, gid)["visible"] is True
    np.testing.assert_array_equal(visible_pixels, np.asarray(rendered.figure.canvas.buffer_rgba()))
    np.testing.assert_array_equal(collection.get_offsets(), offsets)
    np.testing.assert_array_equal(collection.get_sizes(), sizes)
    assert limits == (rendered.axes[0].get_xlim(), rendered.axes[0].get_ylim())


def test_point_replay_locator_and_parent_child_precedence_are_deterministic():
    original = _figure()
    manifest = build_manifest(original)
    gid = "axes_0.series_0.point_2"
    edits = validate_overrides([
        {"gid": gid, "prop": "color", "value": "#eb6834"},
        {"gid": gid, "prop": "visible", "value": False},
        {"gid": "axes_0.series_0", "prop": "color", "value": "#1baf7a"},
    ], manifest)
    fresh = _figure()
    # No child locators yet: the persisted structural locator must replay.
    assert gid not in fresh.locators
    apply_overrides(fresh, manifest, merge_overrides([], edits))
    actual = build_manifest(fresh)
    assert _item(actual, gid)["color"] == "#eb6834"
    assert _item(actual, gid)["visible"] is False
    assert _item(actual, "axes_0.series_0.point_0")["color"] == "#1baf7a"
    _edit(fresh, actual, gid, visible=True)
    assert to_hex(fresh.axes[0].collections[0].get_facecolors()[2]) == "#eb6834"


@pytest.mark.parametrize("prop,value", [("offset_px", [1, 2]), ("x", 9), ("fontsize", 12)])
def test_point_cannot_change_coordinates_or_text_style(prop, value):
    manifest = build_manifest(_figure())
    with pytest.raises(OverrideError):
        validate_overrides([{"gid": "axes_0.series_0.point_0", "prop": prop, "value": value}], manifest)


def test_spines_have_independent_actual_color_width_visibility_and_replay():
    rendered = _figure()
    manifest = build_manifest(rendered)
    others = {side: (spine.get_edgecolor(), spine.get_linewidth(), spine.get_visible())
              for side, spine in rendered.axes[0].spines.items() if side != "left"}
    gid = "axes_0.spine_left"
    updated = _edit(rendered, manifest, gid, color="#eb6834", linewidth=3, visible=False)
    spine = rendered.axes[0].spines["left"]
    assert to_hex(spine.get_edgecolor()) == _item(updated, gid)["color"] == "#eb6834"
    assert spine.get_linewidth() == 3 and spine.get_visible() is False
    assert _item(updated, gid)["visible"] is False
    assert others == {side: (s.get_edgecolor(), s.get_linewidth(), s.get_visible())
                      for side, s in rendered.axes[0].spines.items() if side != "left"}
    fresh = _figure()
    assert find_by_locator(fresh, _item(manifest, gid)["locator"]) is fresh.axes[0].spines["left"]
    restored = _edit(rendered, updated, gid, visible=True)
    assert _item(restored, gid)["visible"] is True


def test_tick_label_font_offset_visibility_preserves_ticks_and_numeric_data():
    rendered = _figure()
    manifest = build_manifest(rendered)
    target = next(item for item in _items(manifest, "ticklabel")
                  if item["locator"]["axis"] == "x" and item["visible"])
    gid = target["gid"]
    artist = rendered.artist_for(gid)
    ticks_before = rendered.axes[0].get_xticks().copy()
    points_before = rendered.axes[0].collections[0].get_offsets().copy()
    bbox_before = deepcopy(target["bbox"])
    family = "DejaVu Sans"
    moved = _edit(rendered, manifest, gid, offset_px=[7, -4], fontfamily=family, fontweight="bold")
    assert artist.get_fontfamily() == [family]
    assert artist.get_fontweight() == "bold"
    # Check offset separately from font-dependent text width.
    expected = artist.get_window_extent(rendered.figure.canvas.get_renderer())
    assert _item(moved, gid)["bbox"]["x0"] == pytest.approx(expected.x0 * 72 / rendered.figure.dpi, abs=0.001)
    assert _item(moved, gid)["bbox"] != bbox_before
    hidden = _edit(rendered, moved, gid, visible=False)
    assert _item(hidden, gid)["visible"] is False
    restored = _edit(rendered, hidden, gid, visible=True)
    assert _item(restored, gid)["visible"] is True
    np.testing.assert_array_equal(rendered.axes[0].get_xticks(), ticks_before)
    np.testing.assert_array_equal(rendered.axes[0].collections[0].get_offsets(), points_before)
    fresh = _figure()
    relocated = find_by_locator(fresh, target["locator"])
    assert relocated is not None and relocated.get_text() == target["label"]
    invalid = {**target["locator"], "tick_value": 1234567}
    assert find_by_locator(fresh, invalid) is None


def test_minor_tick_labels_and_both_sides_can_be_discovered_without_renumbering():
    from matplotlib.ticker import FixedLocator, FixedFormatter
    fig = Figure(figsize=(4, 3))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.scatter([0, 10], [0, 10])
    ax.xaxis.set_minor_locator(FixedLocator([1, 3, 7]))
    ax.xaxis.set_minor_formatter(FixedFormatter(["a", "b", "c"]))
    ax.tick_params(axis="x", which="minor", labeltop=True)
    rendered = assign_locators(RenderedFigure(fig, [ax]))
    manifest = build_manifest(rendered)
    minor = [item for item in _items(manifest, "ticklabel") if item["locator"]["which"] == "minor"]
    assert len(minor) == 6
    assert {item["locator"]["side"] for item in minor} == {"label1", "label2"}
    assert all(find_by_locator(rendered, item["locator"]) is rendered.artist_for(item["gid"]) for item in minor)


@pytest.mark.parametrize("role", ["title", "xlabel", "ylabel"])
def test_text_font_and_hide_restore_match_real_artist(role):
    rendered = _figure()
    manifest = build_manifest(rendered)
    gid = f"axes_0.{role}"
    changed = _edit(rendered, manifest, gid, fontfamily="DejaVu Sans", fontweight="bold", visible=False)
    artist = rendered.artist_for(gid)
    assert artist.get_fontfamily() == ["DejaVu Sans"] and artist.get_fontweight() == "bold"
    assert _item(changed, gid)["fontfamily"] == "DejaVu Sans"
    assert _item(changed, gid)["fontweight"] == "bold"
    assert _item(changed, gid)["visible"] is False
    restored = _edit(rendered, changed, gid, visible=True)
    assert _item(restored, gid)["visible"] is True


@pytest.mark.parametrize("prop,value,code", [
    ("fontfamily", "SYNTHETIC Font Definitely Not Installed", "unavailable_font"),
    ("fontfamily", ["DejaVu Sans"], "invalid_font"),
    ("fontweight", "ultra-fake", "invalid_fontweight"),
    ("fontweight", 700, "invalid_fontweight"),
    ("visible", "false", "invalid_value"),
])
def test_font_and_visibility_validation_never_claims_fallback_success(prop, value, code):
    manifest = build_manifest(_figure())
    with pytest.raises(OverrideError) as exc:
        validate_overrides([{"gid": "axes_0.title", "prop": prop, "value": value}], manifest)
    assert exc.value.code == code


def test_font_options_are_exact_existing_font_manager_families():
    from pathlib import Path
    from matplotlib import font_manager
    actual = {entry.name for entry in font_manager.fontManager.ttflist if Path(entry.fname).is_file()}
    assert set(available_font_families()) == actual
    assert ("Microsoft YaHei" in available_font_families()) == ("Microsoft YaHei" in actual)


def test_single_bar_style_hide_restore_preserves_all_patch_data():
    rendered = _figure("bar")
    manifest = build_manifest(rendered)
    bars = rendered.axes[0].containers[0].patches
    data = [(p.get_x(), p.get_y(), p.get_width(), p.get_height()) for p in bars]
    gid = "axes_0.series_0.bar_1"
    assert len(_items(manifest, "bar")) == len(bars)
    changed = _edit(rendered, manifest, gid, color="#eb6834", linewidth=3, visible=False)
    assert bars[1].get_visible() is False and bars[1].get_linewidth() == 3
    assert to_hex(bars[1].get_facecolor()) == "#eb6834"
    assert bars[0].get_visible() and to_hex(bars[0].get_facecolor()) != "#eb6834"
    assert _item(changed, gid)["visible"] is False
    assert "color" not in _item(changed, "axes_0.series_0")
    _edit(rendered, changed, gid, visible=True)
    assert bars[1].get_visible() is True
    assert data == [(p.get_x(), p.get_y(), p.get_width(), p.get_height()) for p in bars]
    fresh = _figure("bar")
    assert find_by_locator(fresh, _item(manifest, gid)["locator"]) is fresh.axes[0].containers[0].patches[1]


@pytest.mark.parametrize("chart", ["line", "errorbar", "bar"])
def test_series_color_and_hide_cover_errorbar_caps_and_intervals(chart):
    rendered = _figure(chart, "sd")
    manifest = build_manifest(rendered)
    gid = "axes_0.series_0"
    from matplotlib.container import ErrorbarContainer
    errorbar = next(c for c in rendered.axes[0].containers if isinstance(c, ErrorbarContainer))
    central, caps, intervals = errorbar.lines
    segments = [interval.get_segments() for interval in intervals]
    changed = _edit(rendered, manifest, gid, color="#eb6834", visible=False)
    for member in [central, *caps, *intervals]:
        if member is not None:
            assert member.get_visible() is False
            colour = member.get_color()
            assert to_hex(colour if isinstance(colour, str) else colour[0]) == "#eb6834"
    restored = _edit(rendered, changed, gid, visible=True)
    assert _item(restored, gid)["visible"] is True
    for interval, before in zip(intervals, segments):
        for segment, wanted in zip(interval.get_segments(), before):
            np.testing.assert_array_equal(segment, wanted)


@pytest.mark.parametrize("chart", ["line", "errorbar"])
def test_line_marker_styles_change_real_central_artist_without_moving_data(chart):
    rendered = _figure(chart, "sd")
    manifest = build_manifest(rendered)
    gid = "axes_0.series_0"
    central = rendered.artist_for(gid)
    x, y = central.get_xdata().copy(), central.get_ydata().copy()
    changed = _edit(rendered, manifest, gid, marker="s", markersize=8,
                    markerfacecolor="#1baf7a", linestyle="--")
    item = _item(changed, gid)
    assert item["marker"] == central.get_marker() == "s"
    assert item["markersize"] == central.get_markersize() == 8
    assert item["markerfacecolor"] == to_hex(central.get_markerfacecolor()) == "#1baf7a"
    assert item["linestyle"] == central.get_linestyle() == "--"
    no_marks = _edit(rendered, changed, gid, marker="None", linestyle="None")
    assert _item(no_marks, gid)["marker"] == "None"
    assert _item(no_marks, gid)["linestyle"] == "None"
    np.testing.assert_array_equal(central.get_xdata(), x)
    np.testing.assert_array_equal(central.get_ydata(), y)


@pytest.mark.parametrize("chart", ["bar"])
def test_non_marker_series_does_not_advertise_or_accept_marker_properties(chart):
    manifest = build_manifest(_figure(chart))
    item = _item(manifest, "axes_0.series_0")
    assert not {"marker", "markersize", "markerfacecolor", "linestyle"}.intersection(item["editable"])
    with pytest.raises(OverrideError):
        validate_overrides([{"gid": item["gid"], "prop": "marker", "value": "o"}], manifest)


def test_transparent_marker_face_is_not_claimed_as_black():
    rendered = _figure("line")
    rendered.artist_for("axes_0.series_0").set_markerfacecolor("none")
    assert "markerfacecolor" not in _item(build_manifest(rendered), "axes_0.series_0")


@pytest.mark.parametrize("prop,value", [("marker", "SYNTHETIC-invalid"),
                                         ("linestyle", "SYNTHETIC-invalid"),
                                         ("markersize", float("inf")), ("markersize", 100)])
def test_marker_style_value_bounds_are_validated(prop, value):
    manifest = build_manifest(_figure("line"))
    with pytest.raises(OverrideError):
        validate_overrides([{"gid": "axes_0.series_0", "prop": prop, "value": value}], manifest)


@pytest.mark.parametrize("chart", ["line", "bar"])
def test_script_container_inventory_can_hide_restore_entire_errorbar_group(chart):
    fig = Figure(figsize=(4, 3))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    if chart == "line":
        container = ax.errorbar([1, 5, 12], [3, 7, 5], yerr=[1, 2, 1], capsize=3)
    else:
        container = ax.bar([1, 5, 12], [3, 7, 5], yerr=[1, 2, 1], capsize=3)
    rendered = assign_locators(RenderedFigure(fig, [ax]))
    manifest = build_manifest(rendered, source="script")
    primary = container.lines[0] if chart == "line" else container.patches[0]
    gid = next(gid for gid, entry in rendered.locators.items()
               if entry.get("role") == "series" and entry.get("artist") is primary
               and (len(entry.get("members", [])) > 1 or entry.get("error_members")))
    hidden = _edit(rendered, manifest, gid, visible=False, color="#1baf7a")
    assert _item(hidden, gid)["visible"] is False
    members = rendered.locators[gid]["members"] + rendered.locators[gid].get("error_members", [])
    assert all(not member.get_visible() for member in members)
    restored = _edit(rendered, hidden, gid, visible=True)
    assert _item(restored, gid)["visible"] is True
    assert all(member.get_visible() for member in members)
    if chart == "bar":
        assert len([item for item in _items(restored, "bar") if item["parent_gid"] == gid]) == 3


def test_legend_family_weight_and_hidden_state_remain_restorable():
    rendered = _figure()
    ax = rendered.axes[0]
    ax.collections[0].set_label("SYNTHETIC points")
    legend = ax.legend(title="SYNTHETIC legend")
    assign_locators(rendered)
    manifest = build_manifest(rendered)
    changed = _edit(rendered, manifest, "axes_0.legend", fontfamily="DejaVu Sans",
                    fontweight="bold", visible=False)
    assert _item(changed, "axes_0.legend")["visible"] is False
    for text in [*legend.get_texts(), legend.get_title()]:
        assert text.get_fontfamily() == ["DejaVu Sans"] and text.get_fontweight() == "bold"
    restored = _edit(rendered, changed, "axes_0.legend", visible=True)
    assert _item(restored, "axes_0.legend")["visible"] is True


def _legend_figure(kind="scatter", duplicate=False):
    fig = Figure(figsize=(4, 3))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    for index, label in enumerate(("first", "first" if duplicate else "second")):
        if kind == "scatter":
            ax.scatter([1, 4, 9], [2 + index, 6 + index, 4 + index],
                       s=[16, 36, 64], alpha=0.7, label=label)
        elif kind == "errorbar":
            ax.errorbar([1, 4, 9], [2 + index, 6 + index, 4 + index],
                        yerr=[0.2, 0.4, 0.3], marker="o", capsize=3, label=label)
        else:
            ax.plot([1, 4, 9], [2 + index, 6 + index, 4 + index], marker="o", label=label)
    ax.legend(title="legend")
    ax.set_title("original")
    ax.annotate("note", (4, 6))
    return assign_locators(RenderedFigure(fig, [ax]))


@pytest.mark.parametrize("role", ["title", "xlabel", "ylabel", "annot_0"])
def test_literal_text_content_empty_identity_and_actual_exports(role):
    from src.figures.exporter import export_all
    import xml.etree.ElementTree as ET
    rendered = _legend_figure()
    manifest = build_manifest(rendered)
    gid = "axes_0." + role
    literal = '<script>alert(1)</script> $\\\\input{bad}$ & plain'
    before = rendered.axes[0].collections[0].get_offsets().copy()
    edited = _edit(rendered, manifest, gid, text=literal, fontstyle="italic", horizontalalignment="left")
    artist = rendered.artist_for(gid)
    assert artist.get_text() == _item(edited, gid)["text"] == literal
    assert artist.get_usetex() is False and artist.get_parse_math() is False
    exports = export_all(rendered, formats=("svg", "png"))
    root = ET.fromstring(exports["svg"])
    texts = [element.text for element in root.iter() if element.tag.endswith("}text")]
    assert literal in texts
    literal_node = next(element for element in root.iter() if element.tag.endswith('}text') and element.text == literal)
    assert literal_node.get('font-style') == 'italic'
    assert literal_node.get('text-anchor') == 'start'
    assert not any(element.tag.endswith("}script") for element in root.iter())
    assert exports["png"].startswith(b"\x89PNG\r\n\x1a\n")
    empty = _edit(rendered, edited, gid, text="")
    assert _item(empty, gid)["text"] == "" and "text" in _item(empty, gid)["editable"]
    assign_locators(rendered)
    assert gid in rendered.locators
    _edit(rendered, empty, gid, text="restored")
    np.testing.assert_array_equal(rendered.axes[0].collections[0].get_offsets(), before)


@pytest.mark.parametrize("prop,value", [
    ("text", "x" * 2001), ("text", {"html": "bad"}), ("text", "a\x00b"),
    ("text", "\ud800"), ("fontstyle", "url(bad)"), ("horizontalalignment", "justify"),
    ("marker", "$\\\\input{bad}$"), ("marker", [1, 2]), ("markeredgecolor", "url(javascript:bad)"),
    ("markeredgewidth", -1), ("markeredgewidth", 9), ("alpha", float("nan")),
    ("alpha", True), ("alpha", -0.1), ("alpha", 1.1), ("ncols", 0),
    ("ncols", 7), ("ncols", 1.5), ("ncols", True), ("loc", "arbitrary"),
])
def test_new_property_invalid_values_fail_closed(prop, value):
    manifest = build_manifest(_legend_figure())
    role = "title" if prop in {"text", "fontstyle", "horizontalalignment"} else "legend" if prop in {"ncols", "loc"} else "series_0"
    with pytest.raises(OverrideError):
        validate_overrides([{"gid": "axes_0." + role, "prop": prop, "value": value}], manifest)


@pytest.mark.parametrize('marker', ['x', '+'])
def test_open_scatter_markers_gain_visible_strokes_without_changing_data(marker):
    from src.figures.exporter import export_all
    import xml.etree.ElementTree as ET
    rendered = _figure()
    collection = rendered.axes[0].collections[0]
    before_offsets, before_sizes = collection.get_offsets().copy(), collection.get_sizes().copy()
    assert len(collection.get_edgecolors()) == 0
    manifest = build_manifest(rendered)
    updated = _edit(rendered, manifest, 'axes_0.series_0', marker=marker)
    assert _item(updated, 'axes_0.series_0')['marker'] == marker
    np.testing.assert_array_equal(collection.get_edgecolors(), collection.get_facecolors())
    svg = ET.fromstring(export_all(rendered, formats=('svg',))['svg'])
    group = next(node for node in svg.iter() if node.get('id') == 'axes_0.series_0')
    assert any(node.get('stroke') not in (None, 'none') for node in group.iter())
    np.testing.assert_array_equal(collection.get_offsets(), before_offsets)
    np.testing.assert_array_equal(collection.get_sizes(), before_sizes)


def test_point_edge_creation_and_alpha_preserve_other_unfilled_points():
    rendered = _figure()
    collection = rendered.axes[0].collections[0]
    sizes = collection.get_sizes().copy()
    offsets = collection.get_offsets().copy()
    manifest = build_manifest(rendered)
    assert len(collection.get_edgecolors()) == 0
    changed = _edit(rendered, manifest, 'axes_0.series_0.point_1', markeredgecolor='#202020', alpha=.4)
    assert collection.get_edgecolors()[1, 3] == .4
    assert np.all(collection.get_edgecolors()[[0, 2, 3, 4, 5], 3] == 0)
    _edit(rendered, changed, 'axes_0.series_0.point_2', alpha=.6)
    assert collection.get_edgecolors()[2, 3] == 0
    np.testing.assert_array_equal(collection.get_offsets(), offsets)
    np.testing.assert_array_equal(collection.get_sizes(), sizes)


def test_point_face_color_preserves_original_rgba_opacity():
    rendered = _legend_figure()
    collection = rendered.axes[0].collections[0]
    collection.set_alpha(None)
    collection.set_facecolors([(1, 0, 0, .2), (0, 1, 0, .4), (0, 0, 1, .6)])
    manifest = build_manifest(rendered)
    _edit(rendered, manifest, 'axes_0.series_0.point_1', markerfacecolor='#102030')
    np.testing.assert_allclose(collection.get_facecolors()[:, 3], [.2, .4, .6])


def test_numeric_tick_and_whole_legend_content_are_not_editable():
    manifest = build_manifest(_legend_figure())
    for target in [*_items(manifest, "ticklabel"), *_items(manifest, "legend")]:
        assert "text" not in target["editable"]
        with pytest.raises(OverrideError):
            validate_overrides([{"gid": target["gid"], "prop": "text", "value": "999"}], manifest)


def test_scatter_shape_edges_alpha_and_point_size_preserve_data_and_replay():
    from src.figures.exporter import export_all
    from matplotlib.markers import MarkerStyle
    rendered = _legend_figure()
    manifest = build_manifest(rendered)
    parent = "axes_0.series_0"
    child = parent + ".point_1"
    collection = rendered.artist_for(parent)
    offsets, sizes = collection.get_offsets().copy(), collection.get_sizes().copy()
    limits = rendered.axes[0].get_xlim(), rendered.axes[0].get_ylim()
    before_png = export_all(rendered, formats=("png",))["png"]
    edits = validate_overrides([
        {"gid": child, "prop": "marker", "value": "D"},
        {"gid": child, "prop": "markersize", "value": 11},
        {"gid": child, "prop": "markerfacecolor", "value": "#e04030"},
        {"gid": child, "prop": "alpha", "value": 0.9},
        {"gid": parent, "prop": "marker", "value": "s"},
        {"gid": parent, "prop": "markerfacecolor", "value": "#208040"},
        {"gid": parent, "prop": "markeredgecolor", "value": "#102030"},
        {"gid": parent, "prop": "markeredgewidth", "value": 2},
        {"gid": parent, "prop": "alpha", "value": 0.4},
        {"gid": parent, "prop": "label", "value": "renamed"},
    ], manifest)
    apply_overrides(rendered, manifest, merge_overrides([], edits))
    updated = build_manifest(rendered)
    assert _item(updated, child)["marker"] == "D"
    assert _item(updated, child)["markersize"] == 11
    assert _item(updated, child)["alpha"] == pytest.approx(0.9)
    assert "marker" not in _item(updated, parent)
    np.testing.assert_array_equal(collection.get_offsets(), offsets)
    np.testing.assert_array_equal(collection.get_sizes(), [sizes[0], 121, sizes[2]])
    assert (rendered.axes[0].get_xlim(), rendered.axes[0].get_ylim()) == limits
    np.testing.assert_allclose(collection.get_linewidths(), [2, 2, 2])
    for index, marker in enumerate(["s", "D", "s"]):
        expected = MarkerStyle(marker)
        np.testing.assert_array_equal(collection.get_paths()[index].vertices,
                                      expected.get_path().transformed(expected.get_transform()).vertices)
    legend = rendered.axes[0].get_legend()
    mark = legend.legend_handles[0]
    assert legend.get_texts()[0].get_text() == "renamed"
    assert to_hex(mark.get_facecolors()[0]) == "#208040"
    assert to_hex(mark.get_edgecolors()[0]) == "#102030"
    assert mark.get_facecolors()[0, 3] == pytest.approx(0.4)
    assert mark.get_linewidths()[0] == 2
    # Point-only size does not change the original legend marker diameter.
    assert mark.get_sizes()[0] != 121
    exported = export_all(rendered, formats=("svg", "png"))
    assert exported["png"] != before_png
    assert b"renamed" in exported["svg"] and b"#208040" in exported["svg"]
    replay = _legend_figure()
    apply_overrides(replay, manifest, merge_overrides([], edits))
    assert export_all(replay, formats=("png",))["png"] == exported["png"]
    assert _item(build_manifest(replay), child)["marker"] == "D"


@pytest.mark.parametrize("kind", ["line", "errorbar", "scatter"])
def test_series_label_and_all_legend_marks_survive_replay(kind):
    from src.figures.exporter import export_all
    from matplotlib.lines import Line2D
    from matplotlib.collections import PathCollection
    rendered = _legend_figure(kind)
    manifest = build_manifest(rendered)
    first = next(item for item in _items(manifest, "series") if item["label"] == "first")
    assert len(_items(manifest, "series")) == 2
    gid = first["gid"]
    props = dict(label="new $literal$", marker="^", markersize=9,
                 markerfacecolor="#208040", markeredgecolor="#102030", markeredgewidth=2.5,
                 color="#903010", alpha=0.6)
    if kind != "scatter":
        props["linestyle"] = "--"
    edits = validate_overrides([{"gid": gid, "prop": key, "value": value} for key, value in props.items()], manifest)
    apply_overrides(rendered, manifest, merge_overrides([], edits))
    legend = rendered.axes[0].get_legend()
    assert legend.get_texts()[0].get_text() == "new $literal$"
    assert legend.get_texts()[0].get_parse_math() is False
    rows = [row for column in legend._legend_handle_box.get_children() for row in column.get_children()]
    marks = rows[0].get_children()[0].get_children()
    marker = next(mark for mark in reversed(marks) if isinstance(mark, (Line2D, PathCollection)))
    if kind == "scatter":
        assert marker.get_sizes()[0] == 81
        assert marker.get_linewidths()[0] == 2.5
        assert marker.get_facecolors()[0, 3] == pytest.approx(0.6)
    else:
        assert marker.get_marker() == "^" and marker.get_markersize() == 9
        assert marker.get_markeredgewidth() == 2.5
        assert to_hex(marker.get_markeredgecolor()) == "#102030"
        assert all(mark.get_alpha() == 0.6 for mark in marks)
        line = rendered.artist_for(gid)
        np.testing.assert_array_equal(line.get_xdata(), [1, 4, 9])
        np.testing.assert_array_equal(line.get_ydata(), [2, 6, 4])
    assert _item(build_manifest(rendered), gid)["label"] == "new $literal$"
    fresh = _legend_figure(kind)
    apply_overrides(fresh, manifest, merge_overrides([], edits))
    assert export_all(fresh, formats=("png",))["png"] == export_all(rendered, formats=("png",))["png"]


@pytest.mark.parametrize('label', ['', '_literal', 'x' * 2000])
def test_series_empty_underscore_and_long_labels_replay_as_literal_metadata(label):
    rendered = _legend_figure()
    manifest = build_manifest(rendered)
    updated = _edit(rendered, manifest, 'axes_0.series_0', label=label)
    assert _item(updated, 'axes_0.series_0')['label'] == label
    assert rendered.axes[0].get_legend().get_texts()[0].get_text() == label
    updated = _edit(rendered, updated, 'axes_0.series_0', marker='D')
    assert _item(updated, 'axes_0.series_0')['label'] == label


def test_ambiguous_duplicate_or_custom_legend_labels_reject_before_mutation():
    for duplicate in (True, False):
        rendered = _legend_figure(duplicate=duplicate)
        if not duplicate:
            rendered.axes[0].get_legend().get_texts()[0].set_text("custom alias")
        manifest = build_manifest(rendered)
        edits = validate_overrides([{"gid": "axes_0.series_0", "prop": "label", "value": "changed"}], manifest)
        with pytest.raises(OverrideError, match="唯一关联") as exc:
            apply_overrides(rendered, manifest, edits)
        assert exc.value.code == "ambiguous_legend"
        assert rendered.artist_for("axes_0.series_0").get_label() == "first"


def test_unlabelled_source_with_custom_legend_alias_rejects_rename():
    fig = Figure(figsize=(4, 3))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    line, = ax.plot([1, 4], [2, 6])
    ax.legend([line], ['custom alias'])
    rendered = assign_locators(RenderedFigure(fig, [ax]))
    manifest = build_manifest(rendered)
    edits = validate_overrides([{'gid': 'axes_0.series_0', 'prop': 'label', 'value': 'new label'}], manifest)
    with pytest.raises(OverrideError) as exc:
        apply_overrides(rendered, manifest, edits)
    assert exc.value.code == 'ambiguous_legend'
    assert ax.get_legend().get_texts()[0].get_text() == 'custom alias'


def test_legend_columns_location_and_text_styles_preserve_rows_and_marks():
    from src.figures.exporter import export_all
    rendered = _legend_figure("errorbar")
    manifest = build_manifest(rendered)
    legend = rendered.axes[0].get_legend()
    old_marks = list(legend.legend_handles)
    before_png = export_all(rendered, formats=("png",))["png"]
    updated = _edit(rendered, manifest, "axes_0.legend", ncols=2, loc="lower left",
                    fontstyle="oblique", horizontalalignment="right", fontweight="bold")
    assert legend.legend_handles == old_marks
    assert len(legend._legend_handle_box.get_children()) == 2
    assert _item(updated, "axes_0.legend")["ncols"] == 2
    assert _item(updated, "axes_0.legend")["loc"] == "lower left"
    assert all(text.get_fontstyle() == "oblique" for text in legend.get_texts())
    assert export_all(rendered, formats=("png",))["png"] != before_png

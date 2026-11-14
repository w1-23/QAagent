"""Synthetic scatter geometry; assert display bounds and scientific coordinates."""
from __future__ import annotations

import json

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from src.figures.core import RenderedFigure, build_resolved_figure
from src.figures.manifest import assign_locators, build_manifest
from src.figures.overrides import apply_overrides, validate_overrides
from src.figures.resolve import resolve_spec
from src.figures.spec import parse_spec


def _scatter(x, y, *, dpi=100, **kwargs):
    figure = Figure(figsize=(4, 3), dpi=dpi)
    FigureCanvasAgg(figure)
    ax = figure.subplots()
    artist = ax.scatter(x, y, **kwargs)
    rendered = assign_locators(RenderedFigure(figure, [ax]))
    return rendered, artist


def _series(manifest):
    return [item for item in manifest["objects"] if item["role"] == "series"]


@pytest.mark.parametrize("dpi", [72, 100, 300])
@pytest.mark.parametrize("points", [([2.5], [9.0]), ([2.5, 8, 19], [9, -4, 15])])
def test_scatter_bounds_cover_transformed_points_and_marker_radius(dpi, points):
    rendered, artist = _scatter(*points, dpi=dpi, s=36, edgecolors="none", color="#0072b2")
    before = artist.get_offsets().copy()
    manifest = build_manifest(rendered)
    json.dumps(manifest, allow_nan=False)
    item, = _series(manifest)
    bbox = item["bbox"]
    display = artist.get_offset_transform().transform(before)
    svg = display * (72.0 / dpi)
    svg[:, 1] = rendered.figure.get_figheight() * 72 - svg[:, 1]
    # Circular scatter s=36 has a 3-point radius, independently of DPI.
    assert bbox == pytest.approx({
        "x0": svg[:, 0].min() - 3, "x1": svg[:, 0].max() + 3,
        "y0": svg[:, 1].min() - 3, "y1": svg[:, 1].max() + 3,
    }, abs=0.001)
    assert item["anchor"] == [bbox["x0"], bbox["y0"]]
    assert bbox["x1"] > bbox["x0"] and bbox["y1"] > bbox["y0"]
    assert item["color"] == "#0072b2"
    np.testing.assert_array_equal(artist.get_offsets(), before)


@pytest.mark.parametrize("x,y", [([], []), ([float("nan")], [2]),
                                    (np.ma.masked_all(2), [1, 2])])
def test_empty_or_fully_masked_scatter_has_no_invented_box(x, y):
    rendered, artist = _scatter(x, y, s=36)
    before = artist.get_offsets().copy()
    manifest = build_manifest(rendered)
    json.dumps(manifest, allow_nan=False)
    item, = _series(manifest)
    assert "bbox" not in item and "anchor" not in item
    assert item["gid"] == "axes_0.series_0"
    assert "color" in item["editable"]
    np.testing.assert_array_equal(artist.get_offsets(), before)


def test_partially_masked_scatter_uses_only_visible_point_bounds():
    rendered, artist = _scatter([2, float("nan"), 8], [3, 999999, 6], s=36)
    before = artist.get_offsets().copy()
    manifest = build_manifest(rendered)
    json.dumps(manifest, allow_nan=False)
    bbox = _series(manifest)[0]["bbox"]
    centers = artist.get_offset_transform().transform(np.array([[2, 3], [8, 6]]))
    centers *= 72 / rendered.figure.dpi
    assert bbox["x0"] == pytest.approx(centers[:, 0].min() - 3, abs=0.001)
    assert bbox["y0"] == pytest.approx(216 - centers[:, 1].max() - 3, abs=0.001)
    np.testing.assert_array_equal(artist.get_offsets(), before)


@pytest.mark.parametrize("colors,expected", [
    ("#0072b2", "#0072b2"),
    (["#0072b2"] * 3, "#0072b2"),
    (["#0072b2", "#d55e00", "#009e73"], None),
    (["#0072b2", "#d55e00"], None),
])
def test_collection_rgba_matrix_is_not_mistaken_for_one_rgba_vector(colors, expected):
    count = len(colors) if isinstance(colors, list) else 3
    rendered, artist = _scatter(range(count), range(count), c=colors, edgecolors="black")
    before = artist.get_offsets().copy()
    manifest = build_manifest(rendered)
    json.dumps(manifest, allow_nan=False)
    item, = _series(manifest)
    assert item.get("color") == expected
    np.testing.assert_array_equal(artist.get_offsets(), before)


def test_scalar_colormap_does_not_claim_one_series_color():
    rendered, artist = _scatter([1, 5, 20], [8, 2, 9], c=[0, 0.5, 1], cmap="viridis")
    manifest = build_manifest(rendered)
    json.dumps(manifest, allow_nan=False)
    assert "color" not in _series(manifest)[0]


def test_unfilled_scatter_reports_edge_color():
    rendered, artist = _scatter([1, 5], [8, 2], facecolors="none", edgecolors="#0072b2")
    assert _series(build_manifest(rendered))[0]["color"] == "#0072b2"


@pytest.mark.parametrize("grouped", [False, True])
def test_real_spec_single_and_multiple_series_style_edits_preserve_points(grouped):
    raw = {"chart": "scatter", "title": "SYNTHETIC scatter", "error": "none",
           "x": {"kind": "inline", "values": [2.5, 8, 19, 24]},
           "y": {"kind": "inline", "values": [9, -4, 15, 6]}}
    if grouped:
        raw["group"] = {"kind": "inline", "values": ["A", "B", "A", "B"]}
    resolved, _ = resolve_spec(parse_spec(raw))
    rendered = build_resolved_figure(resolved)
    before = [artist.get_offsets().copy() for artist in rendered.axes[0].collections]
    manifest = build_manifest(rendered)
    items = _series(manifest)
    assert len(items) == (2 if grouped else 1)
    assert all(item.get("color") and item.get("bbox") for item in items)
    edits = validate_overrides([
        {"gid": items[0]["gid"], "prop": "color", "value": "#d55e00"},
        {"gid": items[0]["gid"], "prop": "linewidth", "value": 2.5},
    ], manifest)
    apply_overrides(rendered, manifest, edits)
    after = build_manifest(rendered)
    json.dumps(after, allow_nan=False)
    assert _series(after)[0]["color"] == "#d55e00"
    for artist, original in zip(rendered.axes[0].collections, before):
        np.testing.assert_array_equal(artist.get_offsets(), original)
    assert list(rendered.axes[0].collections[0].get_linewidths()) == [2.5]


def _decorated_scatter():
    rendered, _ = _scatter([2, 8], [9, 3], color="#0072b2", label="SYNTHETIC A")
    ax = rendered.axes[0]
    ax.scatter([4, 12], [5, 7], color="#d55e00", label="SYNTHETIC B")
    ax.set_title("_SYNTHETIC title")
    ax.set_xlabel("SYNTHETIC horizontal label")
    ax.set_ylabel("SYNTHETIC vertical label")
    ax.annotate("SYNTHETIC note", xy=(4, 5))
    ax.legend()
    return assign_locators(rendered)


@pytest.mark.parametrize("role,colour", [
    ("title", "black"), ("xlabel", "red"),
    ("ylabel", "#abc"), ("annot", (0.2, 0.4, 0.6, 0.8)),
])
def test_manifest_text_has_displayed_text_and_normalised_artist_color(role, colour):
    from matplotlib.colors import to_hex

    rendered = _decorated_scatter()
    gid = f"axes_0.{role}" if role != "annot" else "axes_0.annot_0"
    artist = rendered.artist_for(gid)
    artist.set_label("SYNTHETIC internal metadata must not replace visible text")
    artist.set_color(colour)
    manifest = build_manifest(rendered)
    json.dumps(manifest, allow_nan=False)
    item = next(item for item in manifest["objects"] if item["gid"] == gid)
    assert item["label"] == artist.get_text()
    assert item["color"] == to_hex(artist.get_color())
    assert len(item["color"]) == 7 and item["color"].startswith("#")
    assert item["fontsize"] == artist.get_fontsize()


def test_default_spec_title_reports_actual_black_color_instead_of_missing_field():
    raw = {"chart": "scatter", "title": "SYNTHETIC default title",
           "x": {"kind": "inline", "values": [2, 8]},
           "y": {"kind": "inline", "values": [9, 3]}}
    resolved, _ = resolve_spec(parse_spec(raw))
    rendered = build_resolved_figure(resolved)
    from matplotlib.colors import to_hex
    item = next(item for item in build_manifest(rendered)["objects"] if item["role"] == "title")
    assert item["color"] == to_hex(rendered.axes[0].title.get_color()) == "#000000"
    assert item["label"] == rendered.axes[0].title.get_text()


@pytest.mark.parametrize("frameon", [False, True])
@pytest.mark.parametrize("colours,title_color,expected", [
    (["red", "#ff0000"], "red", "#ff0000"),
    (["red", "blue"], "red", None),
    (["red", "red"], "black", None),
])
def test_legend_records_real_frame_and_only_uniform_displayed_text_color(frameon, colours, title_color, expected):
    rendered = _decorated_scatter()
    legend = rendered.axes[0].get_legend()
    legend.set_title("SYNTHETIC legend title")
    legend.get_title().set_color(title_color)
    for text, colour in zip(legend.get_texts(), colours):
        text.set_color(colour)
    legend.set_frame_on(frameon)
    manifest = build_manifest(rendered)
    json.dumps(manifest, allow_nan=False)
    item = next(item for item in manifest["objects"] if item["role"] == "legend")
    assert item["frameon"] is legend.get_frame_on() is frameon
    assert item.get("color") == expected


def test_empty_legend_has_no_invented_color():
    rendered = _decorated_scatter()
    legend = rendered.axes[0].legend([], [], frameon=False)
    assign_locators(rendered)
    item = next(item for item in build_manifest(rendered)["objects"] if item["role"] == "legend")
    assert not legend.get_texts() and not legend.get_title().get_text()
    assert "color" not in item
    assert item["frameon"] is False


def test_text_and_legend_style_round_trip_manifest_matches_actual_edited_artists():
    from matplotlib.colors import to_hex

    rendered = _decorated_scatter()
    legend = rendered.axes[0].get_legend()
    # Empty legend title has its own black default; it must not hide the actual
    # uniform red label colour, since there is no displayed title.
    for text in legend.get_texts():
        text.set_color("red")
    before_points = [artist.get_offsets().copy() for artist in rendered.axes[0].collections]
    manifest = build_manifest(rendered)
    before_legend = next(item for item in manifest["objects"] if item["role"] == "legend")
    assert before_legend["color"] == "#ff0000"
    edits = validate_overrides([
        {"gid": "axes_0.title", "prop": "color", "value": "#009e73"},
        {"gid": "axes_0.legend", "prop": "color", "value": "#0072b2"},
        {"gid": "axes_0.legend", "prop": "frameon", "value": False},
    ], manifest)
    apply_overrides(rendered, manifest, edits)
    after = build_manifest(rendered)
    title_item = next(item for item in after["objects"] if item["role"] == "title")
    legend_item = next(item for item in after["objects"] if item["role"] == "legend")
    assert title_item["color"] == to_hex(rendered.axes[0].title.get_color()) == "#009e73"
    assert all(legend_item["color"] == to_hex(text.get_color()) == "#0072b2"
               for text in legend.get_texts())
    assert legend_item["frameon"] is legend.get_frame_on() is False
    for artist, original in zip(rendered.axes[0].collections, before_points):
        np.testing.assert_array_equal(artist.get_offsets(), original)


@pytest.mark.parametrize("colour,expected", [("red", "#ff0000"), ("#abc", "#aabbcc")])
def test_named_series_color_is_normalised_without_changing_artist(colour, expected):
    rendered, artist = _scatter([2, 8], [9, 3], color=colour)
    before = artist.get_facecolors().copy()
    assert _series(build_manifest(rendered))[0]["color"] == expected
    np.testing.assert_array_equal(artist.get_facecolors(), before)

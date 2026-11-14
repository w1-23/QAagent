"""Synthetic export regression: actual SVG paint/fonts and rendered image pixels."""
from __future__ import annotations

import io
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image

from src.figures.core import RenderedFigure
from src.figures.exporter import export_all, flatten_svg, sanitize_svg
from src.figures.manifest import assign_locators, available_font_families, build_manifest
from src.figures.overrides import apply_overrides, validate_overrides


NS = "http://www.w3.org/2000/svg"
PURPLE = "#6250d6"


def _synthetic_scatter():
    import matplotlib
    fig = Figure(figsize=(4, 3), dpi=100)
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.scatter([1, 3, 6], [2, 5, 4], s=144, color="#2a78d6", edgecolors="none",
               alpha=0.85, label="SYNTHETIC points")
    ax.legend(loc="lower right")
    rendered = assign_locators(RenderedFigure(fig, [ax]))
    rendered.rc_params = {**matplotlib.rcParams, "svg.fonttype": "none"}
    return rendered


def _apply(rendered, edits):
    manifest = build_manifest(rendered)
    apply_overrides(rendered, manifest, validate_overrides(edits, manifest))
    return build_manifest(rendered)


def _painted_paths(svg, gid):
    root = ET.fromstring(svg)
    group = next(node for node in root.iter() if node.get("id") == gid)
    paths = []

    def walk(node, inherited):
        if node.tag.endswith("}defs"):
            return
        paint = dict(inherited)
        for attr in ("fill", "stroke", "fill-opacity", "stroke-opacity"):
            if attr in node.attrib:
                paint[attr] = node.get(attr)
        paint["opacity"] = inherited.get("opacity", 1.0) * float(node.get("opacity", "1"))
        if node.tag.endswith("}path"):
            paths.append(paint)
        for child in node:
            walk(child, paint)

    walk(group, {"fill": "#000000", "stroke": "none", "opacity": 1.0})
    return paths


def _raster_svg(svg):
    # Optional test renderer only; no server, model, external executable or network.
    fitz = pytest.importorskip("fitz")
    with fitz.open(stream=svg, filetype="svg") as document:
        pdf = document.convert_to_pdf()
    with fitz.open(stream=pdf, filetype="pdf") as document:
        pixmap = document[0].get_pixmap(alpha=False)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def _point_centres(rendered):
    fig = rendered.figure
    collection = rendered.axes[0].collections[0]
    points = collection.get_offset_transform().transform(collection.get_offsets()) * 72 / fig.dpi
    points[:, 1] = fig.get_figheight() * 72 - points[:, 1]
    return [(int(round(x)), int(round(y))) for x, y in points]


def test_actual_point_svg_fill_and_image_pixels_match_png_after_edit_hide_restore():
    rendered = _synthetic_scatter()
    collection = rendered.axes[0].collections[0]
    original = collection.get_offsets().copy()
    manifest = _apply(rendered, [
        {"gid": "axes_0.series_0", "prop": "color", "value": "#2a78d6"},
        {"gid": "axes_0.series_0.point_0", "prop": "color", "value": PURPLE},
    ])
    assert next(item for item in manifest["objects"] if item["gid"].endswith(".point_0"))["color"] == PURPLE
    exported = export_all(rendered, formats=("svg", "png"), dpi=72)
    paints = _painted_paths(exported["svg"], "axes_0.series_0")
    assert [paint["fill"] for paint in paints] == [PURPLE, "#2a78d6", "#2a78d6"]
    assert all(paint.get("fill-opacity") == "0.85" for paint in paints)
    assert b"<use" not in exported["svg"] and b"style=" not in exported["svg"]
    assert sanitize_svg(exported["svg"].decode())
    svg_image = _raster_svg(exported["svg"])
    png_image = Image.open(io.BytesIO(exported["png"])).convert("RGB")
    centres = _point_centres(rendered)
    assert svg_image.size == png_image.size
    for point, colour in zip(centres, [(98, 80, 214), (42, 120, 214), (42, 120, 214)]):
        expected = np.asarray(colour) * 0.85 + 255 * 0.15
        np.testing.assert_allclose(svg_image.getpixel(point), expected, atol=2)
        np.testing.assert_allclose(svg_image.getpixel(point), png_image.getpixel(point), atol=2)
    _apply(rendered, [{"gid": "axes_0.series_0.point_0", "prop": "visible", "value": False}])
    hidden = export_all(rendered, formats=("svg",))["svg"]
    assert _raster_svg(hidden).getpixel(centres[0]) == (255, 255, 255)
    _apply(rendered, [{"gid": "axes_0.series_0.point_0", "prop": "visible", "value": True}])
    restored = export_all(rendered, formats=("svg",))["svg"]
    assert _raster_svg(restored).getpixel(centres[0]) == svg_image.getpixel(centres[0])
    np.testing.assert_array_equal(collection.get_offsets(), original)


def test_errorbar_cap_marker_edges_follow_series_colour_in_actual_svg():
    import matplotlib
    fig = Figure(figsize=(4, 3))
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.errorbar([1, 2], [3, 4], yerr=[.4, .6], color="#42949e",
                ecolor="#42949e", capsize=3, label="SYNTHETIC intervals")
    ax.legend()
    rendered = assign_locators(RenderedFigure(fig, [ax]))
    rendered.rc_params = {**matplotlib.rcParams, "svg.fonttype": "none"}
    _apply(rendered, [{"gid": "axes_0.series_0", "prop": "color", "value": PURPLE}])
    svg = export_all(rendered, formats=("svg",))["svg"].decode().lower()
    assert PURPLE in svg
    assert "#42949e" not in svg  # includes cap markers and copied legend marks


def test_use_paint_inherits_without_overwriting_explicit_definition_paint():
    source = f'''<svg xmlns="{NS}" viewBox="0 0 40 40">
      <defs><path id="plain" d="M0 0h4v4z"/><path id="explicit" fill="#eb6834" d="M0 0h4v4z"/></defs>
      <g id="SYNTHETIC"><use href="#plain" x="2.123456789" y="3" transform="scale(2)"
      style="fill:#6250d6;stroke:#1baf7a;stroke-width:2;fill-opacity:0.4;opacity:0.5"/>
      <use href="#explicit" x="10" fill="#6250d6"/></g></svg>'''
    flat = flatten_svg(source)
    paints = _painted_paths(flat, "SYNTHETIC")
    assert paints[0] == {"fill": PURPLE, "stroke": "#1baf7a", "fill-opacity": "0.4", "opacity": 0.5}
    assert paints[1]["fill"] == "#eb6834"
    root = ET.fromstring(flat)
    wrapper = next(node for node in root.iter() if node.get("fill") == PURPLE and node.get("stroke"))
    assert wrapper.get("transform") == "scale(2) translate(2.123456789,3.0)"
    assert wrapper.get("stroke-width") == "2"


@pytest.mark.parametrize("font,weight,size,family", [
    ("9px 'Microsoft YaHei'", "normal", "9px", "'Microsoft YaHei'"),
    ("700 13px 'DejaVu Sans'", "700", "13px", "'DejaVu Sans'"),
    ("bold 10pt 'Microsoft YaHei', sans-serif", "bold", "10pt", "'Microsoft YaHei', sans-serif"),
    ("italic 700 12px/1.2 'DejaVu Sans'", "700", "12px", "'DejaVu Sans'"),
])
def test_legacy_matplotlib_font_shorthand_becomes_real_family_weight_and_size(font, weight, size, family):
    source = f'''<svg xmlns="{NS}" viewBox="0 0 100 50"><text x="5" y="20" style="font:{font}">SYNTHETIC</text></svg>'''
    flat = flatten_svg(source)
    text = next(node for node in ET.fromstring(flat).iter() if node.tag.endswith("}text"))
    assert text.get("font-family") == family
    assert text.get("font-weight") == weight
    assert text.get("font-size") == size
    assert text.get("font-style") == ("italic" if font.startswith("italic") else "normal")
    assert "style" not in text.attrib


def test_font_shorthand_and_longhand_follow_declaration_order():
    source = f'''<svg xmlns="{NS}" viewBox="0 0 100 50"><text style="font-weight:bold;font:9px 'DejaVu Sans';font-size:14px">SYNTHETIC</text></svg>'''
    text = next(node for node in ET.fromstring(flatten_svg(source)).iter() if node.tag.endswith("}text"))
    assert text.get("font-weight") == "normal"
    assert text.get("font-size") == "14px"
    assert text.get("font-family") == "'DejaVu Sans'"


@pytest.mark.parametrize("font", ["9px url(https://invalid.test/font)", "9px expression(alert(1))", "var(--font)"])
def test_font_shorthand_never_preserves_active_or_unparsed_css(font):
    source = f'''<svg xmlns="{NS}" viewBox="0 0 100 50"><text style="font:{font}">SYNTHETIC</text></svg>'''
    text = next(node for node in ET.fromstring(flatten_svg(source)).iter() if node.tag.endswith("}text"))
    assert not {"font-family", "font-size", "font-weight", "style"}.intersection(text.attrib)


@pytest.mark.parametrize("move_title", [False, True])
def test_actual_matplotlib_export_preserves_edited_text_font_family_weight_and_size(move_title):
    rendered = _synthetic_scatter()
    ax = rendered.axes[0]
    ax.set_title("SYNTHETIC edited title")
    assign_locators(rendered)
    family = "Microsoft YaHei" if "Microsoft YaHei" in available_font_families() else "DejaVu Sans"
    _apply(rendered, [
        {"gid": "axes_0.title", "prop": "fontfamily", "value": family},
        {"gid": "axes_0.title", "prop": "fontweight", "value": "bold"},
        {"gid": "axes_0.title", "prop": "fontstyle", "value": "oblique"},
        {"gid": "axes_0.title", "prop": "fontsize", "value": 13},
        *([{"gid": "axes_0.title", "prop": "offset_px", "value": [15, 9]}] if move_title else []),
    ])
    svg = export_all(rendered, formats=("svg",))["svg"]
    root = ET.fromstring(svg)
    text = next(node for node in root.iter() if node.text == "SYNTHETIC edited title")
    assert family in text.get("font-family", "")
    assert text.get("font-weight") in {"700", "bold"}
    assert text.get("font-size") == "13px"
    assert text.get("font-style") == "oblique"
    from src.tools.draw_svg import _sanitize_svg
    clean = ET.fromstring(_sanitize_svg(svg.decode('utf-8')))
    clean_text = next(node for node in clean.iter() if node.text == "SYNTHETIC edited title")
    assert clean_text.get('font-style') == 'oblique'


@pytest.mark.parametrize("chart", ["scatter", "line", "errorbar", "bar"])
def test_actual_svg_legend_marks_follow_series_styles_without_recolouring_text(chart):
    from src.figures.core import build_resolved_figure
    from src.figures.spec import parse_spec
    from src.figures.resolve import resolve_spec
    raw = {"chart": chart, "error": "sd" if chart in {"errorbar", "bar"} else "none",
           "x": {"kind": "inline", "values": [1, 1, 3, 3] * 2},
           "y": {"kind": "inline", "values": [2, 4, 5, 7, 3, 5, 7, 9]},
           "group": {"kind": "inline", "values": ["SYNTHETIC A"] * 4 + ["SYNTHETIC B"] * 4}}
    rendered = build_resolved_figure(resolve_spec(parse_spec(raw))[0])
    edits = [{"gid": "axes_0.series_0", "prop": "color", "value": "#eb6834"},
             {"gid": "axes_0.legend", "prop": "color", "value": "#444444"}]
    if chart in {"line", "errorbar"}:
        edits += [{"gid": "axes_0.series_0", "prop": key, "value": value} for key, value in
                  {"marker": "s", "markersize": 7, "markerfacecolor": PURPLE, "linestyle": "--"}.items()]
    _apply(rendered, edits)
    legend = rendered.axes[0].get_legend()
    assert all(text.get_color() == "#444444" for text in legend.get_texts())
    svg = export_all(rendered, formats=("svg",))["svg"]
    paints = _painted_paths(svg, "axes_0.legend")
    assert any(paint.get("fill") == "#eb6834" or paint.get("stroke") == "#eb6834" for paint in paints)
    if chart in {"line", "errorbar"}:
        assert any(paint.get("fill") == PURPLE for paint in paints)
    _apply(rendered, [{"gid": "axes_0.series_0", "prop": "visible", "value": False}])
    assert legend.get_texts()[0].get_visible() is False
    assert legend.get_texts()[1].get_visible() is True
    _apply(rendered, [{"gid": "axes_0.series_0", "prop": "visible", "value": True}])
    assert all(text.get_visible() for text in legend.get_texts())
    assert all(text.get_color() == "#444444" for text in legend.get_texts())

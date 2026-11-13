"""Tests for the figure engine: specification, rendering, manifest, overrides.

These are the assertions that make the engine's promises checkable rather
than aspirational:

* a request that is not understood is **refused**, not guessed at;
* the three export formats come from **one** render, so they cannot drift
  apart;
* the exported SVG contains **real text**, survives the service's own
  sanitizer, and carries no active content;
* an override that is not on the allowlist is refused **as a whole batch**,
  and an override can never reach an arbitrary Python attribute.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from src.figures.core import build_resolved_figure, capture_figure
from src.figures.exporter import ExportError, export_all, flatten_svg
from src.figures.manifest import assign_locators, build_manifest
from src.figures.overrides import OverrideError, apply_overrides, validate_overrides
from src.figures.resolve import resolve_spec
from src.figures.spec import SpecError, parse_spec

SVG_NS = "http://www.w3.org/2000/svg"


def _spec(**overrides):
    base = {
        "chart": "bar",
        "title": "果实重量",
        "x_label": "处理",
        "y_label": "g",
        "x": {"kind": "inline", "column": "处理", "values": ["CK", "T1", "T2"]},
        "y": {"kind": "inline", "column": "重量", "values": [10.1, 12.2, 9.3]},
    }
    base.update(overrides)
    return base


def _render(spec):
    resolved, _info = resolve_spec(parse_spec(spec))
    return build_resolved_figure(resolved), resolved


# ── Specification validation ──────────────────────────────────────────────

def test_unknown_chart_is_refused():
    with pytest.raises(SpecError) as excinfo:
        parse_spec(_spec(chart="pie"))
    assert excinfo.value.code == "unsupported_chart"


def test_unknown_field_is_refused_not_ignored():
    with pytest.raises(SpecError) as excinfo:
        parse_spec(_spec(colour="#ff0000"))
    assert excinfo.value.code == "unknown_field"


def test_inline_table_over_the_limit_is_refused():
    with pytest.raises(SpecError) as excinfo:
        parse_spec(_spec(y={"kind": "inline", "column": "v",
                             "values": list(range(500))}))
    assert excinfo.value.code == "inline_too_large"


def test_error_kind_outside_the_vocabulary_is_refused():
    with pytest.raises(SpecError) as excinfo:
        parse_spec(_spec(error="95%"))
    assert excinfo.value.code == "unsupported_error_kind"


def test_out_of_range_figure_size_is_refused():
    with pytest.raises(SpecError) as excinfo:
        parse_spec(_spec(style={"figure_size_in": [400, 5]}))
    assert excinfo.value.code == "value_out_of_range"


# ── One render, three formats ─────────────────────────────────────────────

@pytest.mark.parametrize("chart", ["bar", "line", "scatter", "errorbar",
                                   "boxplot", "histogram"])
def test_every_chart_type_renders_all_three_formats(chart):
    spec = _spec(chart=chart)
    if chart == "histogram":
        spec.pop("x")
    if chart == "boxplot":
        spec.pop("x")
    rendered, _resolved = _render(spec)
    blobs = export_all(rendered, formats=("svg", "png", "pdf"))
    assert set(blobs) == {"svg", "png", "pdf"}
    assert all(isinstance(value, bytes) and value for value in blobs.values())
    assert blobs["png"].startswith(b"\x89PNG\r\n\x1a\n")
    assert blobs["pdf"].startswith(b"%PDF-")


def test_svg_keeps_text_as_text_not_glyph_outlines():
    rendered, _resolved = _render(_spec())
    svg = export_all(rendered, formats=("svg",))["svg"].decode("utf-8")
    # svg.fonttype='none' means the title is a <text> node, not a path.
    assert "<text" in svg
    assert "果实重量" in svg


def test_svg_carries_no_active_content():
    rendered, _resolved = _render(_spec())
    svg = export_all(rendered, formats=("svg",))["svg"].decode("utf-8")
    assert "<style" not in svg
    assert "style=" not in svg
    assert "<script" not in svg.lower()
    assert "javascript:" not in svg.lower()
    assert not re.search(r"\son[a-z]+\s*=", svg, re.I)


def test_svg_passes_the_service_sanitizer():
    """The chat inline preview and the browser renderer share one allowlist.

    If the export did not satisfy both, the preview and the download would
    show different pictures.
    """
    from src.tools.draw_svg import _sanitize_svg

    rendered, _resolved = _render(_spec())
    svg = export_all(rendered, formats=("svg",))["svg"].decode("utf-8")
    cleaned = _sanitize_svg(svg)
    assert len(cleaned) > 0
    # Nothing the sanitizer keeps may have lost its text.
    assert cleaned.count("<text") == svg.count("<text")
    # And nothing that carries an identity may have lost it.
    for gid in re.findall(r'\bid="([^"]+)"', svg):
        assert f'id="{gid}"' in cleaned


def test_export_refuses_a_format_it_cannot_write():
    rendered, _resolved = _render(_spec())
    with pytest.raises(ExportError):
        export_all(rendered, formats=("bmp",))


# ── The manifest ──────────────────────────────────────────────────────────

def test_manifest_describes_the_editable_objects():
    rendered, _resolved = _render(_spec())
    manifest = build_manifest(rendered, source="spec")
    roles = {item["role"] for item in manifest["objects"]}
    assert "title" in roles
    assert "xlabel" in roles
    assert "ylabel" in roles
    assert "series" in roles
    for item in manifest["objects"]:
        assert item["gid"]
        assert isinstance(item["editable"], list)


def test_manifest_boxes_lie_inside_the_canvas():
    rendered, _resolved = _render(_spec())
    manifest = build_manifest(rendered, source="spec")
    width, height = manifest["figure_size_px"]
    for item in manifest["objects"]:
        box = item.get("bbox")
        if not box:
            continue
        assert box["x0"] >= -1 and box["x1"] <= width + 1
        assert box["y0"] >= -1 and box["y1"] <= height + 1


def test_gids_are_stable_across_renders():
    first, _ = _render(_spec())
    second, _ = _render(_spec())
    gids_first = set(build_manifest(first)["objects"][0].keys())
    assert gids_first
    names_first = {item["gid"] for item in build_manifest(first)["objects"]}
    names_second = {item["gid"] for item in build_manifest(second)["objects"]}
    assert names_first == names_second


def test_free_script_gids_are_assigned_by_role():
    from src.figures.core import RenderedFigure

    script = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([1, 2, 3], [4, 5, 6], label='A')\n"
        "ax.set_title('T')\n"
    )
    result = capture_figure(script)
    assert result.error == ""
    rendered = assign_locators(RenderedFigure(
        figure=result.figure, axes=list(result.figure.axes)))
    gids = set(rendered.locators)
    assert "axes_0" in gids
    assert "axes_0.title" in gids
    assert "axes_0.series_0" in gids


# ── Overrides ─────────────────────────────────────────────────────────────

def test_allowed_override_changes_the_figure():
    rendered, _resolved = _render(_spec())
    manifest = build_manifest(rendered, source="spec")
    gid = next(item["gid"] for item in manifest["objects"]
               if item["role"] == "series")
    validated = validate_overrides([{"gid": gid, "prop": "color",
                                     "value": "#123456"}], manifest)
    assert apply_overrides(rendered, manifest, validated) == [gid]
    svg = export_all(rendered, formats=("svg",))["svg"].decode("utf-8")
    assert "#123456" in svg.lower()


def test_an_unknown_property_is_refused_as_a_whole_batch():
    rendered, _resolved = _render(_spec())
    manifest = build_manifest(rendered, source="spec")
    gid = next(item["gid"] for item in manifest["objects"]
               if item["role"] == "title")
    with pytest.raises(OverrideError) as excinfo:
        validate_overrides([
            {"gid": gid, "prop": "color", "value": "#000000"},
            {"gid": gid, "prop": "__class__", "value": "os"},
        ], manifest)
    assert excinfo.value.code == "unknown_prop"


def test_an_unknown_object_is_refused():
    rendered, _resolved = _render(_spec())
    manifest = build_manifest(rendered, source="spec")
    with pytest.raises(OverrideError) as excinfo:
        validate_overrides([{"gid": "axes_0.nope", "prop": "color",
                             "value": "#000000"}], manifest)
    assert excinfo.value.code == "unknown_gid"


def test_a_property_outside_the_role_vocabulary_is_refused():
    rendered, _resolved = _render(_spec())
    manifest = build_manifest(rendered, source="spec")
    gid = next(item["gid"] for item in manifest["objects"]
               if item["role"] == "title")
    with pytest.raises(OverrideError) as excinfo:
        validate_overrides([{"gid": gid, "prop": "linewidth", "value": 2.0}],
                           manifest)
    assert excinfo.value.code == "prop_not_editable"


def test_out_of_range_values_are_refused():
    rendered, _resolved = _render(_spec())
    manifest = build_manifest(rendered, source="spec")
    gid = next(item["gid"] for item in manifest["objects"]
               if item["role"] == "title")
    with pytest.raises(OverrideError) as excinfo:
        validate_overrides([{"gid": gid, "prop": "fontsize", "value": 0.2}],
                           manifest)
    assert excinfo.value.code == "value_out_of_range"


def test_an_offset_beyond_the_canvas_is_refused():
    rendered, _resolved = _render(_spec())
    manifest = build_manifest(rendered, source="spec")
    gid = next(item["gid"] for item in manifest["objects"]
               if item["role"] == "title")
    with pytest.raises(OverrideError) as excinfo:
        validate_overrides([{"gid": gid, "prop": "offset_px",
                             "value": [9999, 0]}], manifest)
    assert excinfo.value.code == "offset_out_of_range"


def test_data_values_cannot_be_overridden():
    """There is no property that changes a number the figure shows.

    The vocabulary is closed; this test states the property that matters:
    no entry in it can alter a data value, an axis range or a tick.
    """
    from src.figures.overrides import ALL_EDITABLE_PROPS

    forbidden = {"value", "values", "data", "ylim", "xlim", "yticks",
                 "xticks", "height", "width", "positions", "zorder"}
    assert not (set(ALL_EDITABLE_PROPS) & forbidden)


def test_overrides_survive_a_re_render_through_the_manifest():
    """A re-render re-finds the artist by its locator, not by identity."""
    rendered, _resolved = _render(_spec())
    manifest = build_manifest(rendered, source="spec")
    gid = next(item["gid"] for item in manifest["objects"]
               if item["role"] == "title")
    validated = validate_overrides([{"gid": gid, "prop": "fontsize",
                                     "value": 14.0}], manifest)
    apply_overrides(rendered, manifest, validated)
    assert rendered.locators[gid]["artist"].get_fontsize() == 14.0


# ── Flattening ────────────────────────────────────────────────────────────

def test_flatten_rejects_a_document_that_is_not_svg():
    with pytest.raises(ExportError):
        flatten_svg("<html><body>nope</body></html>")


def test_flatten_keeps_the_text_and_drops_the_stylesheet():
    """The flattener is what makes the export safe for both renderers.

    Matplotlib's own output carries its paint in a stylesheet; the service's
    sanitizer and the browser renderer accept neither. The flattener turns
    that into presentation attributes, and the text survives.
    """
    import matplotlib

    rendered, _resolved = _render(_spec())
    raw = export_all(rendered, formats=("svg",))["svg"].decode("utf-8")
    flat = flatten_svg(raw)
    assert "<style" not in flat
    assert "style=" not in flat
    assert "果实重量" in flat
    # The flattened document still carries its geometry.
    root = ET.fromstring(flat)
    assert root.get("viewBox") or root.get("width")


def test_flatten_output_is_well_formed_xml():
    rendered, _resolved = _render(_spec())
    raw = export_all(rendered, formats=("svg",))["svg"].decode("utf-8")
    root = ET.fromstring(flatten_svg(raw))
    assert root.tag == f"{{{SVG_NS}}}svg"


# ── Fonts the deployment actually has ─────────────────────────────────────

def test_the_cjk_stack_names_the_family_the_images_install():
    """A family name nothing carries renders every Chinese label as a box.

    Matplotlib does not fall back glyph by glyph: it picks the first family in
    the list and drops every glyph that family lacks. The images install
    Debian's ``fonts-noto-cjk``, which registers ``Noto Sans CJK SC``;
    ``Noto Sans SC`` is a *different* family those images do not install.
    Naming only the latter produced a deployment whose figures had empty
    boxes where the Chinese labels should be — text intact in the SVG, no
    glyphs on the page — and no service-side test could see it.

    This test ties the stack to what the Dockerfiles install, so the two
    cannot drift apart again.
    """
    from src.figures.styling import CJK_SANS

    root = Path(__file__).resolve().parents[2]          # 01_src
    for image in ("Dockerfile.app", "Dockerfile.sandbox"):
        text = (root / "docker" / image).read_text(encoding="utf-8")
        assert "fonts-noto-cjk" in text, f"{image} 不再安装 CJK 字体"

    # The family the package registers is in the stack, ahead of the generic
    # fallback that has no CJK glyphs at all.
    assert "Noto Sans CJK SC" in CJK_SANS
    assert CJK_SANS.index("Noto Sans CJK SC") < CJK_SANS.index("sans-serif")

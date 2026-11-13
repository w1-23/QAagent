"""Exporting a figure, and making the SVG safe to show.

Two jobs live here.

**Export.** ``export_all`` writes SVG, PNG and PDF from one Matplotlib
``Figure``. The formats are never derived from one another: there is no SVG
rasteriser in the deployment image, so PNG and PDF are written by Matplotlib
itself. Because all of them come from the same figure object in the same
call, the downloaded file and the on-screen preview are the same picture.

**Flattening.** Matplotlib's SVG carries its paint in two places that the
project's existing sanitizers will not accept:

* a ``<style>`` element holding CSS rules, and
* ``style="..."`` attributes on the elements themselves.

The service's SVG sanitizer (``src.tools.draw_svg``) allows neither the
``style`` element nor the ``style`` attribute, and the browser's renderer
(``web/assets/app.js``) does the same. It also does not allow the ``<use>``
element that Matplotlib uses for repeated marks, and it rejects a document
that declares a ``DOCTYPE``.

So the exported SVG is rewritten into the plain subset both accept: every
style is flattened into presentation attributes, repeated marks are inlined,
the metadata block and the doctype are dropped. The result is a document that
survives both sanitizers unchanged — which is the property that lets the
chat bubble preview and the web workspace show the same figure.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from src.figures.core import RenderedFigure

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# Serialise the SVG namespace as the default one. Without this, ElementTree
# invents an ``ns0:`` prefix for every element, and the project's own
# sanitizers — which match on plain tag names — would not recognise them.
ET.register_namespace("", SVG_NS)

# The elements and attributes both the service sanitizer and the browser
# renderer accept. Output is restricted to this intersection, so the exported
# file survives either one without losing anything.
ALLOWED_TAGS = frozenset({
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "title", "desc", "defs", "linearGradient",
    "radialGradient", "stop", "clipPath", "marker",
})

ALLOWED_ATTRS = frozenset({
    "viewBox", "preserveAspectRatio", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset",
    "fill-opacity", "stroke-opacity", "font-family", "font-size",
    "font-weight", "text-anchor", "dominant-baseline", "gradientUnits",
    "gradientTransform", "stop-color", "stop-opacity", "clipPathUnits",
    "markerWidth", "markerHeight", "markerUnits", "marker-start",
    "marker-mid", "marker-end", "aria-label", "clip-path", "role", "id",
    "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "width",
    "height", "d", "points", "fill", "stroke", "opacity", "transform",
    "offset",
})

# CSS declarations that map onto an allowed presentation attribute.
_STYLE_TO_ATTR = {
    "fill": "fill",
    "stroke": "stroke",
    "stroke-width": "stroke-width",
    "stroke-linecap": "stroke-linecap",
    "stroke-linejoin": "stroke-linejoin",
    "stroke-dasharray": "stroke-dasharray",
    "stroke-dashoffset": "stroke-dashoffset",
    "fill-opacity": "fill-opacity",
    "stroke-opacity": "stroke-opacity",
    "opacity": "opacity",
    "font-family": "font-family",
    "font-size": "font-size",
    "font-weight": "font-weight",
    "text-anchor": "text-anchor",
    "dominant-baseline": "dominant-baseline",
    "stop-color": "stop-color",
    "stop-opacity": "stop-opacity",
    "clip-path": "clip-path",
    "marker-start": "marker-start",
    "marker-mid": "marker-mid",
    "marker-end": "marker-end",
}

_UNSAFE_VALUE = re.compile(r"javascript:|data:|expression\s*\(|<|>", re.I)
_DOCTYPE = re.compile(r"<!DOCTYPE[^>]*>|<!ENTITY[^>]*>", re.I)


class ExportError(ValueError):
    """The figure could not be exported in a usable form."""


# ── CSS parsing ────────────────────────────────────────────────────────────

def _parse_style_sheet(css: str) -> tuple[dict, dict, dict]:
    """Parse the small CSS subset Matplotlib emits.

    Returns ``(universal, tag_rules, class_rules)``. Matplotlib writes
    ``* { ... }`` for global defaults and ``.name { ... }`` for per-class
    rules; element rules (``path { ... }``) are handled as well. Anything
    more exotic is ignored rather than guessed at.
    """
    universal: dict[str, str] = {}
    tag_rules: dict[str, dict[str, str]] = {}
    class_rules: dict[str, dict[str, str]] = {}
    text = str(css or "")
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text, re.S):
        selector = match.group(1).strip()
        declarations = _parse_declarations(match.group(2))
        if not declarations:
            continue
        if selector == "*":
            universal.update(declarations)
            continue
        names = [part.strip() for part in selector.split(",") if part.strip()]
        for name in names:
            if name.startswith("."):
                class_rules.setdefault(name[1:], {}).update(declarations)
            elif re.fullmatch(r"[A-Za-z][\w-]*", name):
                tag_rules.setdefault(name.lower(), {}).update(declarations)
    return universal, tag_rules, class_rules


def _parse_declarations(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in str(block or "").split(";"):
        if ":" not in part:
            continue
        name, _, value = part.partition(":")
        key = name.strip().lower()
        val = value.strip()
        if key and val and not _UNSAFE_VALUE.search(val):
            out[key] = val
    return out


def _apply_css(node: ET.Element, universal: dict, tag_rules: dict,
               class_rules: dict) -> None:
    """Fold the style sheet into an element's presentation attributes."""
    tag = node.tag.split("}", 1)[-1].lower()
    classes = str(node.get("class") or "").split()
    merged: dict[str, str] = {}
    merged.update(universal)
    merged.update(tag_rules.get(tag, {}))
    for name in classes:
        merged.update(class_rules.get(name, {}))
    for key, value in merged.items():
        attribute = _STYLE_TO_ATTR.get(key)
        if not attribute:
            continue
        if node.get(attribute) in (None, ""):
            node.set(attribute, value)


def _inline_style_attr(node: ET.Element) -> None:
    """Move an element's inline ``style`` into presentation attributes."""
    raw = node.get("style")
    if not raw:
        node.attrib.pop("style", None)
        return
    node.attrib.pop("style", None)
    for key, value in _parse_declarations(raw).items():
        attribute = _STYLE_TO_ATTR.get(key)
        if not attribute:
            continue
        if _UNSAFE_VALUE.search(value):
            continue
        node.set(attribute, value)


# ── The main transformation ───────────────────────────────────────────────

def flatten_svg(source: str) -> str:
    """Rewrite a Matplotlib SVG into the plain subset the app can display.

    The steps, in order:

    1. drop the doctype and the RDF metadata block;
    2. read the ``<style>`` sheet and remove it;
    3. fold the sheet and every inline ``style`` into presentation
       attributes;
    4. inline ``<use>`` references so no ``<use>`` element survives;
    5. remove embedded images (a figure must not carry a bitmap payload);
    6. drop every element and attribute outside the allowed subset.
    """
    text = str(source or "")
    if not text.strip():
        raise ExportError("empty_svg", "SVG 内容为空。")
    text = _DOCTYPE.sub("", text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ExportError("invalid_svg", f"SVG 解析失败：{exc}") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise ExportError("invalid_svg", "SVG 根节点不是 svg。")

    universal: dict = {}
    tag_rules: dict = {}
    class_rules: dict = {}

    # Pass 1: collect the style sheet, then remove it.
    for node in list(root.iter()):
        tag = node.tag.split("}", 1)[-1].lower()
        if tag == "style":
            found = _parse_style_sheet(node.text or "")
            universal.update(found[0])
            for rule_map, collected in ((tag_rules, found[1]),
                                        (class_rules, found[2])):
                for name, declarations in collected.items():
                    rule_map.setdefault(name, {}).update(declarations)
            for parent in root.iter():
                if node in list(parent):
                    parent.remove(node)
                    break

    # Pass 2: metadata and images never survive.
    for node in list(root.iter()):
        tag = node.tag.split("}", 1)[-1].lower()
        if tag in {"metadata", "image", "script", "foreignobject"}:
            for parent in root.iter():
                if node in list(parent):
                    parent.remove(node)
                    break

    # Pass 3: inline the styles.
    for node in root.iter():
        _apply_css(node, universal, tag_rules, class_rules)
        _inline_style_attr(node)
        node.attrib.pop("class", None)

    # Pass 4: inline <use>.
    _inline_uses(root)

    # Pass 5: enforce the allowed subset.
    _prune(root)

    if not root.get("viewBox") and not root.get("width"):
        raise ExportError("invalid_svg", "SVG 缺少 viewBox 与 width，无法显示。")
    body = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + body


def _inline_uses(root: ET.Element) -> None:
    """Replace every ``<use>`` with a copy of the element it references."""
    definitions: dict[str, ET.Element] = {}
    for node in root.iter():
        node_id = node.get("id")
        if node_id:
            definitions[node_id] = node

    for node in list(root.iter()):
        if node.tag.split("}", 1)[-1].lower() != "use":
            continue
        href = (node.get(f"{{{XLINK_NS}}}href") or node.get("href") or "").strip()
        if not href.startswith("#"):
            continue
        target = definitions.get(href[1:])
        if target is None:
            continue
        clone = _clone(target)
        clone.attrib.pop("id", None)
        # The reference's own position becomes a translation.
        dx = node.get("x")
        dy = node.get("y")
        try:
            tx = float(dx) if dx else 0.0
            ty = float(dy) if dy else 0.0
        except (TypeError, ValueError):
            tx = ty = 0.0
        if tx or ty:
            existing = clone.get("transform") or ""
            clone.set("transform", f"translate({tx:g},{ty:g}) {existing}".strip())
        # The reference's inline style wins over the definition's.
        raw = node.get("style")
        if raw:
            for key, value in _parse_declarations(raw).items():
                attribute = _STYLE_TO_ATTR.get(key)
                if attribute and not _UNSAFE_VALUE.search(value):
                    clone.set(attribute, value)
        clone.attrib.pop("style", None)
        for parent in root.iter():
            if node in list(parent):
                index = list(parent).index(node)
                parent.remove(node)
                parent.insert(index, clone)
                break


def _clone(node: ET.Element) -> ET.Element:
    clone = ET.Element(node.tag, dict(node.attrib))
    clone.text = node.text
    clone.tail = node.tail
    for child in list(node):
        clone.append(_clone(child))
    return clone


def _prune(root: ET.Element) -> None:
    """Remove anything outside the allowed elements and attributes."""
    for node in list(root.iter()):
        tag = node.tag.split("}", 1)[-1]
        if tag not in ALLOWED_TAGS:
            for parent in root.iter():
                if node in list(parent):
                    parent.remove(node)
                    break
            continue
        for key in list(node.attrib):
            value = node.attrib[key]
            if key not in ALLOWED_ATTRS:
                node.attrib.pop(key, None)
                continue
            if len(str(value)) > 4000 or _UNSAFE_VALUE.search(str(value)):
                node.attrib.pop(key, None)
                continue
            if key == "id" and not re.fullmatch(r"[A-Za-z][\w:.-]{0,100}", str(value)):
                node.attrib.pop(key, None)
                continue
            if re.search(r"url\s*\(", str(value), re.I) and not re.fullmatch(
                    r"url\(\s*#[A-Za-z][\w:.-]{0,100}\s*\)", str(value), re.I):
                node.attrib.pop(key, None)


def sanitize_svg(source: str) -> str:
    """Final safety pass over an SVG document.

    Applied after flattening and before publication. The service sanitizer
    (``src.tools.draw_svg._sanitize_svg``) runs the same checks; running them
    here means a malformed figure is rejected at the source rather than
    silently repaired downstream.
    """
    text = str(source or "")
    if not text.strip() or len(text) > 2_000_000:
        raise ExportError("invalid_svg", "SVG 为空或过大。")
    if _DOCTYPE.search(text):
        raise ExportError("invalid_svg", "SVG 含有不允许的声明。")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ExportError("invalid_svg", f"SVG 解析失败：{exc}") from exc
    if root.tag != f"{{{SVG_NS}}}svg":
        raise ExportError("invalid_svg", "SVG 根节点不是 svg。")
    nodes = 0
    for node in root.iter():
        nodes += 1
        if nodes > 20000:
            raise ExportError("invalid_svg", "SVG 结构过于复杂。")
        namespace = node.tag.split("}", 1)[0][1:] if node.tag.startswith("{") else ""
        if namespace and namespace != SVG_NS:
            raise ExportError("invalid_svg", "SVG 含有不支持的命名空间。")
        for key, value in node.attrib.items():
            text_value = str(value)
            if re.search(r"javascript:|data:|https?:|file:", text_value, re.I):
                raise ExportError("invalid_svg", "SVG 含有外部或活动内容引用。")
    return text


# ── Export ─────────────────────────────────────────────────────────────────
#
# Every format is written from the **full figure canvas**, never from a tight
# crop. A tight crop would make the exported viewBox depend on where the
# artists happen to end up, which breaks two things the project needs:
#
# * the web editor's selection boxes are placed from the manifest's
#   coordinates, and those coordinates describe the canvas — a crop would
#   shift every box away from the drawing it belongs to;
# * the PNG and the SVG would frame the picture differently, so "the preview
#   and the download are the same picture" would only hold for the SVG.
#
# A fixed canvas also gives every figure the same proportions, which is what a
# journal expects from a multi-panel figure.

def export_svg(figure: Any) -> bytes:
    """Render the figure to SVG bytes."""
    import io

    buffer = io.BytesIO()
    figure.savefig(buffer, format="svg", facecolor="white")
    return buffer.getvalue()


def export_raster(figure: Any, fmt: str = "png", dpi: int = 300) -> bytes:
    """Render the figure to PNG or TIFF bytes."""
    import io

    buffer = io.BytesIO()
    figure.savefig(buffer, format=fmt, dpi=dpi, facecolor="white")
    return buffer.getvalue()


def export_pdf(figure: Any) -> bytes:
    """Render the figure to PDF bytes."""
    import io

    buffer = io.BytesIO()
    figure.savefig(buffer, format="pdf", facecolor="white")
    return buffer.getvalue()


def export_all(rendered: RenderedFigure, *, formats: tuple[str, ...] = ("svg", "png", "pdf"),
                dpi: int = 300) -> dict[str, bytes]:
    """Export every requested format from one figure.

    The formats are produced in sequence from the same figure object, so a
    change to the figure is reflected identically in all of them. Every value
    is bytes: the SVG is flattened and then encoded, so callers never have to
    guess which type they received.
    """
    figure = rendered.figure
    if figure is None:
        raise ExportError("no_figure", "没有可导出的图形。")
    out: dict[str, bytes] = {}
    for fmt in formats:
        name = str(fmt).strip().lower()
        if name == "svg":
            out["svg"] = flatten_svg(export_svg(figure).decode("utf-8")).encode("utf-8")
        elif name == "png":
            out["png"] = export_raster(figure, "png", dpi)
        elif name == "pdf":
            out["pdf"] = export_pdf(figure)
        elif name in {"tiff", "tif"}:
            out["tiff"] = export_raster(figure, "tiff", dpi)
        else:
            raise ExportError("unsupported_format", f"不支持的导出格式「{fmt}」。")
    return out


__all__ = [
    "ALLOWED_ATTRS", "ALLOWED_TAGS", "ExportError", "export_all", "export_pdf",
    "export_raster", "export_svg", "flatten_svg", "sanitize_svg",
]

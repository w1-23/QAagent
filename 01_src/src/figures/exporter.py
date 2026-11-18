"""Host export adapter sharing the package SVG and raster implementation."""
from __future__ import annotations
from citrus_figures.core import RenderedFigure
from citrus_figures.exporter import (
    SVG_NS,
    XLINK_NS,
    ALLOWED_TAGS,
    ALLOWED_ATTRS,
    _STYLE_TO_ATTR,
    _UNSAFE_VALUE,
    _DOCTYPE,
    ExportError,
    _parse_style_sheet,
    _font_shorthand,
    _parse_declarations,
    _apply_css,
    _inline_style_attr,
    flatten_svg,
    _inline_uses,
    _clone,
    _prune,
    sanitize_svg,
    export_svg,
    export_raster,
    export_pdf,
    export_all as _package_export_all,
    _export_all,
)


def export_all(rendered: RenderedFigure, *, formats: tuple[str, ...] = ("svg", "png", "pdf"),
               dpi: int = 300) -> dict[str, bytes]:
    from src.figures.runtime import render_context
    with render_context({**(rendered.rc_params or {}), "svg.fonttype": "none"}):
        return _package_export_all(rendered, formats=formats, dpi=dpi)


__all__ = [
    "ALLOWED_ATTRS", "ALLOWED_TAGS", "ExportError", "export_all", "export_pdf",
    "export_raster", "export_svg", "flatten_svg", "sanitize_svg",
]

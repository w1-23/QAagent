"""Host manifest adapter preserving legacy scientific QA and object IDs."""
from __future__ import annotations
from typing import Any
from citrus_figures.core import RenderedFigure
from citrus_figures.manifest import (
    MANIFEST_VERSION,
    _TEXT_PROPS,
    FONT_WEIGHTS,
    FONT_STYLES,
    HORIZONTAL_ALIGNMENTS,
    LEGEND_LOCS,
    MARKER_STYLE_PROPS,
    LINE_STYLE_PROPS,
    MARKERS,
    LINESTYLES,
    EDITABLE_BY_ROLE,
    ALL_EDITABLE_PROPS,
    TEXT_ROLES,
    available_font_families,
    _BBOX_KEYS,
    _is_plain,
    _artist_bbox,
    _anchor,
    _label_of,
    _series_color,
    _legend_color,
    assign_locators,
    _register,
    series_source,
    _marker_metadata,
    _data_artists,
    _grid_members,
    _error_container,
    _style_metadata,
    _discover_semantics,
    build_manifest as _package_build_manifest,
    _build_manifest,
    scientific_qa_receipt,
    _sort_key,
    _series_width,
    _font_size,
    find_by_locator,
)


def build_manifest(rendered: RenderedFigure, *, source: str = "spec",
                   figure_size_px: tuple[float, float] | None = None,
                   include_qa: bool = True) -> dict[str, Any]:
    from src.figures.runtime import render_context
    with render_context(rendered.rc_params):
        return _package_build_manifest(rendered, source=source,
                                       figure_size_px=figure_size_px, include_qa=include_qa)


__all__ = [
    "ALL_EDITABLE_PROPS", "EDITABLE_BY_ROLE", "TEXT_ROLES",
    "assign_locators", "build_manifest", "find_by_locator",
]

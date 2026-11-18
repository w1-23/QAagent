"""Legacy figure imports backed by the independent citrus_figures kernel.

Explicit imports retain the historical helpers without aliasing host modules.
"""
from citrus_figures.overrides import (
    _COLOR_PROPS,
    _OFFSET_LIMIT,
    _MIN_FONTSIZE,
    _MAX_FONTSIZE,
    _MIN_LINEWIDTH,
    _MAX_LINEWIDTH,
    OverrideError,
    _normalise_color,
    _normalise_offset,
    _normalise_number,
    _normalise_fontfamily,
    validate_overrides,
    _members,
    _set_colour,
    _set_colour_one,
    _set_linewidth,
    _set_linewidth_one,
    _offset_artist,
    apply_overrides,
    _set_error_style,
    _set_plain_text,
    _set_marker_style,
    _legend_rows,
    _set_legend_columns,
    _capture_legend_links,
    _sync_series_legends,
    _set_fontsize,
    _role_of,
    merge_overrides,
)

from citrus_figures.core import RenderedFigure
from citrus_figures.manifest import (
    ALL_EDITABLE_PROPS, EDITABLE_BY_ROLE, TEXT_ROLES, FONT_WEIGHTS,
    available_font_families, find_by_locator, LINE_STYLE_PROPS, MARKERS, LINESTYLES,
    FONT_STYLES, HORIZONTAL_ALIGNMENTS, LEGEND_LOCS, series_source,
)


__all__ = [
    "ALL_EDITABLE_PROPS", "EDITABLE_BY_ROLE", "OverrideError",
    "apply_overrides", "merge_overrides", "validate_overrides",
]

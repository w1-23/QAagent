"""Legacy figure imports backed by the independent citrus_figures kernel.

Explicit imports retain the historical helpers without aliasing host modules.
"""
from citrus_figures.spec import (
    CHART_TYPES,
    REF_KINDS,
    ERROR_KINDS,
    _MAX_INLINE_ROWS,
    _MAX_INLINE_COLS,
    _MAX_TITLE_CHARS,
    _MAX_LABEL_CHARS,
    _MAX_ANNOTATIONS,
    _FIGURE_SIZE_MIN,
    _FIGURE_SIZE_MAX,
    _FONT_SIZE_MIN,
    _FONT_SIZE_MAX,
    _DPI_MIN,
    _DPI_MAX,
    SpecError,
    ColumnRef,
    StyleSpec,
    FigureSpec,
    Series,
    ResolvedFigure,
    _text,
    _number,
    _parse_ref,
    _parse_style,
    _parse_annotations,
    _parse_layout,
    parse_qa_profile,
    parse_spec,
)


__all__ = [
    "CHART_TYPES", "ERROR_KINDS", "REF_KINDS", "ColumnRef", "FigureSpec",
    "ResolvedFigure", "Series", "SpecError", "StyleSpec", "parse_spec",
]

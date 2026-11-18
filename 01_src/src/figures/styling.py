"""Legacy figure imports backed by the independent citrus_figures kernel.

Explicit imports retain the historical helpers without aliasing host modules.
"""
from citrus_figures.styling import (
    FONT_SANS,
    CJK_SANS,
    _CJK_RANGES,
    PALETTE,
    DEFAULT_COLORS,
    SEQUENTIAL_LIGHT,
    PALETTES,
    palette_colors,
    needs_cjk,
    _installed_fonts,
    _INSTALLED_FONTS,
    _resolve_stack,
    font_stack,
    base_rcparams,
    apply_base_rcparams,
    _text_glyphs,
    figure_glyph_coverage,
    prepare_figure_fonts,
    installed_fonts,
)


__all__ = [
    "CJK_SANS", "DEFAULT_COLORS", "FONT_SANS", "PALETTE", "PALETTES",
    "SEQUENTIAL_LIGHT", "apply_base_rcparams", "base_rcparams", "font_stack",
    "installed_fonts", "needs_cjk", "palette_colors",
]

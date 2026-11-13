"""Figure styling: palettes and the rcParams the engine always applies.

The values here follow the publication conventions the project adopts for
scientific figures: a sans-serif stack that exists on the target machine, a
single restrained palette, only the left and bottom spines, no legend frame,
and — crucially — ``svg.fonttype='none'`` so that text in the exported SVG
stays real text instead of being converted to glyph outlines.

``svg.fonttype='none'`` is not a cosmetic choice. It is what makes the
exported SVG editable in a vector editor and readable by the semantic
manifest, and it is why the exporter can work with ``<text>`` nodes.

Nothing in this module imports the service. The palette values are plain
constants taken from the project's own figure specification.
"""
from __future__ import annotations

# Publication-safe font stack. "Arial" first because it is the common target
# for journal submission; DejaVu Sans is the guaranteed fallback in the
# deployment image; the generic family is the final backstop.
FONT_SANS = ["Arial", "Helvetica", "DejaVu Sans", "Liberation Sans", "sans-serif"]

# Matplotlib does not fall back glyph by glyph: it picks the first font in
# the list and reports every glyph that font lacks. A figure with Chinese
# labels rendered through the Latin stack therefore loses its characters even
# though the file still contains the text. So the stack is chosen from the
# content of the figure, not fixed once for the process.
#
# These are the CJK-capable families the project ships with, in preference
# order. "Microsoft YaHei" is present on Windows and in the Windows
# deployment; "Noto Sans SC" is present on Linux hosts and in the sandbox
# image.
CJK_SANS = ["Microsoft YaHei", "Noto Sans SC", "SimHei", "Source Han Sans SC",
            "WenQuanYi Zen Hei", "sans-serif"]

# Ranges that require a CJK-capable face.
_CJK_RANGES = (
    (0x2E80, 0x9FFF),      # CJK radicals through CJK unified ideographs
    (0x3000, 0x303F),      # CJK punctuation
    (0xFF00, 0xFFEF),      # fullwidth forms
    (0xAC00, 0xD7AF),      # Hangul syllables
    (0x3040, 0x30FF),      # kana
)

# The base palette. One neutral family, one signal family, one accent family.
# Green and red are reserved for directional meaning; the first three entries
# are the default categorical order.
PALETTE: dict[str, str] = {
    "blue_main": "#0F4D92",
    "blue_secondary": "#3775BA",
    "green_1": "#DDF3DE",
    "green_2": "#AADCA9",
    "green_3": "#8BCF8B",
    "red_1": "#F6CFCB",
    "red_2": "#E9A6A1",
    "red_strong": "#B64342",
    "neutral_light": "#CFCECE",
    "neutral_mid": "#767676",
    "neutral_dark": "#4D4D4D",
    "neutral_black": "#272727",
    "gold": "#FFD700",
    "teal": "#42949E",
    "violet": "#9A4D8E",
    "magenta": "#EA84DD",
}

DEFAULT_COLORS: tuple[str, ...] = (
    PALETTE["blue_main"],
    PALETTE["green_3"],
    PALETTE["red_strong"],
    PALETTE["teal"],
    PALETTE["violet"],
    PALETTE["neutral_light"],
)

# A light sequential ramp, for ordered categories. It is never used as a
# categorical palette: a sequential scale must not double as a set of
# unrelated nominal colours.
SEQUENTIAL_LIGHT: tuple[str, ...] = (
    "#EAF1F8", "#C9DCEC", "#A3C4DE", "#7BA9CE", "#4F86B4",
    PALETTE["blue_secondary"], PALETTE["blue_main"], "#0A3566",
)

# Named palettes a request may ask for by name.
PALETTES: dict[str, tuple[str, ...]] = {
    "nature": DEFAULT_COLORS,
    "sequential": SEQUENTIAL_LIGHT,
    "muted": (
        PALETTE["blue_main"], PALETTE["teal"], PALETTE["violet"],
        PALETTE["red_strong"], PALETTE["neutral_mid"], PALETTE["gold"],
    ),
}


def palette_colors(name: str) -> tuple[str, ...]:
    """Return the colour tuple for a named palette, falling back to the default."""
    key = str(name or "nature").strip().lower()
    return PALETTES.get(key, DEFAULT_COLORS)


def needs_cjk(text: str) -> bool:
    """Whether ``text`` contains characters a Latin-only face cannot draw."""
    for character in str(text or ""):
        code = ord(character)
        for low, high in _CJK_RANGES:
            if low <= code <= high:
                return True
    return False


def font_stack(*texts: str) -> list[str]:
    """The font stack for a figure's text.

    A figure that contains any CJK character is rendered with a CJK-capable
    face first, so its labels are actually drawn rather than silently
    missing. Latin-only figures keep the publication stack.
    """
    combined = " ".join(str(item or "") for item in texts)
    if needs_cjk(combined):
        return list(CJK_SANS)
    return list(FONT_SANS)


def base_rcparams(font_size: float = 7.0,
                  font_stack_values: list[str] | None = None) -> dict[str, object]:
    """The rcParams every figure in this engine is rendered with.

    Applied through ``matplotlib.rcParams.update`` before any figure is
    created, so a script that sets its own rcParams afterwards still starts
    from a compliant baseline.
    """
    return {
        "font.family": "sans-serif",
        "font.sans-serif": list(font_stack_values or FONT_SANS),
        "font.size": float(font_size),
        "svg.fonttype": "none",   # keep text as <text>, not glyph outlines
        "pdf.fonttype": 42,       # embed TrueType, keep text editable
        "ps.fonttype": 42,
        "axes.spines.right": False,
        "axes.spines.top": False,
        "axes.linewidth": 0.8,
        "axes.labelsize": float(font_size),
        "axes.titlesize": float(font_size) + 0.5,
        "xtick.labelsize": float(font_size),
        "ytick.labelsize": float(font_size),
        "legend.fontsize": float(font_size),
        "legend.frameon": False,
        "figure.dpi": 100,
        "savefig.dpi": 300,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "axes.unicode_minus": False,
    }


def apply_base_rcparams(font_size: float = 7.0,
                        font_stack_values: list[str] | None = None) -> None:
    """Install the base rcParams into the live matplotlib configuration."""
    import matplotlib

    matplotlib.rcParams.update(base_rcparams(font_size, font_stack_values))


__all__ = [
    "CJK_SANS", "DEFAULT_COLORS", "FONT_SANS", "PALETTE", "PALETTES",
    "SEQUENTIAL_LIGHT", "apply_base_rcparams", "base_rcparams", "font_stack",
    "needs_cjk", "palette_colors",
]

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
# order. "Microsoft YaHei" is present on Windows; the Noto entries are what a
# Linux host and the container image actually install.
#
# The exact family name matters, and it is easy to get wrong: Debian's
# ``fonts-noto-cjk`` package registers ``Noto Sans CJK SC`` (the CJK-aware
# build), while ``Noto Sans SC`` is a *different*, separately packaged family
# that the image does not install. Listing only the latter produced a figure
# whose Chinese labels rendered as empty boxes in the deployment — every
# glyph missing, the text still there. Both names are listed because a host
# may have either.
CJK_SANS = ["Microsoft YaHei", "Noto Sans CJK SC", "Noto Sans SC",
            "Noto Sans CJK JP", "SimHei", "Source Han Sans SC",
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


def _installed_fonts() -> frozenset[str]:
    """The font families Matplotlib can actually see on this machine.

    A name in a stack is a wish, not a fact: Matplotlib picks the first
    family it finds and reports every glyph the rest lack. So the list of
    usable families is asked of Matplotlib itself, and the answer is cached
    for the life of the process.
    """
    global _INSTALLED_FONTS
    if _INSTALLED_FONTS is not None:
        return _INSTALLED_FONTS
    names: frozenset[str] = frozenset()
    try:
        from matplotlib import font_manager
        for entry in font_manager.fontManager.ttflist:
            family = getattr(entry, "name", "") or ""
            if family:
                names = names | {family}
    except Exception:
        names = frozenset()
    _INSTALLED_FONTS = names
    return names


_INSTALLED_FONTS: frozenset[str] | None = None


def _resolve_stack(preferred: list[str], installed: frozenset[str]) -> list[str]:
    """Keep the families that exist; drop the ones that do not.

    The generic ``sans-serif`` entry always stays, because it is Matplotlib's
    own backstop: if every named family is missing, Matplotlib still draws
    the text with whatever it considers sans-serif. It is not a real family
    and so never appears in the installed list, but it is not "missing" in
    the sense that matters.
    """
    resolved = [name for name in preferred
                if name == "sans-serif" or name in installed]
    if not resolved:
        return list(preferred)
    return resolved


def font_stack(*texts: str) -> list[str]:
    """The font stack for a figure's text.

    A figure that contains any CJK character is rendered with a CJK-capable
    face first, so its labels are actually drawn rather than silently
    missing. Latin-only figures keep the publication stack.

    Names that the machine does not have are dropped rather than left in
    place: a missing family in the list is harmless in itself, but it pushes
    Matplotlib onto its generic fallback, which may not cover the glyphs
    either. Dropping them keeps the choice honest about what will be drawn.
    """
    combined = " ".join(str(item or "") for item in texts)
    preferred = list(CJK_SANS) if needs_cjk(combined) else list(FONT_SANS)
    installed = _installed_fonts()
    if not installed:
        return preferred
    resolved = _resolve_stack(preferred, installed)
    if len(resolved) == 1 and resolved[0] == "sans-serif":
        # Nothing named survived; fall back to whatever Matplotlib ships.
        return _resolve_stack(["DejaVu Sans", "sans-serif"], installed)
    return resolved


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
        "grid.linewidth": 0.8,
        "xtick.major.width": 0.8,
        "xtick.minor.width": 0.5,
        "ytick.major.width": 0.8,
        "ytick.minor.width": 0.5,
        "axes.labelsize": float(font_size),
        "axes.titlesize": float(font_size) + 0.5,
        "xtick.labelsize": float(font_size),
        "ytick.labelsize": float(font_size),
        "xtick.top": False,
        "xtick.bottom": True,
        "xtick.labeltop": False,
        "xtick.labelbottom": True,
        "ytick.right": False,
        "ytick.left": True,
        "ytick.labelright": False,
        "ytick.labelleft": True,
        "legend.fontsize": float(font_size),
        "legend.frameon": False,
        "figure.dpi": 100,
        "savefig.dpi": 300,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "axes.unicode_minus": False,
        # Matplotlib salts its internal element ids with a random number, so
        # two renders of the same figure differ in the bytes even when the
        # picture is identical. A fixed salt makes the export reproducible:
        # the same data and the same edits give the same file, which is what
        # lets a user check that a re-export did not change the figure.
        "svg.hashsalt": "citrusqa-figure",
    }


def apply_base_rcparams(font_size: float = 7.0,
                        font_stack_values: list[str] | None = None) -> None:
    """Install the base rcParams into the live matplotlib configuration."""
    import matplotlib

    matplotlib.rcParams.update(base_rcparams(font_size, font_stack_values))


def _text_glyphs(text):
    """Query the exact FreeType fallback map used by Matplotlib's renderer."""
    from matplotlib import font_manager
    value = text.get_text()
    props = text.get_fontproperties()
    paths = font_manager.fontManager._find_fonts_by_props(props)
    font = font_manager.get_font(paths)
    if hasattr(font, "_get_fontmap"):
        # Matplotlib <= 3.10 exposes the renderer's per-character fallback map.
        mapping = font._get_fontmap(value)
        return sorted({ord(char) for char in value if not char.isspace()
                       and not mapping[char].get_char_index(ord(char))})

    # Matplotlib 3.11 replaced the map with shaped glyph records. Each record
    # names the actual selected font and its source cluster (possibly several
    # characters, e.g. an ffi ligature), rather than one Unicode character.
    from pathlib import Path
    from matplotlib import _text_helpers
    items = _text_helpers.layout(
        value, font, features=text.get_fontfeatures(), language=text.get_language())
    missing, seen = set(), set()
    for item in items:
        face = item.ft_object
        # 3.11 automatically adds Last Resort: its block-symbol placeholders
        # have nonzero glyph indices but do not depict the requested text.
        placeholder = (Path(face.fname).name == "LastResortHE-Regular.ttf"
                       or face.postscript_name == "LastResortHE-Regular")
        for char in item.char:
            if char.isspace():
                continue
            seen.add(char)
            if placeholder or not item.glyph_index or not face.get_char_index(ord(char)):
                missing.add(ord(char))
    if {char for char in value if not char.isspace()} - seen:
        raise ValueError("Glyph layout did not account for every source character")
    return sorted(missing)


def figure_glyph_coverage(figure, *, repair=False) -> dict:
    """Check actual rendered Text, including labels loaded from data files.

    Initial rendering may replace a missing-glyph face with an installed face
    that covers the entire label. Manual edits are measured without substitution.
    This is a first-party FreeType check, independent of the Nature PDF audits.
    Math/TeX shaping is explicitly outside this character-map check.
    """
    from matplotlib.text import Text
    from matplotlib import cbook

    missing, unsupported, fallbacks = [], [], []
    checked = 0
    for text in figure.findobj(match=Text):
        value = text.get_text()
        if (not value or not text.get_visible()
                or (text.axes is not None and not text.axes.get_visible())):
            continue
        if text.get_usetex() or cbook.is_math_text(value):
            unsupported.append(value[:120])
            continue
        try:
            absent = _text_glyphs(text)
            if absent and repair:
                original = text.get_fontproperties().copy()
                # Use installed family names, then re-resolve style/weight.
                # A family name alone does not establish glyph coverage.
                for family in font_stack(value) + ["DejaVu Sans"]:
                    if family not in _installed_fonts():
                        continue
                    props = original.copy()
                    props.set_file(None)
                    props.set_family([family])
                    text.set_fontproperties(props)
                    if not _text_glyphs(text):
                        fallbacks.append({"text": value[:120], "family": family})
                        absent = []
                        break
                if absent:
                    text.set_fontproperties(original)
            checked += 1
            if absent:
                missing.append({"text": value[:120],
                                "codepoints": [f"U+{code:04X}" for code in absent[:64]],
                                "characters": "".join(chr(code) for code in absent[:64])})
        except (AttributeError, KeyError, OSError, RuntimeError, ValueError):
            unsupported.append(value[:120])
    return {"status": "fail" if missing else "unsupported" if unsupported else "pass",
            "checked_text_count": checked, "missing": missing[:64],
            "unsupported_text": unsupported[:64], "fallbacks": fallbacks[:64],
            "scope": "First-party actual Matplotlib/FreeType character map; not Nature certification or math/TeX shaping review.",
            "repair": "Install fonts-noto-cjk/fontconfig in the Linux worker image or select an installed covering font; keep original labels and rerender." if missing else ""}


def prepare_figure_fonts(figure) -> dict:
    """Materialize ticks, repair uncovered labels, then redraw before layout QA."""
    figure.canvas.draw()
    receipt = figure_glyph_coverage(figure, repair=True)
    figure.canvas.draw()
    return receipt


def installed_fonts() -> frozenset[str]:
    """The font families available on this machine."""
    return _installed_fonts()


__all__ = [
    "CJK_SANS", "DEFAULT_COLORS", "FONT_SANS", "PALETTE", "PALETTES",
    "SEQUENTIAL_LIGHT", "apply_base_rcparams", "base_rcparams", "font_stack",
    "installed_fonts", "needs_cjk", "palette_colors",
]

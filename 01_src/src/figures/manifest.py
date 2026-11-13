"""The semantic object inventory: ``objects.json``.

A manifest describes every editable object in a figure: what it is, what it
may be changed to, and where it sits on the canvas. It is the contract
between the renderer, the web editor and the override applier.

Why a manifest at all
---------------------
An SVG is a tree of shapes. That tree knows nothing about which shape is the
title and which is the third data series. The manifest supplies that meaning,
and it supplies it in a way that survives a re-render: each entry carries both
a **gid** (the identity, which Matplotlib writes into the SVG as the element
``id``) and a **locator** (the structure — which axes, which role, which
index — used to find the artist again after the script or spec changed).

Two rules make the inventory trustworthy:

* An object that cannot be located unambiguously is simply not listed. A
  figure with three unmatched titles reports one title, not three guesses.
* Re-applying an override to a figure whose structure changed is refused
  rather than approximated. Applying a legend offset to what is now a data
  curve would silently misrepresent the figure.
"""
from __future__ import annotations

from typing import Any

from src.figures.core import RenderedFigure

# Which properties each role may be edited through.
EDITABLE_BY_ROLE: dict[str, tuple[str, ...]] = {
    "title": ("color", "fontsize", "offset_px"),
    "xlabel": ("color", "fontsize", "offset_px"),
    "ylabel": ("color", "fontsize", "offset_px"),
    "legend": ("color", "fontsize", "offset_px", "frameon"),
    "annot": ("color", "fontsize", "offset_px"),
    "series": ("color", "linewidth", "offset_px"),
    "axes": ("offset_px",),
}

# The complete set of editable properties, for validation.
ALL_EDITABLE_PROPS: frozenset[str] = frozenset(
    prop for props in EDITABLE_BY_ROLE.values() for prop in props)

# Roles whose text colour may be set.
TEXT_ROLES = frozenset({"title", "xlabel", "ylabel", "legend", "annot"})

_BBOX_KEYS = ("x0", "y0", "x1", "y1")


def _is_plain(value: Any) -> bool:
    """Whether a value can go into the stored manifest as-is.

    The manifest travels to the browser and into a database column, so it may
    hold only numbers, strings and booleans. Anything else — a Matplotlib
    artist, a numpy scalar — is a bug that must not reach a file.
    """
    return isinstance(value, (str, int, float, bool))


def _artist_bbox(artist: Any, figure: Any) -> dict[str, float] | None:
    """Bounding box of an artist in SVG user units.

    Matplotlib's own ``get_window_extent`` reports display pixels, whose
    origin is bottom-left; an SVG viewBox has a top-left origin and measures
    in points (72 per inch). The conversion below puts the box in exactly the
    units the exported SVG uses, so the web editor can position its overlay
    directly over the drawing without a second calibration step.

    A series may be several artists (the patches of one bar group); the box
    then covers all of them.
    """
    members = list(artist) if isinstance(artist, (list, tuple)) else [artist]
    members = [member for member in members if member is not None]
    if not members:
        return None
    try:
        figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        boxes = [member.get_window_extent(renderer) for member in members]
    except Exception:
        return None
    if not boxes:
        return None
    x0 = float(min(box.x0 for box in boxes))
    x1 = float(max(box.x1 for box in boxes))
    y0 = float(min(box.y0 for box in boxes))
    y1 = float(max(box.y1 for box in boxes))
    # Display pixels → points, and the vertical axis flipped: SVG counts down
    # from the top of the canvas while Matplotlib counts up from the bottom.
    scale = 72.0 / float(figure.dpi)
    width, height = figure.get_size_inches() * figure.dpi
    return {"x0": x0 * scale, "y0": float(height - y1) * scale,
            "x1": x1 * scale, "y1": float(height - y0) * scale,
            "width": (x1 - x0) * scale, "height": (y1 - y0) * scale}


def _anchor(bbox: dict[str, float] | None, role: str) -> list[float] | None:
    """The drag handle position for an object, in SVG units."""
    if not bbox:
        return None
    if role in {"title", "xlabel", "ylabel", "annot"}:
        # Text reads best when dragged from its horizontal centre.
        return [(bbox["x0"] + bbox["x1"]) / 2.0, bbox["y0"]]
    if role == "legend":
        return [bbox["x0"], bbox["y0"]]
    # A series or a whole subplot is dragged from its upper-left corner.
    return [bbox["x0"], bbox["y0"]]


def _label_of(artist: Any) -> str:
    if isinstance(artist, (list, tuple)):
        artist = artist[0] if artist else None
    if artist is None:
        return ""
    for attribute in ("get_label", ):
        getter = getattr(artist, attribute, None)
        if callable(getter):
            try:
                value = getter()
            except Exception:
                value = ""
            if value and value != "_child0" and not str(value).startswith("_"):
                return str(value)[:120]
    return ""


def _series_color(artist: Any) -> str:
    if isinstance(artist, (list, tuple)):
        artist = artist[0] if artist else None
    if artist is None:
        return ""
    for attribute in ("get_color", "get_facecolor", "get_edgecolor"):
        getter = getattr(artist, attribute, None)
        if not callable(getter):
            continue
        try:
            value = getter()
        except Exception:
            continue
        if isinstance(value, str) and value:
            return value
        try:
            r, g, b, *_ = value
        except (TypeError, ValueError):
            continue
        return "#%02x%02x%02x" % (int(round(r * 255)), int(round(g * 255)),
                                   int(round(b * 255)))
    return ""


def assign_locators(rendered: RenderedFigure) -> RenderedFigure:
    """Give every meaningful artist in a captured figure a semantic gid.

    Used by the free-script path, where the figure was drawn by code we did
    not write. The traversal walks each axes in order and assigns gids by
    role and index, so two runs of the same script produce the same gids.
    """
    figure = rendered.figure
    if figure is None:
        return rendered

    for axes_index, ax in enumerate(rendered.axes):
        axes_gid = f"axes_{axes_index}"
        _register(rendered, axes_gid, ax, "axes", {
            "axes_index": axes_index, "role": "axes"})

        if ax.get_title():
            _register(rendered, f"{axes_gid}.title", ax.title, "title", {
                "axes_index": axes_index, "role": "title"})
        if ax.get_xlabel():
            _register(rendered, f"{axes_gid}.xlabel", ax.xaxis.label, "xlabel", {
                "axes_index": axes_index, "role": "xlabel"})
        if ax.get_ylabel():
            _register(rendered, f"{axes_gid}.ylabel", ax.yaxis.label, "ylabel", {
                "axes_index": axes_index, "role": "ylabel"})
        if ax.get_legend() is not None:
            _register(rendered, f"{axes_gid}.legend", ax.get_legend(), "legend", {
                "axes_index": axes_index, "role": "legend"})

        for index, artist in enumerate(_data_artists(ax)):
            _register(rendered, f"{axes_gid}.series_{index}", artist, "series", {
                "axes_index": axes_index, "role": "series", "series_index": index})

        for index, artist in enumerate(list(ax.texts)):
            if artist is ax.title or artist is ax.xaxis.label \
                    or artist is ax.yaxis.label:
                continue
            _register(rendered, f"{axes_gid}.annot_{index}", artist, "annot", {
                "axes_index": axes_index, "role": "annot", "annot_index": index})

    _register(rendered, "figure_1", figure, "figure", {"scope": "figure"})
    return rendered


def _register(rendered: RenderedFigure, gid: str, artist: Any, role: str,
              locator: dict[str, Any]) -> None:
    try:
        artist.set_gid(gid)
    except Exception:  # pragma: no cover
        pass
    entry = dict(locator)
    entry["artist"] = artist
    rendered.locators[gid] = entry


def _data_artists(ax: Any) -> list[Any]:
    """The data-bearing artists of one axes, in a stable order."""
    artists: list[Any] = []
    for container in ax.containers:
        # Bar containers hold the individual patches that were drawn.
        if hasattr(container, "patches") and len(container.patches):
            artists.append(container)
        else:
            artists.append(container)
    for collection in ax.collections:
        artists.append(collection)
    for line in ax.lines:
        artists.append(line)
    return artists


def build_manifest(rendered: RenderedFigure, *, source: str = "spec",
                   figure_size_px: tuple[float, float] | None = None) -> dict[str, Any]:
    """Build the ``objects.json`` inventory for a rendered figure."""
    figure = rendered.figure
    objects: list[dict[str, Any]] = []
    for gid, entry in rendered.locators.items():
        artist = entry.get("artist")
        role = str(entry.get("role") or "")
        bbox = _artist_bbox(artist, figure) if artist is not None else None
        record: dict[str, Any] = {
            "gid": gid,
            "role": role,
            "artist": type(artist).__name__ if artist is not None else "",
            "label": _label_of(artist) if artist is not None else "",
            "editable": list(EDITABLE_BY_ROLE.get(role, ())),
            # The locator names a *position* in the figure, not an object in
            # it: the artist itself is not serialisable and must never reach
            # the stored manifest or the browser.
            "locator": {k: v for k, v in entry.items()
                        if k not in {"artist", "members"} and _is_plain(v)},
        }
        if bbox:
            record["bbox"] = {key: round(bbox[key], 3) for key in _BBOX_KEYS}
            anchor = _anchor(bbox, role)
            if anchor:
                record["anchor"] = [round(anchor[0], 3), round(anchor[1], 3)]
        if role == "series" and artist is not None:
            colour = _series_color(artist)
            if colour:
                record["color"] = colour
            width = _series_width(artist)
            if width is not None:
                record["linewidth"] = width
        if role in TEXT_ROLES and artist is not None:
            size = _font_size(artist)
            if size is not None:
                record["fontsize"] = size
        objects.append(record)

    objects.sort(key=lambda item: _sort_key(item["gid"]))
    width = height = None
    if figure_size_px is None and figure is not None:
        try:
            # The canvas size in the same units as the boxes: SVG user units
            # (points), which is what the exported viewBox measures in.
            size = figure.get_size_inches() * 72.0
            width, height = float(size[0]), float(size[1])
        except Exception:  # pragma: no cover
            width = height = None

    return {
        "version": 1,
        "source": source,
        "figure_size_px": [width, height] if width else None,
        "objects": objects,
    }


def _sort_key(gid: str) -> tuple:
    parts = str(gid).split(".")
    head = parts[0]
    tail = parts[1] if len(parts) > 1 else ""
    order = {"": 0, "title": 1, "xlabel": 2, "ylabel": 3, "legend": 4}
    if tail in order:
        return (head, order[tail], 0)
    if tail.startswith("series_"):
        return (head, 10, int(tail.split("_")[1]))
    if tail.startswith("annot_"):
        return (head, 20, int(tail.split("_")[1]))
    return (head, 30, 0)


def _series_width(artist: Any) -> float | None:
    getter = getattr(artist, "get_linewidth", None)
    if callable(getter):
        try:
            value = getter()
            if isinstance(value, (int, float)):
                return round(float(value), 3)
        except Exception:
            pass
    lines = getattr(artist, "get_lines", None)
    if callable(lines):
        try:
            for line in lines():
                width = line.get_linewidth()
                if isinstance(width, (int, float)):
                    return round(float(width), 3)
        except Exception:
            pass
    return None


def _font_size(artist: Any) -> float | None:
    getter = getattr(artist, "get_fontsize", None)
    if callable(getter):
        try:
            value = getter()
            if isinstance(value, (int, float)):
                return round(float(value), 3)
        except Exception:
            return None
    return None


def find_by_locator(rendered: RenderedFigure, locator: dict[str, Any]) -> Any | None:
    """Re-find an artist from a manifest locator.

    The locator names the structural position — axes index, role, series or
    annotation index — rather than a Matplotlib identity, so it still works
    after the figure was rebuilt.
    """
    role = str(locator.get("role") or "")
    axes_index = int(locator.get("axes_index", 0) or 0)
    if role == "figure":
        return rendered.figure
    if axes_index >= len(rendered.axes):
        return None
    ax = rendered.axes[axes_index]
    if role == "axes":
        return ax
    if role == "title":
        return ax.title
    if role == "xlabel":
        return ax.xaxis.label
    if role == "ylabel":
        return ax.yaxis.label
    if role == "legend":
        return ax.get_legend()
    if role == "series":
        index = int(locator.get("series_index", 0) or 0)
        artists = _data_artists(ax)
        return artists[index] if 0 <= index < len(artists) else None
    if role == "annot":
        index = int(locator.get("annot_index", 0) or 0)
        texts = list(ax.texts)
        return texts[index] if 0 <= index < len(texts) else None
    return None


__all__ = [
    "ALL_EDITABLE_PROPS", "EDITABLE_BY_ROLE", "TEXT_ROLES",
    "assign_locators", "build_manifest", "find_by_locator",
]

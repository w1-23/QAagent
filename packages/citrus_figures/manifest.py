"""The semantic object inventory: ``objects.json``.

A manifest describes every editable object in a figure: what it is, what it
may be changed to, and where it sits on the canvas. It is the contract
between the renderer, the web editor and the override applier.

Why a manifest at all
---------------------
An SVG is a tree of shapes. That tree knows nothing about which shape is the
title and which is the third data series. The manifest supplies that meaning,
and supplies a **gid** (the current render's SVG element id) plus an Artist
**locator** (axes, role and index). Neither is a permanent object identity.
Explicit ``register_semantic_identity`` keys form a separate additive protocol;
FigureDocument in the service binds those keys to persisted object ids. Unknown
or ambiguous entries remain render-scoped, including legacy name-derived gids.

Two rules make the inventory trustworthy:

* An object that cannot be located unambiguously is simply not listed. A
  figure with three unmatched titles reports one title, not three guesses.
* Re-applying an override to a figure whose structure changed is refused
  rather than approximated. Applying a legend offset to what is now a data
  curve would silently misrepresent the figure.
"""
from __future__ import annotations

from typing import Any
import math

from citrus_figures.core import RenderedFigure

# Which properties each role may be edited through.
MANIFEST_VERSION = 3
_TEXT_PROPS = ("color", "fontsize", "fontfamily", "fontweight", "fontstyle",
               "horizontalalignment", "offset_px", "visible")
FONT_WEIGHTS = ("normal", "bold", "light", "medium", "semibold", "heavy", "black")
FONT_STYLES = ("normal", "italic", "oblique")
HORIZONTAL_ALIGNMENTS = ("left", "center", "right")
LEGEND_LOCS = ("best", "upper right", "upper left", "lower left", "lower right", "right",
               "center left", "center right", "lower center", "upper center", "center")
MARKER_STYLE_PROPS = ("marker", "markersize", "markerfacecolor", "markeredgecolor", "markeredgewidth")
LINE_STYLE_PROPS = (*MARKER_STYLE_PROPS, "linestyle")
MARKERS = ("o", "s", "^", "v", "D", "P", "X", "+", "x", ".", "None")
LINESTYLES = ("-", "--", "-.", ":", "None")
EDITABLE_BY_ROLE: dict[str, tuple[str, ...]] = {
    "title": (*_TEXT_PROPS, "text"),
    "xlabel": (*_TEXT_PROPS, "text"),
    "ylabel": (*_TEXT_PROPS, "text"),
    "legend": (*_TEXT_PROPS, "frameon", "ncols", "loc"),
    "annot": (*_TEXT_PROPS, "text"),
    "ticklabel": _TEXT_PROPS,
    "series": ("color", "linewidth", "visible", "label", "alpha", "facecolor", "edgecolor", *LINE_STYLE_PROPS),
    "point": ("color", "visible", "alpha", *MARKER_STYLE_PROPS),
    "bar": ("color", "facecolor", "edgecolor", "linewidth", "alpha", "visible"),
    "grid": ("color", "linewidth", "linestyle", "alpha", "visible"),
    "errorbar": ("color", "linewidth", "linestyle", "alpha", "visible", "capsize"),
    "colorbar": ("visible", "label", "offset_px"),
    "spine": ("color", "linewidth", "visible"),
    "axes": ("offset_px",),
}

# The complete set of editable properties, for validation.
ALL_EDITABLE_PROPS: frozenset[str] = frozenset(
    prop for props in EDITABLE_BY_ROLE.values() for prop in props)

# Roles whose text colour may be set.
TEXT_ROLES = frozenset({"title", "xlabel", "ylabel", "legend", "annot", "ticklabel"})


def available_font_families() -> list[str]:
    """Installed family names backed by existing files, never invented options."""
    from pathlib import Path
    from matplotlib import font_manager
    return sorted({entry.name for entry in font_manager.fontManager.ttflist
                   if Path(entry.fname).is_file()}, key=str.casefold)

_BBOX_KEYS = ("x0", "y0", "x1", "y1")


def _is_plain(value: Any) -> bool:
    """Whether a value can go into the stored manifest as-is.

    The manifest travels to the browser and into a database column, so it may
    hold only numbers, strings and booleans. Anything else — a Matplotlib
    artist, a numpy scalar — is a bug that must not reach a file.
    """
    return isinstance(value, (str, int, float, bool))


def _artist_bbox(artist: Any, figure: Any, *, draw: bool = True) -> dict[str, float] | None:
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
    members = [member for member in members if member is not None
               and (not callable(getattr(member, "get_visible", None)) or member.get_visible())]
    if not members:
        return None
    try:
        if draw:
            figure.canvas.draw()
        renderer = figure.canvas.get_renderer()
        boxes = []
        for member in members:
            box = member.get_window_extent(renderer)
            if not all(math.isfinite(float(value)) for value in box.extents):
                # Collection.get_window_extent may be Bbox.null() for scatter.
                # Its data bounds plus marker radius give a selectable display
                # box without moving the observations or emitting JSON Infinity.
                from matplotlib.collections import Collection
                if isinstance(member, Collection) and member.axes is not None:
                    box = member.get_datalim(member.axes.transData).transformed(member.axes.transData)
                    sizes = getattr(member, "get_sizes", lambda: [])()
                    if len(sizes):
                        radius = math.sqrt(max(0.0, float(max(sizes)))) * float(figure.dpi) / 144.0
                        box = box.padded(radius)
            if all(math.isfinite(float(value)) for value in box.extents):
                boxes.append(box)
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
    # Text.get_label() is artist metadata, not the displayed title/label.
    text = getattr(artist, "get_text", None)
    if callable(text):
        return str(text())[:120]
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
        colours = [_series_color(member) for member in artist if member is not None]
        return colours[0] if colours and colours[0] and all(value == colours[0] for value in colours) else ""
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
        try:
            import numpy as np
            from matplotlib.colors import to_rgba_array

            # Collections return (N, 4), including (1, 4) for a solid colour.
            # A per-point palette has no honest single series colour: leave
            # the field absent rather than reporting an arbitrary first row
            # or the edge colour as though it described the whole series.
            colours = to_rgba_array(value)
            if not len(colours):
                continue
            if not np.isfinite(colours).all() or not np.all(colours == colours[0]):
                return ""
            r, g, b = colours[0, :3]
            return "#%02x%02x%02x" % (int(round(r * 255)), int(round(g * 255)),
                                       int(round(b * 255)))
        except (TypeError, ValueError):
            continue
    return ""


def _legend_color(artist: Any) -> str:
    """Only report a colour shared by the legend's displayed text and title."""
    from matplotlib.colors import to_hex, to_rgba

    texts = list(artist.get_texts())
    title = artist.get_title()
    if title is not None:
        texts.append(title)
    try:
        colours = [to_rgba(text.get_color()) for text in texts
                   if text.get_visible() and text.get_text()]
        if colours and all(colour == colours[0] for colour in colours):
            return to_hex(colours[0])
    except (TypeError, ValueError):
        pass
    return ""


def register_semantic_identity(artist: Any, key: str, *, role: str) -> Any:
    """Declare a source-owned key, independently of a render's gid/locator.

    Register the collected Artist (or a series container) before building its
    manifest. Keys must come from the author's domain model, never an inferred
    label, traversal index or geometry. Registration adds no editing capability.
    A reused Artist can have different keys for distinct roles (series/bar).
    Duplicate keys are reported as ambiguous by the inventory, never matched.
    """
    import re
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", key):
        raise ValueError("semantic key must be a bounded explicit identifier")
    if role not in {*EDITABLE_BY_ROLE, "figure"}:
        raise ValueError("semantic identity requires a known manifest role")
    identities = dict(getattr(artist, "_citrus_semantic_keys", {}))
    if role in identities and identities[role] != key:
        raise ValueError("semantic identity is already registered for this role")
    identities[role] = key
    artist._citrus_semantic_keys = identities
    return artist


def assign_locators(rendered: RenderedFigure) -> RenderedFigure:
    """Give collected Artists render-local gids and positional locators.

    Used by the free-script path, where the figure was drawn by code we did
    not write. Traversal order may reproduce gids for an unchanged script;
    it never establishes permanent identity across source changes.
    """
    figure = rendered.figure
    if figure is None:
        return rendered

    for axes_index, ax in enumerate(rendered.axes):
        axes_gid = f"axes_{axes_index}"
        _register(rendered, axes_gid, ax, "axes", {
            "axes_index": axes_index, "role": "axes"})

        for role, text in (("title", ax.title), ("xlabel", ax.xaxis.label), ("ylabel", ax.yaxis.label)):
            _register(rendered, f"{axes_gid}.{role}", text, role, {
                "axes_index": axes_index, "role": role})
        if ax.get_legend() is not None:
            _register(rendered, f"{axes_gid}.legend", ax.get_legend(), "legend", {
                "axes_index": axes_index, "role": "legend"})

        from matplotlib.container import BarContainer, ErrorbarContainer
        owned = set()
        for container in ax.containers:
            if isinstance(container, BarContainer):
                owned.update(container.patches)
                if container.errorbar is not None:
                    owned.add(id(container.errorbar))
            if isinstance(container, ErrorbarContainer):
                central, caps, intervals = container.lines
                owned.update(item for item in (central, *caps, *intervals) if item is not None)
        # Keep an explicitly keyed series' existing address. Public spec
        # rendering also calls this collector; adding a second panel/axes
        # address for the same Artist would make its key falsely ambiguous.
        existing_series = [entry for entry in rendered.locators.values()
                           if entry.get("role") == "series" and entry.get("axes_index") == axes_index]
        preserve_series = any(
            getattr(entry.get("artist"), "_citrus_semantic_keys", {}).get("series")
            or getattr(series_source(rendered, entry), "_citrus_semantic_keys", {}).get("series")
            for entry in existing_series)
        registered_sources = ({id(series_source(rendered, entry)) for entry in existing_series}
                              if preserve_series else set())
        # A colorbar ramp encodes the mappable's norm. It is not an ordinary
        # recolourable data series; expose only its semantic presentation below.
        for index, artist in enumerate([] if getattr(ax, "_colorbar", None) is not None else _data_artists(ax)):
            if id(artist) in registered_sources:
                continue
            if (isinstance(artist, (BarContainer, ErrorbarContainer)) and id(artist) in owned
                    or not isinstance(artist, (BarContainer, ErrorbarContainer)) and artist in owned):
                continue
            gid = f"{axes_gid}.series_{index}"
            if preserve_series and gid in rendered.locators:
                gid = f"{axes_gid}.series_collected_{index}"
            _register(rendered, gid, artist, "series", {
                "axes_index": axes_index, "role": "series", "series_index": index})

        for index, artist in enumerate(list(ax.texts)):
            if artist is ax.title or artist is ax.xaxis.label \
                    or artist is ax.yaxis.label:
                continue
            _register(rendered, f"{axes_gid}.annot_{index}", artist, "annot", {
                "axes_index": axes_index, "role": "annot", "annot_index": index})

    if figure._suptitle is not None:
        _register(rendered, "figure_1.title", figure._suptitle, "title", {"scope": "figure", "role": "title"})
    for index, legend in enumerate(figure.legends):
        _register(rendered, f"figure_1.legend_{index}", legend, "legend",
                  {"scope": "figure", "role": "legend", "legend_index": index})
    _register(rendered, "figure_1", figure, "figure", {"scope": "figure"})
    return rendered


def _register(rendered: RenderedFigure, gid: str, artist: Any, role: str,
              locator: dict[str, Any]) -> None:
    from matplotlib.container import BarContainer, ErrorbarContainer
    entry = dict(locator)
    if isinstance(artist, BarContainer):
        entry["members"] = list(artist.patches)
        if artist.errorbar is not None:
            central, caps, intervals = artist.errorbar.lines
            entry["error_members"] = [item for item in (central, *caps, *intervals) if item is not None]
        artist = artist.patches[0] if artist.patches else None
    elif isinstance(artist, ErrorbarContainer):
        central, caps, intervals = artist.lines
        entry["members"] = [item for item in (central, *caps, *intervals) if item is not None]
        artist = entry["members"][0] if entry["members"] else None
    try:
        artist.set_gid(gid)
    except Exception:  # pragma: no cover
        pass
    entry["artist"] = artist
    rendered.locators[gid] = entry


def series_source(rendered: RenderedFigure, entry: dict[str, Any]) -> Any:
    """Recover container labels instead of the central artist's _nolegend_."""
    from matplotlib.container import BarContainer, ErrorbarContainer
    artist = entry.get("artist")
    ax = rendered.axes[int(entry.get("axes_index", 0))]
    for container in ax.containers:
        if isinstance(container, BarContainer) and any(artist is patch for patch in container.patches):
            return container
        if isinstance(container, ErrorbarContainer):
            central, caps, intervals = container.lines
            if any(artist is member for member in (central, *caps, *intervals)):
                return container
    return artist


def _marker_metadata(artist: Any) -> dict[str, Any]:
    import numpy as np
    from matplotlib.collections import PathCollection
    from matplotlib.colors import to_hex, to_rgba
    from matplotlib.lines import Line2D
    from matplotlib.markers import MarkerStyle
    from citrus_figures.core import ScatterPoint
    record = {}
    if isinstance(artist, Line2D):
        record.update(marker=str(artist.get_marker()), markersize=float(artist.get_markersize()),
                      linestyle=str(artist.get_linestyle()), markeredgewidth=float(artist.get_markeredgewidth()),
                      alpha=float(artist.get_alpha()) if artist.get_alpha() is not None else 1.0)
        for prop in ("markerfacecolor", "markeredgecolor"):
            colour = to_rgba(getattr(artist, "get_" + prop)())
            if colour[3] != 0:
                record[prop] = to_hex(colour)
        return record
    point = artist if isinstance(artist, ScatterPoint) else None
    collection = point.collection if point else artist
    if not isinstance(collection, PathCollection):
        alpha = getattr(artist, "get_alpha", lambda: None)()
        if alpha is not None:
            record["alpha"] = float(alpha)
        return record
    def selected(values):
        return [values[point.index % len(values)]] if point and len(values) else values
    for prop, values in (("markersize", np.sqrt(collection.get_sizes())),
                         ("markeredgewidth", collection.get_linewidths())):
        values = selected(values)
        if len(values) and np.allclose(values, values[0]):
            record[prop] = float(values[0])
    paths = selected(collection.get_paths())
    if len(paths) and all(np.array_equal(path.vertices, paths[0].vertices)
                          and np.array_equal(path.codes, paths[0].codes) for path in paths):
        for marker in MARKERS:
            style = MarkerStyle(marker)
            candidate = style.get_path().transformed(style.get_transform())
            if np.array_equal(candidate.vertices, paths[0].vertices) and np.array_equal(candidate.codes, paths[0].codes):
                record["marker"] = marker
                break
    alphas = []
    for prop, which in (("markerfacecolor", "face"), ("markeredgecolor", "edge")):
        colours = point._colours(which) if point else getattr(collection, "_qa_point_style_state", {}).get(which)
        if colours is None:
            colours = getattr(collection, "get_" + which + "colors")()
        colours = np.asarray(selected(colours))
        if len(colours):
            alphas.extend(colours[:, 3])
            if np.allclose(colours[:, :3], colours[0, :3]) and np.all(colours[:, 3] != 0):
                record[prop] = to_hex(colours[0])
    if alphas and np.allclose(alphas, alphas[0]):
        record["alpha"] = float(alphas[0])
    return record


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


def _grid_members(ax: Any, axis_name: str, which: str) -> list[Any]:
    axis = {"x": ax.xaxis, "y": ax.yaxis}.get(axis_name)
    if axis is None or which not in {"major", "minor"}:
        return []
    low, high = sorted(axis.get_view_interval())
    return [tick.gridline for tick in getattr(axis, f"get_{which}_ticks")()
            if math.isfinite(float(tick.get_loc())) and low <= tick.get_loc() <= high]


def _error_container(ax: Any, entry: dict[str, Any]) -> Any | None:
    from matplotlib.container import BarContainer, ErrorbarContainer
    artist = entry.get("artist")
    for container in ax.containers:
        if isinstance(container, BarContainer) and any(artist is patch for patch in container.patches):
            return container.errorbar
        if isinstance(container, ErrorbarContainer):
            central, caps, intervals = container.lines
            if any(artist is member for member in (central, *caps, *intervals)):
                return container
    return None


def _style_metadata(members: list[Any], props: tuple[str, ...]) -> dict[str, Any]:
    """Publish only uniform actual properties, never a guessed first member."""
    from matplotlib.colors import to_rgba, to_hex
    result = {}
    for prop in props:
        values = []
        for member in members:
            getter = getattr(member, "get_" + prop, None)
            if not callable(getter):
                break
            value = getter()
            if prop in {"facecolor", "edgecolor"}:
                try:
                    rgba = to_rgba(value)
                    if not rgba[3]:
                        break
                    value = to_hex(rgba)
                except (TypeError, ValueError):
                    break
            elif prop == "alpha":
                if value is None:
                    break
                value = float(value)
            elif prop == "linewidth":
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    break
            if not isinstance(value, (str, int, float, bool)):
                break
            values.append(value)
        if len(values) == len(members) and values and all(value == values[0] for value in values):
            result[prop] = values[0]
    return result


def _discover_semantics(rendered: RenderedFigure) -> None:
    """Enrich old/base inventories without renumbering any existing series gid."""
    import numpy as np
    from matplotlib.collections import PathCollection
    from matplotlib.container import BarContainer
    from matplotlib.patches import Rectangle
    from citrus_figures.core import ScatterPoint

    for gid in list(rendered.locators):
        if rendered.locators[gid].get("role") in {"spine", "ticklabel", "point", "bar", "grid", "errorbar", "colorbar"}:
            del rendered.locators[gid]

    for axes_index, ax in enumerate(rendered.axes):
        prefix = next((gid for gid, entry in rendered.locators.items()
                       if entry.get("role") == "axes" and entry.get("artist") is ax),
                      f"axes_{axes_index}")
        for role, text in (("title", ax.title), ("xlabel", ax.xaxis.label), ("ylabel", ax.yaxis.label)):
            if f"{prefix}.{role}" not in rendered.locators:
                _register(rendered, f"{prefix}.{role}", text, role,
                          {"axes_index": axes_index, "role": role})
        if getattr(ax, "_colorbar", None) is not None:
            # Reuse the axes artist without replacing its existing SVG identity.
            rendered.locators[f"{prefix}.colorbar"] = {
                "axes_index": axes_index, "role": "colorbar", "artist": ax}
        else:
            for axis_name in ("x", "y"):
                for which in ("major", "minor"):
                    members = _grid_members(ax, axis_name, which)
                    if members:
                        rendered.locators[f"{prefix}.grid_{axis_name}_{which}"] = {
                            "axes_index": axes_index, "role": "grid", "axis": axis_name,
                            "which": which, "artist": members[0], "members": members}
        for side, spine in ax.spines.items():
            _register(rendered, f"{prefix}.spine_{side}", spine, "spine",
                      {"axes_index": axes_index, "role": "spine", "spine": side})
        for axis_name, axis in (("x", ax.xaxis), ("y", ax.yaxis)):
            low, high = sorted(axis.get_view_interval())
            for which in ("major", "minor"):
                ticks = getattr(axis, f"get_{which}_ticks")()
                for index, tick in enumerate(ticks):
                    value = float(tick.get_loc())
                    if not math.isfinite(value) or value < low or value > high:
                        continue
                    for side in ("label1", "label2"):
                        text = getattr(tick, side)
                        _register(rendered, f"{prefix}.tick_{axis_name}_{which}_{index}_{side}", text,
                                  "ticklabel", {"axes_index": axes_index, "role": "ticklabel",
                                                "axis": axis_name, "which": which, "tick_index": index,
                                                "tick_value": value, "side": side})

    for parent_gid, entry in list(rendered.locators.items()):
        if entry.get("role") != "series":
            continue
        artist = entry.get("artist")
        axes_index = int(entry.get("axes_index", 0))
        ax = rendered.axes[axes_index]
        errorbar = _error_container(ax, entry)
        if errorbar is not None:
            _, caps, intervals = errorbar.lines
            members = [*caps, *intervals]
            if members:
                rendered.locators[f"{parent_gid}.errorbar"] = {
                    "axes_index": axes_index, "role": "errorbar", "parent_gid": parent_gid,
                    "container_index": next(i for i, container in enumerate(ax.containers) if container is errorbar),
                    "artist": members[0], "members": members}
        if isinstance(artist, PathCollection):
            collection_index = list(ax.collections).index(artist)
            for index, offset in enumerate(artist.get_offsets()):
                if np.ma.is_masked(offset) or not np.isfinite(offset).all():
                    continue
                gid = f"{parent_gid}.point_{index}"
                rendered.locators[gid] = {
                    "axes_index": axes_index, "role": "point", "parent_gid": parent_gid,
                    "collection_index": collection_index, "point_index": index,
                    "artist": ScatterPoint(artist, index)}
        else:
            patches = (list(artist.patches) if isinstance(artist, BarContainer)
                       else list(entry.get("members") or []))
            if not patches or not all(isinstance(patch, Rectangle) for patch in patches):
                continue
            container_index = next((i for i, container in enumerate(ax.containers)
                                    if isinstance(container, BarContainer)
                                    and list(container.patches) == patches), None)
            if container_index is None:
                continue
            for index, patch in enumerate(patches):
                # A patch may already carry the original series gid. Keep it;
                # child selection uses semantic bbox/locator, not SVG mutation.
                rendered.locators[f"{parent_gid}.bar_{index}"] = {
                    "axes_index": axes_index, "role": "bar", "parent_gid": parent_gid,
                    "container_index": container_index, "bar_index": index, "artist": patch}


def build_manifest(rendered: RenderedFigure, *, source: str = "spec",
                   figure_size_px: tuple[float, float] | None = None,
                   include_qa: bool = True) -> dict[str, Any]:
    """Build the ``objects.json`` inventory for a rendered figure."""
    from citrus_figures.runtime import render_context
    with render_context(rendered.rc_params):
        return _build_manifest(rendered, source=source, figure_size_px=figure_size_px, include_qa=include_qa)


def _build_manifest(rendered: RenderedFigure, *, source: str,
                    figure_size_px: tuple[float, float] | None, include_qa: bool = True) -> dict[str, Any]:
    figure = rendered.figure
    if figure is not None:
        figure.canvas.draw()
        _discover_semantics(rendered)
    from citrus_figures.svg_projection import projection_manifest
    projections = projection_manifest(rendered, _artist_bbox) if figure is not None else {}
    style_nodes = getattr(rendered, "_svg_style_nodes", {})
    objects: list[dict[str, Any]] = []
    for gid, entry in rendered.locators.items():
        artist = entry.get("artist")
        role = str(entry.get("role") or "")
        members = list(entry.get("members") or []) + list(entry.get("error_members") or [])
        bbox = _artist_bbox(members or artist, figure, draw=False) if artist is not None else None
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
                        if k not in {"artist", "members", "error_members"} and _is_plain(v)},
        }
        # Identity metadata is deliberately outside the Artist locator. In
        # particular, series_id/panel prefixes and parent indices are not keys.
        identity_artists = [artist]
        if role == "series":
            identity_artists.append(series_source(rendered, entry))
        keys = {getattr(item, "_citrus_semantic_keys", {}).get(role)
                for item in identity_artists if item is not None}
        keys.discard(None)
        if len(keys) == 1:
            record["semantic_key"] = keys.pop()
        elif keys:
            record["identity_reason"] = "conflicting_semantic_keys"
        if bbox:
            record["bbox"] = {key: round(bbox[key], 3) for key in _BBOX_KEYS}
            anchor = _anchor(bbox, role)
            if anchor:
                record["anchor"] = [round(anchor[0], 3), round(anchor[1], 3)]
        if role in {"series", "point"} and artist is not None:
            from matplotlib.lines import Line2D
            from matplotlib.collections import PathCollection
            record.update(_marker_metadata(artist))
            if role == "series":
                source_artist = series_source(rendered, entry)
                record["label"] = str(source_artist.get_label() or "")
                if record["label"].startswith("_") and not hasattr(source_artist, "_qa_display_label"):
                    record["label"] = ""
                if not isinstance(artist, Line2D):
                    excluded = {"linestyle"} if isinstance(artist, PathCollection) else set(LINE_STYLE_PROPS)
                    record["editable"] = [prop for prop in record["editable"] if prop not in excluded]
        from matplotlib.patches import Rectangle
        patch_members = list(entry.get("members") or [artist])
        if role == "series" and not (patch_members and all(isinstance(member, Rectangle) for member in patch_members)):
            record["editable"] = [prop for prop in record["editable"] if prop not in {"facecolor", "edgecolor"}]
        if role == "bar" or (role == "series" and "facecolor" in record["editable"]):
            record.pop("alpha", None)  # first-patch alpha cannot describe a mixed series
            record.update(_style_metadata(patch_members, ("facecolor", "edgecolor", "alpha", "linewidth")))
        if role in {"grid", "errorbar"}:
            from matplotlib.lines import Line2D
            styled = members or [artist]
            record.update(_style_metadata(styled, ("alpha",)))
            colour = _series_color(styled)
            if colour:
                record["color"] = colour
            strokes = [member for member in styled if role == "grid" or not isinstance(member, Line2D)]
            record.update(_style_metadata(strokes, ("linestyle",)))
            widths = []
            for member in styled:
                value = (member.get_markeredgewidth() if role == "errorbar" and isinstance(member, Line2D)
                         else member.get_linewidth())
                import numpy as np
                widths.extend(float(width) for width in np.atleast_1d(value))
            if widths and all(width == widths[0] for width in widths):
                record["linewidth"] = widths[0]
            if role == "errorbar":
                record["linestyle_options"] = [style for style in LINESTYLES if style != "None"]
                caps = [member for member in styled if isinstance(member, Line2D)]
                if caps:
                    sizes = [float(cap.get_markersize()) / 2 for cap in caps]
                    if all(size == sizes[0] for size in sizes):
                        record["capsize"] = sizes[0]
                else:
                    record["editable"].remove("capsize")
        if role == "colorbar":
            colorbar = artist._colorbar
            record["label"] = (artist.yaxis.label if colorbar.orientation == "vertical" else artist.xaxis.label).get_text()
        if role in {"series", "errorbar"} and any(not hasattr(legend, "_qa_shared_groups") for legend in figure.legends):
            # Captured custom figure legends copy their handles and expose no
            # source identity. Do not guess their series links from labels.
            record["editable"] = []
            record["editing_limitation"] = "unverified_figure_legend_links"
        if "parent_gid" in entry:
            record["parent_gid"] = entry["parent_gid"]
        if "visible" in EDITABLE_BY_ROLE.get(role, ()) and artist is not None:
            visible_values = [bool(member.get_visible()) for member in (members or [artist])
                              if callable(getattr(member, "get_visible", None))]
            if visible_values and all(value == visible_values[0] for value in visible_values):
                record["visible"] = visible_values[0]
        if role in {"series", "point", "bar", "spine"} and artist is not None:
            from matplotlib.colors import to_hex
            colour = (to_hex(artist.get_edgecolor()) if role == "spine" else
                      _series_color(members or artist) if role == "series" else _series_color(artist))
            if colour:
                record["color"] = colour
            width = _series_width(artist)
            if width is not None and not (role == "series" and "facecolor" in record["editable"]):
                record["linewidth"] = width
        if role in TEXT_ROLES and artist is not None:
            colour = _legend_color(artist) if role == "legend" else _series_color(artist)
            if colour:
                record["color"] = colour
            if role == "legend":
                record["frameon"] = bool(artist.get_frame_on())
                record["ncols"] = int(artist._ncols)
                record["loc"] = next((name for name, code in artist.codes.items() if code == artist._loc), None)
            elif role in {"title", "xlabel", "ylabel", "annot"}:
                record["text"] = str(artist.get_text())
            size = _font_size(artist)
            if size is not None:
                record["fontsize"] = size
            from matplotlib import font_manager
            texts = ([*artist.get_texts(), artist.get_title()] if role == "legend" else [artist])
            texts = [text for text in texts if text is not None and (text.get_text() or role != "legend")]
            for prop in ("fontstyle", "horizontalalignment"):
                values = [getattr(text, "get_" + prop)() for text in texts]
                if values and all(value == values[0] for value in values):
                    record[prop] = values[0]
            families, weights = [], []
            for text in texts:
                try:
                    path = font_manager.findfont(text.get_fontproperties(), fallback_to_default=False)
                    families.append(font_manager.FontProperties(fname=path).get_name())
                except ValueError:
                    families.append(None)
                weights.append(text.get_fontweight())
            if families and families[0] and all(value == families[0] for value in families):
                record["fontfamily"] = families[0]
            if weights and all(value == weights[0] for value in weights):
                record["fontweight"] = weights[0]
        record.update(projections.get(gid, {}))
        record.update(style_nodes.get(gid, {}))
        for link in record.get("legend_links", []):
            link["style_nodes"] = {node_id: style_nodes.get(node_id, {}) for node_id in link["mark_gids"]}
        objects.append(record)

    from collections import Counter
    semantic_counts = Counter(item.get("semantic_key") for item in objects if item.get("semantic_key"))
    for item in objects:
        key = item.get("semantic_key")
        unique = bool(key and semantic_counts[key] == 1)
        item["identity_scope"] = "semantic" if unique else "render"
        item["identity_reason"] = ("explicit_key" if unique else "ambiguous_key" if key
                                   else item.get("identity_reason", "unregistered"))
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
        "version": MANIFEST_VERSION,
        "semantic_identity_version": 1,
        "font_families": available_font_families(),
        "font_weights": list(FONT_WEIGHTS),
        "font_styles": list(FONT_STYLES),
        "horizontalalignment_options": list(HORIZONTAL_ALIGNMENTS),
        "legend_loc_options": list(LEGEND_LOCS),
        "marker_options": list(MARKERS),
        "linestyle_options": list(LINESTYLES),
        "source": source,
        "figure_size_px": [width, height] if width else None,
        "objects": objects,
        **({"qa_receipt": scientific_qa_receipt(rendered)} if include_qa else {}),
        "shared_legend_groups": {
            label: [gid for gid, _ in members]
            for legend in getattr(figure, "legends", [])
            for label, members in getattr(legend, "_qa_shared_groups", {}).items()},
    }


def scientific_qa_receipt(rendered: RenderedFigure) -> dict[str, Any]:
    """Measure the saved canvas in points; never claim scientific peer review."""
    import numpy as np
    from matplotlib.text import Text
    from matplotlib import font_manager
    from matplotlib.lines import Line2D
    from matplotlib.spines import Spine
    from matplotlib.collections import Collection, PathCollection
    from citrus_figures.spec import parse_qa_profile
    from citrus_figures.styling import figure_glyph_coverage

    profile = parse_qa_profile(rendered.qa_profile or {})
    figure = rendered.figure
    width, height = [float(value) * 25.4 for value in figure.get_size_inches()]
    target = profile.get("final_size_mm")
    if target is None:
        target_width = {"nature_single": 89.0, "nature_double": 183.0}.get(profile["name"], width)
        target = [target_width, height * target_width / width]
    scales = [target[0] / width, target[1] / height]
    isotropic = math.isclose(scales[0], scales[1], rel_tol=0.01)
    scale = min(scales)
    fonts, strokes, nonfinite, unsupported_data = [], [], [], []
    measured_series = 0
    families, unresolved_fonts = set(), []
    renderer = figure.canvas.get_renderer()
    outside = []
    undrawn_tick_labels = set()
    for ax in figure.axes:
        for axis in (ax.xaxis, ax.yaxis):
            ticks = axis.get_major_ticks() + axis.get_minor_ticks()
            # Matplotlib's draw path uses this view/transform-aware selection.
            # get_visible() alone includes cached labels outside the view limits.
            drawn = axis._update_ticks() if ax.get_visible() and ax.axison and axis.get_visible() else []
            drawn_ids = {id(label) for tick in drawn if tick.get_visible()
                         for label in (tick.label1, tick.label2)}
            undrawn_tick_labels.update(id(label) for tick in ticks
                                       for label in (tick.label1, tick.label2)
                                       if id(label) not in drawn_ids)
    for artist in figure.findobj():
        if id(artist) in undrawn_tick_labels:
            continue
        if not callable(getattr(artist, "get_visible", None)) or not artist.get_visible():
            continue
        if isinstance(artist, Text) and artist.get_text():
            # Tick Text remains visible outside the view interval; only rendered
            # text on a visible axes participates in a final-canvas receipt.
            if artist.axes is not None and not artist.axes.get_visible():
                continue
            bbox = artist.get_window_extent(renderer)
            if not all(math.isfinite(v) for v in bbox.extents):
                continue
            if bbox.x1 < 0 or bbox.y1 < 0 or bbox.x0 > figure.bbox.x1 or bbox.y0 > figure.bbox.y1:
                continue
            fonts.append(float(artist.get_fontsize()) * scale)
            try:
                path = font_manager.findfont(artist.get_fontproperties(), fallback_to_default=False)
                families.add(font_manager.FontProperties(fname=path).get_name())
            except ValueError:
                unresolved_fonts.append(str(artist.get_text())[:120])
            if bbox.x0 < -0.5 or bbox.y0 < -0.5 or bbox.x1 > figure.bbox.x1 + 0.5 or bbox.y1 > figure.bbox.y1 + 0.5:
                outside.append(str(artist.get_text())[:120])
        if isinstance(artist, (Line2D, Collection, Spine)):
            getter = getattr(artist, "get_linewidths", None) or getattr(artist, "get_linewidth", None)
            if getter:
                for value in np.atleast_1d(getter()):
                    if float(value) > 0:
                        strokes.append(float(value) * scale)
    for gid, entry in rendered.locators.items():
        if entry.get("role") != "series":
            continue
        artist = entry.get("artist")
        try:
            if isinstance(artist, Line2D):
                values = np.column_stack(artist.get_data(orig=False))
            elif isinstance(artist, PathCollection):
                values = artist.get_offsets()
            elif entry.get("members") and all(hasattr(a, "get_height") for a in entry["members"]):
                values = [a.get_height() for a in entry["members"]]
            else:
                unsupported_data.append(gid)
                continue
            measured_series += 1
            if not np.isfinite(np.asarray(values, dtype=float)).all():
                nonfinite.append(gid)
        except (TypeError, ValueError):
            unsupported_data.append(gid)
    def meets_floor(values, floor):
        # Unit conversion / proportional sizing can put an exact threshold a
        # few ULPs below itself (e.g. 5 pt -> 4.999999999999999 pt).
        return bool(values) and (min(values) >= floor or
                                 math.isclose(min(values), floor, rel_tol=1e-12, abs_tol=1e-12))

    checks = {
        "dimensions": {"status": "pass" if isotropic else "fail", "canvas_mm": [width, height],
                       "final_size_mm": target, "rescale": scales,
                       "scope": "Canvas and proportional final-size scaling; target is not a journal certification."},
        "fonts": {"status": "pass" if meets_floor(fonts, profile["min_font_pt"]) and not unresolved_fonts else "fail",
                  "minimum_final_pt": min(fonts) if fonts else None, "threshold_pt": profile["min_font_pt"],
                  "resolved_families": sorted(families), "unresolved_text": unresolved_fonts,
                  "scope": "Final-size text and resolvable installed font families; see the separate glyph map check and retain visual review."},
        "linewidth": {"status": ("pass" if meets_floor(strokes, profile["min_linewidth_pt"]) else "fail") if strokes else "unsupported",
                      "minimum_final_pt": min(strokes) if strokes else None,
                      "threshold_pt": profile["min_linewidth_pt"], "scope": "Visible Line2D, Collection and Spine strokes; filled patches excluded."},
        "data": {"status": "fail" if nonfinite else "unsupported" if unsupported_data or not measured_series else "pass",
                 "measured_series": measured_series, "nonfinite_series": nonfinite, "unsupported_series": unsupported_data,
                 "scope": "Finite plotted coordinates only; analysis and experimental design require source review."},
        "caption": {"status": "pass" if profile["caption"] else "fail",
                    "scope": "Caption supplied; sample size, error definition and statistical claims are not semantically verified."},
        "canvas_text_bounds": {"status": "fail" if outside else "pass", "outside_text": outside},
        "scientific_claims": {"status": "unsupported"},
        "palette_accessibility": {"status": "unsupported"},
        "label_collisions": {"status": "unsupported"},
        "font_glyph_coverage": figure_glyph_coverage(figure),
        "embedded_raster_resolution": {"status": "unsupported"},
    }
    return {"version": 1, "profile": profile, "checks": checks,
            "status": "needs_attention" if any(v["status"] == "fail" for v in checks.values()) else "measured_checks_pass",
            "unsupported": [key for key, value in checks.items() if value["status"] == "unsupported"]}


def _sort_key(gid: str) -> tuple:
    parts = str(gid).split(".")
    head = parts[0]
    tail = parts[1] if len(parts) > 1 else ""
    order = {"": 0, "title": 1, "xlabel": 2, "ylabel": 3, "legend": 4}
    if tail in order:
        return (head, order[tail], 0)
    if tail.startswith("series_"):
        token = tail.split("_", 1)[1]
        return (head, 10, (0, int(token)) if token.isdigit() else (1, token))
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
    if locator.get("scope") == "figure":
        if role == "legend":
            legends = rendered.figure.legends
            index = locator.get("legend_index", 0)
            return legends[index] if isinstance(index, int) and 0 <= index < len(legends) else None
        if role == "title":
            return rendered.figure._suptitle
    if axes_index < 0 or axes_index >= len(rendered.axes):
        return None
    ax = rendered.axes[axes_index]
    if role == "axes":
        return ax
    if role == "colorbar":
        return ax if getattr(ax, "_colorbar", None) is not None else None
    if role == "grid":
        rendered.figure.canvas.draw()
        return _grid_members(ax, locator.get("axis"), locator.get("which")) or None
    if role == "errorbar":
        from matplotlib.container import ErrorbarContainer
        index = locator.get("container_index")
        if isinstance(index, int) and 0 <= index < len(ax.containers):
            container = ax.containers[index]
            if isinstance(container, ErrorbarContainer):
                _, caps, intervals = container.lines
                return [*caps, *intervals] or None
        return None
    if role == "spine":
        return ax.spines.get(str(locator.get("spine", "")))
    if role == "ticklabel":
        axis = {"x": ax.xaxis, "y": ax.yaxis}.get(locator.get("axis"))
        which, side = locator.get("which"), locator.get("side")
        if axis is None or which not in {"major", "minor"} or side not in {"label1", "label2"}:
            return None
        rendered.figure.canvas.draw()
        ticks = getattr(axis, f"get_{which}_ticks")()
        matches = [tick for tick in ticks if float(tick.get_loc()) == locator.get("tick_value")]
        return getattr(matches[0], side) if len(matches) == 1 else None
    if role == "point":
        from matplotlib.collections import PathCollection
        from citrus_figures.core import ScatterPoint
        index, point = int(locator.get("collection_index", -1)), int(locator.get("point_index", -1))
        collections = list(ax.collections)
        if 0 <= index < len(collections) and isinstance(collections[index], PathCollection):
            collection = collections[index]
            if 0 <= point < len(collection.get_offsets()):
                return ScatterPoint(collection, point)
        return None
    if role == "bar":
        from matplotlib.container import BarContainer
        index, bar = int(locator.get("container_index", -1)), int(locator.get("bar_index", -1))
        if 0 <= index < len(ax.containers) and isinstance(ax.containers[index], BarContainer):
            patches = ax.containers[index].patches
            return patches[bar] if 0 <= bar < len(patches) else None
        return None
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

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
import math

from src.figures.core import RenderedFigure

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
    "series": ("color", "linewidth", "visible", "label", "alpha", *LINE_STYLE_PROPS),
    "point": ("color", "visible", "alpha", *MARKER_STYLE_PROPS),
    "bar": ("color", "linewidth", "visible"),
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
        for index, artist in enumerate(_data_artists(ax)):
            if (isinstance(artist, (BarContainer, ErrorbarContainer)) and id(artist) in owned
                    or not isinstance(artist, (BarContainer, ErrorbarContainer)) and artist in owned):
                continue
            _register(rendered, f"{axes_gid}.series_{index}", artist, "series", {
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
        if isinstance(container, ErrorbarContainer) and container.lines[0] is artist:
            return container
    return artist


def _marker_metadata(artist: Any) -> dict[str, Any]:
    import numpy as np
    from matplotlib.collections import PathCollection
    from matplotlib.colors import to_hex, to_rgba
    from matplotlib.lines import Line2D
    from matplotlib.markers import MarkerStyle
    from src.figures.core import ScatterPoint
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


def _discover_semantics(rendered: RenderedFigure) -> None:
    """Enrich old/base inventories without renumbering any existing series gid."""
    import numpy as np
    from matplotlib.collections import PathCollection
    from matplotlib.container import BarContainer
    from matplotlib.patches import Rectangle
    from src.figures.core import ScatterPoint

    for gid in list(rendered.locators):
        if rendered.locators[gid].get("role") in {"spine", "ticklabel", "point", "bar"}:
            del rendered.locators[gid]

    for axes_index, ax in enumerate(rendered.axes):
        prefix = next((gid for gid, entry in rendered.locators.items()
                       if entry.get("role") == "axes" and entry.get("artist") is ax),
                      f"axes_{axes_index}")
        for role, text in (("title", ax.title), ("xlabel", ax.xaxis.label), ("ylabel", ax.yaxis.label)):
            if f"{prefix}.{role}" not in rendered.locators:
                _register(rendered, f"{prefix}.{role}", text, role,
                          {"axes_index": axes_index, "role": role})
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
                   figure_size_px: tuple[float, float] | None = None) -> dict[str, Any]:
    """Build the ``objects.json`` inventory for a rendered figure."""
    from src.figures.runtime import render_context
    with render_context(rendered.rc_params):
        return _build_manifest(rendered, source=source, figure_size_px=figure_size_px)


def _build_manifest(rendered: RenderedFigure, *, source: str,
                    figure_size_px: tuple[float, float] | None) -> dict[str, Any]:
    figure = rendered.figure
    if figure is not None:
        figure.canvas.draw()
        _discover_semantics(rendered)
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
        if role == "series" and any(not hasattr(legend, "_qa_shared_groups") for legend in figure.legends):
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
            if width is not None:
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
        "version": MANIFEST_VERSION,
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
        "qa_receipt": scientific_qa_receipt(rendered),
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
    from src.figures.spec import parse_qa_profile

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
    for artist in figure.findobj():
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
    checks = {
        "dimensions": {"status": "pass" if isotropic else "fail", "canvas_mm": [width, height],
                       "final_size_mm": target, "rescale": scales,
                       "scope": "Canvas and proportional final-size scaling; target is not a journal certification."},
        "fonts": {"status": "pass" if fonts and min(fonts) >= profile["min_font_pt"] and not unresolved_fonts else "fail",
                  "minimum_final_pt": min(fonts) if fonts else None, "threshold_pt": profile["min_font_pt"],
                  "resolved_families": sorted(families), "unresolved_text": unresolved_fonts,
                  "scope": "Final-size text and resolvable installed font families; glyph coverage requires visual review."},
        "linewidth": {"status": ("pass" if min(strokes) >= profile["min_linewidth_pt"] else "fail") if strokes else "unsupported",
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
        "font_glyph_coverage": {"status": "unsupported"},
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
        from src.figures.core import ScatterPoint
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

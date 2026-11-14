"""Building a Matplotlib figure from resolved data.

Two entry points, one output shape:

``build_resolved_figure``
    The declarative path. A :class:`~src.figures.spec.ResolvedFigure` becomes
    a Matplotlib ``Figure`` with named, addressable artists.

``capture_figure``
    The free-script path. A script that produces a figure is executed in a
    controlled namespace and the resulting ``Figure`` is taken.

Both paths end at the same place: a figure whose artists carry stable
semantic gids, so the manifest can describe it and an override can be
re-applied after a re-render.

The gid scheme
--------------
``figure_1``            the whole figure
``axes_0``              one subplot, as a block
``axes_0.title``        the subplot title
``axes_0.xlabel``       the x axis label
``axes_0.ylabel``       the y axis label
``axes_0.legend``       the legend
``axes_0.series_0``     the first drawn data series
``axes_0.annot_0``      the first annotation
``axes_0.group_0``      the first legend group (categorical colour mapping)

Matplotlib writes a gid straight into the SVG as the element ``id``, which is
what lets an exported figure be identified again after a round trip. The
manifest keeps a structural locator as well, so a re-render can re-find the
artist even if the script was edited.
"""
from __future__ import annotations

import io
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from src.figures.spec import ResolvedFigure, Series, SpecError
from src.figures.styling import apply_base_rcparams, font_stack, palette_colors


@dataclass
class RenderedFigure:
    """A figure plus the addressable artists the manifest will describe."""

    figure: Any
    axes: list[Any] = field(default_factory=list)
    locators: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    rc_params: dict[str, Any] | None = None
    qa_profile: dict[str, Any] = field(default_factory=dict)
    data_provenance: dict[str, Any] = field(default_factory=dict)

    def artist_for(self, gid: str) -> Any | None:
        """Return the artist a gid refers to, or ``None``."""
        entry = self.locators.get(str(gid or ""))
        if not entry:
            return None
        return entry.get("artist")


class ScatterPoint:
    """A logical point inside one PathCollection, without altering its offsets.

    Matplotlib has no per-point Artist. This adapter edits RGBA rows and keeps
    visibility separately so hiding and restoring never loses colour or data.
    The collection's native SVG gid remains the backwards-compatible series id.
    """

    def __init__(self, collection: Any, index: int):
        self.collection = collection
        self.index = index
        self.axes = collection.axes

    def get_figure(self):
        return self.collection.get_figure()

    def _colours(self, which: str):
        import numpy as np
        state = getattr(self.collection, "_qa_point_style_state", None)
        if state is not None:
            return state[which]
        colours = getattr(self.collection, f"get_{which}colors")()
        count = len(self.collection.get_offsets())
        return np.asarray([colours[i % len(colours)] for i in range(count)]) if len(colours) else np.empty((0, 4))

    def _state(self):
        import numpy as np
        state = getattr(self.collection, "_qa_point_style_state", None)
        if state is None:
            self.collection.update_scalarmappable()
            count = len(self.collection.get_offsets())
            face, edge = self._colours("face").copy(), self._colours("edge").copy()
            alpha = self.collection.get_alpha()
            if alpha is None:
                alpha = face[:, 3] if len(face) else edge[:, 3] if len(edge) else 1.0
            opacity = np.asarray(alpha, dtype=float)
            state = {"face": face, "edge": edge,
                     "face_present": np.full(count, bool(len(face))),
                     "edge_present": np.full(count, bool(len(edge))),
                     "alpha": np.resize(opacity, count),
                     "visible": np.ones(count, dtype=bool)}
            self.collection._qa_point_style_state = state
        return state

    def _apply(self):
        state = self._state()
        # Freeze the existing mapped RGBA before applying local styles. No
        # scientific offsets, sizes or axis limits are rewritten.
        self.collection.set_array(None)
        self.collection.set_alpha(None)
        for which in ("face", "edge"):
            colours = state[which].copy()
            if len(colours):
                colours[~state["visible"], 3] = 0
            getattr(self.collection, f"set_{which}color")(colours)

    def get_facecolor(self):
        colours = self._colours("face")
        return colours[self.index:self.index + 1]

    def get_edgecolor(self):
        colours = self._colours("edge")
        return colours[self.index:self.index + 1]

    def set_color(self, colour):
        from matplotlib.colors import to_rgba
        state = self._state()
        for which in ("face", "edge"):
            if len(state[which]):
                state[which][self.index, :3] = to_rgba(colour)[:3]
        self._apply()

    def _set_marker_colour(self, which, colour):
        import numpy as np
        from matplotlib.colors import to_rgba
        state = self._state()
        if not len(state[which]):
            state[which] = np.tile(to_rgba(colour), (len(state["visible"]), 1))
            state[which][:, 3] = 0
            state[which][self.index, 3] = state["alpha"][self.index]
        state[which][self.index, :3] = to_rgba(colour)[:3]
        missing = ~state[which + "_present"][self.index]
        state[which][self.index, 3] = np.where(
            missing, state["alpha"][self.index], state[which][self.index, 3])
        state[which + "_present"][self.index] = True
        self._apply()

    def set_markerfacecolor(self, colour):
        self._set_marker_colour("face", colour)

    def set_markeredgecolor(self, colour):
        self._set_marker_colour("edge", colour)

    def set_alpha(self, alpha):
        import numpy as np
        state = self._state()
        for which in ("face", "edge"):
            if len(state[which]):
                state[which][self.index, 3] = np.where(state[which + "_present"][self.index], alpha, 0)
        state["alpha"][self.index] = alpha
        self._apply()

    def set_marker(self, marker):
        from matplotlib.markers import MarkerStyle
        paths = self.collection.get_paths()
        count = len(self.collection.get_offsets())
        expanded = [paths[i % len(paths)] for i in range(count)]
        style = MarkerStyle(marker)
        path = style.get_path().transformed(style.get_transform())
        if isinstance(self.index, slice):
            expanded = [path] * count
        else:
            expanded[self.index] = path
        self.collection.set_paths(expanded)
        if not style.is_filled() and marker != "None":
            # scatter() normally gives open markers a stroke from their face
            # colour. set_paths() alone skips that Matplotlib constructor step.
            import numpy as np
            state = self._state()
            if len(state["face"]):
                if not len(state["edge"]):
                    state["edge"] = np.zeros_like(state["face"])
                indices = np.arange(count)[self.index]
                indices = np.atleast_1d(indices)
                missing = indices[~state["edge_present"][indices]]
                state["edge"][missing] = state["face"][missing]
                state["edge_present"][missing] = True
                self._apply()

    def set_markersize(self, diameter):
        self._set_vector("sizes", float(diameter) ** 2)

    def set_markeredgewidth(self, width):
        self._set_vector("linewidths", float(width))

    def _set_vector(self, name, value):
        import numpy as np
        original = getattr(self.collection, "get_" + name)()
        count = len(self.collection.get_offsets())
        expanded = np.asarray([original[i % len(original)] if len(original) else 0
                               for i in range(count)], dtype=float)
        expanded[self.index] = value
        getattr(self.collection, "set_" + name)(expanded)

    def get_visible(self):
        state = getattr(self.collection, "_qa_point_style_state", None)
        return bool(state is None or state["visible"][self.index])

    def set_visible(self, visible):
        self._state()["visible"][self.index] = bool(visible)
        self._apply()

    def get_window_extent(self, renderer=None):
        import numpy as np
        from matplotlib.transforms import Bbox
        if not self.get_visible() or not self.collection.get_visible():
            return Bbox.null()
        offset = self.collection.get_offsets()[self.index]
        if np.ma.is_masked(offset) or not np.isfinite(offset).all():
            return Bbox.null()
        center = self.collection.get_offset_transform().transform(offset)
        sizes = self.collection.get_sizes()
        size = sizes[self.index % len(sizes)] if len(sizes) else 0
        radius = np.sqrt(max(0.0, float(size))) * self.get_figure().dpi / 144.0
        return Bbox.from_extents(center[0] - radius, center[1] - radius,
                                 center[0] + radius, center[1] + radius)


def _gid(prefix: str, index: int) -> str:
    return f"{prefix}_{index}"


def _series_gid(axes_index: int, series_index: int) -> str:
    return f"axes_{axes_index}.series_{series_index}"


def _register(rendered: RenderedFigure, gid: str, artist: Any,
              role: str, locator: dict[str, Any]) -> None:
    """Attach a gid to an artist and record how to find it again.

    A series may be several artists — the patches of one bar group, for
    instance. In that case the gid goes on the first artist and the whole
    group is recorded, so a later override reaches every member.
    """
    members = list(artist) if isinstance(artist, (list, tuple)) else [artist]
    for member in members:
        try:
            member.set_gid(gid)
        except Exception:  # pragma: no cover - artists without a gid are still usable
            pass
    locator = dict(locator)
    locator["artist"] = members[0] if members else None
    locator["members"] = members
    rendered.locators[gid] = locator


def _numeric(values: Any) -> list[float]:
    out: list[float] = []
    for value in values or ():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number or number in (float("inf"), float("-inf")):
            continue
        out.append(number)
    return out


def _labels(values: Any) -> list[str]:
    return [("" if v is None else str(v)) for v in (values or ())]


def _apply_series_colour(ax: Any, index: int, colours: tuple[str, ...]) -> str:
    colour = colours[index % len(colours)] if colours else "#0F4D92"
    return colour


def _set_axis_labels(ax: Any, resolved: ResolvedFigure) -> None:
    if resolved.x_label:
        ax.set_xlabel(resolved.x_label)
    if resolved.y_label:
        ax.set_ylabel(resolved.y_label)


def _plot_data(resolved: ResolvedFigure) -> tuple[list[tuple[Any, ...]], list[Any], bool]:
    """Validate whole series before drawing; never filter or broadcast here."""
    import numpy as np

    series_list = resolved.series or (Series(values=resolved.y_values),)
    domain = list(dict.fromkeys(resolved.x_values))
    for series in series_list:
        for value in series.x_values:
            if value not in domain:
                domain.append(value)
    if any(isinstance(value, (int, float, np.number)) and not np.isfinite(value)
           for value in domain):
        raise SpecError("invalid_number", "已解析的数值 X 必须是有限数值。")
    numeric_x = bool(domain) and all(
        isinstance(value, (int, float, np.number)) for value in domain)
    categorical = not numeric_x
    if not domain:
        domain = list(range(len(series_list[0].values)))
        categorical = resolved.chart == "bar"
    # Raw categorical observations historically occupy one slot per row,
    # including repeated labels. Keep that useful convention for one unnamed
    # series; grouped series instead share the category domain.
    raw_x = series_list[0].x_values or resolved.x_values
    row_categories = (categorical and len(series_list) == 1
                      and not series_list[0].name and not resolved.group_name
                      and len(raw_x) > len(domain))
    if row_categories:
        domain = list(raw_x)
    positions_by_label = {value: index for index, value in enumerate(domain)}
    prepared = []
    for series in series_list:
        xs = series.x_values or resolved.x_values or tuple(domain)
        if len(xs) != len(series.values) or (
                series.errors and len(series.errors) != len(series.values)):
            raise SpecError("length_mismatch", "每条序列的 X、Y 和误差必须逐点对齐，不能截短或广播。")
        if series.row_indices and len(series.row_indices) != len(series.values):
            raise SpecError("length_mismatch", "序列行身份必须与数据点逐点对齐。")
        try:
            values = np.asarray(series.values, dtype=float)
            errors = np.asarray([np.nan if value is None else value
                                 for value in series.errors], dtype=float)
        except (TypeError, ValueError):
            raise SpecError("invalid_number", "已解析的 Y 和误差必须是数值。") from None
        if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
            raise SpecError("invalid_number", "已解析的 Y 必须是非空的一维有限数值。")
        if errors.ndim != 1 or any(
                value is not None and (not np.isfinite(error) or error < 0)
                for value, error in zip(series.errors, errors)):
            raise SpecError("invalid_number", "误差必须是非负有限数值，缺少估计时使用 None。")
        positions = np.asarray(
            range(len(xs)) if row_categories else
            [positions_by_label[x] for x in xs] if categorical else xs, dtype=float)
        prepared.append((series, positions, values, errors))
    return prepared, domain, categorical


def _draw_errorbars(ax: Any, series: Series, positions: Any, values: Any,
                    errors: Any, colour: str, error_kind: str) -> Any | None:
    """Draw aligned points; None error estimates leave their points visible."""
    if error_kind == "none" or not series.errors:
        return None
    return ax.errorbar(
        positions, values, yerr=errors, label=series.name or None,
        color=colour, ecolor=colour, elinewidth=0.8, capsize=2.0,
        capthick=0.8, linewidth=1.2, markersize=3.5, marker="o",
        zorder=3,
    )


def build_resolved_figure(resolved: ResolvedFigure) -> RenderedFigure:
    """Render a resolved figure request into a Matplotlib figure.

    Raises :class:`SpecError` when the resolved data cannot produce the
    requested chart — for example a bar chart with no categories.
    """
    import matplotlib
    from src.figures.runtime import render_context

    with render_context():
        rendered = (_build_panels(resolved) if resolved.panels else _build_resolved_figure(resolved))
        rendered.qa_profile = dict(resolved.qa_profile)
        rendered.rc_params = dict(matplotlib.rcParams)
        return rendered


def _build_resolved_figure(resolved: ResolvedFigure, *, rendered=None, ax=None,
                           axes_index=0, prefix="axes_0", colour_indices=None) -> RenderedFigure:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import numpy as np

    # A CJK label needs a CJK-capable face; Matplotlib picks one font for
    # the whole figure and does not fall back per glyph.
    stack = font_stack(resolved.title, resolved.x_label, resolved.y_label,
                       "频数" if resolved.chart == "histogram" else "",
                       *[str(value) for value in (resolved.x_values or ())])
    apply_base_rcparams(resolved.style.font_size, stack)
    colours = palette_colors(resolved.style.palette)

    prepared, domain, categorical = ([], [], False)
    if resolved.chart in {"bar", "line", "scatter", "errorbar"}:
        prepared, domain, categorical = _plot_data(resolved)
    width, height = resolved.style.figure_size_in
    # A declarative render must not remain in pyplot's global figure manager.
    if rendered is None:
        fig = Figure(figsize=(width, height))
        FigureCanvasAgg(fig)
        ax = fig.subplots()
        rendered = RenderedFigure(figure=fig)
        rendered.axes.append(ax)
    else:
        fig = rendered.figure
    def series_gid(index):
        if resolved.panel_id:
            import hashlib
            name = (resolved.series[index].name if resolved.series else "") or "ungrouped"
            token = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
            return f"{prefix}.series_{token}"
        return _series_gid(axes_index, index)
    _register(rendered, "figure_1", fig, "figure", {"scope": "figure"})
    # The subplot itself is addressable: a whole axes block can be moved.
    _register(rendered, prefix, ax, "axes", {"axes_index": axes_index, "role": "axes"})

    chart = resolved.chart
    y_values = _numeric(resolved.y_values)

    if chart == "histogram":
        if not y_values:
            raise SpecError("empty_data", "直方图没有可用的数值。")
        ax.hist(y_values, bins=20, color=colours[0], edgecolor="white",
                linewidth=0.5)
        _set_axis_labels(ax, resolved)
        ax.set_ylabel(resolved.y_label or "频数")
    elif chart == "boxplot":
        if not resolved.series:
            raise SpecError("empty_data", "箱线图需要至少一组数据。")
        payload = []
        names = []
        for series in resolved.series:
            values = _numeric(series.values)
            if not values:
                continue
            payload.append(values)
            names.append(series.name)
        if not payload:
            raise SpecError("empty_data", "箱线图没有可用的数值。")
        ax.boxplot(payload, tick_labels=names, patch_artist=False,
                   widths=0.55, showmeans=True,
                   meanprops={"marker": "o", "markerfacecolor": "white",
                              "markeredgecolor": colours[0], "markersize": 4},
                   medianprops={"color": colours[0], "linewidth": 1.2},
                   whiskerprops={"color": colours[0], "linewidth": 0.8},
                   capprops={"color": colours[0], "linewidth": 0.8},
                   flierprops={"marker": "o", "markersize": 3,
                               "markerfacecolor": "none",
                               "markeredgecolor": colours[3], "alpha": 0.8})
        _set_axis_labels(ax, resolved)
    else:
        if chart not in {"bar", "line", "scatter", "errorbar"}:
            raise SpecError("unsupported_chart", f"不支持的图形类型「{chart}」。")
        for index, (series, positions, values, errors) in enumerate(prepared):
            colour = _apply_series_colour(ax, (colour_indices or {}).get(series.name, index), colours)
            if chart == "bar":
                width_each = 0.8 / len(prepared)
                offset = (index - (len(prepared) - 1) / 2) * width_each
                container = ax.bar(
                    positions + offset, values, width=width_each * 0.92,
                    yerr=errors if series.errors and resolved.error_kind != "none" else None,
                    label=series.name or None, color=colour,
                    ecolor=colour, capsize=2.0,
                    error_kw={"elinewidth": 0.8, "capthick": 0.8}, zorder=3)
                artist = container.patches
            elif chart in {"line", "errorbar"}:
                container = _draw_errorbars(
                    ax, series, positions, values, errors, colour, resolved.error_kind)
                if container is None:
                    artist, = ax.plot(
                        positions, values, label=series.name or None,
                        color=colour, linewidth=1.2, marker="o", markersize=3.5,
                        linestyle="-" if chart == "line" else "none", zorder=3)
                else:
                    # The series identity belongs to the central data line,
                    # never to the last cap line added by errorbar().
                    central, caps, intervals = container.lines
                    if chart == "errorbar":
                        central.set_linestyle("none")
                    artist = [central, *caps, *intervals]
            else:
                artist = ax.scatter(
                    positions, values, label=series.name or None, color=colour,
                    s=18, alpha=0.85, zorder=3, edgecolors="none")
            _register(rendered, series_gid(index), artist, "series", {
                "axes_index": axes_index, "role": "series", "series_index": index, "series_id": series_gid(index),
                **({"panel_id": resolved.panel_id} if resolved.panel_id else {}),
            })
            if chart == "bar" and container.errorbar is not None:
                central, caps, intervals = container.errorbar.lines
                rendered.locators[series_gid(index)]["error_members"] = [
                    member for member in (central, *caps, *intervals) if member is not None]
        if categorical:
            ax.set_xticks(np.arange(len(domain)))
            ax.set_xticklabels(_labels(domain))
        _set_axis_labels(ax, resolved)

    # Title, axis labels, legend and annotations all become addressable.
    if resolved.title:
        ax.set_title(resolved.title)
        _register(rendered, f"{prefix}.title", ax.title, "title", {
            "axes_index": axes_index, "role": "title"})
    if resolved.x_label:
        _register(rendered, f"{prefix}.xlabel", ax.xaxis.label, "xlabel", {
            "axes_index": axes_index, "role": "xlabel"})
    if resolved.y_label:
        _register(rendered, f"{prefix}.ylabel", ax.yaxis.label, "ylabel", {
            "axes_index": axes_index, "role": "ylabel"})

    handles, labels = ax.get_legend_handles_labels()
    if resolved.legend and handles:
        legend = ax.legend(handles, labels, loc="best", frameon=False,
                           borderpad=0.4, handlelength=1.6)
        _register(rendered, f"{prefix}.legend", legend, "legend", {
            "axes_index": axes_index, "role": "legend"})

    for index, annotation in enumerate(resolved.annotations):
        text = str(annotation.get("text") or "")
        if not text:
            continue
        artist = ax.annotate(
            text, xy=(annotation.get("x", 0), annotation.get("y", 0)),
            xycoords="axes fraction" if not isinstance(
                annotation.get("x"), (int, float)) else "data",
            fontsize=resolved.style.font_size, color="#272727")
        _register(rendered, f"{prefix}.annot_{index}", artist, "annot", {
            "axes_index": axes_index, "role": "annot", "annot_index": index})

    return rendered


def _build_panels(resolved: ResolvedFigure) -> RenderedFigure:
    from dataclasses import replace
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from src.figures.manifest import series_source

    layout = resolved.layout
    apply_base_rcparams(resolved.style.font_size, font_stack(resolved.title))
    fig = Figure(figsize=resolved.style.figure_size_in)
    FigureCanvasAgg(fig)
    rendered = RenderedFigure(figure=fig)
    grid = fig.add_gridspec(layout["rows"], layout["cols"],
                            wspace=layout["wspace"], hspace=layout["hspace"])
    names = sorted({series.name for panel in resolved.panels for series in panel.series})
    if resolved.shared_legend:
        signatures = {}
        for panel in resolved.panels:
            for series in panel.series:
                signature = (panel.chart, palette_colors(panel.style.palette))
                if series.name in signatures and signatures[series.name] != signature:
                    raise SpecError("incompatible_shared_legend", "共享图例的同名系列必须使用相同图型和配色合同。")
                signatures[series.name] = signature
    colours = palette_colors(resolved.style.palette)
    if resolved.shared_legend and len(names) > len(colours):
        raise SpecError("too_many_shared_series", "共享图例的组数超过可区分的固定配色，请拆分图件。")
    colour_indices = {name: index for index, name in enumerate(names)} if resolved.shared_legend else None
    for index, panel in enumerate(resolved.panels):
        first = rendered.axes[0] if rendered.axes else None
        apply_base_rcparams(panel.style.font_size, font_stack(panel.title, panel.x_label, panel.y_label))
        ax = fig.add_subplot(grid[index // layout["cols"], index % layout["cols"]],
                             sharex=first if layout["share_x"] else None,
                             sharey=first if layout["share_y"] else None)
        rendered.axes.append(ax)
        ax._qa_panel_id = panel.panel_id
        _build_resolved_figure(replace(panel, legend=False) if resolved.shared_legend else panel,
                               rendered=rendered, ax=ax, axes_index=index,
                               prefix="panel_" + panel.panel_id, colour_indices=colour_indices)
    if resolved.title:
        title = fig.suptitle(resolved.title)
        _register(rendered, "figure_1.title", title, "title", {"scope": "figure", "role": "title"})
    if resolved.shared_legend:
        handles, labels, groups = [], [], {}
        for gid, entry in rendered.locators.items():
            if entry.get("role") != "series":
                continue
            source = series_source(rendered, entry)
            label = str(source.get_label() or "")
            if not label or label.startswith("_"):
                continue
            if label not in labels:
                labels.append(label)
                handles.append(source)
            groups.setdefault(label, []).append((gid, source))
        if handles:
            legend = fig.legend(handles, labels, loc="lower center", frameon=False,
                                ncols=min(4, len(handles)))
            legend._qa_shared_groups = groups
            _register(rendered, "figure_1.legend", legend, "legend",
                      {"scope": "figure", "role": "legend"})
            fig.subplots_adjust(bottom=0.18)
    return rendered


def _last_data_artist(ax: Any, chart: str) -> Any | None:
    """Return the artist that represents the most recently drawn series.

    For a bar chart the meaningful artist is the group of patches that make
    up the bars, not the container: the container is a bookkeeping object
    without a gid of its own, while each patch carries one.
    """
    if chart == "bar":
        containers = list(ax.containers)
        if not containers:
            return None
        last = containers[-1]
        patches = getattr(last, "patches", None)
        if patches:
            return patches
        return last
    if chart in {"line", "errorbar"}:
        lines = list(ax.lines)
        return lines[-1] if lines else None
    if chart == "scatter":
        collections = list(ax.collections)
        return collections[-1] if collections else None
    return None


def compile_spec(resolved: ResolvedFigure) -> str:
    """Return the Python source that reproduces ``resolved``.

    The declarative path renders directly; this function exists so that the
    archived "script" for a declarative figure is real, runnable code rather
    than an opaque blob. Reproducing the figure from the archived script is
    what makes the archived result checkable.
    """
    return "\n".join([
        '"""Generated by CitrusSchalor figure engine — declarative figure."""',
        "import matplotlib",
        "matplotlib.use('Agg')",
        "from src.figures.core import build_resolved_figure",
        "from src.figures.spec import ResolvedFigure, Series, StyleSpec",
        f"payload = {resolved.as_dict()!r}",
        "def decode(payload):",
        "    payload['series'] = tuple(Series(**item) for item in payload['series'])",
        "    payload['style'] = StyleSpec(**payload['style'])",
        "    payload['panels'] = tuple(decode(item) for item in payload.get('panels', []))",
        "    return ResolvedFigure(**payload)",
        "rendered = build_resolved_figure(decode(payload))",
        "fig = rendered.figure",
        "ax = rendered.axes[0]",
        "fig.canvas.draw()",
        "",
    ])


@dataclass
class ScriptResult:
    """What running a figure script produced."""

    figure: Any | None = None
    error: str = ""
    error_type: str = ""
    stdout: str = ""
    figures_seen: int = 0


def capture_figure(script: str, *, namespace: dict[str, Any] | None = None,
                   on_figure: Callable[[Any], None] | None = None) -> ScriptResult:
    """Run a Matplotlib script and return the figure it produced.

    The script is executed with ``matplotlib`` in the Agg backend and
    ``pyplot.show`` neutered, so a script that ends with a ``show()`` call
    still yields a figure instead of blocking on a display.

    If the script creates several figures, the last one is returned — that is
    the one a script conventionally leaves on screen. The count is reported
    so the caller can tell the difference between "one figure" and "the third
    of five".
    """
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    # A free script sets its own text; the engine cannot know whether that
    # text needs a CJK-capable face before running it. Matplotlib does not
    # fall back glyph by glyph, so the stack is chosen from the script's own
    # source — the literals it will draw.
    apply_base_rcparams(font_size=7.0, font_stack_values=font_stack(script))
    plt.close("all")
    scope: dict[str, Any] = {"__name__": "__citrusfig__"}
    if namespace:
        scope.update(namespace)
    scope["plt"] = plt
    scope["matplotlib"] = matplotlib
    scope["show"] = lambda *args, **kwargs: None

    previous_show = plt.show
    plt.show = lambda *args, **kwargs: None
    stdout = io.StringIO()
    original_stdout = sys.stdout
    result = ScriptResult()
    try:
        sys.stdout = stdout
        compiled = compile(script, "<citrusfig>", "exec")
        exec(compiled, scope)  # noqa: S102 - confined to the sandbox worker
        result.stdout = stdout.getvalue()
        figures = [f for f in plt.get_fignums()]
        result.figures_seen = len(figures)
        if figures:
            result.figure = plt.figure(figures[-1])
            if on_figure is not None:
                on_figure(result.figure)
        else:
            result.error = "脚本没有产生任何图形。"
            result.error_type = "no_figure"
    except BaseException as exc:  # noqa: BLE001 - report any script failure
        result.stdout = stdout.getvalue()
        result.error = f"{type(exc).__name__}: {exc}"
        result.error_type = "script_error"
    finally:
        sys.stdout = original_stdout
        plt.show = previous_show
    return result


__all__ = [
    "RenderedFigure", "ScriptResult", "build_resolved_figure",
    "capture_figure", "compile_spec",
]

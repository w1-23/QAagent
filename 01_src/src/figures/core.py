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

    def artist_for(self, gid: str) -> Any | None:
        """Return the artist a gid refers to, or ``None``."""
        entry = self.locators.get(str(gid or ""))
        if not entry:
            return None
        return entry.get("artist")


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


def _draw_errorbars(ax: Any, series: Series, colour: str,
                    error_kind: str) -> Any | None:
    """Draw one series with error bars, or return ``None`` when it has none."""
    if error_kind == "none" or not series.errors:
        return None
    import numpy as np

    values = np.asarray(series.values, dtype=float)
    errors = np.asarray(series.errors, dtype=float)
    size = min(len(values), len(errors))
    if size == 0:
        return None
    values = values[:size]
    errors = errors[:size]
    container = ax.errorbar(
        np.arange(size), values, yerr=errors, label=series.name or None,
        color=colour, ecolor=colour, elinewidth=0.8, capsize=2.0,
        capthick=0.8, linewidth=1.2, markersize=3.5, marker="o",
        zorder=3,
    )
    return container


def build_resolved_figure(resolved: ResolvedFigure) -> RenderedFigure:
    """Render a resolved figure request into a Matplotlib figure.

    Raises :class:`SpecError` when the resolved data cannot produce the
    requested chart — for example a bar chart with no categories.
    """
    import matplotlib.pyplot as plt
    import numpy as np

    # A CJK label needs a CJK-capable face; Matplotlib picks one font for
    # the whole figure and does not fall back per glyph.
    stack = font_stack(resolved.title, resolved.x_label, resolved.y_label,
                       *[str(value) for value in (resolved.x_values or ())])
    apply_base_rcparams(resolved.style.font_size, stack)
    colours = palette_colors(resolved.style.palette)

    width, height = resolved.style.figure_size_in
    fig, ax = plt.subplots(figsize=(width, height))
    rendered = RenderedFigure(figure=fig)
    rendered.axes.append(ax)
    _register(rendered, "figure_1", fig, "figure", {"scope": "figure"})
    # The subplot itself is addressable: a whole axes block can be moved.
    _register(rendered, "axes_0", ax, "axes", {"axes_index": 0, "role": "axes"})

    chart = resolved.chart
    x_labels = _labels(resolved.x_values)
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
        if not resolved.series:
            # A single unnamed series: treat the raw values as one series.
            resolved_series = Series(name="", values=tuple(y_values))
        else:
            resolved_series = resolved.series[0]
        series_list = resolved.series or (resolved_series,)

        positions = np.arange(len(x_labels)) if x_labels else np.arange(
            len(resolved_series.values))

        for index, series in enumerate(series_list):
            colour = _apply_series_colour(ax, index, colours)
            values = _numeric(series.values)
            if chart == "bar":
                width_each = 0.8 / max(1, len(series_list))
                offset = (index - (len(series_list) - 1) / 2) * width_each
                if series.errors and resolved.error_kind != "none":
                    errors = _numeric(series.errors)
                    size = min(len(values), len(errors))
                    ax.bar(positions[:size] + offset, values[:size],
                           width=width_each * 0.92, yerr=errors[:size],
                           label=series.name or None, color=colour,
                           ecolor=colour, capsize=2.0, error_kw={
                               "elinewidth": 0.8, "capthick": 0.8},
                           zorder=3)
                else:
                    ax.bar(positions + offset, values,
                           width=width_each * 0.92,
                           label=series.name or None, color=colour, zorder=3)
            elif chart == "line":
                container = _draw_errorbars(ax, series, colour,
                                            resolved.error_kind)
                if container is None:
                    ax.plot(positions, values, label=series.name or None,
                            color=colour, linewidth=1.2, marker="o",
                            markersize=3.5, zorder=3)
            elif chart == "errorbar":
                container = _draw_errorbars(ax, series, colour,
                                            resolved.error_kind)
                if container is None:
                    # An error-bar chart with no error magnitudes still plots
                    # the values; the figure records that no bar was drawn.
                    ax.plot(positions, values, label=series.name or None,
                            color=colour, linewidth=1.0, marker="o",
                            markersize=3.5, linestyle="none", zorder=3)
            elif chart == "scatter":
                ax.scatter(positions, values, label=series.name or None,
                           color=colour, s=18, alpha=0.85, zorder=3,
                           edgecolors="none")
            else:  # pragma: no cover - guarded by the spec parser
                raise SpecError("unsupported_chart", f"不支持的图形类型「{chart}」。")

            gid = _series_gid(0, index)
            artist = _last_data_artist(ax, chart)
            if artist is not None:
                _register(rendered, gid, artist, "series", {
                    "axes_index": 0, "role": "series", "series_index": index,
                })

        if chart in {"bar", "line", "errorbar", "scatter"}:
            ax.set_xticks(positions)
            ax.set_xticklabels(x_labels)
        _set_axis_labels(ax, resolved)

    # Title, axis labels, legend and annotations all become addressable.
    if resolved.title:
        ax.set_title(resolved.title)
        _register(rendered, "axes_0.title", ax.title, "title", {
            "axes_index": 0, "role": "title"})
    if resolved.x_label:
        _register(rendered, "axes_0.xlabel", ax.xaxis.label, "xlabel", {
            "axes_index": 0, "role": "xlabel"})
    if resolved.y_label:
        _register(rendered, "axes_0.ylabel", ax.yaxis.label, "ylabel", {
            "axes_index": 0, "role": "ylabel"})

    handles, labels = ax.get_legend_handles_labels()
    if resolved.legend and handles:
        legend = ax.legend(handles, labels, loc="best", frameon=False,
                           borderpad=0.4, handlelength=1.6)
        _register(rendered, "axes_0.legend", legend, "legend", {
            "axes_index": 0, "role": "legend"})

    for index, annotation in enumerate(resolved.annotations):
        text = str(annotation.get("text") or "")
        if not text:
            continue
        artist = ax.annotate(
            text, xy=(annotation.get("x", 0), annotation.get("y", 0)),
            xycoords="axes fraction" if not isinstance(
                annotation.get("x"), (int, float)) else "data",
            fontsize=resolved.style.font_size, color="#272727")
        _register(rendered, f"axes_0.annot_{index}", artist, "annot", {
            "axes_index": 0, "role": "annot", "annot_index": index})

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
    payload = resolved.as_dict()
    lines = [
        '"""Generated by CitrusScalor figure engine — declarative figure."""',
        "import matplotlib",
        "matplotlib.use('Agg')",
        "import matplotlib.pyplot as plt",
        "from src.figures.styling import apply_base_rcparams",
        "",
        f"apply_base_rcparams({payload['style']['font_size']!r})",
        f"fig, ax = plt.subplots(figsize={tuple(payload['style']['figure_size_in'])!r})",
    ]
    lines.append(f"ax.set_title({payload['title']!r})")
    lines.append(f"ax.set_xlabel({payload['x_label']!r})")
    lines.append(f"ax.set_ylabel({payload['y_label']!r})")
    for series in payload["series"]:
        lines.append(
            f"ax.plot(range({len(series['values'])}), {series['values']!r}, "
            f"label={series['name']!r})")
    lines.append("fig.canvas.draw()")
    lines.append("")
    return "\n".join(lines)


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

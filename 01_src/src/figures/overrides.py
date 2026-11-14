"""User edits: the override allowlist and its applier.

An override is a small, closed record: *this object*, *this property*, *this
value*. The set of objects is the manifest, the set of properties is the
role's allowlist, and the value must fit the property's type and range.

This closure is a security property, not a convenience. The web editor sends
overrides from a browser; if the server accepted an arbitrary property name
it would be a remote code execution primitive, because Matplotlib setters
reach far beyond appearance (``set_pickle``-style hooks, ``set_transform``,
arbitrary attribute assignment). So the server decides what may change, and
the client only chooses among the options the server published.

What may not change
-------------------
Data. Bar heights, curve coordinates, error-bar lengths, axis ranges and tick
positions are all fixed by the computation that produced the figure. An
override that tried to change them would alter what the figure *says*, so
those properties do not exist in the vocabulary.

Applying an override happens on the Matplotlib artist, and the figure is then
re-rendered. The exported file is therefore a product of the same pipeline
that produced the preview — the two cannot drift apart.
"""
from __future__ import annotations

from typing import Any

from src.figures.core import RenderedFigure
from src.figures.manifest import (
    ALL_EDITABLE_PROPS, EDITABLE_BY_ROLE, TEXT_ROLES, FONT_WEIGHTS,
    available_font_families, find_by_locator, LINE_STYLE_PROPS, MARKERS, LINESTYLES,
    FONT_STYLES, HORIZONTAL_ALIGNMENTS, LEGEND_LOCS, series_source,
)

# Value rules, one per editable property.
_COLOR_PROPS = frozenset({"color", "markerfacecolor", "markeredgecolor"})
_OFFSET_LIMIT = 200.0
_MIN_FONTSIZE = 5.0
_MAX_FONTSIZE = 24.0
_MIN_LINEWIDTH = 0.2
_MAX_LINEWIDTH = 8.0


class OverrideError(ValueError):
    """An override that must not be applied."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code or "invalid_override")
        self.message = str(message)


def _normalise_color(value: Any) -> str:
    """Accept ``#rgb``/``#rrggbb`` and a small set of named colours."""
    if not isinstance(value, str):
        raise OverrideError("invalid_color", "颜色必须是文本。")
    text = value.strip().lower()
    named = {
        "black": "#000000", "white": "#ffffff", "red": "#b64342",
        "green": "#8bcf8b", "blue": "#0f4d92", "teal": "#42949e",
        "violet": "#9a4d8e", "gold": "#ffd700", "grey": "#767676",
        "gray": "#767676", "orange": "#d97706",
    }
    if text in named:
        return named[text]
    if len(text) == 4 and text.startswith("#"):
        if all(c in "0123456789abcdef" for c in text[1:]):
            return "#" + "".join(c * 2 for c in text[1:])
    if len(text) == 7 and text.startswith("#"):
        if all(c in "0123456789abcdef" for c in text[1:]):
            return text
    raise OverrideError(
        "invalid_color",
        f"颜色「{value}」无效；请使用 #RGB、#RRGGBB 或受支持的常见颜色名。")


def _normalise_offset(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise OverrideError(
            "invalid_offset", "位置偏移必须是两个数字 [dx, dy]。")
    out: list[float] = []
    for item in value:
        try:
            number = float(item)
        except (TypeError, ValueError):
            raise OverrideError(
                "invalid_offset", "位置偏移必须是两个数字 [dx, dy]。") from None
        if number != number or number in (float("inf"), float("-inf")):
            raise OverrideError("invalid_offset", "位置偏移必须是有限数值。")
        if abs(number) > _OFFSET_LIMIT:
            raise OverrideError(
                "offset_out_of_range",
                f"位置偏移超出 ±{_OFFSET_LIMIT:.0f} 像素范围。")
        out.append(number)
    return out


def _normalise_number(value: Any, low: float, high: float,
                      name: str) -> float:
    if isinstance(value, bool):
        raise OverrideError("invalid_number", f"{name} 必须是数字。")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise OverrideError("invalid_number", f"{name} 必须是数字。") from None
    if number != number or number in (float("inf"), float("-inf")):
        raise OverrideError("invalid_number", f"{name} 必须是有限数值。")
    if number < low or number > high:
        raise OverrideError(
            "value_out_of_range",
            f"{name} 必须在 {low}–{high} 之间（当前 {number}）。")
    return number


def _normalise_fontfamily(value: Any) -> str:
    from matplotlib import font_manager
    if not isinstance(value, str):
        raise OverrideError("invalid_font", "字体必须是已安装字体的家族名称。")
    installed = {name.casefold(): name for name in available_font_families()}
    family = installed.get(value.strip().casefold())
    if family is None:
        raise OverrideError("unavailable_font", f"字体「{value}」未安装，不能用替代字体假装应用成功。")
    try:
        path = font_manager.findfont(font_manager.FontProperties(family=[family]), fallback_to_default=False)
        actual = font_manager.FontProperties(fname=path).get_name()
        if actual.casefold() != family.casefold():
            raise ValueError("font family fallback")
    except ValueError:
        raise OverrideError("unavailable_font", f"字体「{family}」无法准确解析。") from None
    return family


def validate_overrides(raw: Any, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a batch of overrides against a manifest.

    The whole batch is rejected if any entry is invalid. A partial application
    would leave the figure in a state the user did not ask for and did not
    see, so the batch is all-or-nothing.

    Returns the normalised override list.
    """
    if raw is None or raw == "":
        return []
    if not isinstance(raw, (list, tuple)):
        raise OverrideError("invalid_batch", "修改内容必须是数组。")
    if len(raw) > 200:
        raise OverrideError(
            "too_many_overrides", "单次修改条目超过 200 条上限。")

    by_gid = {str(item.get("gid")): item for item in manifest.get("objects", [])}
    normalised: list[dict[str, Any]] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise OverrideError("invalid_entry", f"第 {index} 条修改不是对象。")
        gid = str(item.get("gid") or "").strip()
        prop = str(item.get("prop") or "").strip()
        value = item.get("value")

        if not gid:
            raise OverrideError("missing_gid", f"第 {index} 条修改缺少 gid。")
        if not prop:
            raise OverrideError("missing_prop", f"第 {index} 条修改缺少 prop。")
        if prop not in ALL_EDITABLE_PROPS:
            raise OverrideError(
                "unknown_prop",
                f"不允许修改属性「{prop}」；可修改的属性："
                f"{', '.join(sorted(ALL_EDITABLE_PROPS))}。")
        target = by_gid.get(gid)
        if target is None:
            raise OverrideError(
                "unknown_gid",
                f"图中没有对象「{gid}」；请重新打开图件后选择对象。")
        # Stored manifests may predate the current allowlist. Never let an old
        # capability (such as moving data series) bypass today's role policy.
        editable = (set(target.get("editable") or ())
                    & set(EDITABLE_BY_ROLE.get(str(target.get("role") or ""), ())))
        if prop not in editable:
            raise OverrideError(
                "prop_not_editable",
                f"对象「{gid}」（{target.get('role')}）不允许修改「{prop}」；"
                f"可修改：{', '.join(sorted(editable)) or '（无）'}。")

        if prop in _COLOR_PROPS:
            clean: Any = _normalise_color(value)
        elif prop == "fontsize":
            clean = _normalise_number(value, _MIN_FONTSIZE, _MAX_FONTSIZE, "字号")
        elif prop == "linewidth":
            clean = _normalise_number(value, _MIN_LINEWIDTH, _MAX_LINEWIDTH, "线宽")
        elif prop == "offset_px":
            clean = _normalise_offset(value)
        elif prop in {"text", "label"}:
            if (not isinstance(value, str) or len(value) > 2000
                    or any(ord(char) < 32 and char not in "\n\t" for char in value)
                    or any(0xD800 <= ord(char) <= 0xDFFF for char in value)):
                raise OverrideError("invalid_text", "文本必须是最多 2000 个字符的纯文本。")
            clean = value
        elif prop == "markeredgewidth":
            clean = _normalise_number(value, 0.0, 8.0, "标记边框宽度")
        elif prop == "alpha":
            clean = _normalise_number(value, 0.0, 1.0, "不透明度")
        elif prop == "ncols":
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 6:
                raise OverrideError("invalid_value", "图例列数必须是 1 到 6 的整数。")
            clean = value
        elif prop in {"fontstyle", "horizontalalignment", "loc"}:
            allowed = {"fontstyle": FONT_STYLES, "horizontalalignment": HORIZONTAL_ALIGNMENTS,
                       "loc": LEGEND_LOCS}[prop]
            if not isinstance(value, str) or value not in allowed:
                raise OverrideError("invalid_style", f"{prop} 不是支持的样式。")
            clean = value
        elif prop == "markersize":
            clean = _normalise_number(value, 1.0, 24.0, "标记大小")
        elif prop in {"marker", "linestyle"}:
            allowed = MARKERS if prop == "marker" else LINESTYLES
            if not isinstance(value, str) or value not in allowed:
                raise OverrideError("invalid_style", f"{prop} 不是支持的样式。")
            clean = value
        elif prop == "fontfamily":
            clean = _normalise_fontfamily(value)
        elif prop == "fontweight":
            if not isinstance(value, str) or value not in FONT_WEIGHTS:
                raise OverrideError("invalid_fontweight", "字重必须是支持的命名字重。")
            clean = value
        elif prop in {"frameon", "visible"}:
            if not isinstance(value, bool):
                raise OverrideError("invalid_value", f"{prop} 必须是布尔值。")
            clean = value
        else:  # pragma: no cover - the vocabulary is closed above
            raise OverrideError("unknown_prop", f"不允许修改属性「{prop}」。")
        normalised.append({"gid": gid, "prop": prop, "value": clean})
    return normalised


def _members(artist: Any) -> list[Any]:
    """The artists an override applies to.

    A series is often several artists — the patches of one bar group — and
    the change must reach all of them, or the series would end up half
    recoloured.
    """
    if isinstance(artist, (list, tuple)):
        return [member for item in artist for member in _members(item)]
    return [artist] if artist is not None else []


def _set_colour(artist: Any, role: str, colour: str) -> None:
    """Apply a colour to an artist of the given role."""
    for member in _members(artist):
        _set_colour_one(member, role, colour)


def _set_colour_one(artist: Any, role: str, colour: str) -> None:
    if artist is None:
        return
    if role == "legend":
        texts = getattr(artist, "get_texts", None)
        if callable(texts):
            for text in texts():
                text.set_color(colour)
        title = getattr(artist, "get_title", None)
        if callable(title):
            label = title()
            if label is not None:
                label.set_color(colour)
        return
    if role == "series":
        state = getattr(artist, "_qa_point_style_state", None)
        if state is not None:
            from matplotlib.colors import to_rgba
            from src.figures.core import ScatterPoint
            for which in ("face", "edge"):
                if len(state[which]):
                    state[which][:, :3] = to_rgba(colour)[:3]
            ScatterPoint(artist, 0)._apply()
            return
        setter = getattr(artist, "set_color", None)
        if callable(setter):
            setter(colour)
            # Errorbar caps are Line2D marker strokes with an explicit edge
            # colour; set_color alone only changes their otherwise empty line.
            marker = getattr(artist, "get_marker", lambda: None)()
            if isinstance(marker, str) and marker in {"_", "|"}:
                artist.set_markeredgecolor(colour)
            return
        face = getattr(artist, "set_facecolor", None)
        if callable(face):
            face(colour)
            return
        edge = getattr(artist, "set_edgecolor", None)
        if callable(edge):
            edge(colour)
            return
        raise OverrideError("unsupported_artist", "该数据对象不支持颜色修改。")
    setter = getattr(artist, "set_color", None)
    if callable(setter):
        setter(colour)
        return
    raise OverrideError("unsupported_artist", "该对象不支持颜色修改。")


def _set_linewidth(artist: Any, width: float) -> None:
    for member in _members(artist):
        _set_linewidth_one(member, width)


def _set_linewidth_one(artist: Any, width: float) -> None:
    if artist is None:
        return
    setter = getattr(artist, "set_linewidth", None)
    if callable(setter):
        setter(width)
        return
    lines = getattr(artist, "get_lines", None)
    if callable(lines):
        for line in lines():
            line.set_linewidth(width)
        return
    raise OverrideError("unsupported_artist", "该对象不支持线宽修改。")


def _offset_artist(artist: Any, role: str, offset: list[float]) -> None:
    """Translate by SVG viewBox units (points), positive y downwards.

    The legacy API name offset_px refers to manifest units, not screen pixels
    or data coordinates. The manifest canvas is 72 units per inch regardless
    of export DPI. Use physical transforms so PNG/PDF/SVG agree.
    """
    from matplotlib.transforms import Bbox, ScaledTranslation

    figure = artist.get_figure()
    dx, dy = offset
    if figure is None:
        raise OverrideError("missing_figure", "无法定位对象所属画布。")
    shift = ScaledTranslation(dx / 72.0, -dy / 72.0, figure.dpi_scale_trans)
    if role in {"title", "xlabel", "ylabel", "annot", "ticklabel"}:
        transform = artist.get_transform() + shift
        if role == "title" and artist.axes is not None:
            # An explicit y disables Matplotlib's automatic title collision
            # placement, which otherwise cancels a downward drag at draw time.
            alignment = artist.get_horizontalalignment()
            artist.axes.set_title(
                artist.get_text(), y=artist.get_position()[1], loc="center",
                fontproperties=artist.get_fontproperties().copy(), color=artist.get_color())
            artist.set_horizontalalignment(alignment)
        if role == "annot" and hasattr(artist, "set_anncoords"):
            artist.set_anncoords(transform)
        else:
            artist.set_transform(transform)
        return
    if role == "legend":
        bbox = artist.get_bbox_to_anchor()
        px = dx * figure.dpi / 72.0
        py = -dy * figure.dpi / 72.0
        translated = Bbox.from_bounds(bbox.x0 + px, bbox.y0 + py, bbox.width, bbox.height)
        artist.set_bbox_to_anchor(translated.transformed(figure.transFigure.inverted()),
                                  transform=figure.transFigure)
        return
    if role == "axes":
        position = artist.get_position()
        width, height = figure.get_size_inches()
        figure.set_layout_engine(None)
        artist.set_position([position.x0 + dx / (72.0 * width),
                             position.y0 - dy / (72.0 * height),
                             position.width, position.height])
        return
    raise OverrideError("unsupported_offset", "该对象不支持位置修改。")


def apply_overrides(rendered: RenderedFigure, manifest: dict[str, Any],
                    overrides: list[dict[str, Any]]) -> list[str]:
    """Apply validated overrides to a figure.

    Returns the list of gids actually changed. Missing or unsupported artists
    reject the render so no saved revision can silently omit requested edits.
    """
    changed: list[str] = []
    # A shared legend mark represents every listed panel series. Changing only
    # one of those series would make the legend false; require an explicit,
    # identical edit for the entire linked group instead of mutating other data
    # presentations behind the user's selection.
    edits = {(item["gid"], item["prop"]): item["value"] for item in overrides}
    for gids in manifest.get("shared_legend_groups", {}).values():
        for item in overrides:
            if item["gid"] in gids and any(edits.get((gid, item["prop"]), object()) != item["value"] for gid in gids):
                raise OverrideError("shared_legend_conflict", "共享图例组需要对所有关联序列提交相同样式修改。")
    legend_links = _capture_legend_links(rendered, manifest, overrides)
    # Whole-series styles apply before child styles, independent of JSON order.
    # A saved point/bar override therefore survives later series style updates.
    for override in sorted(overrides, key=lambda item: str(item.get("gid", "")).count(".")):
        gid = str(override.get("gid") or "")
        prop = str(override.get("prop") or "")
        value = override.get("value")
        entry = rendered.locators.get(gid)
        artist = entry.get("artist") if entry else None
        members = (list(entry.get("members") or []) + list(entry.get("error_members") or [])) if entry else []
        if entry and not entry.get("members") and entry.get("error_members"):
            members.insert(0, artist)
        if artist is None:
            locator = next((item.get("locator") for item in
                            manifest.get("objects", []) if item.get("gid") == gid), None)
            if locator:
                artist = find_by_locator(rendered, locator)
                members = [artist] if artist is not None else []
        if artist is None:
            raise OverrideError("unresolved_artist", f"无法重新定位对象「{gid}」，修改未保存。")
        role = str(entry.get("role") if entry else "") or _role_of(manifest, gid)
        if prop not in EDITABLE_BY_ROLE.get(role, ()):
            raise OverrideError("prop_not_editable", f"对象「{gid}」不支持该修改。")

        try:
            if prop == "color":
                _set_colour(members or artist, role, str(value))
            elif prop == "fontsize":
                for member in (members or [artist]):
                    _set_fontsize(member, float(value))
            elif prop in LINE_STYLE_PROPS:
                _set_marker_style(artist, prop, value)
            elif prop == "alpha":
                for member in _members(members or artist):
                    _set_marker_style(member, prop, value)
            elif prop == "text":
                _set_plain_text(artist, value)
            elif prop == "label":
                source = series_source(rendered, entry) if entry else artist
                source.set_label(value)
                source._qa_display_label = value
                # The displayed series name is metadata, never executable text.
            elif prop in {"fontfamily", "fontweight", "fontstyle", "horizontalalignment"}:
                font_value = _normalise_fontfamily(value) if prop == "fontfamily" else value
                for member in _members(members or artist):
                    texts = ([*member.get_texts(), member.get_title()] if role == "legend" else [member])
                    for text in texts:
                        getattr(text, "set_" + prop)(font_value)
            elif prop == "visible":
                for member in _members(members or artist):
                    member.set_visible(bool(value))
            elif prop == "linewidth":
                _set_linewidth(members or artist, float(value))
            elif prop == "offset_px":
                _offset_artist(artist, role, list(value))
            elif prop == "loc":
                artist.set_loc(value)
            elif prop == "ncols":
                _set_legend_columns(artist, int(value))
            elif prop == "frameon":
                setter = getattr(artist, "set_frame_on", None)
                if not callable(setter):
                    raise OverrideError("unsupported_artist", "该对象不支持边框修改。")
                setter(bool(value))
            else:  # pragma: no cover - the vocabulary is closed
                raise OverrideError("unknown_prop", "修改属性不受支持。")
        except OverrideError:
            raise
        except Exception as exc:
            raise OverrideError("apply_failed", f"对象「{gid}」修改失败，未保存。") from exc
        changed.append(gid)
    _sync_series_legends(legend_links, overrides)
    return changed


def _set_plain_text(artist: Any, value: str) -> None:
    # User strings are literal glyphs: never mathtext or external TeX programs.
    artist.set_usetex(False)
    artist.set_parse_math(False)
    artist.set_text(value)


def _set_marker_style(artist: Any, prop: str, value: Any) -> None:
    from matplotlib.collections import PathCollection
    from matplotlib.lines import Line2D
    from src.figures.core import ScatterPoint
    if isinstance(artist, PathCollection):
        artist = ScatterPoint(artist, slice(None))
    if prop != "alpha" and not isinstance(artist, (Line2D, ScatterPoint)):
        raise OverrideError("unsupported_artist", "该对象不支持标记样式。")
    setter = getattr(artist, "set_" + prop, None)
    if not callable(setter):
        raise OverrideError("unsupported_artist", "该对象不支持该样式。")
    setter(value)


def _legend_rows(legend: Any) -> list[Any]:
    return [row for column in legend._legend_handle_box.get_children()
            for row in column.get_children()]


def _set_legend_columns(legend: Any, count: int) -> None:
    """Repack existing handler rows, retaining marks and all edited text styles."""
    import numpy as np
    from matplotlib.offsetbox import VPacker
    rows = _legend_rows(legend)
    old_columns = legend._legend_handle_box.get_children()
    alignment = old_columns[0].align if old_columns else "baseline"
    columns = []
    for group in np.array_split(np.asarray(rows, dtype=object), min(count, len(rows)) or 1):
        if len(group):
            columns.append(VPacker(pad=0, sep=legend.labelspacing * legend._fontsize,
                                   align=alignment,
                                   children=list(group)))
    for column in columns:
        column.set_figure(legend.get_figure())
    legend._legend_handle_box._children = columns
    legend.set_ncols(count)
    legend.stale = True


def _capture_legend_links(rendered: RenderedFigure, manifest: dict[str, Any],
                          overrides: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Resolve original labels before mutations, never guess ambiguous aliases."""
    links = {}
    for gid in dict.fromkeys(str(item.get("gid", "")) for item in overrides):
        if _role_of(manifest, gid) != "series":
            continue
        entry = rendered.locators.get(gid)
        if entry is None:
            continue
        source = series_source(rendered, entry)
        ax = rendered.axes[int(entry.get("axes_index", 0))]
        legend = ax.get_legend()
        shared = next((candidate for candidate in rendered.figure.legends
                       if any(any(member is source for _, member in members)
                              for members in getattr(candidate, "_qa_shared_groups", {}).values())), None)
        if shared is not None:
            legend = shared
        if legend is None:
            continue
        # Identity links remain valid if apply_overrides is called twice on the
        # same figure after a rename (including an empty or underscore label).
        cached = getattr(legend, "_qa_series_links", {})
        if id(source) in cached:
            links[gid] = cached[id(source)]
            continue
        _, labels = ax.get_legend_handles_labels()
        label = source.get_label()
        texts = legend.get_texts()
        matches = [i for i, text in enumerate(texts) if text.get_text() == label]
        # An unlabelled source has no automatic legend entry. A custom legend
        # can nevertheless include it with an alias; never silently rename the
        # source while leaving that displayed alias untouched.
        if (not label or str(label).startswith("_")) and not matches:
            renaming = any(item.get("gid") == gid and item.get("prop") == "label" for item in overrides)
            custom = any(text.get_text() not in labels for text in texts)
            if renaming or custom:
                raise OverrideError("ambiguous_legend", "无法唯一关联数据系列与图例；请使用唯一的原始系列名称。")
            continue
        if labels.count(label) != 1 or len(matches) != 1:
            raise OverrideError("ambiguous_legend", "无法唯一关联数据系列与图例；请使用唯一的原始系列名称。")
        rows = _legend_rows(legend)
        if len(rows) != len(texts):
            raise OverrideError("ambiguous_legend", "无法定位图例标记，修改未保存。")
        row = rows[matches[0]]
        from matplotlib.offsetbox import DrawingArea
        areas = [child for child in row.get_children() if isinstance(child, DrawingArea)]
        if len(areas) != 1:
            raise OverrideError("ambiguous_legend", "无法定位图例标记，修改未保存。")
        links[gid] = {"source": source, "marks": list(areas[0].get_children()), "text": texts[matches[0]]}
        cached[id(source)] = links[gid]
        legend._qa_series_links = cached
    return links


def _sync_series_legends(links: dict[str, dict[str, Any]], overrides: list[dict[str, Any]]) -> None:
    """Apply identical series styles to copied handler marks, including errorbars."""
    from matplotlib.collections import PathCollection
    from matplotlib.container import ErrorbarContainer
    from matplotlib.lines import Line2D
    for item in overrides:
        link = links.get(str(item.get("gid", "")))
        if link is None:
            continue
        prop, value = item["prop"], item.get("value")
        source, marks, text = link["source"], link["marks"], link["text"]
        line_marks = [mark for mark in marks if isinstance(mark, Line2D)]
        marker_marks = (line_marks[-1:] if isinstance(source, ErrorbarContainer)
                        else [mark for mark in marks if isinstance(mark, (Line2D, PathCollection))])
        stroke_marks = line_marks[-2:-1] if isinstance(source, ErrorbarContainer) else line_marks
        if prop == "label":
            _set_plain_text(text, value)
        elif prop == "color":
            for mark in marks:
                _set_colour_one(mark, "series", value)
        elif prop == "linewidth":
            for mark in marks:
                _set_linewidth_one(mark, float(value))
        elif prop in LINE_STYLE_PROPS:
            for mark in (stroke_marks if prop == "linestyle" else marker_marks):
                _set_marker_style(mark, prop, value)
        elif prop == "alpha":
            for mark in marks:
                _set_marker_style(mark, prop, value)
        elif prop == "visible":
            for mark in marks:
                mark.set_visible(bool(value))
            text.set_visible(bool(value))


def _set_fontsize(artist: Any, size: float) -> None:
    setter = getattr(artist, "set_fontsize", None)
    if callable(setter):
        setter(size)
        return
    texts = getattr(artist, "get_texts", None)
    if callable(texts):
        for text in texts():
            text.set_fontsize(size)
        return
    raise OverrideError("unsupported_artist", "该对象不支持字号修改。")


def _role_of(manifest: dict[str, Any], gid: str) -> str:
    for item in manifest.get("objects", []):
        if item.get("gid") == gid:
            return str(item.get("role") or "")
    return ""


def merge_overrides(existing: list[dict[str, Any]],
                    incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge a new batch into the stored overrides, replacing per (gid, prop)."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in list(existing or []) + list(incoming or []):
        if not isinstance(item, dict):
            continue
        key = (str(item.get("gid") or ""), str(item.get("prop") or ""))
        if not key[0] or not key[1]:
            continue
        merged[key] = {"gid": key[0], "prop": key[1], "value": item.get("value")}
    return [merged[key] for key in sorted(merged)]


__all__ = [
    "ALL_EDITABLE_PROPS", "EDITABLE_BY_ROLE", "OverrideError",
    "apply_overrides", "merge_overrides", "validate_overrides",
]

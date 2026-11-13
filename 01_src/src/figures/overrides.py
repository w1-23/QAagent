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
    ALL_EDITABLE_PROPS, EDITABLE_BY_ROLE, TEXT_ROLES, find_by_locator,
)

# Value rules, one per editable property.
_COLOR_PROPS = frozenset({"color"})
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
        editable = set(target.get("editable") or ())
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
        elif prop == "frameon":
            if not isinstance(value, bool):
                raise OverrideError("invalid_value", "frameon 必须是布尔值。")
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
        return [item for item in artist if item is not None]
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
        setter = getattr(artist, "set_color", None)
        if callable(setter):
            setter(colour)
            return
        face = getattr(artist, "set_facecolor", None)
        if callable(face):
            face(colour)
            return
        edge = getattr(artist, "set_edgecolor", None)
        if callable(edge):
            edge(colour)
        return
    setter = getattr(artist, "set_color", None)
    if callable(setter):
        setter(colour)


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


def _offset_artist(artist: Any, role: str, offset: list[float]) -> None:
    """Move an artist inside the canvas.

    Text and annotations move by their own position; a legend moves by its
    anchor; a whole subplot moves by its axes position, which carries the
    curves, ticks and labels with it.
    """
    dx, dy = offset
    if role in {"title", "xlabel", "ylabel", "annot"}:
        try:
            x, y = artist.get_position()
        except Exception:
            return
        artist.set_position((x + dx, y + dy))
        return
    if role == "legend":
        try:
            bbox = artist.get_bbox_to_anchor()
            artist.set_bbox_to_anchor((bbox.x0 + dx, bbox.y0 + dy),
                                      transform=artist.get_bbox_to_anchor()
                                      .get_transform())
        except Exception:
            pass
        return
    if role == "axes":
        try:
            position = artist.get_position()
            artist.set_position([position.x0 + dx, position.y0 + dy,
                                 position.width, position.height])
        except Exception:
            pass


def apply_overrides(rendered: RenderedFigure, manifest: dict[str, Any],
                    overrides: list[dict[str, Any]]) -> list[str]:
    """Apply validated overrides to a figure.

    Returns the list of gids that were actually changed. A gid whose artist
    cannot be found in this figure is skipped and reported, rather than
    applied to whatever happens to occupy that position now.
    """
    changed: list[str] = []
    for override in overrides:
        gid = str(override.get("gid") or "")
        prop = str(override.get("prop") or "")
        value = override.get("value")
        entry = rendered.locators.get(gid)
        artist = entry.get("artist") if entry else None
        members = list(entry.get("members") or []) if entry else []
        if artist is None:
            locator = next((item.get("locator") for item in
                            manifest.get("objects", []) if item.get("gid") == gid), None)
            if locator:
                artist = find_by_locator(rendered, locator)
                members = [artist] if artist is not None else []
        if artist is None:
            continue
        role = str(entry.get("role") if entry else "") or _role_of(manifest, gid)

        try:
            if prop == "color":
                _set_colour(members or artist, role, str(value))
            elif prop == "fontsize":
                for member in (members or [artist]):
                    _set_fontsize(member, float(value))
            elif prop == "linewidth":
                _set_linewidth(members or artist, float(value))
            elif prop == "offset_px":
                _offset_artist(artist, role, list(value))
            elif prop == "frameon":
                setter = getattr(artist, "set_frame_on", None)
                if callable(setter):
                    setter(bool(value))
            else:  # pragma: no cover - the vocabulary is closed
                continue
        except Exception:
            continue
        changed.append(gid)
    return changed


def _set_fontsize(artist: Any, size: float) -> None:
    setter = getattr(artist, "set_fontsize", None)
    if callable(setter):
        setter(size)
        return
    texts = getattr(artist, "get_texts", None)
    if callable(texts):
        for text in texts():
            text.set_fontsize(size)


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

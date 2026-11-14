"""Validated figure requests.

Two shapes live here.

``FigureSpec``
    What the model or a user asks for. It carries *references* to data
    (an attachment slot, an account-library file, or an inline table) plus
    presentation settings. It is validated on arrival: an unknown chart type,
    an unknown field, an out-of-range size or an over-long inline table is
    rejected with a specific reason rather than silently defaulted.

``ResolvedFigure``
    What the renderer actually plots: every reference has been replaced by
    plain numbers and category labels, aggregated by the service layer. The
    renderer never touches a file, a spreadsheet or an account.

Validation is fail-closed. A request that is not understood is refused; it is
never repaired by guessing.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Chart types the declarative path can render. The free-script path is not
# limited to this list, but it cannot promise the semantic manifest.
CHART_TYPES = ("bar", "line", "scatter", "errorbar", "boxplot", "histogram")

# Where a data column comes from.
REF_KINDS = ("attachment", "account_file", "inline")

# Error-bar kinds. "none" means the request explicitly asked for no bars.
ERROR_KINDS = ("sd", "se", "ci95", "none")

# Inline tables are a convenience for small results, not a data channel.
_MAX_INLINE_ROWS = 200
_MAX_INLINE_COLS = 20
_MAX_TITLE_CHARS = 200
_MAX_LABEL_CHARS = 120
_MAX_ANNOTATIONS = 20

_FIGURE_SIZE_MIN = (1.5, 1.2)
_FIGURE_SIZE_MAX = (12.0, 12.0)
_FONT_SIZE_MIN = 5.0
_FONT_SIZE_MAX = 24.0
_DPI_MIN = 72
_DPI_MAX = 1200


class SpecError(ValueError):
    """A figure request that cannot be rendered as written.

    ``code`` is a stable machine-readable reason so the caller can report it
    to the model in a form the model can act on.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code or "invalid_spec")
        self.message = str(message)


@dataclass(frozen=True)
class ColumnRef:
    """One data column, addressed by where it lives and what it is called."""

    kind: str = ""                 # attachment | account_file | inline
    column: str = ""               # column name for attachment/account_file
    index: int = 0                 # attachment slot for "attachment"
    file_id: str = ""              # account-library file for "account_file"
    values: tuple[Any, ...] = ()   # inline values for "inline"

    def describe(self) -> str:
        if self.kind == "attachment":
            return f"附件 {self.index} 的「{self.column}」列"
        if self.kind == "account_file":
            return f"资料库文件 {self.file_id[:8]}… 的「{self.column}」列"
        if self.kind == "inline":
            return f"内联列「{self.column or 'values'}」"
        return "未指定的数据列"


@dataclass(frozen=True)
class StyleSpec:
    """Presentation defaults, before any user override is applied."""

    palette: str = "nature"
    font_size: float = 7.0
    figure_size_in: tuple[float, float] = (7.0, 5.0)
    dpi: int = 300
    palette_colors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "palette": self.palette,
            "font_size": self.font_size,
            "figure_size_in": list(self.figure_size_in),
            "dpi": self.dpi,
        }


@dataclass(frozen=True)
class FigureSpec:
    """A validated declarative figure request."""

    chart: str = "bar"
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    x: ColumnRef | None = None
    y: ColumnRef | None = None
    group: ColumnRef | None = None
    error: str = "none"
    weight: ColumnRef | None = None
    legend: bool = True
    annotations: tuple[dict[str, Any], ...] = ()
    style: StyleSpec = field(default_factory=StyleSpec)
    name: str = ""
    panel_id: str = ""
    panels: tuple[FigureSpec, ...] = ()
    layout: dict[str, Any] = field(default_factory=dict)
    shared_legend: bool = False
    qa_profile: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Serialisable form, used for the archived spec and for re-rendering."""

        def _ref(ref: ColumnRef | None) -> dict[str, Any] | None:
            if ref is None:
                return None
            return {
                "kind": ref.kind, "column": ref.column, "index": ref.index,
                "file_id": ref.file_id,
                "values": list(ref.values) if ref.kind == "inline" else [],
            }

        if self.panels:
            return {"title": self.title, "name": self.name, "style": self.style.as_dict(),
                    "panels": [p.as_dict() for p in self.panels], "layout": dict(self.layout),
                    "shared_legend": self.shared_legend,
                    **({"qa_profile": dict(self.qa_profile)} if self.qa_profile else {})}
        return {
            "chart": self.chart,
            "title": self.title,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "x": _ref(self.x),
            "y": _ref(self.y),
            "group": _ref(self.group),
            "error": self.error,
            "weight": _ref(self.weight),
            "legend": self.legend,
            "annotations": [dict(a) for a in self.annotations],
            "style": self.style.as_dict(),
            "name": self.name,
            **({"panel_id": self.panel_id} if self.panel_id else {}),
            **({"panels": [p.as_dict() for p in self.panels],
                "layout": dict(self.layout), "shared_legend": self.shared_legend}
               if self.panels else {}),
            **({"qa_profile": dict(self.qa_profile)} if self.qa_profile else {}),
        }


@dataclass(frozen=True)
class Series:
    """One drawn series: a name, its values, and optional error magnitudes.

    Errors are magnitudes, not bounds: the renderer draws them symmetrically
    around the value. The kind (``sd``/``se``/``ci95``) is carried on the
    figure, not per series, so one figure has one error convention. A None
    magnitude means insufficient replicates; it must not drop the data point.
    """

    name: str = ""
    values: tuple[float, ...] = ()
    errors: tuple[float | None, ...] = ()
    # Empty x_values retains the legacy shared-axis contract. Explicit values
    # allow sparse groups without padding, truncation or NumPy broadcasting.
    x_values: tuple[Any, ...] = ()
    # Original zero-based row ordinals contributing to each plotted value.
    row_indices: tuple[tuple[int, ...], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "values": list(self.values),
                "errors": list(self.errors), "x_values": list(self.x_values),
                "row_indices": [list(rows) for rows in self.row_indices]}


@dataclass(frozen=True)
class ResolvedFigure:
    """A figure request whose data has been resolved into plain values.

    For paired charts, one series has matching top-level X/Y convenience
    columns. With multiple series, x_values is the shared domain, y_values
    is empty and each series carries its own coordinates. Distribution
    charts use y_values for raw observations.
    """

    chart: str = "bar"
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    x_name: str = ""
    y_name: str = ""
    group_name: str = ""
    x_values: tuple[Any, ...] = ()
    y_values: tuple[float, ...] = ()
    error_kind: str = "none"
    series: tuple[Series, ...] = ()
    legend: bool = True
    annotations: tuple[dict[str, Any], ...] = ()
    style: StyleSpec = field(default_factory=StyleSpec)
    panel_id: str = ""
    panels: tuple[ResolvedFigure, ...] = ()
    layout: dict[str, Any] = field(default_factory=dict)
    shared_legend: bool = False
    qa_profile: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chart": self.chart,
            "title": self.title,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "x_name": self.x_name,
            "y_name": self.y_name,
            "group_name": self.group_name,
            "x_values": list(self.x_values),
            "y_values": list(self.y_values),
            "error_kind": self.error_kind,
            "series": [s.as_dict() for s in self.series],
            "legend": self.legend,
            "annotations": [dict(a) for a in self.annotations],
            "style": self.style.as_dict(),
            **({"panel_id": self.panel_id} if self.panel_id else {}),
            **({"panels": [p.as_dict() for p in self.panels],
                "layout": dict(self.layout), "shared_legend": self.shared_legend}
               if self.panels else {}),
            **({"qa_profile": dict(self.qa_profile)} if self.qa_profile else {}),
        }


def _text(value: Any, limit: int, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SpecError("invalid_field_type", f"{field_name} 必须是文本。")
    cleaned = value.strip()
    if len(cleaned) > limit:
        raise SpecError(
            "field_too_long",
            f"{field_name} 超过 {limit} 字符上限（当前 {len(cleaned)}）。")
    return cleaned


def _number(value: Any, low: float, high: float, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SpecError("invalid_number", f"{field_name} 必须是数字。") from None
    if not math.isfinite(number):
        raise SpecError("invalid_number", f"{field_name} 必须是有限数值。")
    if number < low or number > high:
        raise SpecError(
            "value_out_of_range",
            f"{field_name} 必须在 {low}–{high} 之间（当前 {number}）。")
    return number


def _parse_ref(raw: Any, field_name: str) -> ColumnRef | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, dict):
        raise SpecError("invalid_field_type", f"{field_name} 必须是对象。")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in REF_KINDS:
        raise SpecError(
            "invalid_ref_kind",
            f"{field_name}.kind 只能是 {', '.join(REF_KINDS)} 之一。")
    column = _text(raw.get("column"), _MAX_LABEL_CHARS, f"{field_name}.column")
    index = raw.get("index", 0)
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise SpecError("invalid_ref_index", f"{field_name}.index 必须是整数。") from None
    if index < 0 or index > 15:
        raise SpecError("invalid_ref_index", f"{field_name}.index 必须在 0–15 之间。")
    file_id = _text(raw.get("file_id"), 64, f"{field_name}.file_id")
    values: tuple[Any, ...] = ()
    if kind == "inline":
        raw_values = raw.get("values")
        if not isinstance(raw_values, (list, tuple)):
            raise SpecError("invalid_inline_values", f"{field_name}.values 必须是数组。")
        if len(raw_values) > _MAX_INLINE_ROWS:
            raise SpecError(
                "inline_too_large",
                f"{field_name}.values 超过 {_MAX_INLINE_ROWS} 行上限；"
                "大数据请上传表格后按列引用。")
        values = tuple(raw_values)
        if not column:
            column = "values"
    if kind == "attachment" and not column:
        raise SpecError("missing_column", f"{field_name} 需要指定 column。")
    if kind == "account_file" and not (column and file_id):
        raise SpecError("missing_column", f"{field_name} 需要同时指定 file_id 与 column。")
    return ColumnRef(kind=kind, column=column, index=index, file_id=file_id, values=values)


def _parse_style(raw: Any) -> StyleSpec:
    if raw is None or raw == "":
        return StyleSpec()
    if not isinstance(raw, dict):
        raise SpecError("invalid_field_type", "style 必须是对象。")
    palette = _text(raw.get("palette"), 32, "style.palette") or "nature"
    font_size = _number(
        raw.get("font_size", 7.0), _FONT_SIZE_MIN, _FONT_SIZE_MAX, "style.font_size")
    size_raw = raw.get("figure_size_in", [7.0, 5.0])
    if not isinstance(size_raw, (list, tuple)) or len(size_raw) != 2:
        raise SpecError("invalid_field_type", "style.figure_size_in 必须是两个数字。")
    width = _number(size_raw[0], _FIGURE_SIZE_MIN[0], _FIGURE_SIZE_MAX[0], "style.figure_size_in[0]")
    height = _number(size_raw[1], _FIGURE_SIZE_MIN[1], _FIGURE_SIZE_MAX[1], "style.figure_size_in[1]")
    try:
        dpi = int(raw.get("dpi", 300))
    except (TypeError, ValueError):
        raise SpecError("invalid_number", "style.dpi 必须是整数。") from None
    if dpi < _DPI_MIN or dpi > _DPI_MAX:
        raise SpecError(
            "value_out_of_range",
            f"style.dpi 必须在 {_DPI_MIN}–{_DPI_MAX} 之间（当前 {dpi}）。")
    return StyleSpec(
        palette=palette, font_size=font_size,
        figure_size_in=(width, height), dpi=dpi)


def _parse_annotations(raw: Any) -> tuple[dict[str, Any], ...]:
    if raw is None or raw == "":
        return ()
    if not isinstance(raw, (list, tuple)):
        raise SpecError("invalid_field_type", "annotations 必须是数组。")
    if len(raw) > _MAX_ANNOTATIONS:
        raise SpecError(
            "too_many_annotations",
            f"annotations 超过 {_MAX_ANNOTATIONS} 条上限。")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise SpecError("invalid_field_type", "annotations 的每一项必须是对象。")
        text = _text(item.get("text"), _MAX_LABEL_CHARS, "annotation.text")
        if not text:
            continue
        out.append({"x": item.get("x", ""), "y": item.get("y", ""), "text": text})
    return tuple(out)


def _parse_layout(raw: Any, count: int) -> dict[str, Any]:
    raw = {} if raw is None else raw
    allowed = {"rows", "cols", "share_x", "share_y", "wspace", "hspace"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise SpecError("invalid_layout", "layout 含有未知字段。")
    out = {}
    for key, default in (("cols", min(count, 2)), ("rows", None)):
        value = raw.get(key, default if default is not None else math.ceil(count / out["cols"]))
        if type(value) is not int or not 1 <= value <= 12:
            raise SpecError("invalid_layout", f"layout.{key} 必须是 1–12 的整数。")
        out[key] = value
    if out["rows"] * out["cols"] < count or out["rows"] * out["cols"] > 16:
        raise SpecError("invalid_layout", "layout 网格不足或超过 16 个位置。")
    for key in ("share_x", "share_y"):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise SpecError("invalid_layout", f"layout.{key} 必须是布尔值。")
        out[key] = value
    for key in ("wspace", "hspace"):
        out[key] = _number(raw.get(key, 0.35), 0, 2, "layout." + key)
    return out


def parse_qa_profile(raw: Any) -> dict[str, Any]:
    """Explicit project QA thresholds; names do not imply journal certification."""
    if raw is None or raw == "":
        return {}
    allowed = {"name", "final_size_mm", "min_font_pt", "min_linewidth_pt", "caption"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise SpecError("invalid_profile", "qa_profile 含有未知字段。")
    name = str(raw.get("name") or "scientific")
    widths = {"scientific": None, "nature_single": 89.0, "nature_double": 183.0}
    if name not in widths:
        raise SpecError("invalid_profile", "支持 scientific/nature_single/nature_double 配置。")
    out = {"name": name, "min_font_pt": _number(raw.get("min_font_pt", 5), 1, 24, "min_font_pt"),
           "min_linewidth_pt": _number(raw.get("min_linewidth_pt", 0.5), 0.1, 4, "min_linewidth_pt"),
           "caption": _text(raw.get("caption"), 4000, "caption")}
    size = raw.get("final_size_mm")
    if size is not None:
        if not isinstance(size, (list, tuple)) or len(size) != 2:
            raise SpecError("invalid_profile", "final_size_mm 需要宽和高。")
        out["final_size_mm"] = [_number(v, 20, 400, "final_size_mm") for v in size]
    return out


def parse_spec(raw: Any) -> FigureSpec:
    """Validate a declarative figure request.

    Raises :class:`SpecError` with a stable ``code`` for every rejected
    request. Unknown top-level keys are rejected rather than ignored: a
    request the caller meant to be read differently must not be rendered as
    something else.
    """
    if not isinstance(raw, dict):
        raise SpecError("invalid_field_type", "图规格必须是对象。")
    allowed = {
        "chart", "title", "x_label", "y_label", "x", "y", "group", "error",
        "weight", "legend", "annotations", "style", "name", "panels", "panel_id",
        "layout", "shared_legend", "qa_profile",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SpecError(
            "unknown_field",
            f"图规格含有未知字段：{', '.join(unknown)}。"
            f"可用字段：{', '.join(sorted(allowed))}。")

    panel_id = _text(raw.get("panel_id"), 48, "panel_id")
    if panel_id:
        import re
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", panel_id):
            raise SpecError("invalid_panel_id", "panel_id 必须是以字母开头的稳定标识。")
    profile = parse_qa_profile(raw.get("qa_profile"))
    panels_raw = raw.get("panels")
    if panels_raw is not None:
        if not isinstance(panels_raw, list) or not 1 <= len(panels_raw) <= 12:
            raise SpecError("invalid_panels", "panels 必须包含 1–12 个子图。")
        if any(key in raw for key in ("x", "y", "group", "weight", "chart", "error", "annotations", "panel_id")):
            raise SpecError("ambiguous_panels", "多子图的数据和图型必须分别写入各 panel。")
        panels = []
        for item in panels_raw:
            if not isinstance(item, dict) or "panels" in item or not item.get("panel_id"):
                raise SpecError("invalid_panels", "每个子图需要唯一 panel_id，且不能嵌套 panels。")
            if any(key in item for key in ("qa_profile", "layout", "shared_legend")):
                raise SpecError("invalid_panels", "layout、shared_legend 和 qa_profile 只允许在整图定义。")
            panels.append(parse_spec({"style": raw.get("style"), **item}))
        if len({p.panel_id for p in panels}) != len(panels):
            raise SpecError("duplicate_panel_id", "panel_id 不能重复。")
        layout = _parse_layout(raw.get("layout"), len(panels))
        shared = raw.get("shared_legend", False)
        if not isinstance(shared, bool):
            raise SpecError("invalid_field_type", "shared_legend 必须是布尔值。")
        return FigureSpec(title=_text(raw.get("title"), _MAX_TITLE_CHARS, "title"),
                          name=_text(raw.get("name"), _MAX_TITLE_CHARS, "name"),
                          style=_parse_style(raw.get("style")), panels=tuple(panels),
                          layout=layout, shared_legend=shared, qa_profile=profile)
    if "layout" in raw or "shared_legend" in raw:
        raise SpecError("invalid_layout", "layout/shared_legend 需要 panels。")
    chart = str(raw.get("chart") or "bar").strip().lower()
    if chart not in CHART_TYPES:
        raise SpecError(
            "unsupported_chart",
            f"不支持的图形类型「{chart}」；可用：{', '.join(CHART_TYPES)}。")

    error = str(raw.get("error") or "none").strip().lower()
    if error not in ERROR_KINDS:
        raise SpecError(
            "unsupported_error_kind",
            f"不支持的误差类型「{error}」；可用：{', '.join(ERROR_KINDS)}。")

    legend = raw.get("legend", True)
    if not isinstance(legend, bool):
        raise SpecError("invalid_field_type", "legend 必须是布尔值。")

    spec = FigureSpec(
        chart=chart,
        title=_text(raw.get("title"), _MAX_TITLE_CHARS, "title"),
        x_label=_text(raw.get("x_label"), _MAX_LABEL_CHARS, "x_label"),
        y_label=_text(raw.get("y_label"), _MAX_LABEL_CHARS, "y_label"),
        x=_parse_ref(raw.get("x"), "x"),
        y=_parse_ref(raw.get("y"), "y"),
        group=_parse_ref(raw.get("group"), "group"),
        error=error,
        weight=_parse_ref(raw.get("weight"), "weight"),
        legend=legend,
        annotations=_parse_annotations(raw.get("annotations")),
        style=_parse_style(raw.get("style")),
        name=_text(raw.get("name"), _MAX_TITLE_CHARS, "name"),
        panel_id=panel_id, qa_profile=profile,
    )
    if spec.weight is not None:
        raise SpecError("unsupported_weight", "暂不支持加权绘图；请先按明确的权重定义汇总数据。")
    if spec.chart in {"bar", "line", "errorbar", "boxplot", "scatter", "histogram"}:
        if spec.chart == "histogram":
            if spec.y is None:
                raise SpecError("missing_column", "直方图需要 y 列。")
        elif spec.y is None:
            raise SpecError("missing_column", f"{spec.chart} 图需要 y 列。")
        if spec.chart in {"bar", "line", "errorbar", "scatter"} and spec.x is None:
            raise SpecError("missing_column", f"{spec.chart} 图需要 x 列。")
    if spec.error != "none" and spec.chart not in {"bar", "errorbar", "line"}:
        raise SpecError(
            "unsupported_error_kind",
            f"{spec.chart} 图不支持误差棒。")
    return spec


__all__ = [
    "CHART_TYPES", "ERROR_KINDS", "REF_KINDS", "ColumnRef", "FigureSpec",
    "ResolvedFigure", "Series", "SpecError", "StyleSpec", "parse_spec",
]

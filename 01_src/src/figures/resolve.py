"""Turning a figure specification into numbers the renderer can plot.

A specification says *which* data to plot; the renderer needs the numbers
themselves. This module is the step between: it resolves each reference,
aggregates the rows where a grouping calls for it, and produces a
:class:`~src.figures.spec.ResolvedFigure` that contains no file handles, no
column names and no notion of an account.

Why aggregation happens here
-----------------------------
A figure usually shows a *summary* of many rows: the mean fruit weight per
treatment with an error bar, not three hundred overlapping points. The
aggregation is therefore part of the figure's definition, and it happens in
one place with one definition of what "the error bar" means. The renderer
draws what it is told; it never decides how to summarise.

The error-bar convention
------------------------
``error`` names what the bar is: ``sd`` (standard deviation of the group),
``se`` (standard error of the mean) or ``ci95`` (the 95% confidence
interval of the mean). The value is computed from the group's own rows, so
the bar always describes the data it sits on. A caller that asks for ``se``
gets the standard error and nothing else — the engine does not silently
substitute a different quantity, because a figure whose error bar means
something other than what the caption says is worse than no figure.

Where the rows come from
------------------------
* ``attachment`` — this turn's uploads, read through the service's own
  table reader, so the existing size and cell budgets apply unchanged.
* ``account_file`` — a file in the account's library, fetched with the
  ownership check the library already performs.
* ``inline`` — the values in the request itself, for small results.

Nothing else is readable. A reference that names a path, a URL or a file id
the caller does not own is refused.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_TABLES: ContextVar[dict | None] = ContextVar("figure_render_tables", default=None)


@contextmanager
def table_resolution_scope(seed: dict | None = None):
    """Reuse parsed tables only within one render and its recursive panels."""
    if _TABLES.get() is not None:
        yield
        return
    token = _TABLES.set(dict(seed or {}))
    try:
        yield
    finally:
        _TABLES.reset(token)

from src.figures.spec import ColumnRef, FigureSpec, ResolvedFigure, Series, SpecError

# The maximum number of rows an inline table may carry.
MAX_INLINE_ROWS = 200

# The maximum number of groups a figure may show. A figure with hundreds of
# bars is not a figure, it is a table.
MAX_GROUPS = 60


def table_figure_guidance(frame: Any) -> dict[str, Any]:
    """Describe shape, not scientific roles; inspect every parsed row.

    A unique label is evidence of a possible identifier, not a treatment.
    Numeric identifiers cannot reliably be distinguished from measurements
    without the column's meaning, so they remain explicit selection choices.
    No values or identifiers are included in this model-facing profile.
    """
    columns = []
    for column in frame.columns:
        present = [value for value in frame[column] if not _missing(value)]
        numbers = _numeric(present)
        unique = len({str(value) for value in present})
        numeric = bool(present) and len(numbers) == len(present)
        possible_identifier = bool(present) and unique >= 4 and unique / len(present) >= 0.8
        columns.append({
            "column": str(column), "non_missing": len(present),
            "missing": len(frame) - len(present), "unique": unique,
            "numeric": numeric, "possible_identifier": possible_identifier,
            "role": "requires_semantic_confirmation" if numeric or possible_identifier else "category_candidate",
        })
    return {
        "rows": len(frame), "columns": columns,
        "selection_policy": "Choose variables and form from the question and units; never infer treatment or time from identifiers.",
        "guidance": [
            "单个数值指标可用分布图；低基数且有重复观测的真实组别可用箱线图并展示原始点。",
            "两数值变量的关系在含义确认后用散点；仅有真实时间/剂量顺序时连线。",
            "多指标或矩阵先明确研究问题、单位及变换；可分面或用带明确色标的热图，不默认取第一列。",
            "标识符不能默认作处理组；不要逐个标注几十个样本，也不要静默取前 N 行或按值筛选。",
        ],
    }


def select_table_figure(frame: Any, *, file_id: str, group_column: str = "") -> dict[str, Any]:
    """Conservative library-import default; ambiguity is a selection request.

    Imports have no research question. A distribution of the sole metric is
    defensible; choosing one of several metrics or inventing an X relationship
    is not. Even repeated text may identify subjects rather than treatments;
    grouped distributions require an explicitly confirmed group column.
    """
    profile = table_figure_guidance(frame)
    if group_column and group_column not in frame.columns:
        raise SpecError("figure_selection_required", "指定分组列不存在；请确认真实组别的列名。")
    numeric = [item["column"] for item in profile["columns"]
               if item["numeric"] and item["column"] != group_column]
    if not numeric:
        raise SpecError("figure_selection_required", "没有完整可解析的数值指标；请确认列、表头和缺失/非数值单元格的处理。")
    if len(numeric) != 1:
        raise SpecError("figure_selection_required",
                        f"检测到 {len(numeric)} 个数值列，不能默认取第一列或把编号当测量值。"
                        "请明确要比较的指标与单位：单指标分布、两个已确认变量的散点、真实时间趋势，或矩阵热图。")
    metric = numeric[0]
    if group_column:
        counts: dict[str, int] = {}
        for value in frame[group_column]:
            if _missing(value):
                raise SpecError("figure_selection_required", "分组列存在缺失；请明确缺失处理后再创建分组图。")
            counts[str(value)] = counts.get(str(value), 0) + 1
        if not 2 <= len(counts) <= 8 or min(counts.values()) < 2:
            raise SpecError("figure_selection_required", "分组箱线图需要 2–8 个明确组别且每组至少 2 行；请确认实验单位及合适图型。")
    def ref(column: str) -> dict[str, str]:
        return {"kind": "account_file", "file_id": file_id, "column": column}
    spec: dict[str, Any] = {
        "chart": "histogram", "y": ref(metric),
        "x_label": metric, "y_label": "Count", "legend": False,
        "error": "none",
    }
    if group_column:
        spec.update(chart="boxplot", group=ref(group_column), x_label=group_column, y_label=metric)
    return spec


def _attachment_table(index: int) -> Any:
    """Read this turn's uploaded table."""
    from src.research.tools import _load_frame
    cache = _TABLES.get()
    key = ("attachment", int(index))
    if cache is not None and key in cache:
        return cache[key]
    frame, meta = _load_frame(int(index), "", 1, None)
    if meta.get("truncated"):
        raise SpecError("truncated_source", "表格读取达到解析预算，未对截断数据出图；请明确提供完整的目标数据范围。")
    if cache is not None:
        cache[key] = frame
    return frame


def _account_table(file_id: str, owner_user_id: int | None = None) -> Any:
    """Read a table from the account library, with the ownership check.

    The owner is taken from the request context when the caller does not name
    one. The workspace passes its own, because it runs outside a chat turn's
    context — and a figure must never be read from an account the caller does
    not own, whoever asks.
    """
    from src.session import files as session_files
    from src.core.tracing import get_user_id
    from src.research.tools import _load_frame_from_bytes

    owner = int(owner_user_id if owner_user_id is not None else (get_user_id() or 0))
    entry = session_files.get_account_file(str(file_id), owner)
    if not entry:
        raise SpecError("unreadable_source",
                        "资料库中没有该文件，或该文件不属于当前账号。")
    cache = _TABLES.get()
    key = (owner, str(file_id))
    if cache is not None and key in cache:
        return cache[key]
    try:
        frame, meta = _load_frame_from_bytes(
            entry["path"].read_bytes(),
            str(entry.get("filename") or "table.csv"))
        if meta.get("truncated"):
            raise SpecError("truncated_source", "表格读取达到解析预算，未对截断数据出图；请明确提供完整的目标数据范围。")
        if cache is not None:
            cache[key] = frame
        return frame
    except SpecError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SpecError("unreadable_source", f"资料库文件无法解析：{exc}") from exc


def _column_values(frame: Any, column: str, label: str) -> list[Any]:
    """One column of a table, as a plain list."""
    if column not in frame.columns:
        available = "、".join(str(name) for name in list(frame.columns)[:40])
        raise SpecError("unknown_column",
                        f"{label}里没有列「{column}」；可用列：{available}。")
    return list(frame[column])


def _inline_values(ref: ColumnRef, label: str) -> list[Any]:
    values = list(ref.values or ())
    if len(values) > MAX_INLINE_ROWS:
        raise SpecError(
            "inline_too_large",
            f"{label}的内联数据有 {len(values)} 行，超过 {MAX_INLINE_ROWS} 行上限；"
            "大数据请上传表格后按列引用。")
    return values


def _numeric(values: list[Any]) -> list[float]:
    out: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        out.append(number)
    return out


def _standard_error(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance / len(values))


def _standard_deviation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _ci95(values: list[float]) -> float | None:
    """Two-sided Student t 95% mean interval, with n - 1 degrees of freedom."""
    count = len(values)
    if count < 2:
        return None
    standard_error = _standard_error(values)
    if standard_error is None:
        return None
    from scipy.stats import t

    return float(t.ppf(0.975, count - 1)) * standard_error


def _error_magnitude(kind: str, values: list[float]) -> float | None:
    if kind == "sd":
        return _standard_deviation(values)
    if kind == "se":
        return _standard_error(values)
    if kind == "ci95":
        return _ci95(values)
    return None


def _resolve_one(ref: ColumnRef | None, label: str,
                 owner_user_id: int | None = None) -> tuple[list[Any], list[dict[str, Any]]]:
    """Read one column, whichever way it is addressed.

    Returns the values and the names of the sources that were read, so the
    project can record what the figure was made from.
    """
    if ref is None:
        return [], []
    if ref.kind == "inline":
        return _inline_values(ref, label), []
    if ref.kind == "attachment":
        frame = _attachment_table(ref.index)
        return _column_values(frame, ref.column, label), [
            {"kind": "attachment", "index": ref.index, "column": ref.column}]
    if ref.kind == "account_file":
        frame = _account_table(ref.file_id, owner_user_id)
        return _column_values(frame, ref.column, label), [
            {"kind": "account_file", "file_id": ref.file_id, "column": ref.column}]
    raise SpecError("invalid_ref_kind", f"{label}的引用方式无法识别。")


def _missing(value: Any) -> bool:
    if value is None or (isinstance(value, str) and not value.strip()):
        return True
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        # Includes pandas NA/NaT without importing the table reader.
        return type(value).__name__ in {"NAType", "NaTType"}


def _numeric_axis(values: list[Any]) -> bool:
    """Numeric columns (including numeric strings) keep their actual spacing."""
    present = [value for value in values if not _missing(value)]
    return bool(present) and (
        any(not isinstance(value, str) for value in present)
        or all(_numeric([value]) for value in present))


def resolve_spec(spec: FigureSpec, *, owner_user_id: int | None = None) -> tuple[ResolvedFigure, dict[str, Any]]:
    with table_resolution_scope():
        return _resolve_spec(spec, owner_user_id=owner_user_id)


def _resolve_spec(spec: FigureSpec, *, owner_user_id: int | None = None) -> tuple[ResolvedFigure, dict[str, Any]]:
    """Resolve aligned rows, retaining original zero-based row ordinals.

    Grouped summaries use (group, x); an ungrouped request for errors uses
    x alone. Scatter always retains individual observations. A singleton
    has a mean but no estimated spread, represented by a None error.
    The info result includes retained rows, excluded rows/reasons and notes.
    """
    if spec.panels:
        from dataclasses import replace
        children, provenance = [], {}
        for panel in spec.panels:
            child, info = resolve_spec(panel, owner_user_id=owner_user_id)
            children.append(child)
            provenance[panel.panel_id] = info
        # Sharing categorical axes with different domains silently re-labels
        # another panel's points. Refuse that contract before rendering.
        if spec.layout.get("share_x"):
            domains = [tuple(child.x_values) for child in children]
            categorical = any(any(isinstance(v, str) for v in domain) for domain in domains)
            if categorical and any(domain != domains[0] for domain in domains):
                raise SpecError("incompatible_shared_axis", "共享分类 X 轴需要相同的类别和顺序。")
        refs = [ref for info in provenance.values() for ref in info.get("refs", [])]
        return ResolvedFigure(title=spec.title, style=spec.style, panels=tuple(children),
                              layout=dict(spec.layout), shared_legend=spec.shared_legend,
                              qa_profile=spec.qa_profile), {"refs": refs, "panels": provenance}
    if spec.weight is not None:
        raise SpecError("unsupported_weight", "暂不支持加权绘图；请先按明确的权重定义汇总数据。")
    refs: list[dict[str, Any]] = []
    columns: dict[str, list[Any]] = {}
    for label in ("x", "y", "group"):
        ref = getattr(spec, label)
        if ref is not None:
            columns[label], source_refs = _resolve_one(ref, label, owner_user_id)
            refs.extend(source_refs)
    if len({len(values) for values in columns.values()}) > 1:
        lengths = ", ".join(f"{name}={len(values)}" for name, values in columns.items())
        raise SpecError("length_mismatch", f"数据列行数必须一致（{lengths}）；不能截短或广播。")
    if not columns.get("y"):
        raise SpecError("empty_data", "y 没有可用取值。")
    paired = spec.chart in {"bar", "line", "scatter", "errorbar"}
    if paired and "x" not in columns:
        raise SpecError("missing_column", f"{spec.chart} 图需要 x 列。")
    numeric_x = paired and _numeric_axis(columns["x"])
    rows: list[tuple[int, Any, float, str]] = []
    excluded: list[dict[str, Any]] = []
    for index, value in enumerate(columns["y"]):
        reasons = []
        numbers = _numeric([value])
        if not numbers:
            reasons.append("invalid_y")
        x = columns["x"][index] if paired else None
        if paired:
            if numeric_x:
                x_numbers = _numeric([x])
                if not x_numbers:
                    reasons.append("invalid_x")
                else:
                    x = x_numbers[0]
            elif _missing(x):
                reasons.append("missing_x")
            else:
                x = str(x)
        group = columns["group"][index] if spec.group is not None else ""
        if spec.group is not None and _missing(group):
            reasons.append("missing_group")
        if reasons:
            excluded.append({"row_index": index, "reasons": reasons})
        else:
            rows.append((index, x, numbers[0], str(group)))
    if not rows:
        raise SpecError("empty_data", "联合筛选后没有可用的数值。")
    info: dict[str, Any] = {
        "refs": refs, "row_index_base": 0,
        "retained_row_indices": [row[0] for row in rows],
        "excluded_rows": excluded, "warnings": [],
    }
    groups: dict[str, list[tuple[int, Any, float, str]]] = {}
    for row in rows:
        groups.setdefault(row[3], []).append(row)
    if len(groups) > MAX_GROUPS:
        raise SpecError("too_many_groups", f"分组后有 {len(groups)} 组，超过 {MAX_GROUPS} 组上限。")

    series: list[Series] = []
    aggregate = paired and spec.chart != "scatter" and (
        spec.group is not None or spec.error != "none")
    for name, group_rows in groups.items():
        if aggregate:
            buckets: dict[Any, list[tuple[int, Any, float, str]]] = {}
            for row in group_rows:
                buckets.setdefault(row[1], []).append(row)
            xs, ys, errors, identities = [], [], [], []
            for x, bucket in buckets.items():
                values = [row[2] for row in bucket]
                xs.append(x)
                ys.append(sum(values) / len(values))
                identities.append(tuple(row[0] for row in bucket))
                if spec.error != "none":
                    error = _error_magnitude(spec.error, values)
                    errors.append(error)
                    if error is None:
                        info["warnings"].append({
                            "code": "insufficient_replicates", "group": name,
                            "x": x, "row_indices": list(identities[-1]),
                            "message": "样本数少于 2，保留均值但不绘制误差棒。"})
            series.append(Series(name=name, values=tuple(ys), errors=tuple(errors),
                                 x_values=tuple(xs), row_indices=tuple(identities)))
        else:
            series.append(Series(
                name=name, values=tuple(row[2] for row in group_rows),
                x_values=tuple(row[1] for row in group_rows) if paired else (),
                row_indices=tuple((row[0],) for row in group_rows)))

    # Each series carries its own X, so absent combinations remain absent.
    # Preserve the legacy paired raw columns for ungrouped observations.
    x_domain = tuple(row[1] for row in rows) if paired else ()
    if aggregate or spec.group is not None:
        x_domain = tuple(dict.fromkeys(x_domain))
    if paired and len(series) == 1:
        x_domain = series[0].x_values
    # A single paired series also exposes aligned X/Y convenience columns.
    # Multiple groups have their own coordinates, not one shared Y column.
    y_column = (series[0].values if len(series) == 1 else ()) if paired else tuple(
        row[2] for row in rows)
    return ResolvedFigure(
        chart=spec.chart, title=spec.title, x_label=spec.x_label,
        y_label=spec.y_label or ("频数" if spec.chart == "histogram" else ""),
        x_name=spec.x.column if spec.x else "",
        y_name=spec.y.column if spec.y else "",
        group_name=spec.group.column if spec.group else "",
        x_values=x_domain, y_values=y_column,
        error_kind=spec.error, series=tuple(series),
        legend=spec.legend if spec.chart != "histogram" else False,
        annotations=spec.annotations, style=spec.style, panel_id=spec.panel_id,
        qa_profile=dict(spec.qa_profile)), info


__all__ = ["MAX_GROUPS", "MAX_INLINE_ROWS", "resolve_spec", "select_table_figure", "table_figure_guidance"]

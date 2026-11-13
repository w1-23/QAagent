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
from typing import Any

from src.figures.spec import ColumnRef, FigureSpec, ResolvedFigure, Series, SpecError

# The maximum number of rows an inline table may carry.
MAX_INLINE_ROWS = 200

# The maximum number of groups a figure may show. A figure with hundreds of
# bars is not a figure, it is a table.
MAX_GROUPS = 60


def _attachment_table(index: int) -> Any:
    """Read this turn's uploaded table."""
    from src.research.tools import _load_frame
    frame, _meta = _load_frame(int(index), "", 1, None)
    return frame


def _account_table(file_id: str) -> Any:
    """Read a table from the account library, with the ownership check."""
    from src.session import files as session_files
    from src.core.tracing import get_user_id
    from src.research.tools import _load_frame_from_bytes

    entry = session_files.get_account_file(str(file_id), int(get_user_id() or 0))
    if not entry:
        raise SpecError("unreadable_source",
                        "资料库中没有该文件，或该文件不属于当前账号。")
    try:
        return _load_frame_from_bytes(entry["path"].read_bytes(),
                                      str(entry.get("filename") or "table.csv"))
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
    """Half-width of the 95% confidence interval of the mean.

    A normal approximation with the t factor for small samples. The sample
    size is at most a few hundred here — a figure that aggregates thousands
    of rows would be summarised before it got this far — so the normal
    approximation with a t correction is accurate enough and does not need
    SciPy.
    """
    count = len(values)
    if count < 2:
        return None
    standard_error = _standard_error(values)
    if standard_error is None:
        return None
    t_factor = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
                7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262, 11: 2.228,
                12: 2.201, 13: 2.179, 14: 2.160, 15: 2.145, 16: 2.131,
                17: 2.120, 18: 2.110, 19: 2.101, 20: 2.093}.get(count, 1.96)
    return t_factor * standard_error


def _error_magnitude(kind: str, values: list[float]) -> float | None:
    if kind == "sd":
        return _standard_deviation(values)
    if kind == "se":
        return _standard_error(values)
    if kind == "ci95":
        return _ci95(values)
    return None


def _resolve_one(ref: ColumnRef | None, label: str) -> tuple[list[Any], list[str]]:
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
        frame = _account_table(ref.file_id)
        return _column_values(frame, ref.column, label), [
            {"kind": "account_file", "file_id": ref.file_id, "column": ref.column}]
    raise SpecError("invalid_ref_kind", f"{label}的引用方式无法识别。")


def _grouped_series(x_values: list[Any], y_values: list[Any],
                    group_values: list[Any], error_kind: str) -> tuple[
                        tuple[str, ...], tuple[Series, ...]]:
    """One series per group, with the group's own summary as the bar."""
    buckets: dict[str, list[float]] = {}
    order: list[str] = []
    for index, value in enumerate(y_values):
        if index >= len(group_values):
            break
        key = str(group_values[index])
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        number = _numeric([value])
        if number:
            buckets[key].append(number[0])
    if not order:
        raise SpecError("empty_data", "分组后没有可用的数值。")
    if len(order) > MAX_GROUPS:
        raise SpecError(
            "too_many_groups",
            f"分组后有 {len(order)} 组，超过 {MAX_GROUPS} 组上限；"
            "请先聚合再作图。")
    names: list[str] = []
    series: list[Series] = []
    for key in order:
        values = buckets[key]
        if not values:
            continue
        names.append(key)
        mean = sum(values) / len(values)
        magnitude = _error_magnitude(error_kind, values) if error_kind != "none" else None
        series.append(Series(
            name=key,
            values=(mean,),
            errors=(magnitude,) if magnitude is not None else ()))
    return tuple(names), tuple(series)


def resolve_spec(spec: FigureSpec) -> tuple[ResolvedFigure, dict[str, Any]]:
    """Resolve a specification into a figure the renderer can draw.

    The second return value describes the sources that were read, so the
    project can record them and the workspace can re-render later.
    """
    refs: list[str] = []

    if spec.chart == "histogram":
        y_values, source_refs = _resolve_one(spec.y, "y")
        refs.extend(source_refs)
        numbers = _numeric(y_values)
        if not numbers:
            raise SpecError("empty_data", "没有可用于直方图的数值。")
        return ResolvedFigure(
            chart="histogram", title=spec.title, x_label=spec.x_label,
            y_label=spec.y_label or "频数", x_name=spec.y.column or "value",
            y_name=spec.y.column or "value",
            y_values=tuple(numbers), error_kind="none", legend=False,
            annotations=spec.annotations, style=spec.style), {"refs": refs}

    if spec.chart == "boxplot":
        # A box plot shows the distribution of one or more groups. The
        # groups come from the ``group`` reference; without one there is a
        # single group.
        y_values, source_refs = _resolve_one(spec.y, "y")
        refs.extend(source_refs)
        numbers = _numeric(y_values)
        if not numbers:
            raise SpecError("empty_data", "没有可用于箱线图的数值。")
        group_values: list[Any] = []
        if spec.group is not None:
            group_values, group_refs = _resolve_one(spec.group, "group")
            refs.extend(group_refs)
        if group_values:
            buckets: dict[str, list[float]] = {}
            order: list[str] = []
            for index, value in enumerate(y_values):
                if index >= len(group_values):
                    break
                key = str(group_values[index])
                if key not in buckets:
                    buckets[key] = []
                    order.append(key)
                number = _numeric([value])
                if number:
                    buckets[key].append(number[0])
            names = tuple(order)
            series = tuple(Series(name=key, values=tuple(buckets[key]))
                           for key in order if buckets.get(key))
        else:
            names = ("",)
            series = (Series(name="", values=tuple(numbers)),)
        return ResolvedFigure(
            chart="boxplot", title=spec.title, x_label=spec.x_label,
            y_label=spec.y_label, x_name=spec.x.column if spec.x else "",
            y_name=spec.y.column or "", group_name=spec.group.column if spec.group else "",
            series=series, error_kind="none", legend=spec.legend,
            annotations=spec.annotations, style=spec.style), {"refs": refs}

    # bar / line / scatter / errorbar
    x_values, x_refs = _resolve_one(spec.x, "x")
    y_values, y_refs = _resolve_one(spec.y, "y")
    refs.extend(x_refs)
    refs.extend(y_refs)
    if not x_values:
        raise SpecError("empty_data", "x 没有可用取值。")
    if len(y_values) < len(x_values):
        x_values = x_values[:len(y_values)]
    if not y_values:
        raise SpecError("empty_data", "y 没有可用取值。")

    if spec.group is not None:
        group_values, group_refs = _resolve_one(spec.group, "group")
        refs.extend(group_refs)
        names, series = _grouped_series(
            x_values, y_values, group_values, spec.error)
        # One bar per group: the x axis becomes the group names.
        return ResolvedFigure(
            chart="bar" if spec.chart == "bar" else spec.chart,
            title=spec.title, x_label=spec.x_label,
            y_label=spec.y_label, x_name=spec.group.column or "group",
            y_name=spec.y.column or "value",
            group_name=spec.group.column or "",
            x_values=names, y_values=tuple(series[0].values) if series else (),
            error_kind=spec.error, series=series, legend=spec.legend,
            annotations=spec.annotations, style=spec.style), {"refs": refs}

    numbers = _numeric(y_values)
    if not numbers:
        raise SpecError("empty_data", "y 列没有可用的数值。")
    size = min(len(numbers), len(x_values))
    labels = tuple(str(value) for value in x_values[:size])
    if spec.chart == "errorbar" and spec.error != "none":
        # Without a group, the error bar describes the variation of the
        # whole column, drawn once.
        magnitude = _error_magnitude(spec.error, numbers)
        errors = (magnitude,) if magnitude is not None else ()
        series = (Series(name="", values=tuple(numbers), errors=errors),)
    else:
        series = (Series(name="", values=tuple(numbers)),)
    return ResolvedFigure(
        chart=spec.chart, title=spec.title, x_label=spec.x_label,
        y_label=spec.y_label, x_name=spec.x.column or "",
        y_name=spec.y.column or "", x_values=labels,
        y_values=tuple(numbers), error_kind=spec.error, series=series,
        legend=spec.legend, annotations=spec.annotations,
        style=spec.style), {"refs": refs}


__all__ = ["MAX_GROUPS", "MAX_INLINE_ROWS", "resolve_spec"]

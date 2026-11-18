"""Authorize host table sources, then delegate pure numeric resolution.

The host cache remains owner-keyed; only explicitly authorized tables cross
into citrus_figures. No service discovery is added to the independent package.
"""
from __future__ import annotations
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from citrus_figures.spec import ColumnRef, FigureSpec, ResolvedFigure, Series, SpecError
from citrus_figures.resolve import _TABLES as _PACKAGE_TABLES
from citrus_figures.resolve import resolve_spec as _package_resolve_spec
from citrus_figures.resolve import (
    MAX_INLINE_ROWS,
    MAX_GROUPS,
    table_figure_guidance,
    select_table_figure,
    _column_values,
    _inline_values,
    _numeric,
    _standard_error,
    _standard_deviation,
    _ci95,
    _error_magnitude,
    _missing,
    _numeric_axis,
)

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


def resolve_spec(spec: FigureSpec, *, owner_user_id: int | None = None) -> tuple[ResolvedFigure, dict[str, Any]]:
    with table_resolution_scope():
        tables: dict = {}

        def authorize(node: FigureSpec) -> None:
            if node.panels:
                for panel in node.panels:
                    authorize(panel)
                return
            if node.weight is not None:
                raise SpecError("unsupported_weight", "暂不支持加权绘图；请先按明确的权重定义汇总数据。")
            for name in ("x", "y", "group", "series_key"):
                ref = getattr(node, name)
                if ref is not None and ref.kind == "attachment":
                    tables[("attachment", int(ref.index))] = _attachment_table(ref.index)
                elif ref is not None and ref.kind == "account_file":
                    tables[("account_file", str(ref.file_id))] = _account_table(ref.file_id, owner_user_id)

        authorize(spec)
        # Establish this host invocation's exact table set even if a caller
        # already entered an independent-package table-resolution scope.
        token = _PACKAGE_TABLES.set(tables)
        try:
            return _package_resolve_spec(spec, tables=tables, owner_user_id=owner_user_id)
        finally:
            _PACKAGE_TABLES.reset(token)


def _resolve_spec(spec: FigureSpec, *, owner_user_id: int | None = None) -> tuple[ResolvedFigure, dict[str, Any]]:
    return resolve_spec(spec, owner_user_id=owner_user_id)


__all__ = ["MAX_GROUPS", "MAX_INLINE_ROWS", "resolve_spec", "select_table_figure", "table_figure_guidance"]

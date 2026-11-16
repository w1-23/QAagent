"""Materialize verified scientific snapshots into document/table blocks.

Consumers pass a result reference, never numbers retyped by a model. The source
snapshot remains immutable; these are presentation projections of that result.
"""
from __future__ import annotations

import math
from typing import Any


def result_table(owner: int, reference: dict | str, section: str = "outputs") -> dict[str, Any]:
    from src.research.analysis_results import load_analysis_result

    if int(owner or 0) <= 0:
        raise ValueError("分析结果需要已登录账号。")
    result = load_analysis_result(int(owner), reference)
    payload = result.to_dict()
    if section not in {"outputs", "descriptive", "grouped", "test", "tests", "coverage", "exclusions", "design"}:
        raise ValueError("不支持的分析结果字段。")
    if section in {"outputs", "coverage", "exclusions", "design"}:
        selected = payload.get(section)
    else:
        selected = payload.get("outputs", {}).get(section)
    if selected is None:
        raise ValueError("该分析结果没有请求的内容，未生成空表。")
    rows: list[list[Any]] = []

    def visit(value: Any, path: str, depth: int = 0) -> None:
        if depth > 20 or len(rows) >= 5000:
            raise ValueError("分析表超过单次展示范围；请选择更具体的结果字段。")
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{path}.{key}" if path else str(key), depth + 1)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index + 1}]", depth + 1)
        elif value is None or type(value) in (str, int, float, bool):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("分析快照包含非有限数，不能作为有效结果交付。")
            rows.append([path, value])
        else:
            raise ValueError("分析结果包含不可交付的数据类型。")

    visit(selected, section)
    if not rows:
        raise ValueError("分析结果无可展示数据，未生成空表。")
    ref = result.ref
    identity = str(ref.get("id", ""))
    digest = str(ref.get("sha256", ""))
    return {"columns": ["结果字段", "数值或内容"], "rows": rows,
            "caption": f"已保存分析结果 {identity} · SHA-256 {digest}",
            "analysis_result_ref": dict(ref), "dataset_ref": payload.get("dataset_ref"),
            "coverage": payload.get("coverage", {})}


def dataset_table(owner: int, reference: dict, *, output_format: str = "csv") -> dict[str, Any]:
    """Project an owned, hash-verified dataset without model-retyped cells."""
    import datetime
    import numpy as np
    import pandas as pd
    from src.research.analysis_results import load_dataset_snapshot

    snapshot = load_dataset_snapshot(owner, reference)
    frame = snapshot.to_frame()
    if frame.empty:
        raise ValueError("数据集没有可交付的数据行。")
    if len(frame) > 5000 or frame.size > 100000:
        raise ValueError("数据集超过单次表格交付范围；请先保存明确分段的数据集，不会截断导出。")
    columns = list(frame.columns)
    if (not columns or len(columns) > 200
            or any(not isinstance(c, str) or not c.strip() or c != c.strip() for c in columns)
            or len(set(columns)) != len(columns)):
        raise ValueError("数据集须有1–200个唯一非空文字列名，且首尾无空白；不会自动改名。")

    def cell(value):
        if value is None or value is pd.NA or value is pd.NaT:
            return None
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float):
            if math.isnan(value):
                return None
            if not math.isfinite(value):
                raise ValueError("数据集含无穷数值，未发布。")
        if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
            return value.isoformat()
        if type(value) not in (str, int, float, bool):
            raise ValueError("数据集含不支持的单元格类型，未发布。")
        # Excel stores numbers with at most 15 significant decimal digits.
        # Preserve larger integer identifiers as literal text, never round them.
        if output_format == "xlsx" and type(value) is int and len(str(abs(value))) > 15:
            return str(value)
        return value

    return {"columns": columns,
            "rows": [[cell(value) for value in row]
                     for row in frame.itertuples(index=False, name=None)],
            "dataset_ref": snapshot.ref}


def materialize_word_blocks(owner: int, blocks: list[dict] | None) -> tuple[list[dict] | None, list[dict]]:
    if blocks is None:
        return None, []
    if not isinstance(blocks, list):
        raise ValueError("文档内容须为结构块数组。")
    materialized, references = [], []
    for source in blocks:
        if not isinstance(source, dict) or source.get("type") != "analysis_table":
            materialized.append(source)
            continue
        if set(source) - {"type", "block_id", "section_id", "analysis_result_ref", "result_section", "caption"}:
            raise ValueError("引用分析结果的表格不能覆盖其数值或列名。")
        projection = result_table(owner, source.get("analysis_result_ref", ""), source.get("result_section", "outputs"))
        caption = str(source.get("caption") or "")
        materialized.append({"type": "table", "block_id": source.get("block_id"),
                             "section_id": source.get("section_id"),
                             "columns": projection["columns"], "rows": projection["rows"],
                             "caption": (caption + "\n" if caption else "") + projection["caption"]})
        references.append(projection["analysis_result_ref"])
    return materialized, references

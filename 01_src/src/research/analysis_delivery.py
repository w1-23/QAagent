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

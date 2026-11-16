"""Versioned, read-only script presets and the existing capability catalog.

Presets are application code, not expiring task assets. Their metadata is a
small discovery index; source is read only on demand. No code executes here.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from copy import deepcopy

_RELATIONSHIP_RENDERER = """import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

frame = data["relations_data"]
panels = list(frame["panel"].drop_duplicates())
columns = min(3, len(panels))
rows = (len(panels) + columns - 1) // columns
fig, axes = plt.subplots(rows, columns, figsize=(4.0 * columns, 3.5 * rows),
                         squeeze=False, constrained_layout=True)
for index, panel in enumerate(panels):
    selected = frame.loc[frame["panel"] == panel]
    ax = axes.flat[index]
    ax.scatter(selected["x"], selected["y"], s=28, color="#2a78d6",
               edgecolors="white", linewidths=0.7, alpha=0.9)
    ax.set_xlabel(str(selected["x_label"].iloc[0]))
    ax.set_ylabel(str(selected["y_label"].iloc[0]))
    ax.set_title(str(selected["y_label"].iloc[0]))
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=0.15, linewidth=0.7)
    ax.set_axisbelow(True)
for index in range(len(panels), rows * columns):
    axes.flat[index].set_visible(False)
"""


_PRESETS = {
    "wide_to_long": {
        "description": "按明确字段把宽表整理成长表，保留缺失，不修改原数据；输出内部 tidy 表。",
        "parameters": {"id_columns": "identifier columns (list)", "value_columns": "measurement columns (nonempty list)",
                       "input_format": "csv | excel | dataset", "sheet": "Excel sheet (optional)",
                       "variable_name": "output field, default metric", "value_name": "output field, default value"},
        "inputs": {"data": "authorized file_id or dataset_ref"},
        "outputs": {"version": 2, "tables": [{"name": "tidy", "parents": ["data"]}], "figures": []},
    },
    "relationship_panels": {
        "description": "明确X与1至6个数值Y的散点分面图；保留全部有效点，不做推断检验或自动删异常值。",
        "parameters": {"x": "explicit numeric X column", "ys": "one to six response columns",
                       "labels": "field -> display label including known units", "input_format": "csv | excel | dataset",
                       "sheet": "Excel sheet (optional)"},
        "inputs": {"data": "authorized file_id or dataset_ref"},
        "outputs": {"version": 2, "tables": [{"name": "relations_data", "parents": ["data"]}],
                    "figures": [{"name": "relations", "title": "变量关系",
                     "formats": ["svg", "png", "pdf"], "parents": ["relations_data"],
                     "render_script": _RELATIONSHIP_RENDERER}]},
    },
}


def get_preset(ref: dict | str, *, include_source: bool = True) -> dict:
    identity = ref if isinstance(ref, str) else ref.get("id") if isinstance(ref, dict) else None
    if identity not in _PRESETS:
        raise ValueError("Unknown built-in script")
    source = (Path(__file__).parent / "presets" / (identity + ".py")).read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    reference = {"kind": "builtin_script", "id": identity, "version": 1, "sha256": digest}
    if isinstance(ref, dict) and (set(ref) != set(reference) or ref != reference):
        raise ValueError("Built-in version changed; read its current reference before running")
    result = {"script_ref": reference, "name": identity + ".py", **deepcopy(_PRESETS[identity]),
              "read_only": True, "retention": "service_version"}
    if include_source:
        result["source"] = source
    return result


def list_presets() -> list[dict]:
    return [get_preset(name, include_source=False) for name in _PRESETS]


def capability_catalog() -> list[dict]:
    """Derive the compact tool inventory; do not create a second name list."""
    from src.tools import _TOOL_BINDINGS
    from src.core.tool_catalog import TOOL_METADATA
    return [{"tool": name, "label": TOOL_METADATA[name].display_label,
             "requires": list(binding[0]), "produces": list(binding[1])}
            for name, binding in sorted(_TOOL_BINDINGS.items())]

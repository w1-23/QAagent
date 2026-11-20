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

_VOLCANO_RENDERER = """import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

frame = data["volcano_data"]
fig, ax = plt.subplots(figsize=(4.4, 3.8), layout="constrained")
colors = {"not_significant": "#9aa0a6", "down": "#2e86de", "up": "#c0392b"}
for name in ("not_significant", "down", "up"):
    selected = frame.loc[frame["direction"] == name]
    if not selected.empty:
        ax.scatter(selected["effect"], selected["neg_log10_p"], s=12,
                   color=colors[name], linewidths=0, alpha=0.85, label=name)
effect_threshold = float(frame["effect_threshold"].iloc[0])
neg_threshold = float(frame["neg_log10_threshold"].iloc[0])
xmin = float(frame["effect"].min())
xmax = float(frame["effect"].max())
ymin = min(0.0, float(frame["neg_log10_p"].min()))
ymax = float(frame["neg_log10_p"].max())
ax.plot([effect_threshold, effect_threshold], [ymin, ymax], color="#6b7280",
        linewidth=0.8, linestyle="--")
ax.plot([-effect_threshold, -effect_threshold], [ymin, ymax], color="#6b7280",
        linewidth=0.8, linestyle="--")
ax.plot([xmin, xmax], [neg_threshold, neg_threshold], color="#6b7280",
        linewidth=0.8, linestyle="--")
for _, row in frame.loc[frame["label"] != ""].iterrows():
    ax.text(float(row["effect"]), float(row["neg_log10_p"]), str(row["label"]),
            fontsize=6, ha="left", va="bottom", color="#111827")
ax.set_xlabel(str(frame["effect_label"].iloc[0]))
ax.set_ylabel("-log10(" + str(frame["significance_label"].iloc[0]) + ")")
ax.set_title("Volcano plot")
ax.spines[["top", "right"]].set_visible(False)
ax.grid(alpha=0.15, linewidth=0.7)
ax.set_axisbelow(True)
ax.legend(frameon=False, fontsize=6, loc="upper left",
          bbox_to_anchor=(1.01, 1.0), borderaxespad=0)
"""

_ROC_RENDERER = """import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

frame = data["roc_data"]
fig, ax = plt.subplots(figsize=(3.8, 3.8), layout="constrained")
ax.plot(frame["fpr"], frame["tpr"], color="#1f6feb", linewidth=1.4)
ax.plot([0.0, 1.0], [0.0, 1.0], color="#9aa0a6", linewidth=0.8, linestyle="--")
auc = float(frame["auc"].iloc[0])
ax.set_xlabel("False positive rate")
ax.set_ylabel("True positive rate")
ax.set_title("ROC (AUC = %.4f)" % auc)
ax.set_xlim(0.0, 1.0)
ax.set_ylim(0.0, 1.0)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(alpha=0.15, linewidth=0.7)
ax.set_axisbelow(True)
"""

_DOTPLOT_RENDERER = """import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

frame = data["dotplot_data"]
categories = frame[["category_index", "category"]].drop_duplicates().sort_values("category_index")
groups = frame[["group_index", "group"]].drop_duplicates().sort_values("group_index")
size_min = float(frame["size_min"].iloc[0])
size_max = float(frame["size_max"].iloc[0])
if size_max > size_min:
    normalized = (frame["size_value"].to_numpy(dtype="float64") - size_min) / (size_max - size_min)
else:
    normalized = np.full(len(frame), 0.5)
color_min = float(frame["color_min"].iloc[0])
color_max = float(frame["color_max"].iloc[0])
if color_min == color_max:
    color_min -= 0.5
    color_max += 0.5
fig, ax = plt.subplots(figsize=(max(4.2, min(9.0, 2.2 + 0.5 * len(categories))),
                                max(3.0, min(9.0, 1.6 + 0.3 * len(groups)))),
                       layout="constrained")
scatter = ax.scatter(frame["category_index"], frame["group_index"],
                     s=18.0 + 210.0 * normalized, c=frame["color_value"],
                     cmap="viridis", vmin=color_min, vmax=color_max,
                     linewidths=0, edgecolors="none")
ax.set_xticks(categories["category_index"].to_numpy(), categories["category"].to_numpy())
ax.set_yticks(groups["group_index"].to_numpy(), groups["group"].to_numpy())
for label in ax.get_xticklabels():
    label.set_rotation(45)
    label.set_horizontalalignment("right")
    label.set_rotation_mode("anchor")
ax.set_xlabel(str(frame["category_label"].iloc[0]))
ax.set_ylabel(str(frame["group_label"].iloc[0]))
ax.set_title("Dot plot")
ax.spines[["top", "right"]].set_visible(False)
bar = fig.colorbar(scatter, ax=ax, fraction=0.05, pad=0.02)
if bar.solids is not None:
    # Matplotlib rasterizes dense colorbar solids by default; keep the
    # SVG export vector so the existing display/sanitizer path accepts it.
    bar.solids.set_rasterized(False)
bar.set_label(str(frame["color_label"].iloc[0]))
"""

_MARGINAL_RENDERER = """import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

points = data["marginal_points"]
bins = data["marginal_bins"]
fig = plt.figure(figsize=(4.6, 4.6), layout="constrained")
grid = fig.add_gridspec(2, 2, width_ratios=(4, 1), height_ratios=(1, 4),
                        wspace=0.06, hspace=0.06)
ax_main = fig.add_subplot(grid[1, 0])
ax_top = fig.add_subplot(grid[0, 0], sharex=ax_main)
ax_right = fig.add_subplot(grid[1, 1], sharey=ax_main)
blank = fig.add_subplot(grid[0, 1])
blank.set_visible(False)
ax_main.scatter(points["x"], points["y"], s=9, color="#2a78d6", linewidths=0,
                alpha=0.75)
bins_x = bins.loc[bins["axis"] == "x"].sort_values("bin_index")
bins_y = bins.loc[bins["axis"] == "y"].sort_values("bin_index")
ax_top.bar(bins_x["bin_left"], bins_x["count"],
           width=bins_x["bin_right"] - bins_x["bin_left"], align="edge",
           color="#9ecae1", linewidth=0)
ax_right.barh(bins_y["bin_left"], bins_y["count"],
              height=bins_y["bin_right"] - bins_y["bin_left"], align="edge",
              color="#9ecae1", linewidth=0)
ax_top.tick_params(labelbottom=False)
ax_right.tick_params(labelleft=False)
ax_main.set_xlabel(str(points["x_label"].iloc[0]))
ax_main.set_ylabel(str(points["y_label"].iloc[0]))
ax_top.set_ylabel("Count")
ax_right.set_xlabel("Count")
ax_main.set_title("Joint and marginal distributions")
for ax in (ax_main, ax_top, ax_right):
    ax.spines[["top", "right"]].set_visible(False)
"""

_PAIRED_RENDERER = """import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection

frame = data["paired_data"]
conditions = frame[["condition_index", "condition"]].drop_duplicates().sort_values("condition_index")
wide = frame.pivot(index="subject_index", columns="condition_index", values="value")
segments = [[(0.0, float(row.iloc[0])), (1.0, float(row.iloc[1]))]
            for _, row in wide.iterrows()]
fig, ax = plt.subplots(figsize=(3.8, 3.8), layout="constrained")
ax.add_collection(LineCollection(segments, colors="#9aa0a6", linewidths=0.6, alpha=0.6))
ax.scatter(np.zeros(len(wide)), wide.iloc[:, 0].to_numpy(dtype="float64"),
           s=16, color="#2a78d6", linewidths=0, zorder=3)
ax.scatter(np.ones(len(wide)), wide.iloc[:, 1].to_numpy(dtype="float64"),
           s=16, color="#c0392b", linewidths=0, zorder=3)
ax.set_xlim(-0.25, 1.25)
low = float(frame["value"].min())
high = float(frame["value"].max())
pad = (high - low) * 0.08 if high > low else 0.5
ax.set_ylim(low - pad, high + pad)
ax.set_xticks([0.0, 1.0], conditions["condition"].to_numpy())
ax.set_xlabel("Condition")
ax.set_ylabel(str(frame["value_label"].iloc[0]))
ax.set_title("Paired observations")
ax.spines[["top", "right"]].set_visible(False)
ax.grid(alpha=0.15, linewidth=0.7)
ax.set_axisbelow(True)
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
    "nature_volcano": {
        "description": "明确效应量与p值列的火山图；阈值为分类用途，保留全部点与全精度值，不做推断或删点。",
        "parameters": {"effect": "explicit effect-size column", "significance": "explicit p-value column in (0, 1]",
                       "effect_threshold": "positive absolute effect threshold", "p_threshold": "p-value threshold in (0, 1]",
                       "effect_label": "effect axis label including units", "significance_label": "p-value label",
                       "label_column": "optional feature label column", "label_top": "significant features to label (0-200)",
                       "input_format": "csv | excel | dataset", "sheet": "Excel sheet (optional)"},
        "inputs": {"data": "authorized file_id or dataset_ref"},
        "outputs": {"version": 2, "tables": [{"name": "volcano_data", "parents": ["data"]}],
                    "figures": [{"name": "volcano", "title": "火山图",
                     "formats": ["svg"], "parents": ["volcano_data"],
                     "render_script": _VOLCANO_RENDERER}]},
    },
    "nature_roc": {
        "description": "明确分数与二值真值列的ROC；按分数稳定降序、重复FPR保留垂直段并按TPR升序，梯形AUC全精度。",
        "parameters": {"score": "explicit numeric score, higher means positive", "truth": "binary 0/1 outcome column",
                       "score_label": "score label", "truth_label": "truth label",
                       "input_format": "csv | excel | dataset", "sheet": "Excel sheet (optional)"},
        "inputs": {"data": "authorized file_id or dataset_ref"},
        "outputs": {"version": 2, "tables": [{"name": "roc_data", "parents": ["data"]}],
                    "figures": [{"name": "roc", "title": "ROC曲线",
                     "formats": ["svg"], "parents": ["roc_data"],
                     "render_script": _ROC_RENDERER}]},
    },
    "nature_dotplot": {
        "description": "明确类别、分组、大小与颜色列的点图；类别顺序可显式指定，重复点拒绝、不聚合、不插值。",
        "parameters": {"category": "explicit category column", "group": "explicit group column",
                       "size": "explicit numeric size column", "color": "explicit numeric color column",
                       "category_order": "optional explicit category order", "group_order": "optional explicit group order",
                       "category_label": "category axis label", "group_label": "group axis label",
                       "size_label": "size legend label including units", "color_label": "color legend label including units",
                       "input_format": "csv | excel | dataset", "sheet": "Excel sheet (optional)"},
        "inputs": {"data": "authorized file_id or dataset_ref"},
        "outputs": {"version": 2, "tables": [{"name": "dotplot_data", "parents": ["data"]}],
                    "figures": [{"name": "dotplot", "title": "点图",
                     "formats": ["svg"], "parents": ["dotplot_data"],
                     "render_script": _DOTPLOT_RENDERER}]},
    },
    "nature_marginal": {
        "description": "明确X/Y列的联合散点与边缘直方图；联合与边缘使用同源同单位完整值，缺失与非有限值拒绝。",
        "parameters": {"x": "explicit numeric X column", "y": "explicit numeric Y column",
                       "x_label": "X label including units", "y_label": "Y label including units",
                       "bins": "histogram bin count (1-200)", "input_format": "csv | excel | dataset",
                       "sheet": "Excel sheet (optional)"},
        "inputs": {"data": "authorized file_id or dataset_ref"},
        "outputs": {"version": 2,
                    "tables": [{"name": "marginal_points", "parents": ["data"]},
                               {"name": "marginal_bins", "parents": ["data"]}],
                    "figures": [{"name": "marginal", "title": "联合与边缘分布",
                     "formats": ["svg"],
                     "parents": ["marginal_points", "marginal_bins"],
                     "render_script": _MARGINAL_RENDERER}]},
    },
    "nature_paired": {
        "description": "恰好两个条件的配对观测；重复与缺失默认拒绝，明确选择排除时输出被排除受试者清单。",
        "parameters": {"subject": "explicit subject identifier column", "condition": "explicit condition column",
                       "value": "explicit numeric value column", "condition_order": "optional explicit two-condition order",
                       "value_label": "value axis label including units",
                       "exclusion_policy": "reject | list (default reject)",
                       "input_format": "csv | excel | dataset", "sheet": "Excel sheet (optional)"},
        "inputs": {"data": "authorized file_id or dataset_ref"},
        "outputs": {"version": 2,
                    "tables": [{"name": "paired_data", "parents": ["data"]},
                               {"name": "paired_excluded", "parents": ["data"]}],
                    "figures": [{"name": "paired", "title": "配对观测",
                     "formats": ["svg"], "parents": ["paired_data"],
                     "render_script": _PAIRED_RENDERER}]},
    },
}

# The five Nature templates are hashed v2 presets: frozen tables plus an
# independent renderer. Legacy presets keep their original id, version and
# source identity byte-for-byte.
_VERSIONS = {"nature_volcano": 2, "nature_roc": 2, "nature_dotplot": 2,
             "nature_marginal": 2, "nature_paired": 2}
_DISCOVERY_NOTE = (
    "先只读探查工作簿（inspect_uploaded_table）；"
    "三种隔离环境：Docker、WSL-Docker、Apptainer。"
)


def get_preset(ref: dict | str, *, include_source: bool = True) -> dict:
    identity = ref if isinstance(ref, str) else ref.get("id") if isinstance(ref, dict) else None
    if identity not in _PRESETS:
        raise ValueError("Unknown built-in script")
    source = (Path(__file__).parent / "presets" / (identity + ".py")).read_text(encoding="utf-8")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    reference = {"kind": "builtin_script", "id": identity,
                 "version": _VERSIONS.get(identity, 1), "sha256": digest}
    if isinstance(ref, dict) and (set(ref) != set(reference) or ref != reference):
        raise ValueError("Built-in version changed; read its current reference before running")
    entry = deepcopy(_PRESETS[identity])
    entry["description"] = _DISCOVERY_NOTE + entry["description"]
    result = {"script_ref": reference, "name": identity + ".py", **entry,
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

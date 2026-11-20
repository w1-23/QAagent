"""F3 composition tests for one v2 figure built from frozen tables.

The tests run the real offline figure Worker (``LocalBackend``) on a small
synthetic fixture. They do not call an external model, the network, a deployed
service or the Web UI. Composition is contract-driven: the analysis script
emits named tables, the render script receives exactly the declared parents as
``data[...]``, and no branch maps a chart name to a hard-coded code path.

``LocalBackend`` is the development/test runner, not a security boundary. The
production paths use the isolated Docker/Apptainer backend; this file only
checks the shared v2 output and frozen-render protocol.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path

import pytest

pytest.importorskip("pandas")
pytest.importorskip("matplotlib")
pytest.importorskip("citrus_figures")

from src.figures.dataset_codec import decode_frame
from src.figures.frozen_render import typed_payload_sha256
from src.figures.sandbox import LocalBackend, SandboxJob
from src.figures.script_outputs import normalize_output_contract, output_slots, strict_json

_TABLES = ("base_bar", "base_scatter", "panel_a", "panel_b", "panel_c")
_FIGURE = "composite"
_NATURE_TEMPLATE_IDS = ("nature_volcano", "nature_roc", "nature_dotplot",
                        "nature_marginal", "nature_paired")

# A generic synthetic fixture: three groups, ordered days, and continuous
# coordinates. Values are exact decimals so the expectation below is stable.
_RECORDS = []
for _index, _group in enumerate("ABCABCABCABCABCABC"):
    _RECORDS.append({
        "group": _group,
        "response": 10.0 + _index * 1.5 + "ABC".index(_group),
        "x": _index * 0.5,
        "y": 20.0 + _index * 0.7,
        "day": _index % 4,
        "trend": 5.0 + (_index % 4) * 1.2,
        "residual": _index - 6.5,
        "feature": "g%d" % _index,
        "effect": -2.5 + _index * 0.3,
        "p_value": 0.001 + (_index % 5) * 0.01,
    })


def _csv_text() -> str:
    columns = ("group", "response", "x", "y", "day", "trend", "residual",
               "feature", "effect", "p_value")
    def cell(value):
        return value if isinstance(value, str) else repr(value)
    lines = [",".join(columns)]
    lines.extend(",".join(cell(row[column]) for column in columns) for row in _RECORDS)
    return "\n".join(lines) + "\n"


_ANALYSIS = """
import pandas as pd
frame = pd.read_csv(data)
bar = frame.groupby("group", sort=True)["response"].agg(["mean", "std"]).reset_index()
bar.columns = ["group", "mean", "sd"]
scatter = frame[["x", "y"]].dropna().reset_index(drop=True)
trend = frame.groupby("day", sort=True)["trend"].mean().reset_index(name="value")
hist = frame[["residual"]].rename(columns={"residual": "value"}).dropna().reset_index(drop=True)
box = frame[["group", "response"]].rename(columns={"response": "value"}).dropna().reset_index(drop=True)
emit.table("base_bar", bar, parents=["data"])
emit.table("base_scatter", scatter, parents=["data"])
emit.table("panel_a", trend, parents=["data"])
emit.table("panel_b", hist, parents=["data"])
emit.table("panel_c", box, parents=["data"])
emit.report({"rows": len(frame), "inference": "descriptive_only"})
"""

_RENDER = """
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

bar = data["base_bar"]
scatter = data["base_scatter"]
trend = data["panel_a"]
hist = data["panel_b"]
box = data["panel_c"]

fig = plt.figure(figsize=(7.2, 6.0))
outer = GridSpec(2, 1, figure=fig, height_ratios=[1.0, 1.55], hspace=0.55)
top = outer[0].subgridspec(1, 2, wspace=0.38)
bottom = outer[1].subgridspec(2, 2, height_ratios=[1.3, 1.0], wspace=0.38, hspace=0.55)
axes = [fig.add_subplot(top[0, 0]), fig.add_subplot(top[0, 1])]
axes += [fig.add_subplot(bottom[0, :]), fig.add_subplot(bottom[1, 0]),
         fig.add_subplot(bottom[1, 1])]
labels = ["A", "B", "C", "D", "E"]
titles = ["Group mean", "X vs Y", "Daily trend", "Residuals", "By group"]

axes[0].bar(bar["group"], bar["mean"], yerr=bar["sd"], color="#2a78d6",
            capsize=2, edgecolor="white")
axes[1].scatter(scatter["x"], scatter["y"], s=14, color="#2a78d6",
                edgecolors="white", linewidths=0.4)
axes[2].plot(trend["day"], trend["value"], marker="o", ms=3, color="#2a78d6")
axes[3].hist(hist["value"], bins=8, color="#8ab6e8", edgecolor="white")
groups = list(box["group"].drop_duplicates())
axes[4].boxplot([box.loc[box["group"] == group, "value"].to_numpy() for group in groups],
                tick_labels=groups, widths=0.55)
for ax, label, title in zip(axes, labels, titles):
    ax.set_title(title)
    ax.text(-0.10, 1.02, label, transform=ax.transAxes, fontweight="bold", va="bottom")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=6)
axes[0].set_ylabel("Response")
axes[1].set_xlabel("X"); axes[1].set_ylabel("Y")
axes[2].set_xlabel("Day"); axes[2].set_ylabel("Mean trend")
axes[3].set_xlabel("Residual"); axes[3].set_ylabel("Count")
axes[4].set_ylabel("Response")
"""

_MIXED_RENDER = """
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

relations = data["relations_data"]
bar = data["base_bar"]
panels = list(relations["panel"].drop_duplicates())
count = len(panels) + 1
fig = plt.figure(figsize=(7.2, 3.4))
grid = GridSpec(1, count, figure=fig, wspace=0.5)
axes = [fig.add_subplot(grid[0, index]) for index in range(count)]
for ax, panel in zip(axes, panels):
    selected = relations.loc[relations["panel"] == panel]
    ax.scatter(selected["x"], selected["y"], s=14, color="#2a78d6",
               edgecolors="white", linewidths=0.4)
    ax.set_xlabel(str(selected["x_label"].iloc[0]))
    ax.set_ylabel(str(selected["y_label"].iloc[0]))
    ax.set_title(str(selected["y_label"].iloc[0]))
axes[-1].bar(bar["group"], bar["mean"], yerr=bar["sd"], color="#2a78d6",
             capsize=2, edgecolor="white")
axes[-1].set_title("Group mean")
for index, ax in enumerate(axes):
    ax.text(-0.12, 1.02, chr(ord("A") + index), transform=ax.transAxes,
            fontweight="bold", va="bottom")
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=6)
"""


_VOLCANO_MIXED_RENDER = """
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

volcano = data["volcano_data"]
bar = data["base_bar"]
colors = {"not_significant": "#9aa0a6", "down": "#2e86de", "up": "#c0392b"}
fig = plt.figure(figsize=(7.2, 3.4))
grid = GridSpec(1, 2, figure=fig, wspace=0.4)
ax = fig.add_subplot(grid[0, 0])
for direction, color in colors.items():
    selected = volcano.loc[volcano["direction"] == direction]
    if not selected.empty:
        ax.scatter(selected["effect"], selected["neg_log10_p"], s=12,
                   color=color, linewidths=0, label=direction)
effect_threshold = float(volcano["effect_threshold"].iloc[0])
neg_threshold = float(volcano["neg_log10_threshold"].iloc[0])
ymin = min(0.0, float(volcano["neg_log10_p"].min()))
ymax = float(volcano["neg_log10_p"].max())
ax.plot([effect_threshold, effect_threshold], [ymin, ymax], color="#6b7280",
        linewidth=0.8, linestyle="--")
ax.plot([-effect_threshold, -effect_threshold], [ymin, ymax], color="#6b7280",
        linewidth=0.8, linestyle="--")
ax.plot([float(volcano["effect"].min()), float(volcano["effect"].max())],
        [neg_threshold, neg_threshold], color="#6b7280", linewidth=0.8, linestyle="--")
ax.set_xlabel(str(volcano["effect_label"].iloc[0]))
ax.set_ylabel("-log10(" + str(volcano["significance_label"].iloc[0]) + ")")
ax.set_title("Volcano")

ax2 = fig.add_subplot(grid[0, 1])
ax2.bar(bar["group"], bar["mean"], yerr=bar["sd"], color="#2a78d6",
        capsize=2, edgecolor="white")
ax2.set_title("Group mean")
for index, item in enumerate((ax, ax2)):
    item.text(-0.12, 1.02, chr(ord("A") + index), transform=item.transAxes,
              fontweight="bold", va="bottom")
    item.spines[["top", "right"]].set_visible(False)
    item.tick_params(labelsize=6)
"""


def _run_worker(tmp_path, analysis, tables, render_script, *, figure=_FIGURE,
                formats=("svg", "png"), title="Composite"):
    contract = normalize_output_contract({
        "version": 2,
        "tables": [{"name": name, "parents": ["data"]} for name in tables],
        "figures": [{"name": figure, "title": title, "formats": list(formats),
                     "parents": list(tables), "render_script": render_script}],
    })
    source = tmp_path / "measurements.csv"
    source.write_text(_csv_text(), encoding="utf-8")
    job = SandboxJob(
        script=analysis, job_id="composition-" + figure,
        inputs={"data": "data.csv"}, sources={"data": str(source)},
        expected_input_sha256={"data.csv": hashlib.sha256(source.read_bytes()).hexdigest()},
        outputs=output_slots(contract), output_contract=contract,
        dpi=150, timeout_sec=120,
    )
    return LocalBackend().run(job), contract


def _require_ok(result):
    assert result.ok, (
        "offline worker failed: exit={} backend={} message={!r} stderr={!r}".format(
            result.exit_code, result.backend, result.message, result.stderr[-2000:]))


def _frame(result, name):
    return decode_frame(strict_json(result.outputs["table:" + name]))


def _assert_frozen_receipt(result, manifest, tables, render_script):
    receipt = manifest.get("frozen_render_receipt")
    assert isinstance(receipt, dict), "composition must bind a frozen render receipt"
    assert receipt["version"] == 1
    assert receipt["renderer_sha256"] == hashlib.sha256(render_script.encode("utf-8")).hexdigest()
    expected = {name: typed_payload_sha256(strict_json(result.outputs["table:" + name]))
                for name in tables}
    assert receipt["data_sha256"] == expected
    assert receipt["geometry_status"] == "supported"
    assert receipt["geometry_unsupported"] == []
    return receipt


def test_two_base_plots_and_three_panel_asymmetrical_composition(tmp_path):
    result, _ = _run_worker(tmp_path, _ANALYSIS, _TABLES, _RENDER)
    _require_ok(result)

    receipt = json.loads(result.outputs["result"])
    assert receipt["status"] == "ok"
    assert receipt["figure_count"] == 1
    for name in _TABLES:
        assert receipt["slots"]["table:" + name]["status"] == "ok"
    for fmt in ("svg", "png"):
        assert receipt["slots"]["figure:composite:" + fmt]["status"] == "ok"
    assert receipt["slots"]["figure:composite:manifest"]["status"] == "ok"

    manifest = json.loads(result.outputs["figure:composite:manifest"])
    _assert_frozen_receipt(result, manifest, _TABLES, _RENDER)

    # Published bytes are exactly the bytes acknowledged by the render receipt.
    checks = manifest["render_receipt"]["checks"]
    for fmt in ("svg", "png"):
        blob = result.outputs["figure:composite:" + fmt]
        assert checks[fmt]["sha256"] == hashlib.sha256(blob).hexdigest()
        assert checks[fmt]["size_bytes"] == len(blob)

    # Five axes: two base plots in the top row, a wide hero plus two equal
    # companions below. Asymmetry is measured from the rendered manifest.
    axes = [item for item in manifest["objects"]
            if isinstance(item, dict) and item.get("role") == "axes"]
    assert len(axes) == 5
    widths = [item["bbox"]["x1"] - item["bbox"]["x0"] for item in axes]
    assert widths[0] == pytest.approx(widths[1], abs=1.0)
    assert widths[2] > widths[3] * 1.8
    assert widths[2] > widths[4] * 1.8
    assert widths[3] == pytest.approx(widths[4], abs=1.0)

    blocking = manifest["nature_skill_receipt"]["checks"]
    assert blocking["panel_alignment"]["status"] == "pass"
    assert blocking["pdf_text"]["status"] == "pass"
    assert blocking["pdf_collisions"]["status"] == "pass"

    # Every plotted value comes from the frozen tables; no sampling or padding.
    groups = sorted({row["group"] for row in _RECORDS})
    bar = _frame(result, "base_bar")
    assert list(bar["group"]) == groups
    assert list(bar["mean"]) == pytest.approx([
        statistics.mean(row["response"] for row in _RECORDS if row["group"] == group)
        for group in groups])
    assert list(bar["sd"]) == pytest.approx([
        statistics.stdev([row["response"] for row in _RECORDS if row["group"] == group])
        for group in groups])

    scatter = _frame(result, "base_scatter")
    assert list(scatter["x"]) == pytest.approx([row["x"] for row in _RECORDS])
    assert list(scatter["y"]) == pytest.approx([row["y"] for row in _RECORDS])

    trend = _frame(result, "panel_a")
    expected_days = sorted({row["day"] for row in _RECORDS})
    assert list(trend["day"]) == expected_days
    assert list(trend["value"]) == pytest.approx([
        statistics.mean([row["trend"] for row in _RECORDS if row["day"] == day])
        for day in expected_days])

    hist = _frame(result, "panel_b")
    assert list(hist["value"]) == pytest.approx([row["residual"] for row in _RECORDS])

    box = _frame(result, "panel_c")
    assert list(box["group"]) == [row["group"] for row in _RECORDS]
    assert list(box["value"]) == pytest.approx([row["response"] for row in _RECORDS])


def test_template_analysis_table_mixes_with_a_base_plot(tmp_path):
    """A real built-in template table and a base plot share one v2 figure."""
    from src.research.script_presets import get_preset

    preset = get_preset("relationship_panels", include_source=True)
    parameters = {"input_format": "csv", "x": "x", "ys": ["y", "trend"],
                  "labels": {"x": "X (s)", "y": "Y (g)", "trend": "Trend (a.u.)"}}
    tail = """
summary = frame.groupby("group", sort=True)["response"].agg(["mean", "std"]).reset_index()
summary.columns = ["group", "mean", "sd"]
emit.table("base_bar", summary, parents=["data"])
"""
    analysis = "parameters = " + repr(parameters) + "\n" + preset["source"] + tail
    tables = ("relations_data", "base_bar")
    result, _ = _run_worker(tmp_path, analysis, tables, _MIXED_RENDER,
                            figure="mixed", title="Template and base")
    _require_ok(result)

    receipt = json.loads(result.outputs["result"])
    assert receipt["status"] == "ok"
    assert receipt["figure_count"] == 1
    manifest = json.loads(result.outputs["figure:mixed:manifest"])
    _assert_frozen_receipt(result, manifest, tables, _MIXED_RENDER)

    relations = _frame(result, "relations_data")
    assert len(relations) == 2 * len(_RECORDS)
    assert list(relations["panel"].drop_duplicates()) == [0, 1]
    assert set(relations["x_label"]) == {"X (s)"}
    assert set(relations["y_label"]) == {"Y (g)", "Trend (a.u.)"}

    bar = _frame(result, "base_bar")
    groups = sorted({row["group"] for row in _RECORDS})
    assert list(bar["group"]) == groups
    assert list(bar["mean"]) == pytest.approx([
        statistics.mean(row["response"] for row in _RECORDS if row["group"] == group)
        for group in groups])

    checks = manifest["nature_skill_receipt"]["checks"]
    assert checks["panel_alignment"]["status"] == "pass"
    assert checks["pdf_collisions"]["status"] == "pass"


def test_nature_volcano_table_mixes_with_a_base_plot(tmp_path):
    """A real F2 template table and a base plot share one v2 figure."""
    from src.research.script_presets import get_preset

    try:
        preset = get_preset("nature_volcano", include_source=True)
    except ValueError:
        pytest.skip("F2 nature_volcano is not installed yet")
    parameters = {
        "input_format": "csv", "effect": "effect", "significance": "p_value",
        "effect_label": "log2 fold change", "significance_label": "p value",
        "effect_threshold": 1.0, "p_threshold": 0.05, "label_top": 0,
        "label_column": "feature",
    }
    tail = """
summary = frame.groupby("group", sort=True)["response"].agg(["mean", "std"]).reset_index()
summary.columns = ["group", "mean", "sd"]
emit.table("base_bar", summary, parents=["data"])
"""
    analysis = "parameters = " + repr(parameters) + "\n" + preset["source"] + tail
    tables = ("volcano_data", "base_bar")
    result, _ = _run_worker(tmp_path, analysis, tables, _VOLCANO_MIXED_RENDER,
                            figure="volcano_mixed", title="Volcano and base")
    _require_ok(result)

    manifest = json.loads(result.outputs["figure:volcano_mixed:manifest"])
    _assert_frozen_receipt(result, manifest, tables, _VOLCANO_MIXED_RENDER)
    volcano = _frame(result, "volcano_data")
    assert list(volcano["effect"]) == pytest.approx([row["effect"] for row in _RECORDS])
    assert list(volcano["neg_log10_p"]) == pytest.approx(
        [-math.log10(row["p_value"]) for row in _RECORDS])
    assert list(volcano["feature"]) == [row["feature"] for row in _RECORDS]

    report = json.loads(result.outputs["report"])
    assert report["input_rows"] == len(_RECORDS)
    assert report["plotted_rows"] == len(_RECORDS)
    assert report["excluded_rows"] == 0
    assert report["counts"] == {"up": 6, "down": 6, "not_significant": 6}

    checks = manifest["nature_skill_receipt"]["checks"]
    assert checks["panel_alignment"]["status"] == "pass"
    assert checks["pdf_collisions"]["status"] == "pass"


def test_nature_template_presets_declare_composable_v2_outputs():
    """Coordination check: F2 templates expose one table plus a separated renderer.

    The test is skipped until the concurrent F2 presets are installed. When they
    appear it does not execute them; it verifies the declared v2 contract can be
    combined with other tables in a single figure without chart-name routing.
    """
    from src.research.script_presets import list_presets

    presets = {item["script_ref"]["id"]: item for item in list_presets()}
    available = [presets[template_id] for template_id in _NATURE_TEMPLATE_IDS
                 if template_id in presets]
    if not available:
        pytest.skip("F2 nature templates are not installed yet; composition contract is ready")

    table_names, figure_parents = [], []
    for preset in available:
        outputs = preset["outputs"]
        assert outputs.get("version") == 2
        tables = outputs.get("tables", [])
        figures = outputs.get("figures", [])
        assert tables and len(figures) == 1
        declared = [table["name"] for table in tables]
        figure = figures[0]
        assert figure.get("render_script")
        # A template may declare auxiliary evidence tables (for example
        # excluded pairs) and its renderer uses only the tables it needs.
        assert figure["parents"] and set(figure["parents"]).issubset(declared)
        table_names.extend(declared)
        figure_parents.extend(figure["parents"])
    table_names = list(dict.fromkeys([*table_names, "base_scatter"]))
    parents = list(dict.fromkeys([*figure_parents, "base_scatter"]))
    contract = normalize_output_contract({
        "version": 2,
        "tables": [{"name": name, "parents": ["data"]} for name in table_names],
        "figures": [{"name": "mixed", "title": "Mixed template contract",
                     "formats": ["svg"], "parents": parents,
                     "render_script": "raise RuntimeError('metadata check only')"}],
    })
    assert contract["figures"][0]["parents"] == parents


def test_figure_methods_guidance_and_resource_stay_non_authorizing():
    pytest.importorskip("langchain_core")
    from src.agent.host_policy import system_prompt

    resource_id = "references/figure-methods.md"
    assert resource_id not in system_prompt(set())
    assert resource_id not in system_prompt({"read_skill", "web_search"})
    for tool in ("create_research_figure", "run_research_figure_script", "draw_svg"):
        assert resource_id in system_prompt({tool})

    resource = (Path(__file__).resolve().parents[3]
                / "skills" / "nature-figure" / "references" / "figure-methods.md")
    body = resource.read_text(encoding="utf-8")
    for token in ("bar", "line", "scatter", "errorbar", "boxplot", "histogram",
                  "Matplotlib", "Seaborn", "nature_volcano", "nature_roc",
                  "nature_dotplot", "nature_marginal", "nature_paired",
                  "frozen", "caption", "asymmetric", "visualization_enabled",
                  "grants no"):
        assert token in body


def test_skill_session_reads_archived_nature_identity_but_selection_is_current():
    """The root reader accepts archived guidance; new selection stays current."""
    pytest.importorskip("langchain_core")
    from dataclasses import dataclass
    from src.agent import research_skill_catalog as catalog
    from src.agent.skill_adapter import SkillSession, validate_scenario_selection

    archived = catalog._ARCHIVED_NATURE_IDENTITY
    assert catalog.has_identity("nature-figure", *archived)

    @dataclass
    class Identity:
        user_id: int = 1
        session_id: str = "session"
        user_turn_id: str = "turn"
        run_id: str = "run"

    session = SkillSession(Identity(), lambda: {"read_skill"})
    current = catalog.PINNED["nature-figure"]
    for version, sha256 in (current, archived):
        payload = session.page("nature-figure", version, sha256, "SKILL.md", max_chars=120)
        assert payload["skill"]["version"] == version
        assert payload["page"]["resource"] == "SKILL.md"
        assert payload["page"]["text"]
    methods = session.page("nature-figure", *current, "references/figure-methods.md", max_chars=20000)
    assert methods["page"]["sha256"] == hashlib.sha256(methods["page"]["text"].encode()).hexdigest()
    with pytest.raises(ValueError):
        validate_scenario_selection([
            {"id": "nature-figure", "version": archived[0], "sha256": archived[1]}])

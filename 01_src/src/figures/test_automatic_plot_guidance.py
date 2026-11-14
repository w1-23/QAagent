"""Synthetic shape/Artist/layout regressions; never read user research data."""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from src.figures.core import build_resolved_figure, capture_figure, _tick_label_problems
from src.figures.resolve import resolve_spec, select_table_figure, table_figure_guidance
from src.figures.spec import SpecError, parse_spec


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


def test_many_unique_labels_select_distribution_and_preserve_every_observation(monkeypatch):
    from src.figures import resolve
    frame = pd.DataFrame({"record": [f"record-{i:04d}-long-identifier" for i in range(85)],
                          "measurement": np.arange(85, dtype=float) / 7})
    raw = select_table_figure(frame, file_id="synthetic-file")
    assert raw["chart"] == "histogram"
    assert "x" not in raw and "group" not in raw
    assert raw["x_label"] == "measurement" and raw["y_label"] == "Count"
    monkeypatch.setattr(resolve, "_account_table", lambda *_: frame)
    resolved, info = resolve_spec(parse_spec(raw))
    rendered = build_resolved_figure(resolved)
    assert sum(p.get_height() for p in rendered.axes[0].patches) == 85
    assert info["retained_row_indices"] == list(range(85))
    np.testing.assert_array_equal(resolved.y_values, frame["measurement"])
    assert _tick_label_problems(rendered.figure) == []


def test_replicated_low_cardinality_group_selects_boxplot():
    frame = pd.DataFrame({"cohort": ["A"] * 4 + ["B"] * 4,
                          "measurement": [1.2, 2.1, 3.1, 4.3, 2.2, 3.4, 4.4, 5.7]})
    assert select_table_figure(frame, file_id="synthetic-file")["chart"] == "histogram"
    raw = select_table_figure(frame, file_id="synthetic-file", group_column="cohort")
    assert raw["chart"] == "boxplot" and raw["group"]["column"] == "cohort"
    assert raw["y"]["column"] == "measurement"


def test_small_unique_label_set_is_not_inferred_to_be_treatment():
    frame = pd.DataFrame({"record": ["A", "B", "C"], "measurement": [2.1, 3.2, 4.4]})
    assert select_table_figure(frame, file_id="synthetic")["chart"] == "histogram"


@pytest.mark.parametrize("frame", [
    pd.DataFrame({"a": [1.1, 2.2, 3.3], "b": [4.1, 5.2, 6.3]}),
    pd.DataFrame({"record": [1, 2, 3], "measurement": [4.1, 5.2, 6.3]}),
])
def test_ambiguous_metrics_and_numeric_ids_require_selection(frame):
    with pytest.raises(SpecError) as exc:
        select_table_figure(frame, file_id="synthetic")
    assert exc.value.code == "figure_selection_required"


def test_repeated_subject_identifiers_and_categories_are_not_implicit_treatments():
    frame = pd.DataFrame({"subject": ["A", "A", "B", "B"], "batch": ["X", "Y", "X", "Y"],
                          "measurement": [1.1, 2.2, 3.3, 4.4]})
    raw = select_table_figure(frame, file_id="synthetic")
    assert raw["chart"] == "histogram" and "group" not in raw


@pytest.mark.parametrize("group", ["absent", "subject"])
def test_explicit_invalid_or_unreplicated_groups_require_clarification(group):
    frame = pd.DataFrame({"subject": ["A", "B", "C"], "measurement": [1.1, 2.2, 3.3]})
    with pytest.raises(SpecError) as exc:
        select_table_figure(frame, file_id="synthetic", group_column=group)
    assert exc.value.code == "figure_selection_required"


def test_profile_uses_every_parsed_row_without_disclosing_labels():
    frame = pd.DataFrame({"record": [f"private-label-{i}" for i in range(220)],
                          "mixed": list(range(219)) + ["not a number"]})
    profile = table_figure_guidance(frame)
    assert profile["columns"][0]["possible_identifier"] is True
    assert profile["columns"][1]["numeric"] is False
    assert "private-label" not in str(profile)


def test_attachment_budget_truncation_is_refused(monkeypatch):
    from src.figures import resolve
    from src.research import tools
    monkeypatch.setattr(tools, "_load_frame", lambda *_: (pd.DataFrame({"m": [1, 2]}), {"truncated": True}))
    with pytest.raises(SpecError) as exc:
        resolve._attachment_table(0)
    assert exc.value.code == "truncated_source"


def _categorical_spec(labels, *, size=(7, 5)):
    return parse_spec({"chart": "bar", "x_label": "Category", "y_label": "Measurement (a.u.)",
                       "x": {"kind": "inline", "values": labels},
                       "y": {"kind": "inline", "values": list(range(1, len(labels) + 1))},
                       "style": {"figure_size_in": list(size)}})


def test_full_labels_fit_without_altering_numeric_artists_or_doubling_ticks():
    labels = [f"Full category label {i}" for i in range(12)]
    resolved, info = resolve_spec(_categorical_spec(labels))
    rendered = build_resolved_figure(resolved)
    ax = rendered.axes[0]
    assert _tick_label_problems(rendered.figure) == []
    assert [text.get_text() for text in ax.get_xticklabels()] == labels
    assert any(text.get_rotation() for text in ax.get_xticklabels())
    np.testing.assert_array_equal([patch.get_height() for patch in ax.patches], range(1, 13))
    np.testing.assert_allclose([p.get_x() + p.get_width() / 2 for p in ax.patches], range(12))
    assert all(not tick.label2.get_visible() for tick in ax.xaxis.get_major_ticks())
    assert info["retained_row_indices"] == list(range(12))


def test_impossible_label_density_is_refused_without_silent_sampling():
    labels = [f"Full sample label {i:03d}" for i in range(90)]
    resolved, _ = resolve_spec(_categorical_spec(labels, size=(4, 3)))
    with pytest.raises(SpecError) as exc:
        build_resolved_figure(resolved)
    assert exc.value.code == "unreadable_labels"
    assert len(resolved.series[0].values) == 90


def test_captured_script_rejects_crowded_labels_with_corrective_feedback():
    result = capture_figure("""
fig, ax = plt.subplots(figsize=(4, 3))
labels = [f'Full record label {i}' for i in range(60)]
ax.bar(labels, range(60))
""")
    assert result.error_type == "unreadable_labels"
    assert "figure-export/nature-figure" in result.error
    assert len(result.figure.axes[0].patches) == 60


def test_hidden_axes_do_not_report_invisible_tick_collisions():
    result = capture_figure("""
fig, ax = plt.subplots(figsize=(4, 3))
ax.set_xticks(range(60), [f'Hidden identifier label {i}' for i in range(60)])
ax.set_xlim(0, 60)
ax.axis('off')
ax.text(.5, .5, 'Synthetic diagram', transform=ax.transAxes)
""")
    assert not result.error


def test_captured_script_keeps_correct_coordinates_on_readable_layout():
    result = capture_figure("""
fig, ax = plt.subplots(figsize=(6, 4), constrained_layout=True)
ax.plot([1, 10, 100], [2, 4, 8], marker='o')
ax.set_xlabel('Dose (a.u.)'); ax.set_ylabel('Response (a.u.)')
""")
    assert not result.error
    line = result.figure.axes[0].lines[0]
    np.testing.assert_array_equal(line.get_xdata(), [1, 10, 100])
    np.testing.assert_array_equal(line.get_ydata(), [2, 4, 8])

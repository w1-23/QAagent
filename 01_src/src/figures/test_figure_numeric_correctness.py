"""S1 numeric regressions: inspect real Matplotlib artists, not export headers.

All inputs are synthetic inline values. Tests run in pytest's isolated tmp_path
and never read account files, attachments, historical outputs or the network.
"""
from __future__ import annotations

import json
import math
from dataclasses import replace

import matplotlib
matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
from matplotlib.container import BarContainer, ErrorbarContainer
import numpy as np
import pytest

from src.figures.core import build_resolved_figure, compile_spec
from src.figures.resolve import resolve_spec
from src.figures.spec import ColumnRef, ResolvedFigure, Series, SpecError, parse_spec


@pytest.fixture(autouse=True)
def isolated_figures(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield
    plt.close("all")


def _ref(values):
    return {"kind": "inline", "values": values}


def _spec(chart, x, y, *, group=None, error="none"):
    payload = {"chart": chart, "y": _ref(y), "error": error}
    if x is not None:
        payload["x"] = _ref(x)
    if group is not None:
        payload["group"] = _ref(group)
    return parse_spec(payload)


def _render(chart, x, y, **kwargs):
    resolved, info = resolve_spec(_spec(chart, x, y, **kwargs))
    rendered = build_resolved_figure(resolved)
    rendered.figure.canvas.draw()
    return rendered, resolved, info


def _points(rendered, index=0):
    artist = rendered.artist_for(f"axes_0.series_{index}")
    if hasattr(artist, "get_offsets"):
        return np.asarray(artist.get_offsets(), dtype=float)
    return np.column_stack((artist.get_xdata(), artist.get_ydata()))


def _intervals(ax, index=0):
    container = [c for c in ax.containers if isinstance(c, ErrorbarContainer)][index]
    return container.lines[2][0].get_segments()


@pytest.mark.parametrize("chart", ["line", "scatter", "errorbar"])
def test_pairwise_filter_preserves_numeric_x_and_row_identity(chart):
    rendered, resolved, info = _render(
        chart, [2, 9, 40, 75, 110], [3, "bad", 7, float("inf"), 11])
    np.testing.assert_allclose(_points(rendered), [[2, 3], [40, 7], [110, 11]])
    assert resolved.series[0].row_indices == ((0,), (2,), (4,))
    assert info["retained_row_indices"] == [0, 2, 4]
    assert info["excluded_rows"] == [
        {"row_index": 1, "reasons": ["invalid_y"]},
        {"row_index": 3, "reasons": ["invalid_y"]},
    ]
    json.dumps(resolved.as_dict(), allow_nan=False)
    json.dumps(info, allow_nan=False)


@pytest.mark.parametrize("chart", ["line", "scatter", "errorbar"])
def test_numeric_strings_use_numeric_spacing(chart):
    rendered, _, _ = _render(chart, ["1", "10", "100"], [2, 4, 8])
    np.testing.assert_allclose(_points(rendered), [[1, 2], [10, 4], [100, 8]])


def test_numeric_bar_centres_keep_real_x():
    rendered, _, _ = _render("bar", [2, 20, 100], [3, 7, 11])
    patches = rendered.axes[0].patches
    np.testing.assert_allclose([p.get_x() + p.get_width() / 2 for p in patches], [2, 20, 100])
    np.testing.assert_allclose([p.get_height() for p in patches], [3, 7, 11])


@pytest.mark.parametrize("chart", ["bar", "line", "scatter", "errorbar"])
def test_category_pairwise_filter_preserves_labels(chart):
    rendered, _, _ = _render(chart, ["A", "B", "C"], [1, None, 7])
    ax = rendered.axes[0]
    assert [text.get_text() for text in ax.get_xticklabels()] == ["A", "C"]
    if chart == "bar":
        np.testing.assert_allclose([p.get_height() for p in ax.patches], [1, 7])
    else:
        np.testing.assert_allclose(_points(rendered), [[0, 1], [1, 7]])


def test_invalid_x_and_missing_group_exclude_whole_rows_with_reasons():
    rendered, resolved, info = _render(
        "scatter", [1, None, 30, float("inf"), 50, "bad", 70],
        [10, 20, 30, None, 50, 60, 70], group=["a", "a", "b", "a", None, "b", "b"])
    np.testing.assert_allclose(_points(rendered, 0), [[1, 10]])
    np.testing.assert_allclose(_points(rendered, 1), [[30, 30], [70, 70]])
    assert resolved.series[1].row_indices == ((2,), (6,))
    assert info["excluded_rows"] == [
        {"row_index": 1, "reasons": ["invalid_x"]},
        {"row_index": 3, "reasons": ["invalid_y", "invalid_x"]},
        {"row_index": 4, "reasons": ["missing_group"]},
        {"row_index": 5, "reasons": ["invalid_x"]},
    ]


@pytest.mark.parametrize("chart", ["bar", "line", "scatter", "errorbar", "boxplot", "histogram"])
@pytest.mark.parametrize("x,y,group", [([1], [2, 3], None), ([1, 2], [3], None),
                                         ([1, 2], [3, 4], ["A"]),
                                         ([1, 2], [3, 4], ["A", "B", "C"])])
def test_length_mismatch_is_explicit_for_every_chart(chart, x, y, group):
    with pytest.raises(SpecError) as exc:
        resolve_spec(_spec(chart, x, y, group=group))
    assert exc.value.code == "length_mismatch"


@pytest.mark.parametrize("chart", ["bar", "line", "errorbar"])
def test_grouped_summaries_use_each_group_x_cell_without_broadcast(chart):
    rendered, resolved, _ = _render(
        chart, [1, 1, 10, 10, 10, 10, 100, 100],
        [2, 4, 6, 10, 20, 24, 30, 34],
        group=["A"] * 4 + ["B"] * 4, error="se")
    assert resolved.series[0].values == (3, 8)
    assert resolved.series[1].values == (22, 32)
    assert resolved.y_values == ()
    assert resolved.x_values == (1, 10, 100)
    assert resolved.series[0].row_indices == ((0, 1), (2, 3))
    assert resolved.series[1].row_indices == ((4, 5), (6, 7))
    if chart == "bar":
        bars = [c for c in rendered.axes[0].containers if isinstance(c, BarContainer)]
        assert [len(c.patches) for c in bars] == [2, 2]
        for c, xs, ys in zip(bars, [[0.8, 9.8], [10.2, 100.2]], [[3, 8], [22, 32]]):
            np.testing.assert_allclose([p.get_x() + p.get_width() / 2 for p in c.patches], xs)
            np.testing.assert_allclose([p.get_height() for p in c.patches], ys)
        expected_x = [[0.8, 9.8], [10.2, 100.2]]
    else:
        np.testing.assert_allclose(_points(rendered, 0), [[1, 3], [10, 8]])
        np.testing.assert_allclose(_points(rendered, 1), [[10, 22], [100, 32]])
        expected_x = [[1, 10], [10, 100]]
    for index, (xs, ys, errors) in enumerate(zip(expected_x, [[3, 8], [22, 32]], [[1, 2], [2, 2]])):
        expected = [[[x, y - e], [x, y + e]] for x, y, e in zip(xs, ys, errors)]
        np.testing.assert_allclose(_intervals(rendered.axes[0], index), expected)


def test_sparse_categorical_groups_share_axis_without_fake_points():
    rendered, _, _ = _render("line", ["early", "late", "late"], [2, 4, 9], group=["A", "A", "B"])
    np.testing.assert_allclose(_points(rendered, 0), [[0, 2], [1, 4]])
    np.testing.assert_allclose(_points(rendered, 1), [[1, 9]])
    assert [t.get_text() for t in rendered.axes[0].get_xticklabels()] == ["early", "late"]


def test_grouped_scatter_retains_individual_observations():
    rendered, resolved, _ = _render("scatter", [1, 1, 20], [2, 4, 9], group=["A", "A", "B"])
    np.testing.assert_allclose(_points(rendered, 0), [[1, 2], [1, 4]])
    np.testing.assert_allclose(_points(rendered, 1), [[20, 9]])
    assert resolved.series[0].row_indices == ((0,), (1,))


@pytest.mark.parametrize("chart", ["bar", "line", "errorbar"])
@pytest.mark.parametrize("kind,half_width", [("sd", math.sqrt(2)), ("se", 1),
                                              ("ci95", 12.706204736432095)])
def test_ungrouped_errors_describe_same_x_mean_and_replicates(chart, kind, half_width):
    rendered, resolved, info = _render(chart, [2, 2, 50], [2, 4, 99], error=kind)
    assert resolved.series[0].values == (3, 99)
    assert resolved.x_values == (2, 50)
    assert resolved.y_values == (3, 99)
    assert resolved.series[0].errors[1] is None
    assert resolved.series[0].row_indices == ((0, 1), (2,))
    segments = _intervals(rendered.axes[0])
    np.testing.assert_allclose(segments[0], [[2, 3 - half_width], [2, 3 + half_width]], rtol=1e-9)
    assert len(segments[1]) == 0
    if chart == "bar":
        np.testing.assert_allclose([p.get_height() for p in rendered.axes[0].patches], [3, 99])
    else:
        np.testing.assert_allclose(_points(rendered), [[2, 3], [50, 99]])
    assert info["warnings"][0]["code"] == "insufficient_replicates"
    assert info["warnings"][0]["row_indices"] == [2]


@pytest.mark.parametrize("count,t_critical", [(2, 12.706204736432095), (20, 2.093024054408263),
    (21, 2.0859634472658364), (30, 2.045229642132703), (100, 1.9842169515086827),
    (200, 1.971956544249395)])
def test_ci95_actual_artist_bounds_use_student_t_for_all_sample_sizes(count, t_critical):
    values = list(range(1, count + 1))
    rendered, _, _ = _render("errorbar", [17] * count, values, error="ci95")
    mean = (count + 1) / 2
    # Exact sample variance of the integers 1..n is n*(n+1)/12.
    half_width = t_critical * math.sqrt((count + 1) / 12)
    np.testing.assert_allclose(_points(rendered), [[17, mean]])
    np.testing.assert_allclose(_intervals(rendered.axes[0])[0],
                               [[17, mean - half_width], [17, mean + half_width]], rtol=1e-10)


@pytest.mark.parametrize("chart", ["bar", "line", "errorbar"])
def test_singletons_keep_all_points_and_do_not_invent_zero_intervals(chart):
    rendered, resolved, info = _render(chart, [1, 10, 100], [2, 4, 8], error="ci95")
    assert resolved.series[0].errors == (None, None, None)
    assert len(info["warnings"]) == 3
    assert all(len(segment) == 0 for segment in _intervals(rendered.axes[0]))
    if chart == "bar":
        assert len(rendered.axes[0].patches) == 3
    else:
        np.testing.assert_allclose(_points(rendered), [[1, 2], [10, 4], [100, 8]])


@pytest.mark.parametrize("chart", ["bar", "line", "scatter", "errorbar", "boxplot", "histogram"])
def test_weight_refused_by_parser_and_direct_resolver(chart):
    spec = _spec(chart, [1, 2], [3, 4])
    payload = spec.as_dict()
    payload["weight"] = _ref([1, 2])
    with pytest.raises(SpecError) as exc:
        parse_spec(payload)
    assert exc.value.code == "unsupported_weight"
    with pytest.raises(SpecError) as exc:
        resolve_spec(replace(spec, weight=ColumnRef(kind="inline", values=(1, 2))))
    assert exc.value.code == "unsupported_weight"


@pytest.mark.parametrize("series", [Series(values=(1,)), Series(values=(1, 2), errors=(1,)),
    Series(values=(1, 2), errors=(1, 2, 3)), Series(values=(1, 2), x_values=(4,)),
    Series(values=(1, 2), row_indices=((0,),))])
def test_renderer_rejects_shape_broadcast_or_truncation(series):
    with pytest.raises(SpecError) as exc:
        build_resolved_figure(ResolvedFigure(chart="line", x_values=(1, 10), series=(series,)))
    assert exc.value.code == "length_mismatch"


@pytest.mark.parametrize("series", [Series(values=(1, float("nan"))),
    Series(values=(1, 2), errors=(-1, 2)), Series(values=(1, 2), errors=(float("nan"), 2)),
    Series(values=(1, 2), errors=(float("inf"), 2))])
def test_renderer_refuses_invalid_resolved_numbers_instead_of_filtering(series):
    with pytest.raises(SpecError) as exc:
        build_resolved_figure(ResolvedFigure(chart="line", x_values=(1, 10), series=(series,)))
    assert exc.value.code == "invalid_number"


def test_legacy_shared_axis_series_still_works():
    rendered = build_resolved_figure(ResolvedFigure(
        chart="line", x_values=(1, 10), series=(Series(values=(3, 4)),)))
    rendered.figure.canvas.draw()
    np.testing.assert_allclose(_points(rendered), [[1, 3], [10, 4]])


def test_boxplot_retains_matching_group_rows_and_distribution():
    rendered, resolved, info = _render("boxplot", None, [1, None, 9, 3], group=["A", "B", "B", "A"])
    assert [s.values for s in resolved.series] == [(1, 3), (9,)]
    assert [s.row_indices for s in resolved.series] == [((0,), (3,)), ((2,),)]
    assert info["retained_row_indices"] == [0, 2, 3]
    # Each box contributes whiskers, caps, median, mean and fliers.
    medians = [line for line in rendered.axes[0].lines
               if len(line.get_xdata()) == 2 and np.ptp(line.get_xdata()) > 0.4]
    np.testing.assert_allclose([line.get_ydata() for line in medians], [[2, 2], [9, 9]])


def test_histogram_counts_only_retained_rows():
    rendered, resolved, info = _render("histogram", None, [1, 2, None, float("nan"), 2, 5])
    assert sum(patch.get_height() for patch in rendered.axes[0].patches) == 4
    assert resolved.series[0].row_indices == ((0,), (1,), (4,), (5,))
    assert info["retained_row_indices"] == [0, 1, 4, 5]


@pytest.mark.parametrize("chart", ["bar", "line", "scatter", "errorbar"])
def test_raw_repeated_categories_keep_each_observation(chart):
    rendered, resolved, _ = _render(chart, ["A", "A", "B"], [1, 2, 3])
    assert resolved.x_values == ("A", "A", "B")
    assert resolved.y_values == (1, 2, 3)
    assert [t.get_text() for t in rendered.axes[0].get_xticklabels()] == ["A", "A", "B"]
    if chart == "bar":
        np.testing.assert_allclose(
            [p.get_x() + p.get_width() / 2 for p in rendered.axes[0].patches], [0, 1, 2])
        np.testing.assert_allclose([p.get_height() for p in rendered.axes[0].patches], [1, 2, 3])
    else:
        np.testing.assert_allclose(_points(rendered), [[0, 1], [1, 2], [2, 3]])


@pytest.mark.parametrize("bad_x", [float("nan"), float("inf"), -float("inf")])
def test_renderer_refuses_nonfinite_numeric_x(bad_x):
    with pytest.raises(SpecError) as exc:
        build_resolved_figure(ResolvedFigure(chart="line", x_values=(1, bad_x),
                                            series=(Series(values=(3, 4)),)))
    assert exc.value.code == "invalid_number"


def test_all_invalid_rows_refused():
    with pytest.raises(SpecError) as exc:
        resolve_spec(_spec("line", [1, 2], [None, "bad"]))
    assert exc.value.code == "empty_data"


def test_statistics_use_only_jointly_retained_rows():
    rendered, resolved, info = _render(
        "errorbar", [5, 5, 5, None], [2, "bad", 4, 1000], error="se")
    np.testing.assert_allclose(_points(rendered), [[5, 3]])
    np.testing.assert_allclose(_intervals(rendered.axes[0]), [[[5, 2], [5, 4]]])
    assert resolved.series[0].row_indices == ((0, 2),)
    assert info["retained_row_indices"] == [0, 2]


def test_compiled_spec_reproduces_real_coordinates_and_intervals():
    _, resolved, _ = _render("errorbar", [5, 5, 90, 90], [2, 4, 10, 14], error="se")
    scope = {}
    exec(compile_spec(resolved), scope)
    rendered = scope["rendered"]
    np.testing.assert_allclose(_points(rendered), [[5, 3], [90, 12]])
    np.testing.assert_allclose(_intervals(rendered.axes[0]), [[[5, 2], [5, 4]], [[90, 10], [90, 14]]])

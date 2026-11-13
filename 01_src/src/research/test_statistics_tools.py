"""Tests for the statistics tool.

The tool's job is not to produce numbers; it is to produce *true* numbers,
and to refuse to produce a number when the design does not support one.
These tests check both halves of that.

The reference values are computed independently here — with plain Python and
with SciPy directly — so a match means the tool's arithmetic agrees with a
separate calculation, not merely with itself.
"""
from __future__ import annotations

import json
import statistics

import pytest
from scipy import stats

from src.research.context import reset_request_tables, set_request_tables
from src.research.statistics_tools import analyze_research_data

TWO_GROUP = """处理,重量
CK,10.1
CK,9.8
CK,10.5
CK,10.2
CK,9.9
CK,10.4
T1,12.3
T1,11.9
T1,12.8
T1,12.1
T1,12.5
T1,11.7
"""

THREE_GROUP = """处理,重量
CK,10.1
CK,9.8
CK,10.5
T1,12.3
T1,11.9
T1,12.8
T2,9.2
T2,9.5
T2,8.9
"""

PAIRED = """植株,处理,重量
P1,CK,10.0
P1,T1,12.0
P2,CK,9.5
P2,T1,12.5
P3,CK,10.4
P3,T1,11.8
P4,CK,10.1
P4,T1,12.2
"""

WITH_MISSING = """处理,重量
CK,10.1
CK,
CK,10.5
T1,12.3
T1,11.9
T1,
"""

ONE_EACH = """处理,重量
CK,1.0
T1,2.0
"""


def _run(table: str, **kwargs):
    token = set_request_tables([{"name": "trial.csv", "data": table.encode("utf-8")}])
    try:
        raw = analyze_research_data.invoke(kwargs)
    finally:
        reset_request_tables(token)
    return raw


def _payload(table: str, **kwargs) -> dict:
    raw = _run(table, **kwargs)
    return json.loads(raw)


# ── Descriptive statistics ────────────────────────────────────────────────

def test_descriptive_statistics_match_an_independent_calculation():
    payload = _payload(TWO_GROUP)
    values = [10.1, 9.8, 10.5, 10.2, 9.9, 10.4, 12.3, 11.9, 12.8, 12.1, 12.5, 11.7]
    described = payload["descriptive"]["重量"]
    assert described["n"] == len(values)
    assert described["mean"] == pytest.approx(sum(values) / len(values), abs=1e-6)
    assert described["sd"] == pytest.approx(stats.tstd(values), abs=1e-6)
    assert described["se"] == pytest.approx(stats.sem(values), abs=1e-6)
    assert described["median"] == pytest.approx(
        statistics.median(values), abs=1e-6)
    assert described["min"] == pytest.approx(min(values))
    assert described["max"] == pytest.approx(max(values))


def test_missing_values_are_counted():
    payload = _payload(WITH_MISSING)
    described = payload["descriptive"]["重量"]
    assert described["n"] == 4
    assert described["missing"] == 2
    assert described["missing_rate"] == pytest.approx(2 / 6, abs=1e-4)


def test_grouped_summary_covers_every_group():
    payload = _payload(THREE_GROUP, group_by_json='["处理"]')
    groups = {row["处理"]: row for row in payload["grouped"]}
    assert set(groups) == {"CK", "T1", "T2"}
    assert groups["CK"]["n"] == 3
    assert groups["CK"]["mean"] == pytest.approx(10.133333, abs=1e-5)


# ── Tests that match SciPy ────────────────────────────────────────────────

def test_independent_t_test_matches_scipy():
    payload = _payload(TWO_GROUP, group_by_json='["处理"]', test="ttest_ind")
    result = payload["test"]
    assert result["applicable"] is True
    expected = stats.ttest_ind([10.1, 9.8, 10.5, 10.2, 9.9, 10.4],
                               [12.3, 11.9, 12.8, 12.1, 12.5, 11.7],
                               equal_var=True)
    assert result["p"] == pytest.approx(float(expected.pvalue), abs=1e-6)
    assert result["statistic"] == pytest.approx(float(expected.statistic), abs=1e-6)


def test_mann_whitney_matches_scipy():
    payload = _payload(TWO_GROUP, group_by_json='["处理"]', test="mannwhitneyu")
    expected = stats.mannwhitneyu([10.1, 9.8, 10.5, 10.2, 9.9, 10.4],
                                  [12.3, 11.9, 12.8, 12.1, 12.5, 11.7],
                                  alternative="two-sided")
    assert payload["test"]["p"] == pytest.approx(float(expected.pvalue), abs=1e-6)


def test_anova_matches_scipy():
    payload = _payload(THREE_GROUP, group_by_json='["处理"]', test="anova")
    expected = stats.f_oneway([10.1, 9.8, 10.5], [12.3, 11.9, 12.8], [9.2, 9.5, 8.9])
    assert payload["test"]["p"] == pytest.approx(float(expected.pvalue), abs=1e-6)


def test_kruskal_matches_scipy():
    payload = _payload(THREE_GROUP, group_by_json='["处理"]', test="kruskal")
    expected = stats.kruskal([10.1, 9.8, 10.5], [12.3, 11.9, 12.8], [9.2, 9.5, 8.9])
    assert payload["test"]["p"] == pytest.approx(float(expected.pvalue), abs=1e-6)


def test_paired_test_matches_scipy():
    payload = _payload(PAIRED, group_by_json='["处理"]',
                       paired_by_json='["植株"]', test="ttest_rel")
    expected = stats.ttest_rel([10.0, 9.5, 10.4, 10.1], [12.0, 12.5, 11.8, 12.2])
    result = payload["test"]
    assert result["n_pairs"] == 4
    assert result["p"] == pytest.approx(float(expected.pvalue), abs=1e-6)


def test_pearson_correlation_matches_scipy():
    table = "x,y\n1,2.1\n2,4.2\n3,5.9\n4,8.1\n5,9.8\n6,12.2\n"
    payload = _payload(table, metrics_json='["x","y"]', test="pearson")
    expected = stats.pearsonr([1, 2, 3, 4, 5, 6], [2.1, 4.2, 5.9, 8.1, 9.8, 12.2])
    assert payload["test"]["p"] == pytest.approx(float(expected.pvalue), abs=1e-6)
    assert payload["test"]["statistic"] == pytest.approx(
        float(expected.statistic), abs=1e-6)


# ── Refusing what the design does not support ─────────────────────────────

def test_too_few_observations_gives_no_p_value():
    payload = _payload(ONE_EACH, group_by_json='["处理"]', test="ttest_ind")
    result = payload["test"]
    assert result["applicable"] is False
    assert "p" not in result
    assert result["reason"]


def test_a_test_that_needs_two_groups_refuses_three():
    payload = _payload(THREE_GROUP, group_by_json='["处理"]', test="ttest_ind")
    result = payload["test"]
    assert result["applicable"] is False
    assert "p" not in result


def test_a_paired_test_without_a_pair_key_refuses():
    payload = _payload(PAIRED, group_by_json='["处理"]', test="ttest_rel")
    assert payload["test"]["applicable"] is False
    assert "p" not in payload["test"]


def test_a_paired_test_with_unmatched_pairs_refuses():
    table = """植株,处理,重量
P1,CK,10.0
P1,T1,12.0
P2,CK,9.5
P3,T1,12.2
"""
    payload = _payload(table, group_by_json='["处理"]',
                       paired_by_json='["植株"]', test="ttest_rel")
    result = payload["test"]
    assert result["applicable"] is False
    assert "p" not in result


def test_an_unknown_test_name_is_refused_with_the_vocabulary():
    raw = _run(TWO_GROUP, test="wilcoxon")
    assert raw.startswith("[STATS_ARG]")
    assert "ttest_ind" in raw


def test_a_missing_column_is_refused_with_the_available_columns():
    raw = _run(TWO_GROUP, group_by_json='["nope"]')
    assert raw.startswith("[STATS_ARG]")
    assert "处理" in raw


def test_a_correlation_needs_two_columns():
    payload = _payload(TWO_GROUP, metrics_json='["重量"]', test="pearson")
    assert payload["test"]["applicable"] is False
    assert "p" not in payload["test"]


# ── Assumptions and effect sizes ──────────────────────────────────────────

def test_assumptions_are_reported_not_silently_swapped():
    payload = _payload(TWO_GROUP, group_by_json='["处理"]', test="ttest_ind")
    result = payload["test"]
    assert result["assumptions"], "正态性检验结果必须写进输出"
    assert "homoscedasticity" in result
    assert result["homoscedasticity"]["checked"] is True


def test_an_effect_size_is_reported_alongside_the_p_value():
    payload = _payload(TWO_GROUP, group_by_json='["处理"]', test="ttest_ind")
    effect = payload["test"]["effect_size"]
    assert effect["name"] == "hedges_g"
    assert effect["value"] is not None


def test_pairwise_comparisons_carry_a_holm_correction():
    payload = _payload(THREE_GROUP, group_by_json='["处理"]', test="anova")
    pairs = payload["test"]["pairwise"]
    assert len(pairs) == 3
    for pair in pairs:
        assert pair["p_holm"] >= pair["p"], "校正后的 p 必须不小于原始 p"


def test_holm_correction_matches_the_definition():
    """Holm-Bonferroni: sort the p values, multiply by (m - rank), keep it monotone.

    With p = (0.01, 0.03, 0.04) the sorted order is 0.01, 0.03, 0.04, so the
    multipliers are 3, 2, 1: 0.03, 0.06, 0.04 — and monotonicity pushes the
    last one up to 0.06.
    """
    from src.research.statistics_tools import _holm

    adjusted = _holm([0.01, 0.04, 0.03])
    assert adjusted[0] == pytest.approx(0.03, abs=1e-9)
    assert adjusted[1] == pytest.approx(0.06, abs=1e-9)
    assert adjusted[2] == pytest.approx(0.06, abs=1e-9)


def test_the_significance_threshold_is_honoured():
    payload = _payload(TWO_GROUP, group_by_json='["处理"]', test="ttest_ind",
                       alpha=0.999999)
    assert payload["test"]["significant"] is True
    assert payload["alpha"] == 0.999999

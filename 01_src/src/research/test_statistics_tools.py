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

@pytest.fixture(autouse=True)
def isolated_scientific_store(monkeypatch, tmp_path):
    from src import config, auth
    from src.core import tracing
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tracing, "get_user_id", lambda: 991001)
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"id": owner, "active": True})


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
                               equal_var=False)
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


# S1 regressions use small independent fixtures, never historical case inputs.

def test_holm_uses_unrounded_probabilities_at_a_decision_boundary():
    import pandas as pd
    from src.research.statistics_tools import _pairwise

    # For two shifted [0,1,2,3,4] samples, Welch's SE=1 and df=8.
    target_p = 0.0500004
    shift = stats.t.isf(target_p / 2, df=8)
    groups = {"A": pd.Series(range(5), dtype=float),
              "B": pd.Series([x + shift for x in range(5)]),
              "C": pd.Series([x + 20 for x in range(5)])}
    pairs = _pairwise(groups, alpha=0.0500002, parametric=True)
    # C comparisons are far below A/B, so A/B is last in Holm's ordering.
    raw = [float(stats.ttest_ind(groups[a], groups[b], equal_var=False).pvalue)
           for a, b in [("A", "B"), ("A", "C"), ("B", "C")]]
    ordered = sorted(enumerate(raw), key=lambda item: item[1])
    expected = {}
    previous = 0.0
    for rank, (index, p) in enumerate(ordered):
        previous = max(previous, min(1.0, (3 - rank) * p))
        expected[index] = previous
    assert pairs[0]["p"] == pytest.approx(target_p, abs=1e-12)
    assert pairs[0]["significant"] is False
    for index, pair in enumerate(pairs):
        assert pair["p"] == pytest.approx(raw[index], rel=1e-12, abs=1e-20)
        assert pair["p_holm"] == pytest.approx(expected[index], rel=1e-12, abs=1e-20)
    single = _pairwise({"A": groups["A"], "B": groups["B"]}, 0.0500002, True)[0]
    assert single["significant"] is False
    assert single["p_holm"] == pytest.approx(target_p, abs=1e-12)


def test_categorical_only_chi_square_matches_closed_form():
    import math
    table = "arm,response\n" + "a,yes\n" * 20 + "a,no\n" * 10 + "b,yes\n" * 10 + "b,no\n" * 20
    payload = _payload(table, group_by_json=["arm", "response"], test="chi2")
    result = payload["test"]
    # Yates correction: 4*(5-0.5)^2/15=5.4, df=1; survival is erfc(sqrt(x/2)).
    assert result["applicable"] is True
    assert result["statistic"] == pytest.approx(5.4)
    assert result["p"] == pytest.approx(math.erfc(math.sqrt(5.4 / 2)), rel=1e-12)
    # Cramer's V uses uncorrected chi-square: sqrt((4*25/15)/60)=1/3.
    assert result["effect_size"]["value"] == pytest.approx(1 / 3, abs=1e-6)
    assert payload["_tool_outcome"]["status"] == "complete"


def test_numeric_group_names_are_preserved_in_real_xlsx_pairing():
    import io
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["id", "arm", "value"])
    for row in [(1, 10, 2), (1, 20, 1), (2, 10, 5), (2, 20, 3),
                (3, 10, 9), (3, 20, 5)]:
        sheet.append(row)
    stream = io.BytesIO()
    workbook.save(stream)
    token = set_request_tables([{"name": "paired.xlsx", "data": stream.getvalue()}])
    try:
        payload = json.loads(analyze_research_data.invoke({
            "group_by_json": ["arm"], "metrics_json": ["value"],
            "paired_by_json": ["id"], "test": "ttest_rel"}))
    finally:
        reset_request_tables(token)
    result = payload["test"]
    expected = stats.ttest_1samp([1, 2, 4], popmean=0)
    assert result["applicable"] is True
    assert result["n_pairs"] == 3
    assert result["p"] == pytest.approx(expected.pvalue, rel=1e-12)
    assert result["mean_difference"] == pytest.approx(7 / 3, abs=1e-6)
    assert result["pairing"]["incomplete_pairs"] == 0


@pytest.mark.parametrize("extra,field,count", [
    ("P1,CK,99\n", "duplicate_pair_group_rows", 2),
    ("P1,CK,\n", "duplicate_pair_group_rows", 2),
    ("P5,CK,1\n", "incomplete_pairs", 1),
    ("P5,CK,1\nP5,T1,\n", "incomplete_pairs", 1),
    (",CK,1\n", "missing_key_rows", 1),
    ("P5,,1\n", "missing_key_rows", 1),
])
def test_pairing_refuses_duplicates_or_missing_rows_even_with_four_valid_pairs(extra, field, count):
    payload = _payload(PAIRED + extra, group_by_json=["处理"], metrics_json=["重量"],
                       paired_by_json=["植株"], test="ttest_rel")
    result = payload["test"]
    assert result["applicable"] is False
    assert "p" not in result
    assert result["pairing"][field] == count
    assert payload["statistics_status"] == "not_applicable"
    assert payload["_tool_outcome"]["status"] == "not_run"


@pytest.mark.parametrize("kwargs", [
    {"group_by_json": ["处理", "植株"], "test": "ttest_ind"},
    {"group_by_json": ["处理"], "paired_by_json": ["植株", "重量"], "test": "ttest_rel"},
    {"group_by_json": ["处理"], "paired_by_json": ["植株"], "test": "ttest_ind"},
])
def test_unsupported_design_fields_are_not_silently_ignored(kwargs):
    result = _payload(PAIRED, metrics_json=["重量"], **kwargs)["test"]
    assert result["applicable"] is False
    assert "p" not in result


@pytest.mark.parametrize("alpha", [0, 1, -0.1, 2, float("nan"), float("inf"), -float("inf")])
def test_alpha_must_be_finite_and_strictly_between_zero_and_one(alpha):
    assert _run(TWO_GROUP, alpha=alpha).startswith("[STATS_ARG]")


@pytest.mark.parametrize("value", ["inf", "-inf"])
def test_infinite_input_is_explicitly_rejected(value):
    assert _run("x,y\n1,2\n2,3\n" + value + ",4\n", test="pearson",
                metrics_json=["x", "y"]).startswith("[STATS_ARG]")


@pytest.mark.parametrize("method", ["pearson", "spearman"])
def test_constant_correlation_is_not_applicable_not_nan(method):
    payload = _payload("x,y\n1,2\n1,3\n1,4\n", test=method, metrics_json=["x", "y"])
    assert payload["test"]["applicable"] is False
    assert "p" not in payload["test"]
    assert "NaN" not in json.dumps(payload)


@pytest.mark.parametrize("method", ["ttest_ind", "anova", "kruskal"])
def test_degenerate_group_tests_have_no_probability(method):
    payload = _payload("g,v\na,1\na,1\nb,1\nb,1\n", test=method,
                       group_by_json=["g"], metrics_json=["v"])
    assert payload["test"]["applicable"] is False
    assert "p" not in payload["test"]
    assert "NaN" not in json.dumps(payload)


@pytest.mark.parametrize("difference", [0, 1])
def test_zero_variance_paired_differences_are_not_applicable(difference):
    table = "id,g,v\n" + "".join(f"{i},a,{i}\n{i},b,{i + difference}\n" for i in range(3))
    payload = _payload(table, test="ttest_rel", group_by_json=["g"],
                       paired_by_json=["id"], metrics_json=["v"])
    assert payload["test"]["applicable"] is False
    assert "p" not in payload["test"]
    assert payload["test"]["pairing"]["complete_pairs"] == 3
    assert "NaN" not in json.dumps(payload)


def test_degenerate_pairwise_test_is_partial_and_excluded_from_holm():
    payload = _payload("g,v\na,1\na,1\nb,1\nb,1\nc,1\nc,2\nc,3\n",
                       test="anova", group_by_json=["g"], metrics_json=["v"])
    pairs = payload["test"]["pairwise"]
    assert pairs[0]["applicable"] is False
    assert "p" not in pairs[0]
    assert payload["statistics_status"] == "partial"
    assert payload["_tool_outcome"]["status"] == "partial"
    expected = stats.ttest_ind([1, 1], [1, 2, 3], equal_var=False).pvalue
    from statsmodels.stats.multitest import multipletests
    # Omnibus and every applicable post-hoc comparison form the declared family.
    family = [payload["test"]["p"], expected, expected]
    assert payload["multiplicity"]["hypotheses"] == 3
    assert pairs[1]["p_holm"] == pytest.approx(multipletests(family, method="holm")[1][1], rel=1e-12)


def test_assumptions_use_the_requested_alpha():
    import pandas as pd
    from src.research.statistics_tools import _assumption_normality, _assumption_homoscedasticity

    a, b = pd.Series([1, 2, 3, 4, 10]), pd.Series([1, 3, 5, 7, 20])
    shapiro_p = stats.shapiro(a).pvalue
    levene_p = stats.levene(a, b).pvalue
    for alpha in [0.001, 0.9]:
        normality = _assumption_normality(a, alpha)
        variance = _assumption_homoscedasticity([a, b], alpha)
        assert normality["reject_normality_at_alpha"] == bool(shapiro_p < alpha)
        assert variance["reject_equal_variances_at_alpha"] == bool(levene_p < alpha)
        assert "normal_at_alpha" not in normality
        assert "equal_variances" not in variance
        assert normality["caveat"] and variance["caveat"]


def test_recommended_alternative_does_not_claim_execution_or_switch_posthoc():
    table = "g,v\n" + "".join(f"{g},{v + offset}\n" for g, offset in [("a", 0), ("b", 10), ("c", 20)]
                               for v in [0, 0, 0, 0, 10])
    result = _payload(table, test="anova", group_by_json=["g"], metrics_json=["v"])["test"]
    assert "alternative_recommended" not in result
    assert result["selection_basis"] == "explicit classical one-way ANOVA request"
    assert "alternative_used" not in result
    assert result["test"] == "单因素方差分析"
    assert all(pair["test"] == "Welch t 检验" for pair in result["pairwise"])


def test_cliffs_delta_has_first_minus_second_direction_and_tie_handling():
    import pandas as pd
    from src.research.statistics_tools import _cliffs_delta

    a, b = [1, 2, 2, 8], [0, 2, 3]
    expected = sum((x > y) - (x < y) for x in a for y in b) / (len(a) * len(b))
    assert _cliffs_delta(pd.Series(a), pd.Series(b)) == pytest.approx(expected, abs=1e-6)
    assert _cliffs_delta(pd.Series(b), pd.Series(a)) == pytest.approx(-expected, abs=1e-6)
    assert _cliffs_delta(pd.Series([4, 5]), pd.Series([1, 2])) == 1
    assert _cliffs_delta(pd.Series([2, 2]), pd.Series([2, 2])) == 0


def test_real_parser_truncation_is_disclosed_and_partial(monkeypatch):
    from src.research import tools
    from src.tools.outcome import normalize_tool_outcome

    monkeypatch.setattr(tools, "_MAX_TABLE_ROWS", 4)
    raw = _run("x\n" + "".join(f"{x}\n" for x in range(10)))
    payload = json.loads(raw)
    assert payload["source"]["truncated"] is True
    assert payload["source"]["processed_rows"] == 4
    assert payload["source"]["truncation_reasons"]
    assert payload["descriptive"]["x"]["mean"] == 1.5
    outcome = normalize_tool_outcome("analyze_research_data", raw)
    assert outcome.status == "partial"
    assert outcome.reason_class == "coverage_incomplete"


@pytest.mark.parametrize("fmt", ["xlsx", "csv"])
def test_export_protects_formula_strings_in_headers_and_cells(monkeypatch, tmp_path, fmt):
    import csv
    from openpyxl import load_workbook
    from src.research import tools
    from src.tools.outcome import normalize_tool_outcome

    url = "/api/v2/session/s1-test/artifacts/" + "a" * 32
    target = tmp_path / ("result." + fmt)
    def save(filename, content, mime):
        target.write_bytes(content)
        return url
    monkeypatch.setattr(tools, "_save_artifact", save)
    raw = _run("=group,=value\n=SUM(1),-2\n+bad,1\n@bad,3\n-bad,5\n",
               group_by_json=["=group"], metrics_json=["=value"], output_format=fmt)
    payload = json.loads(raw)
    if fmt == "xlsx":
        workbook = load_workbook(target, data_only=False)
        cells = [cell for row in workbook.active for cell in row]
        values = [cell.value for cell in cells]
        assert all(cell.data_type != "f" for cell in cells)
        assert -2 in values  # Numeric negatives remain numeric.
        workbook.close()
    else:
        with target.open(encoding="utf-8-sig", newline="") as handle:
            values = [cell for row in csv.reader(handle) for cell in row]
    for value in ["'=group", "'=value", "'=SUM(1)", "'+bad", "'@bad", "'-bad"]:
        assert value in values
    assert payload["export_status"] == "complete"
    outcome = normalize_tool_outcome("analyze_research_data", raw)
    assert outcome.status == "complete"
    assert outcome.artifacts == (url,)


def test_export_failure_does_not_erase_completed_statistics_or_report_complete(monkeypatch):
    from src.research import tools
    from src.tools.outcome import normalize_tool_outcome, safe_tool_step

    def fail(*args, **kwargs):
        raise OSError("test disk unavailable")
    monkeypatch.setattr(tools, "_save_artifact", fail)
    raw = _run(TWO_GROUP, test="ttest_ind", group_by_json=["处理"], output_format="xlsx")
    payload = json.loads(raw)
    assert payload["statistics_status"] == "complete"
    assert payload["test"]["applicable"] is True
    assert payload["export_status"] == "failed"
    assert "download_url" not in payload
    outcome = normalize_tool_outcome("analyze_research_data", raw)
    assert outcome.status == "partial"
    assert outcome.reason_code == "tool_error"
    assert outcome.artifacts == ()
    assert safe_tool_step("analyze_research_data", raw)["retryable"] is False


def test_malformed_lists_and_unknown_export_formats_are_argument_errors():
    assert _run(TWO_GROUP, group_by_json="not-json").startswith("[STATS_ARG]")
    assert _run(TWO_GROUP, output_format="pdf").startswith("[STATS_ARG]")
    assert _run(TWO_GROUP, metrics_json=["重量", "重量"]).startswith("[STATS_ARG]")

"""Small offline engineering designs; no scientific benchmark material."""
import io
import json
import math

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from src.research.context import set_request_tables, reset_request_tables
from src.research.statistics_tools import analyze_research_data


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    from src import config, auth
    from src.core import tracing
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tracing, "get_user_id", lambda: 991201)
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"id": owner, "active": True})


def run(frame, **kwargs):
    token = set_request_tables([{"name": "engineering.csv", "data": frame.to_csv(index=False).encode()}])
    try:
        return json.loads(analyze_research_data.invoke(kwargs))
    finally:
        reset_request_tables(token)


def design(kind, **kwargs):
    return {"kind": kind, "experimental_unit": "plant", "independent_units": True, **kwargs}


def test_complete_precision_and_shared_results_survive_reload():
    from src.research.analysis_results import load_analysis_result, load_dataset_snapshot
    frame = pd.DataFrame({"g": ["a"] * 4 + ["b"] * 4,
                          "v": [0.123456789123, .2, .3, .4, .6, .7, .8, .95]})
    result = run(frame, group_by_json=["g"], metrics_json=["v"], test="ttest_ind", units_json={"v": "g"})
    assert result["snapshot_status"] == "complete"
    stored = load_analysis_result(991201, result["analysis_result_ref"]).to_dict()
    snapshot = load_dataset_snapshot(991201, result["dataset_ref"])
    assert stored["outputs"]["descriptive"]["v"]["mean"] == frame.v.mean()
    assert stored["outputs"]["test"]["statistic"] == result["test"]["statistic"]
    assert snapshot.to_dict()["units"] == {"v": "g"}
    assert snapshot.to_dict()["row_ids"] == list(range(2, 10))
    # CSV parsing retains original cell text; consumers coerce only explicitly bound metrics.
    assert float(snapshot.to_frame().loc[2, "v"]) == frame.v[0]
    assert stored["dataset_ref"] == result["dataset_ref"]


def test_reanalysis_uses_same_dataset_version_without_upload_and_rechecks_owner_and_units(monkeypatch):
    from src.core import tracing
    initial = run(pd.DataFrame({"g": ["a"] * 3 + ["b"] * 3, "v": [1, 2, 4, 3, 5, 8]}),
                  group_by_json=["g"], metrics_json=["v"], test="ttest_ind", units_json={"v": "g"})
    args = {"dataset_ref": initial["dataset_ref"], "test": "ttest_ind",
            "group_by_json": ["g"], "metrics_json": ["v"], "alpha": .01}
    token = set_request_tables([])
    try:
        repeated = json.loads(analyze_research_data.invoke(args))
        assert repeated["dataset_ref"] == initial["dataset_ref"]
        assert repeated["analysis_result_ref"] != initial["analysis_result_ref"]
        assert repeated["test"]["p"] == initial["test"]["p"]
        assert repeated["test"]["included_row_ids"] == initial["test"]["included_row_ids"]
        assert repeated["test"]["confidence_interval"]["level"] == .99
        assert repeated["design"]["units"] == {"v": "g"}
        assert analyze_research_data.invoke({**args, "units_json": {"v": "kg"}}).startswith("[STATS_ARG]")
        monkeypatch.setattr(tracing, "get_user_id", lambda: 991202)
        assert analyze_research_data.invoke(args).startswith("[STATS_INPUT_ERROR]")
    finally:
        reset_request_tables(token)


def test_every_requested_response_and_all_pairwise_hypotheses_share_holm_family():
    from statsmodels.stats.multitest import multipletests
    frame = pd.DataFrame({"g": ["a"] * 4 + ["b"] * 4,
                          "v": [1, 2, 3, 4, 2, 4, 5, 8],
                          "w": [4, 5, 9, 10, 3, 5, 7, 11]})
    output = run(frame, group_by_json=["g"], metrics_json=["v", "w"], test="ttest_ind")
    assert output["coverage"]["completed_calculations"] == 2
    assert [r["metrics"] for r in output["tests"]] == [["v"], ["w"]]
    expected = [stats.ttest_ind(frame.loc[frame.g == "a", c], frame.loc[frame.g == "b", c], equal_var=False).pvalue for c in ["v", "w"]]
    adjusted = multipletests(expected, method="holm")[1]
    for result, p, adj in zip(output["tests"], expected, adjusted):
        assert result["p"] == pytest.approx(p, rel=1e-13)
        assert result["p_holm"] == pytest.approx(adj, rel=1e-13)
    assert output["multiplicity"]["hypotheses"] == 2


def test_all_correlation_pairs_are_computed_with_joint_row_exclusions():
    frame = pd.DataFrame({"x": [1, 2, None, 4, 5], "y": [2, None, 3, 5, 9], "z": [9, 7, 5, 3, 1]})
    output = run(frame, test="pearson", metrics_json=["x", "y", "z"])
    assert len(output["tests"]) == 3
    first = output["tests"][0]
    assert first["included_row_ids"] == [2, 5, 6]
    assert {r["row_id"] for r in first["excluded_rows"]} == {3, 4}
    assert first["p"] == pytest.approx(stats.pearsonr([1, 4, 5], [2, 5, 9]).pvalue)


def test_one_unsupported_metric_is_an_explicit_partial_result():
    output = run(pd.DataFrame({"g": ["a"] * 3 + ["b"] * 3,
                              "v": [1, 2, 4, 3, 5, 7], "constant": [1] * 6}),
                 test="ttest_ind", metrics_json=["v", "constant"], group_by_json=["g"])
    assert output["coverage"]["completed_calculations"] == 1
    assert output["coverage"]["rejected_calculations"] == 1
    assert output["statistics_status"] == "partial"
    assert output["_tool_outcome"]["status"] == "partial"
    assert "p" not in output["tests"][1]


def test_shapiro_and_levene_diagnostics_do_not_choose_the_method(monkeypatch):
    from src.research import statistics_tools as module
    frame = pd.DataFrame({"g": ["a"] * 3 + ["b"] * 3, "v": [1, 2, 5, 3, 8, 12]})
    for p in [0.000001, 0.99999]:
        monkeypatch.setattr(module, "_assumption_normality", lambda *args: {"checked": True, "p": p})
        monkeypatch.setattr(module, "_assumption_homoscedasticity", lambda *args: {"checked": True, "p": p})
        output = run(frame, group_by_json=["g"], metrics_json=["v"], test="ttest_ind")
        assert output["test"]["test"] == "Welch 校正 t 检验"
        assert output["test"]["p"] == pytest.approx(stats.ttest_ind([1, 2, 5], [3, 8, 12], equal_var=False).pvalue)


@pytest.mark.parametrize("n", [2, 20, 21, 25])
def test_mean_intervals_always_use_student_t(n):
    frame = pd.DataFrame({"v": np.arange(n) / 7})
    result = run(frame)["descriptive"]["v"]
    expected = stats.t.interval(.95, n - 1, loc=frame.v.mean(), scale=stats.sem(frame.v))
    assert result["confidence_interval"]["low"] == pytest.approx(expected[0], rel=1e-13)
    assert result["confidence_interval"]["high"] == pytest.approx(expected[1], rel=1e-13)


def test_regression_matches_independent_matrix_and_hc3_covariance():
    import statsmodels.api as sm
    frame = pd.DataFrame({"x": [0, 1, 2, 3, 4, 5, 6, 7],
                          "y": [1, 2.7, 4.4, 8.1, 8.2, 12.2, 12.4, 15.1]})
    result = run(frame, test="regression", metrics_json=["y"],
                 design_json=design("regression", predictors=["x"]))["test"]
    expected = sm.OLS(frame.y, sm.add_constant(frame.x)).fit(cov_type="HC3")
    assert result["applicable"] is True
    assert result["coefficients"][1]["estimate"] == pytest.approx(expected.params.x, rel=1e-13)
    assert result["coefficients"][1]["p"] == pytest.approx(expected.pvalues.x, rel=1e-13)
    assert result["coefficients"][1]["confidence_interval"]["low"] == pytest.approx(expected.conf_int().loc["x", 0])


def test_factorial_interaction_recovers_known_contrast():
    rows = [(a, b, 10 + 2 * a + 3 * b + 4 * a * b + error)
            for a in [0, 1] for b in [0, 1] for error in [-.4, .1, .3]]
    frame = pd.DataFrame(rows, columns=["a", "b", "y"])
    result = run(frame, test="factorial", metrics_json=["y"], group_by_json=["a", "b"],
                 design_json=design("factorial", factors=["a", "b"], interactions=True))["test"]
    assert result["applicable"] is True
    interaction = next(c for c in result["coefficients"] if ":" in c["term"])
    assert interaction["estimate"] == pytest.approx(4)
    incomplete = frame.loc[~((frame.a == 1) & (frame.b == 1))]
    assert run(incomplete, test="factorial", metrics_json=["y"],
               design_json=design("factorial", factors=["a", "b"]))["test"]["applicable"] is False


def test_rcbd_accounts_for_complete_blocks_and_refuses_duplicate_cells():
    frame = pd.DataFrame({"block": [1, 1, 2, 2, 3, 3, 4, 4], "arm": ["a", "b"] * 4,
                          "y": [10, 12, 20, 23, 30, 31, 40, 42.5]})
    kwargs = {"test": "blocked", "metrics_json": ["y"],
              "design_json": design("blocked", factors=["arm"], block="block")}
    result = run(frame, **kwargs)["test"]
    assert result["applicable"] is True
    treatment = next(c for c in result["coefficients"] if c["term"].startswith("C(v1)"))
    assert treatment["estimate"] == pytest.approx((2 + 3 + 1 + 2.5) / 4)
    assert run(pd.concat([frame, frame.iloc[:1]]), **kwargs)["test"]["applicable"] is False
    assert run(frame.iloc[:-1], **kwargs)["test"]["applicable"] is False


def test_repeated_two_levels_matches_squared_paired_t_and_fails_on_missing_or_duplicate_subject_cells():
    rows = [(i, t, 3 + i * 2 + t * (1 + i / 3)) for i in range(6) for t in [0, 1]]
    frame = pd.DataFrame(rows, columns=["plant", "time", "y"])
    kwargs = {"test": "repeated", "metrics_json": ["y"],
              "design_json": design("repeated", subject="plant", within=["time"])}
    result = run(frame, **kwargs)["test"]
    expected = stats.ttest_rel(frame.loc[frame.time == 0, "y"], frame.loc[frame.time == 1, "y"])
    assert result["applicable"] is True
    assert result["effects"][0]["statistic"] == pytest.approx(expected.statistic ** 2)
    assert result["effects"][0]["p"] == pytest.approx(expected.pvalue)
    assert run(frame.iloc[:-1], **kwargs)["test"]["applicable"] is False
    assert run(pd.concat([frame, frame.iloc[:1]]), **kwargs)["test"]["applicable"] is False


def test_mixed_random_intercepts_match_direct_statsmodels_fit():
    import statsmodels.formula.api as smf
    rng = np.random.default_rng(20261114)
    subject = np.repeat(np.arange(12), 4)
    x = np.tile([0, 1, 2, 3], 12)
    y = 2 + .8 * x + np.repeat(rng.normal(0, 3, 12), 4) + rng.normal(0, .3, 48)
    frame = pd.DataFrame({"plant": subject, "x": x, "y": y})
    result = run(frame, test="mixed", metrics_json=["y"],
                 design_json=design("mixed", subject="plant", predictors=["x"]))["test"]
    expected = smf.mixedlm("y ~ x", frame, groups=frame.plant).fit(reml=True, method="lbfgs", disp=False)
    assert result["applicable"] is True
    assert result["coefficients"][1]["estimate"] == pytest.approx(expected.fe_params.x)
    assert result["random_intercept_variance"] == pytest.approx(expected.cov_re.iloc[0, 0])


def test_power_and_required_sample_size_match_statsmodels():
    from statsmodels.stats.power import TTestIndPower
    frame = pd.DataFrame({"v": [1, 2]})
    method = TTestIndPower()
    result = run(frame, test="power", design_json=design("power", power_test="independent_t", effect_size=.5, nobs=40))["test"]
    assert result["power"] == pytest.approx(method.power(.5, 40, .05, ratio=1))
    solved = run(frame, test="power", design_json=design("power", power_test="independent_t", effect_size=.5, target_power=.8))["test"]
    assert solved["nobs_required"] == math.ceil(method.solve_power(.5, power=.8, alpha=.05))
    assert solved["nobs_continuous"] == pytest.approx(method.solve_power(.5, power=.8, alpha=.05))


def test_prospective_power_needs_no_fabricated_observation_file():
    from src.research.analysis_results import load_dataset_snapshot
    token = set_request_tables([])
    try:
        output = json.loads(analyze_research_data.invoke({"test": "power",
            "design_json": design("power", power_test="paired_t", effect_size=.6, target_power=.8)}))
    finally:
        reset_request_tables(token)
    assert output["test"]["applicable"] is True
    assert output["test"]["data_usage"] == "design_parameters_only"
    assert output["test"]["included_row_ids"] == []
    snap = load_dataset_snapshot(991201, output["dataset_ref"]).to_dict()
    assert snap["source"]["source_type"] == "design_parameters"
    assert snap["source"]["file_id"] is None
    assert snap["data"] == []
    assert output["_tool_outcome"]["status"] == "complete"


def test_seeded_block_randomization_is_balanced_and_reproducible():
    frame = pd.DataFrame({"plant": [f"p{i}" for i in range(12)], "block": [1] * 6 + [2] * 6})
    declared = design("randomization", treatments=["CK", "T1", "T2"], seed=17, block="block")
    kwargs = {"test": "randomization", "design_json": declared}
    first, second = run(frame, **kwargs)["test"], run(frame, **kwargs)["test"]
    assert first["assignments"] == second["assignments"]
    allocation = pd.DataFrame(first["assignments"])
    assert (allocation.groupby(["block", "treatment"]).size() == 2).all()
    assert {a["row_id"] for a in first["assignments"]} == set(range(2, 14))
    assert run(frame.iloc[:-1], **kwargs)["test"]["applicable"] is False


@pytest.mark.parametrize("method,declared", [
    ("regression", {"predictors": ["x"]}),
    ("regression", design("regression", predictors=["x"], block="plant")),
    ("regression", design("regression", predictors=["x"], unknown_structure=True)),
    ("factorial", design("factorial", factors=["x", "plant"], interactions="false")),
    ("power", design("power", power_test="independent_t", effect_size=.5, nobs=5, target_power=.8)),
    ("power", design("power", power_test="paired_t", effect_size=-1, nobs=5)),
    ("randomization", design("randomization", treatments=["a", "b"], seed=True)),
])
def test_invalid_or_unrepresented_semantic_designs_fail_closed(method, declared):
    output = run(pd.DataFrame({"plant": [1, 2, 3, 4], "x": [1, 2, 3, 4], "y": [2, 3, 4, 7]}),
                 test=method, metrics_json=["y"], design_json=declared)
    assert output["test"]["applicable"] is False
    assert "p" not in output["test"]
    assert output["test"]["reason"]


def test_inline_export_includes_exact_result_versions_and_structured_card_facts(monkeypatch):
    from openpyxl import load_workbook
    from src.research import tools
    published = []
    monkeypatch.setattr(tools, "_save_artifact", lambda name, data, mime:
                        published.append(data) or "/api/artifacts/engineering")
    output = run(pd.DataFrame({"v": [1 / 3, 2 / 7, 3 / 11]}), output_format="xlsx")
    book = load_workbook(io.BytesIO(published[0]), data_only=True)
    cells = [cell for row in book.active.values for cell in row]
    assert output["analysis_result_ref"]["sha256"] in cells
    assert output["dataset_ref"]["id"] in cells
    assert output["descriptive"]["v"]["mean"] == pytest.approx(sum([1 / 3, 2 / 7, 3 / 11]) / 3, rel=1e-15)
    assert output["facts"]["n"] == 3
    assert output["_tool_outcome"]["facts"] == output["facts"]
    assert output["result_refs"] == [output["analysis_result_ref"], output["dataset_ref"]]
    assert output["_tool_outcome"]["result_refs"] == output["result_refs"]
    book.close()


def test_result_persistence_failure_retains_values_but_not_a_complete_shared_result(monkeypatch):
    from src.research import analysis_results
    monkeypatch.setattr(analysis_results, "persist_dataset_snapshot", lambda *a, **k: (_ for _ in ()).throw(OSError("engineering disk failure")))
    output = run(pd.DataFrame({"v": [1, 2, 4]}))
    assert output["descriptive"]["v"]["mean"] == 7 / 3
    assert output["snapshot_status"] == "failed"
    assert "analysis_result_ref" not in output
    assert output["_tool_outcome"]["status"] == "partial"

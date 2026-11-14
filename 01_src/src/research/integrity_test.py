"""Synthetic engineering fixtures only; no real scientific materials or IO."""
from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import socket

import pandas as pd
import pytest

from src.research.integrity import (
    METHOD_VERSION, IntegrityConfig, audit_numeric_reuse, table_from_frame, table_from_rows,
)


X = [0, 1, 3, 7, 12, 20]


@pytest.fixture(autouse=True)
def isolated_offline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Synthetic deterministic tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)


def _table(label, rows, columns=None, *, payload=None, **kwargs):
    if columns is None:
        columns = [f"synthetic_value_{i}" for i in range(len(rows[0]) if rows else 1)]
    # These bytes are the complete, deliberately synthetic source, not a
    # fabricated reconstruction of a user's original uploaded attachment.
    source = payload if payload is not None else json.dumps(
        {"fixture": "SYNTHETIC ENGINEERING DATA", "label": label, "columns": columns,
         "rows": rows}, default=str, ensure_ascii=False).encode("utf-8")
    return table_from_rows(rows, columns, source_id=label,
                           source_name=f"SYNTHETIC_{label}.json", source_bytes=source,
                           sheet="synthetic", **kwargs)


def _column(label, values, **kwargs):
    return _table(label, [[value] for value in values], **kwargs)


def _matches(report, method):
    return [item for item in report["findings"] if item["method"] == method]


def test_exact_column_has_two_hashes_and_original_row_column_locations():
    left = _column("a", X, row_numbers=[4, 5, 8, 9, 12, 15], column_numbers=[3])
    right = _column("b", X, data_row_start=10, column_numbers=[28])
    report = audit_numeric_reuse([left, right])
    finding, = _matches(report, "exact_column")
    assert finding["left"]["source_sha256"] == left.source_sha256
    assert finding["right"]["source_sha256"] == right.source_sha256
    assert left.source_sha256 != right.source_sha256
    assert finding["left"]["rows"] == [4, 5, 8, 9, 12, 15]
    assert finding["right"]["rows"] == list(range(10, 16))
    assert finding["left"]["cells"] == [["C4"], ["C5"], ["C8"], ["C9"], ["C12"], ["C15"]]
    assert finding["right"]["cells"][0] == ["AB10"]
    assert finding["method_version"] == METHOD_VERSION
    assert finding["relationship"]["exact"] is True
    assert finding["coverage"]["left_parsed_row_fraction"] == 1
    assert finding["status"] == "review_required"
    assert finding["review_status"] == "pending"
    assert finding["conclusion"] == "numeric_relationship_only"
    json.dumps(report, allow_nan=False)


def test_exact_numeric_equality_is_not_text_or_file_equality():
    report = audit_numeric_reuse([_column("a", ["0.00", "1.0", "3", "7.000", "12", "20"]),
                                  _column("b", X)])
    finding, = _matches(report, "exact_column")
    assert finding["identical_original_file_bytes"] is False


def test_large_integers_are_not_collapsed_by_float64():
    values = [10**18 + x for x in X]
    changed = values.copy()
    changed[2] += 1
    report = audit_numeric_reuse([_column("a", values), _column("b", changed)])
    assert report["findings"] == []
    exact = audit_numeric_reuse([_column("a", values), _column("b", values)])
    assert len(_matches(exact, "exact_column")) == 1


def test_reordered_column_retains_multiset_multiplicity_and_mapping():
    values = [1, 1, 3, 7, 12, 20]
    reordered = [12, 1, 20, 3, 1, 7]
    report = audit_numeric_reuse([_column("a", values), _column("b", reordered)])
    finding, = _matches(report, "reordered_column")
    assert finding["right"]["row_ordinals"] == [1, 4, 3, 5, 0, 2]
    assert finding["mapping_ambiguous_due_to_ties"] is True
    for li, ri in zip(finding["left"]["row_ordinals"], finding["right"]["row_ordinals"]):
        assert values[li] == reordered[ri]


def test_same_unique_values_with_different_counts_are_not_reordered_matches():
    report = audit_numeric_reuse([_column("a", [1, 1, 3, 7, 12, 20]),
                                  _column("b", [1, 3, 3, 7, 12, 20])])
    assert not _matches(report, "reordered_column")


@pytest.mark.parametrize("method,scale,offset", [
    ("constant_offset", 1, 17), ("constant_ratio", -2, 0), ("affine", 3, 5)])
def test_transforms_report_reproducible_coefficients_for_every_row(method, scale, offset):
    report = audit_numeric_reuse([_column("a", X), _column("b", [scale*x + offset for x in X])])
    finding, = report["findings"]
    assert finding["method"] == method
    relationship = finding["relationship"]
    assert Fraction(relationship["scale"]) == scale
    assert Fraction(relationship["offset"]) == offset
    assert relationship["max_absolute_residual"] == "0"
    assert relationship["exact"] is True
    assert finding["matched_rows"] == len(X)


def test_ratio_checks_zero_rows_instead_of_dropping_them():
    y = [2*x for x in X]
    y[0] = 9
    report = audit_numeric_reuse([_column("a", X), _column("b", y)])
    assert report["findings"] == []


def test_arbitrary_noisy_correlation_is_not_affine_evidence():
    y = [2*x + 3 for x in X]
    y[3] += 0.01
    assert audit_numeric_reuse([_column("a", X), _column("b", y)])["findings"] == []


def test_large_baseline_does_not_inflate_residual_tolerance():
    y = [10**18 + 2*x for x in X]
    y[3] += 1
    assert audit_numeric_reuse([_column("a", X), _column("b", y)])["findings"] == []


def test_float_roundoff_is_explicitly_approximate_not_exact():
    x = [0.1, 0.2, 0.35, 0.8, 1.2, 1.9]
    report = audit_numeric_reuse([_column("a", x), _column("b", [2.3*v + 0.7 for v in x])])
    finding, = _matches(report, "affine")
    assert finding["relationship"]["exact"] is False
    assert Fraction(finding["relationship"]["max_absolute_residual"]) <= Fraction(finding["relationship"]["residual_tolerance"])
    strict = audit_numeric_reuse([_column("a", x), _column("b", [2.3*v + 0.7 for v in x])],
                                 config=IntegrityConfig(absolute_tolerance="0", relative_tolerance="0"))
    assert strict["findings"] == []


def test_near_constant_columns_below_tolerance_are_not_fitted():
    a = [0, 1e-12, 3e-12, 7e-12, 12e-12, 20e-12]
    b = [0, 7e-12, 1e-12, 3e-12, 20e-12, 10e-12]
    report = audit_numeric_reuse([_column("a", a), _column("b", b)])
    assert report["findings"] == []
    assert report["coverage"]["methods"]["affine"]["skipped"]["variation_below_absolute_tolerance"] == 1


def test_exact_block_at_different_row_column_offsets_is_extended_and_deduped():
    block = [[1, 9, 100], [2, 4, 120], [4, 7, 105], [8, 2, 143], [16, 6, 151], [32, 3, 160]]
    left = _table("a", [[-9, -8, -7]] + block + [[999, 998, 997]], data_row_start=4)
    right_rows = [[-90, -80, -70, -60, -50], [-40, -30, -20, -10, -5]]
    right_rows += [[800+i, *row, 900+i] for i, row in enumerate(block)]
    right_rows += [[700, 701, 702, 703, 704]]
    right = _table("b", right_rows, data_row_start=10)
    report = audit_numeric_reuse([left, right])
    finding, = _matches(report, "exact_block")
    assert finding["left"]["rows"] == list(range(5, 11))
    assert finding["right"]["rows"] == list(range(12, 18))
    assert [c["index"] for c in finding["left"]["columns"]] == [1, 2, 3]
    assert [c["index"] for c in finding["right"]["columns"]] == [2, 3, 4]
    assert finding["matched_cells_per_source"] == 18
    assert finding["coverage"]["left_parsed_row_fraction"] == 6/8


def test_reordered_block_requires_one_shared_row_permutation():
    left_rows = [[1, 9], [2, 4], [4, 7], [8, 2], [16, 6], [32, 3]]
    permutation = [3, 1, 5, 0, 4, 2]
    right_rows = [left_rows[i] for i in permutation]
    report = audit_numeric_reuse([_table("a", left_rows), _table("b", right_rows)])
    finding, = _matches(report, "reordered_block")
    for li, ri in zip(finding["left"]["row_ordinals"], finding["right"]["row_ordinals"]):
        assert left_rows[li] == right_rows[ri]
    # Independently shuffled columns have equal marginal multisets, but are
    # NOT evidence for a repeated two-column block.
    mismatched = [[left_rows[permutation[i]][0], left_rows[(i+1) % 6][1]] for i in range(6)]
    negative = audit_numeric_reuse([_table("a", left_rows), _table("b", mismatched)])
    assert len(_matches(negative, "reordered_column")) == 2
    assert not _matches(negative, "reordered_block")
    assert not _matches(negative, "exact_block")


def test_missing_values_never_count_as_numeric_matches():
    values = X + [None]
    report = audit_numeric_reuse([_column("a", values), _column("b", values)])
    finding, = _matches(report, "exact_column")
    assert finding["matched_rows"] == 6
    assert finding["coverage"]["left_excluded_row_ordinals"] == [6]
    assert finding["coverage"]["left_parsed_row_fraction"] == 6/7
    assert finding["coverage"]["missing_values_imputed"] is False
    assert report["sources"][0]["excluded_cells_by_reason"] == {"missing": 1}


def test_insufficient_overlap_is_skipped_even_if_survivors_match():
    values = X + [None] * 14
    report = audit_numeric_reuse([_column("a", values), _column("b", values)])
    assert report["findings"] == []
    assert report["coverage"]["methods"]["exact_column"]["skipped"]["insufficient_coverage"] == 1


@pytest.mark.parametrize("values,reason", [([1, 2, 3, 4], "insufficient_rows"),
    ([7]*6, "constant_or_low_distinctness"), ([0, 1]*3, "constant_or_low_distinctness"),
    ([None]*6, "insufficient_rows")])
def test_small_constant_low_entropy_and_all_missing_are_explicitly_skipped(values, reason):
    report = audit_numeric_reuse([_column("a", values), _column("b", values)])
    assert report["findings"] == []
    assert report["coverage"]["methods"]["exact_column"]["skipped"][reason] == 1


def test_unequal_length_columns_are_not_silently_truncated():
    report = audit_numeric_reuse([_column("a", X), _column("b", X + [100])])
    assert report["findings"] == []
    assert report["coverage"]["methods"]["exact_column"]["skipped"]["row_count_mismatch"] == 1


def test_invalid_cells_are_classified_without_imputation():
    values = [None, "", "not a number", True, float("nan"), float("inf"), *X]
    table = _column("a", values)
    coverage = table.coverage()
    assert coverage["finite_cells"] == 6
    assert coverage["excluded_cells_by_reason"] == {"missing": 2, "non_numeric": 1, "boolean": 1, "nonfinite": 2}
    json.dumps(coverage, allow_nan=False)


def test_missing_row_breaks_contiguous_block_seed():
    rows = [[x, x*x + 1] for x in X[:3]] + [[None, None]] + [[x, x*x + 1] for x in X[3:]]
    report = audit_numeric_reuse([_table("a", rows), _table("b", rows)])
    assert not _matches(report, "exact_block")


@pytest.mark.parametrize("coordinates", [
    {"row_numbers": [2, 4, 6, 8, 10, 12]}, {"column_numbers": [1, 3]}])
def test_contiguous_blocks_cannot_bridge_gaps_in_original_coordinates(coordinates):
    rows = [[x, x*x + 1] for x in X]
    report = audit_numeric_reuse([_table("a", rows, **coordinates), _table("b", rows)])
    assert not _matches(report, "exact_block")
    assert report["coverage"]["methods"]["exact_block"]["skipped"]["noncontiguous_source_coordinates"] >= 1


@pytest.mark.parametrize("conversion", ["units", "baseline", "normalization", "same_export"])
def test_normal_conversion_counterexamples_remain_review_leads_not_misconduct(conversion):
    functions = {"units": lambda x: x*1000, "baseline": lambda x: x-5,
                 "normalization": lambda x: (x-10)/2, "same_export": lambda x: x}
    report = audit_numeric_reuse([_column("a", X), _column("b", [functions[conversion](x) for x in X])])
    assert report["findings"]
    for finding in report["findings"]:
        assert finding["severity"] == "review"
        assert finding["review_status"] == "pending"
        assert finding["conclusion"] == "numeric_relationship_only"
        assert any("单位换算" in text for text in finding["normal_explanations"])
        assert "不证明" in finding["caution"]
    assert report["coverage"]["multiple_comparisons"]["significance_claimed"] is False


def test_identical_original_files_are_disclosed_as_possible_duplicate_uploads():
    payload = b'SYNTHETIC identical export bytes'
    report = audit_numeric_reuse([_column("a", X, payload=payload), _column("b", X, payload=payload)])
    assert report["findings"][0]["identical_original_file_bytes"] is True
    assert report["findings"][0]["left"]["source_sha256"] == hashlib.sha256(payload).hexdigest()


def test_parser_truncation_is_propagated_not_reported_as_full_coverage():
    left = _column("a", X, source_rows=100, source_columns=10, truncation_reasons=["parser_cell_limit"])
    report = audit_numeric_reuse([left, _column("b", X)])
    assert report["coverage_status"] == "partial"
    assert report["sources"][0]["truncation_reasons"] == ["parser_cell_limit", "source_rows_not_loaded", "source_columns_not_loaded"]
    assert report["findings"][0]["coverage"]["left_source_row_fraction"] == 0.06


@pytest.mark.parametrize("config,limit", [
    (IntegrityConfig(max_column_pairs=1), "max_column_pairs"),
    (IntegrityConfig(max_block_windows=1), "max_block_windows"),
    (IntegrityConfig(max_block_candidates=1), "max_block_candidates"),
    (IntegrityConfig(max_findings=1), "max_findings")])
def test_resource_limits_leave_explicit_partial_coverage(config, limit):
    rows = [[x, x*x+1, x*x*x-7] for x in X]
    report = audit_numeric_reuse([_table("a", rows), _table("b", rows)], config=config)
    assert report["coverage_status"] == "partial"
    assert limit in report["coverage"]["limit_reasons"]
    assert report["coverage"]["column_pairs_total"] == 9
    if limit == "max_findings":
        assert report["coverage"]["findings_omitted"] > 0


def test_report_and_evidence_ids_are_deterministic_under_input_order_changes():
    left, right = _column("a", X), _column("b", [2*x+3 for x in X])
    first = audit_numeric_reuse([left, right])
    second = audit_numeric_reuse([right, left])
    assert first == second
    assert first["coverage"]["pattern_checks_evaluated"] == sum(
        item["tested"] for item in first["coverage"]["methods"].values())


def test_dataframe_adapter_preserves_duplicate_column_names_by_position():
    frame = pd.DataFrame([[x, x*x] for x in X], columns=["value", "value"], index=[9, 3, 5, 7, 11, 2])
    original = frame.copy(deep=True)
    left = table_from_frame(frame, source_id="a", source_name="SYNTHETIC_a.csv",
                            source_bytes=b"SYNTHETIC source a", sheet="s", data_row_start=5)
    right = table_from_frame(frame, source_id="b", source_name="SYNTHETIC_b.csv",
                             source_bytes=b"SYNTHETIC source b", sheet="s", data_row_start=8)
    report = audit_numeric_reuse([left, right])
    assert len(_matches(report, "exact_column")) == 2
    assert left.row_numbers == (5, 6, 7, 8, 9, 10)
    assert left.column_numbers == (1, 2)
    pd.testing.assert_frame_equal(frame, original)


def test_same_file_different_sheets_are_not_cross_file_comparisons():
    table = _column("a", X)
    report = audit_numeric_reuse([table, replace(table, sheet="other")])
    assert report["findings"] == []
    assert report["coverage_status"] == "not_applicable"
    assert report["coverage"]["same_source_table_pairs_excluded"] == 1


@pytest.mark.parametrize("kwargs", [
    {"row_numbers": [1]}, {"row_numbers": [2, 2, 3, 4, 5, 6]},
    {"column_numbers": [0]}, {"source_rows": 1}, {"source_columns": 0}])
def test_invalid_source_coordinates_and_dimensions_are_rejected(kwargs):
    with pytest.raises(ValueError):
        _column("a", X, **kwargs)


def test_ragged_input_and_ambiguous_source_identity_are_rejected():
    with pytest.raises(ValueError):
        _table("a", [[1], [2, 3]], columns=["x"])
    source = _column("a", X)
    with pytest.raises(ValueError):
        audit_numeric_reuse([source, source])
    with pytest.raises(ValueError):
        audit_numeric_reuse([source, replace(source, sheet="other", source_sha256="b"*64)])


@pytest.mark.parametrize("config", [IntegrityConfig(min_rows=2), IntegrityConfig(min_distinct=1),
    IntegrityConfig(absolute_tolerance="NaN"), IntegrityConfig(relative_tolerance="-1"),
    IntegrityConfig(min_coverage=0), IntegrityConfig(max_findings=0)])
def test_invalid_detection_parameters_are_rejected(config):
    with pytest.raises(ValueError):
        audit_numeric_reuse([], config=config)


# Untrusted numeric/input budgets. These are bounded synthetic unit fixtures,
# not performance benchmarks. Bomb sentinels prevent a broken guard from ever
# attempting the enormous Fraction allocation or consuming an infinite input.
@pytest.mark.parametrize("value,reason", [
    ("1e999999999", "numeric_exponent_limit"),
    ("1e-999999999", "numeric_exponent_limit"),
    ("-1e+999999999", "numeric_exponent_limit"),
    ("-1e-999999999", "numeric_exponent_limit"),
    ("0e999999999", "numeric_exponent_limit"),
    ("1e999999999999999999999999999999", "numeric_exponent_limit"),
    ("1e-999999999999999999999999999999", "numeric_exponent_limit"),
    ("1e9_999_999", "numeric_exponent_limit"),
    ("11e256", "numeric_exponent_limit"),
    ("1.1e-256", "numeric_exponent_limit"),
    ("9" * 129, "numeric_precision_limit"),
    (1 << 2048, "numeric_precision_limit"),
    ("9" * 10000, "numeric_character_limit"),
    (" " * 257, "numeric_character_limit"),
])
def test_numeric_budgets_reject_before_fraction_allocation(monkeypatch, value, reason):
    from src.research import integrity

    def forbidden_fraction(*args, **kwargs):
        raise AssertionError("Excluded input must never reach Fraction")

    monkeypatch.setattr(integrity, "Fraction", forbidden_fraction)
    assert integrity._number(value) == (None, reason)


@pytest.mark.parametrize("value", ["1e256", "1e-256", "9" * 128,
                                  "0." + "1" * 128, " " * 255 + "1"])
def test_numeric_budget_boundaries_still_accept_bounded_values(value):
    from src.research import integrity
    from decimal import Decimal

    result, reason = integrity._number(value)
    assert reason == ""
    assert result == Fraction(Decimal(value))


def test_excluded_numeric_budgets_are_visible_in_source_coverage():
    values = ["1e999999999", "1e-999999999", "9" * 129, "8" * 257, *X]
    table = _column("a", values)
    coverage = table.coverage()
    assert coverage["excluded_cells_by_reason"] == {
        "numeric_exponent_limit": 2, "numeric_precision_limit": 1, "numeric_character_limit": 1}
    assert coverage["numeric_limits"] == {
        "max_characters": 256, "max_significant_digits": 128, "max_absolute_exponent": 256}
    assert table.values[:4] == ((None,),) * 4
    report = audit_numeric_reuse([table, _column("b", values)])
    assert report["findings"] == []  # six valid rows out of ten is insufficient coverage
    json.dumps(report, allow_nan=False)


def _bounded_source(rows, columns, **kwargs):
    return table_from_rows(rows, columns, source_id="synthetic-budget",
                           source_name="SYNTHETIC_BUDGET.csv",
                           source_bytes=b"SYNTHETIC ENGINEERING BUDGET FIXTURE", **kwargs)


def test_row_limit_stops_consumption_at_first_excess_row(monkeypatch):
    from src.research import integrity
    observed = []

    def rows():
        for i in range(4):
            observed.append(i)
            yield [i]
        raise AssertionError("An unbounded tuple would consume past the guard")

    monkeypatch.setattr(integrity, "_number", lambda *_: pytest.fail("Budget check must precede numeric parsing"))
    with pytest.raises(integrity.IntegrityInputLimitError) as exc:
        _bounded_source(rows(), ["x"], max_rows=3)
    assert exc.value.code == "input_row_limit"
    assert exc.value.limit == 3
    assert exc.value.observed_at_least == 4
    assert observed == [0, 1, 2, 3]


def test_cell_limit_stops_before_reading_over_budget_row(monkeypatch):
    from src.research import integrity

    def unreadable_row():
        raise AssertionError("Over-budget row must not be read")
        yield

    monkeypatch.setattr(integrity, "_number", lambda *_: pytest.fail("Budget check must precede numeric parsing"))
    with pytest.raises(integrity.IntegrityInputLimitError) as exc:
        _bounded_source([[1, 2, 3], unreadable_row()], ["a", "b", "c"], max_cells=5)
    assert exc.value.code == "input_cell_limit"
    assert exc.value.observed_at_least == 6


def test_column_limit_stops_column_iterator_before_touching_rows():
    from src.research.integrity import IntegrityInputLimitError

    def columns():
        yield from ("a", "b", "c")
        raise AssertionError("Column iterator was not bounded")

    def rows():
        raise AssertionError("Rows must not be touched after column limit")
        yield

    with pytest.raises(IntegrityInputLimitError) as exc:
        _bounded_source(rows(), columns(), max_columns=2)
    assert exc.value.code == "input_column_limit"
    assert exc.value.observed_at_least == 3


def test_malformed_row_iterator_is_bounded_before_shape_rejection():
    def row():
        yield 1
        yield 2
        raise AssertionError("Ragged row iterator was not bounded")

    with pytest.raises(ValueError, match="rows must match"):
        _bounded_source([row()], ["one_column"])


def test_coordinate_iterators_are_bounded():
    def coordinates():
        yield from (2, 3, 4)
        raise AssertionError("Coordinate iterator was not bounded")

    with pytest.raises(ValueError, match="coordinate counts"):
        _bounded_source([[1], [2]], ["x"], row_numbers=coordinates())


@pytest.mark.parametrize("shape,code", [((20001, 1), "input_row_limit"),
                                        ((2, 201), "input_column_limit"),
                                        ((2000, 200), "input_cell_limit")])
def test_dataframe_budget_precedes_itertuples_or_column_materialization(shape, code):
    from src.research.integrity import IntegrityInputLimitError

    class OversizedSyntheticFrame:
        @property
        def columns(self):
            pytest.fail("Column access must follow shape budget checks")

        def itertuples(self, **kwargs):
            pytest.fail("itertuples must follow shape budget checks")

    frame = OversizedSyntheticFrame()
    frame.shape = shape
    with pytest.raises(IntegrityInputLimitError) as exc:
        table_from_frame(frame, source_id="synthetic", source_name="SYNTHETIC_FRAME",
                         source_bytes=b"SYNTHETIC ENGINEERING BUDGET FIXTURE")
    assert exc.value.code == code


def test_input_limits_allow_exact_boundary_and_explicit_caller_budget():
    table = _bounded_source([[1, 2], [3, 4]], ["a", "b"], max_rows=2, max_columns=2, max_cells=4)
    assert table.coverage()["finite_cells"] == 4
    frame = pd.DataFrame([[1, 2], [3, 4]], columns=["a", "b"])
    converted = table_from_frame(frame, source_id="synthetic", source_name="SYNTHETIC_FRAME",
                                 source_bytes=b"SYNTHETIC ENGINEERING BUDGET FIXTURE",
                                 max_rows=2, max_columns=2, max_cells=4)
    assert converted.values == table.values


@pytest.mark.parametrize("kwargs", [{"max_rows": 0}, {"max_columns": -1}, {"max_cells": 1.5},
                                    {"max_rows": True}, {"max_cells": None}])
def test_invalid_input_limits_are_rejected_before_iterating(kwargs):
    def rows():
        pytest.fail("Invalid budgets must be rejected before consuming input")
        yield

    with pytest.raises(ValueError, match="positive integer"):
        _bounded_source(rows(), ["x"], **kwargs)

"""Explicit audit-plan contract: exact rectangles plus bounded window search."""
import json

import pytest

from citrus_audit.integrity import table_from_rows
from citrus_audit.plan import compare_bound, parse_plan


def _table(rows, columns=("a", "b")):
    return table_from_rows(rows, list(columns), source_id="source", source_name="book.xlsx",
                           source_bytes=b"source", sheet="S", data_row_start=1)


def _plan(comparison):
    return parse_plan({"schema": "audit-plan/1", "mode": "research_integrity",
                       "comparisons": [comparison]})


def _binding(left_rows, right_rows, **extra):
    comparison = {
        "method": "window_search",
        "left": {"file_index": 0, "sheet": "S", "rectangles": [{"rows": left_rows, "columns": ["a", "b"]}]},
        "right": {"file_index": 1, "sheet": "S", "rectangles": [{"rows": right_rows, "columns": ["a", "b"]}]},
        "units": ["1", "1"],
        "window": {"rows": 3, "columns": 2},
    }
    comparison.update(extra)
    return comparison


def _run(comparison, left, right):
    plan = _plan(comparison)
    snapshots = {(0, "S"): (_table(left), {}), (1, "S"): (_table(right), {})}
    return compare_bound(plan, snapshots)[0]


def test_window_search_finds_nonadjacent_block_deterministically():
    left = [[1, 2], [3, 4], [5, 6], [0, 0], [1, 2], [3, 4], [5, 6]]
    right = [[9, 8], [1, 2], [3, 4], [5, 6], [0, 0]]
    comparison = _binding([1, 2, 3, 4, 5, 6, 7], [1, 2, 3, 4, 5])
    first = _run(comparison, left, right)
    second = _run(comparison, left, right)
    assert first["status"] == "complete"
    assert first["interpretation_scope"] == "review_lead_only"
    assert first["evidence"]["window_search_match"] is True
    assert first["evidence"]["misconduct_verdict"] is False
    assert first["coverage"]["windows_scanned"] > 0
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_window_search_applies_units_and_tolerance_in_confirmation():
    left = [[1, 2], [3, 4], [5, 6]]
    right = [[0.001, 0.002], [0.003, 0.0040005], [0.005, 0.006]]
    receipt = _run(_binding([1, 2, 3], [1, 2, 3], units=["mg", "g"],
                            right_factor_to_left="1000", absolute_tolerance="0.001"), left, right)
    assert receipt["evidence"]["window_search_match"] is True
    assert receipt["evidence"]["exact_decimal_match"] is False
    assert receipt["evidence"]["tolerance_applied"] is True


def test_window_search_reports_bounded_truncation():
    left = [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10]]
    right = [[9, 8], [7, 6], [5, 4], [3, 2], [1, 0]]
    receipt = _run(_binding([1, 2, 3, 4, 5], [1, 2, 3, 4, 5], max_pairs=1), left, right)
    assert receipt["status"] == "partial"
    assert receipt["coverage"]["candidate_pairs_examined"] == 1
    assert "max_candidate_pairs" in receipt["coverage"]["limit_reasons"]
    receipt = _run(_binding([1, 2, 3, 4, 5], [1, 2, 3, 4, 5], max_windows=1), left, right)
    assert receipt["status"] == "partial"
    assert receipt["coverage"]["windows_scanned"] == 2
    assert "max_windows" in receipt["coverage"]["limit_reasons"]


def test_window_search_requires_disjoint_rectangles_and_fitting_window():
    comparison = _binding([1, 2, 3], [1, 2, 3])
    comparison["left"]["rectangles"].append({"rows": [2, 3, 4], "columns": ["a", "b"]})
    with pytest.raises(ValueError):
        _plan(comparison)
    comparison = _binding([1, 2, 3], [1, 2, 3])
    comparison["window"] = {"rows": 4, "columns": 2}
    with pytest.raises(ValueError):
        _plan(comparison)


def test_window_search_reports_unavailable_coordinates_without_verdict():
    receipt = _run(_binding([1, 2, 3], [9, 10, 11]),
                   [[1, 2], [3, 4], [5, 6]], [[1, 2], [3, 4], [5, 6]])
    assert receipt["status"] == "skipped"
    assert receipt["reason"] == "bound_rows_unavailable"
    assert "evidence" not in receipt


def test_existing_exact_rectangle_contract_is_preserved():
    comparison = {
        "method": "finite_rectangle",
        "left": {"file_index": 0, "sheet": "S", "columns": ["a", "b"], "rows": [1, 2, 3]},
        "right": {"file_index": 1, "sheet": "S", "columns": ["a", "b"], "rows": [2, 3, 4]},
        "units": ["1", "1"],
    }
    plan = _plan(comparison)
    assert "rectangles" not in plan["comparisons"][0]["left"]
    snapshots = {(0, "S"): (_table([[1, 2], [3, 4], [5, 6]]), {}),
                 (1, "S"): (_table([[0, 0], [1, 2], [3, 4], [5, 6]]), {})}
    receipt = compare_bound(plan, snapshots)[0]
    assert receipt["status"] == "complete"
    assert receipt["evidence"]["finite_rectangle_match"] is True


def test_window_search_prioritizes_content_hash_candidates_within_budget():
    left_rows = [[i, i + 1] for i in range(12)]
    right_rows = [[i * 2, i * 2 + 1] for i in range(12)]
    copied = [[7, 8], [8, 9], [9, 10]]
    left_rows[9:12], right_rows[9:12] = copied, copied
    receipt = _run(_binding(list(range(1, 13)), list(range(1, 13)), max_pairs=1),
                   left_rows, right_rows)
    assert receipt["coverage"]["candidate_pairs_total"] == 100
    assert receipt["coverage"]["exact_signature_pairs_total"] == 1
    assert receipt["coverage"]["exact_signature_pairs_examined"] == 1
    assert receipt["evidence"]["window_search_match"] is True
    assert receipt["locations"][0]["rows"] == [10, 11, 12]


def test_existing_sample_alignment_contract_is_preserved():
    comparison = {
        "method": "sample_alignment",
        "left": {"file_index": 0, "sheet": "S", "columns": ["a", "b"], "key_columns": ["k"]},
        "right": {"file_index": 1, "sheet": "S", "columns": ["a", "b"], "key_columns": ["k"]},
        "units": ["1", "1"],
    }
    plan = _plan(comparison)
    left = _table([[1, 10, 20], [2, 30, 40], [3, 50, 60]], ("k", "a", "b"))
    right = _table([[1, 10, 20], [2, 30, 40], [3, 50, 61]], ("k", "a", "b"))

    def bound_keys(table):
        return {("k",): ({(str(row[0]),): ordinal for ordinal, row in enumerate(table.values)}, 0)}

    receipt = compare_bound(plan, {(0, "S"): (left, bound_keys(left)),
                                   (1, "S"): (right, bound_keys(right))})[0]
    assert receipt["status"] == "complete"
    assert receipt["coverage"]["matched_unique_keys"] == 3
    assert receipt["evidence"]["cells_differing"] == 1

"""Deterministic cross-file numeric reuse leads; no inference of misconduct.

Inputs are already-parsed tables plus their original bytes. No filesystem,
network, model, workbook export or mutation of uploaded data occurs here.
Numeric equality is equality of finite decimal values, not byte equality.
Fractions avoid collapsing large integers through a float64 conversion.

Aligned tests pair row ORDINALS, not biological subjects. Reordering tests
compare multisets and expose one stable value-based mapping; duplicate values
make that mapping non-unique. Transform tests never fit after sorting values.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
from itertools import combinations, islice
import json
from numbers import Integral, Real
import re
from typing import Any, Iterable, Sequence


METHOD_VERSION = "numeric-reuse/1.0.1"
MAX_NUMERIC_CHARACTERS = 256
MAX_SIGNIFICANT_DIGITS = 128
MAX_ABSOLUTE_EXPONENT = 256
DEFAULT_MAX_INPUT_ROWS = 20_000
DEFAULT_MAX_INPUT_COLUMNS = 200
DEFAULT_MAX_INPUT_CELLS = 200_000
_NUMERIC_EXPONENT = re.compile(r"[eE]([+-]?[0-9]+)$")


class IntegrityInputLimitError(ValueError):
    """Input was refused before materialization; never a partial clean audit."""

    def __init__(self, code: str, limit: int, observed_at_least: int) -> None:
        self.code = code
        self.limit = limit
        self.observed_at_least = observed_at_least
        super().__init__(f"{code}: limit={limit}, observed_at_least={observed_at_least}; "
                         "table not audited; explicitly select a smaller range with source coverage")

METHODS = ("exact_column", "reordered_column", "constant_offset", "constant_ratio",
           "affine", "exact_block", "reordered_block", "shared_decimal_tail")
CAUTION = "数值关系仅为人工复核线索，不证明复制来源、研究结论错误或科研造假。"
NORMAL_EXPLANATIONS = [
    "单位换算、归一化、基线校正或预先声明的公式派生",
    "同一实验的不同导出、共享对照、重复测量或公开数据的合法复用",
    "行排序、有限精度、离散量表和多重比较造成的巧合",
]


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _number(value: Any) -> tuple[Fraction | None, str]:
    if value is None or type(value).__name__ in {"NAType", "NaTType"}:
        return None, "missing"
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return None, "boolean"
    if not isinstance(value, (str, Integral, Real, Decimal)):
        return None, "non_numeric"
    # Do not strip/copy an arbitrarily long string or stringify an enormous
    # integer before enforcing a budget. Four bits per decimal digit is a
    # conservative precheck; Decimal's digit count below is authoritative.
    if isinstance(value, str) and len(value) > MAX_NUMERIC_CHARACTERS:
        return None, "numeric_character_limit"
    if isinstance(value, Integral) and int(value).bit_length() > 4 * MAX_SIGNIFICANT_DIGITS:
        return None, "numeric_precision_limit"
    text = str(value)
    if len(text) > MAX_NUMERIC_CHARACTERS:
        return None, "numeric_character_limit"
    text = text.strip()
    if not text:
        return None, "missing"
    # Decimal construction itself does not expand e999999999, but Fraction
    # would allocate 10**999999999. Check the explicit exponent even before
    # Decimal, including exponents outside Decimal's own implementation range.
    match = _NUMERIC_EXPONENT.search(text)
    if match and abs(int(match.group(1))) > MAX_ABSOLUTE_EXPONENT:
        return None, "numeric_exponent_limit"
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, "non_numeric"
    if not number.is_finite():
        return None, "nonfinite"
    parts = number.as_tuple()
    if len(parts.digits) > MAX_SIGNIFICANT_DIGITS:
        return None, "numeric_precision_limit"
    # Check both stored exponent and magnitude: decimal places and mantissa
    # length can shift the exponent even when no e/E notation was supplied.
    if abs(parts.exponent) > MAX_ABSOLUTE_EXPONENT or abs(number.adjusted()) > MAX_ABSOLUTE_EXPONENT:
        return None, "numeric_exponent_limit"
    return Fraction(number), ""


def _rational(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _cell(column: int, row: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters
    return f"{letters}{row}"


@dataclass(frozen=True)
class IntegrityTable:
    """Immutable numeric view; coordinates are original 1-based source positions."""

    source_id: str
    source_name: str
    source_sha256: str
    sheet: str
    column_names: tuple[str, ...]
    row_numbers: tuple[int, ...]
    column_numbers: tuple[int, ...]
    values: tuple[tuple[Fraction | None, ...], ...]
    exclusions: tuple[tuple[str, ...], ...]
    source_rows: int
    source_columns: int
    truncation_reasons: tuple[str, ...]
    table_sha256: str

    @property
    def key(self) -> tuple[str, str]:
        return self.source_id, self.sheet

    def coverage(self) -> dict[str, Any]:
        reasons = Counter(reason for row in self.exclusions for reason in row if reason)
        return {
            "source_id": self.source_id, "source_name": self.source_name,
            "source_sha256": self.source_sha256, "sheet": self.sheet,
            "table_sha256": self.table_sha256,
            "source_rows": self.source_rows, "parsed_rows": len(self.values),
            "source_columns": self.source_columns, "parsed_columns": len(self.column_names),
            "finite_cells": sum(value is not None for row in self.values for value in row),
            "excluded_cells_by_reason": dict(sorted(reasons.items())),
            "numeric_limits": {"max_characters": MAX_NUMERIC_CHARACTERS,
                               "max_significant_digits": MAX_SIGNIFICANT_DIGITS,
                               "max_absolute_exponent": MAX_ABSOLUTE_EXPONENT},
            "columns": [{
                "name": name, "index": self.column_numbers[col],
                "finite_rows": sum(row[col] is not None for row in self.values),
                "distinct_finite_values": len({row[col] for row in self.values if row[col] is not None}),
                "excluded_by_reason": dict(sorted(Counter(row[col] for row in self.exclusions if row[col]).items())),
            } for col, name in enumerate(self.column_names)],
            "truncation_reasons": list(self.truncation_reasons),
        }


def table_from_rows(
    rows: Iterable[Sequence[Any]], column_names: Sequence[str], *,
    source_id: str, source_name: str, source_bytes: bytes, sheet: str = "",
    data_row_start: int = 2, row_numbers: Sequence[int] | None = None,
    column_numbers: Sequence[int] | None = None,
    source_rows: int | None = None, source_columns: int | None = None,
    truncation_reasons: Sequence[str] = (),
    max_rows: int = DEFAULT_MAX_INPUT_ROWS,
    max_columns: int = DEFAULT_MAX_INPUT_COLUMNS,
    max_cells: int = DEFAULT_MAX_INPUT_CELLS,
) -> IntegrityTable:
    """Snapshot parser output; hash the supplied ORIGINAL file bytes.

    Use explicit row_numbers/column_numbers after any filtering/reordering by
    a parser. Defaults mean contiguous rows after the header and columns A.. .
    source_rows excludes headers; source_columns is the full source width.
    Limits apply while consuming inputs, BEFORE building a whole tuple or
    parsing numeric values. Exceeding one raises IntegrityInputLimitError;
    select a smaller range explicitly and preserve source_rows/truncation_reasons
    if a partial audit is desired. Input is never silently cropped here.
    """
    if not source_id or not isinstance(source_bytes, bytes):
        raise ValueError("source_id and original source_bytes are required")
    for key, limit in (("max_rows", max_rows), ("max_columns", max_columns), ("max_cells", max_cells)):
        if type(limit) is not int or limit < 1:
            raise ValueError(f"{key} must be a positive integer")
    raw_names = tuple(islice(iter(column_names), max_columns + 1))
    if len(raw_names) > max_columns:
        raise IntegrityInputLimitError("input_column_limit", max_columns, len(raw_names))
    names = tuple(str(name) for name in raw_names)
    bounded_rows = []
    for index, row in enumerate(rows):
        if index >= max_rows:
            raise IntegrityInputLimitError("input_row_limit", max_rows, index + 1)
        cell_count = (index + 1) * len(names)
        if cell_count > max_cells:
            raise IntegrityInputLimitError("input_cell_limit", max_cells, cell_count)
        # A malformed/infinite row iterator is bounded too, not tuple(row).
        cells = tuple(islice(iter(row), len(names) + 1))
        if len(cells) != len(names):
            raise ValueError("All rows must match column_names; no truncation or broadcasting")
        bounded_rows.append(cells)
    raw_rows = tuple(bounded_rows)
    rn = tuple(islice(iter(row_numbers), len(raw_rows) + 1)) if row_numbers is not None else tuple(range(data_row_start, data_row_start + len(raw_rows)))
    cn = tuple(islice(iter(column_numbers), len(names) + 1)) if column_numbers is not None else tuple(range(1, len(names) + 1))
    if len(rn) != len(raw_rows) or len(cn) != len(names):
        raise ValueError("Source coordinate counts must match the parsed table")
    for coordinates in (rn, cn):
        if any(type(value) is not int or value < 1 for value in coordinates) or len(set(coordinates)) != len(coordinates):
            raise ValueError("Source row/column coordinates must be unique positive integers")
    total_rows = len(raw_rows) if source_rows is None else source_rows
    total_cols = len(names) if source_columns is None else source_columns
    if (type(total_rows) is not int or type(total_cols) is not int
            or total_rows < len(raw_rows) or total_cols < len(names)):
        raise ValueError("Source dimensions cannot be smaller than parsed dimensions")
    reasons = list(truncation_reasons)
    if total_rows > len(raw_rows) and "source_rows_not_loaded" not in reasons:
        reasons.append("source_rows_not_loaded")
    if total_cols > len(names) and "source_columns_not_loaded" not in reasons:
        reasons.append("source_columns_not_loaded")
    parsed = tuple(tuple(_number(value) for value in row) for row in raw_rows)
    values = tuple(tuple(cell[0] for cell in row) for row in parsed)
    exclusions = tuple(tuple(cell[1] for cell in row) for row in parsed)
    table_hash = _hash({"columns": names, "row_numbers": rn, "column_numbers": cn,
                        "values": [[_rational(v) if v is not None else None for v in row] for row in values],
                        "excluded": exclusions})
    return IntegrityTable(str(source_id), str(source_name), hashlib.sha256(source_bytes).hexdigest(),
                          str(sheet), names, rn, cn, values, exclusions,
                          total_rows, total_cols, tuple(str(reason) for reason in reasons), table_hash)


def table_from_frame(frame: Any, **source: Any) -> IntegrityTable:
    """Adapter for audit_uploaded_data; enforce shape before creating iterators."""
    rows, columns = frame.shape
    for key, observed, default, code in (
        ("max_rows", rows, DEFAULT_MAX_INPUT_ROWS, "input_row_limit"),
        ("max_columns", columns, DEFAULT_MAX_INPUT_COLUMNS, "input_column_limit"),
        ("max_cells", rows * columns, DEFAULT_MAX_INPUT_CELLS, "input_cell_limit"),
    ):
        limit = source.get(key, default)
        if type(limit) is not int or limit < 1:
            raise ValueError(f"{key} must be a positive integer")
        if observed > limit:
            raise IntegrityInputLimitError(code, limit, observed)
    return table_from_rows(frame.itertuples(index=False, name=None), frame.columns, **source)


@dataclass(frozen=True)
class IntegrityConfig:
    min_rows: int = 5
    min_distinct: int = 3
    min_coverage: float = 0.8
    # Absolute and relative residual tolerances. Relative error is scaled by
    # variation, not a large baseline that could hide discrepant observations.
    absolute_tolerance: str = "1e-10"
    relative_tolerance: str = "1e-9"
    block_columns: int = 2
    max_column_pairs: int = 5000
    max_block_windows: int = 20000
    max_block_candidates: int = 2000
    max_findings: int = 500
    decimal_tail_min_places: int = 4
    decimal_tail_digits: int = 3

    def validate(self) -> None:
        for name in ("min_rows", "min_distinct", "block_columns", "max_column_pairs",
                     "max_block_windows", "max_block_candidates", "max_findings"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.min_rows < 3 or self.min_distinct < 3 or self.min_distinct > self.min_rows:
            raise ValueError("At least three distinct observations and three rows are required")
        if self.block_columns < 2 or not 0 < self.min_coverage <= 1:
            raise ValueError("block_columns >= 2 and 0 < min_coverage <= 1 are required")
        if (type(self.decimal_tail_min_places) is not int or type(self.decimal_tail_digits) is not int
                or not 4 <= self.decimal_tail_min_places <= 12
                or not 3 <= self.decimal_tail_digits <= self.decimal_tail_min_places):
            raise ValueError("decimal suffix checks require 4–12 decimal places and at least three tail digits")
        for name in ("absolute_tolerance", "relative_tolerance"):
            value, reason = _number(getattr(self, name))
            if reason or value is None or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative decimal")


def _location(table: IntegrityTable, rows: Sequence[int], cols: Sequence[int]) -> dict[str, Any]:
    return {
        "source_id": table.source_id, "source_name": table.source_name,
        "source_sha256": table.source_sha256, "table_sha256": table.table_sha256,
        "sheet": table.sheet, "header_row": table.row_numbers[0] - 1 if table.row_numbers else 1,
        "row_ordinals": list(rows),
        "rows": [table.row_numbers[row] for row in rows],
        "columns": [{"index": table.column_numbers[col], "name": table.column_names[col]} for col in cols],
        "cells": [[_cell(table.column_numbers[col], table.row_numbers[row]) for col in cols] for row in rows],
    }


def _mapped_rows(left: Sequence[Any], right: Sequence[Any], left_rows: Sequence[int],
                 right_rows: Sequence[int]) -> list[int] | None:
    """Match a multiset without dropping duplicates; stable mapping of ties."""
    buckets: dict[Any, deque] = defaultdict(deque)
    for value, row in zip(right, right_rows):
        buckets[value].append(row)
    mapped = []
    for value in left:
        if not buckets[value]:
            return None
        mapped.append(buckets[value].popleft())
    return mapped if len(left_rows) == len(right_rows) and not any(buckets.values()) else None


def _transform(a: Sequence[Fraction], b: Sequence[Fraction], method: str,
               config: IntegrityConfig) -> dict[str, Any] | None:
    if method == "constant_offset":
        slope, intercept = Fraction(1), b[0] - a[0]
    elif method == "constant_ratio":
        pivot = max(range(len(a)), key=lambda i: abs(a[i]))
        slope, intercept = b[pivot] / a[pivot], Fraction(0)
    else:
        low, high = min(range(len(a)), key=a.__getitem__), max(range(len(a)), key=a.__getitem__)
        slope = (b[high] - b[low]) / (a[high] - a[low])
        intercept = b[low] - slope * a[low]
    predictions = [slope * value + intercept for value in a]
    residual = max(abs(actual - expected) for actual, expected in zip(b, predictions))
    scale = max(max(b) - min(b), max(predictions) - min(predictions))
    atol, _ = _number(config.absolute_tolerance)
    rtol, _ = _number(config.relative_tolerance)
    tolerance = atol + rtol * scale
    if residual > tolerance:
        return None
    return {"direction": "right = scale * left + offset", "scale": _rational(slope),
            "offset": _rational(intercept), "max_absolute_residual": _rational(residual),
            "residual_tolerance": _rational(tolerance), "exact": residual == 0,
            "tolerance_scale": "max(observed_range, predicted_range)",
            "coefficient_method": "fixed_offset" if method == "constant_offset" else
                "max_abs_nonzero_pivot" if method == "constant_ratio" else "min_max_x_pivots"}


def _decimal_tail(a: Sequence[Fraction], b: Sequence[Fraction], config: IntegrityConfig):
    """Exact finite-decimal suffix correspondence; no digit-distribution p-value.

    Fractions avoid float modulo artifacts. Do not pad integer/coarse input into
    evidence: require at least four meaningful decimal places in both columns,
    and three distinct suffixes, none being an all-zero padded decimal tail.
    """
    def places(value):
        denominator, twos, fives = value.denominator, 0, 0
        while denominator % 2 == 0 and twos <= 12:
            denominator //= 2
            twos += 1
        while denominator % 5 == 0 and fives <= 12:
            denominator //= 5
            fives += 1
        return max(twos, fives) if denominator == 1 else 1000
    ap, bp = [places(x) for x in a], [places(x) for x in b]
    precision = max(ap + bp)
    if precision > 12:
        return [], {"skip": "nonterminating_or_more_than_twelve_decimals"}
    scale, modulus = 10 ** precision, 10 ** config.decimal_tail_digits
    eligible = [i for i in range(len(a)) if min(ap[i], bp[i]) >= config.decimal_tail_min_places]
    tails_a = {i: (abs(a[i].numerator) * scale // a[i].denominator) % modulus for i in eligible}
    tails_b = {i: (abs(b[i].numerator) * scale // b[i].denominator) % modulus for i in eligible}
    matched = [i for i in eligible if tails_a[i] == tails_b[i] and tails_a[i] % 10 != 0]
    if (len(matched) < config.min_rows or len(matched) / max(len(a), 1) < config.min_coverage
            or len({tails_a[i] for i in matched}) < config.min_distinct):
        return [], {"skip": "insufficient_distinct_high_precision_suffix_matches"}
    return matched, {"comparison": "absolute_value_decimal_suffix_at_fixed_scale", "decimal_places": precision,
                     "tail_digits": config.decimal_tail_digits, "tail_integers": [tails_a[i] for i in matched],
                     "significance_claimed": False, "interpretation": "digit_pattern_only",
                     "counterexamples": ["Unit conversions or normalization", "A shared instrument/export rounding pipeline", "Legitimate derived columns or shared controls"]}


def audit_numeric_reuse(tables: Sequence[IntegrityTable], *,
                        config: IntegrityConfig | None = None) -> dict[str, Any]:
    """Find cross-file numeric relationships and return JSON-safe evidence.

    Exact blocks are contiguous rectangles of at least min_rows × block_columns.
    Reordered blocks use ONE row permutation for an entire full-height contiguous
    column window. Arbitrary submatrix permutations, transposition, time warping,
    nonlinear transforms and partial-row subset searches are not performed.
    """
    config = config or IntegrityConfig()
    config.validate()
    ordered = sorted(tables, key=lambda table: table.key)
    if len({table.key for table in ordered}) != len(ordered):
        raise ValueError("Each (source_id, sheet) must identify a unique parsed table")
    source_hashes: dict[str, str] = {}
    for table in ordered:
        if table.source_id in source_hashes and source_hashes[table.source_id] != table.source_sha256:
            raise ValueError("One source_id cannot identify different original file hashes")
        source_hashes[table.source_id] = table.source_sha256
    pairs = [(a, b) for a, b in combinations(ordered, 2) if a.source_id != b.source_id]
    methods = {name: {"tested": 0, "matched": 0, "skipped": {}} for name in METHODS}
    coverage: dict[str, Any] = {
        "table_pairs": len(pairs), "same_source_table_pairs_excluded": len(ordered) * (len(ordered) - 1) // 2 - len(pairs),
        "column_pairs_total": sum(len(a.column_names) * len(b.column_names) for a, b in pairs),
        "column_pairs_examined": 0, "block_windows_total": 0, "block_windows_scanned": 0,
        "block_candidate_pairs": 0, "block_candidates_examined": 0,
        "findings_omitted": 0, "limit_reasons": [], "methods": methods,
    }
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()

    def skip(method: str, reason: str) -> None:
        counts = methods[method]["skipped"]
        counts[reason] = counts.get(reason, 0) + 1

    def emit(method: str, left: IntegrityTable, right: IntegrityTable,
             lr: Sequence[int], rr: Sequence[int], lc: Sequence[int], rc: Sequence[int],
             *, relation: dict | None = None, ambiguous: bool = False) -> None:
        evidence = {
            "method": method, "method_version": METHOD_VERSION,
            "left": _location(left, lr, lc), "right": _location(right, rr, rc),
            "relationship": relation or {"exact": True},
        }
        identity = _hash(evidence)
        if identity in seen:
            return
        seen.add(identity)
        methods[method]["matched"] += 1
        if len(findings) >= config.max_findings:
            coverage["findings_omitted"] += 1
            return
        left_used, right_used = set(lr), set(rr)
        findings.append({
            "evidence_id": identity, **evidence, "severity": "review",
            "status": "review_required", "review_status": "pending", "conclusion": "numeric_relationship_only",
            "matched_rows": len(lr), "matched_cells_per_source": len(lr) * len(lc),
            "coverage": {
                "left_parsed_row_fraction": len(lr) / len(left.values) if left.values else 0,
                "right_parsed_row_fraction": len(rr) / len(right.values) if right.values else 0,
                "left_source_row_fraction": len(lr) / left.source_rows if left.source_rows else 0,
                "right_source_row_fraction": len(rr) / right.source_rows if right.source_rows else 0,
                "left_excluded_row_ordinals": [i for i in range(len(left.values)) if i not in left_used],
                "right_excluded_row_ordinals": [i for i in range(len(right.values)) if i not in right_used],
                "scope": "located_cells_only", "missing_values_imputed": False,
            },
            "alignment": "value_multiset_mapping" if method.startswith("reordered") else
                         "contiguous_block" if method == "exact_block" else "row_ordinal",
            "mapping_ambiguous_due_to_ties": ambiguous,
            "identical_original_file_bytes": left.source_sha256 == right.source_sha256,
            "normal_explanations": list(NORMAL_EXPLANATIONS), "caution": CAUTION,
        })

    for left, right in pairs:
        if coverage["column_pairs_examined"] >= config.max_column_pairs:
            break
        for lc in range(len(left.column_names)):
            if coverage["column_pairs_examined"] >= config.max_column_pairs:
                break
            for rc in range(len(right.column_names)):
                if coverage["column_pairs_examined"] >= config.max_column_pairs:
                    break
                coverage["column_pairs_examined"] += 1
                aligned_methods = ("exact_column", "constant_offset", "constant_ratio", "affine")
                if len(left.values) != len(right.values):
                    for method in (*aligned_methods, "reordered_column", "shared_decimal_tail"):
                        skip(method, "row_count_mismatch")
                    continue
                count = len(left.values)
                valid = [i for i in range(count) if left.values[i][lc] is not None and right.values[i][rc] is not None]
                a = [left.values[i][lc] for i in valid]
                b = [right.values[i][rc] for i in valid]
                reason = ("insufficient_rows" if len(valid) < config.min_rows else
                          "insufficient_coverage" if len(valid) / count < config.min_coverage else
                          "constant_or_low_distinctness" if min(len(set(a)), len(set(b))) < config.min_distinct else "")
                if reason:
                    for method in aligned_methods:
                        skip(method, reason)
                else:
                    matched = False
                    for method in aligned_methods:
                        if matched:
                            skip(method, "stronger_relation_reported")
                            continue
                        atol, _ = _number(config.absolute_tolerance)
                        if method != "exact_column" and min(max(a) - min(a), max(b) - min(b)) <= atol:
                            skip(method, "variation_below_absolute_tolerance")
                            continue
                        methods[method]["tested"] += 1
                        relation = ({"exact": True} if a == b else None) if method == "exact_column" else _transform(a, b, method, config)
                        if relation is not None:
                            emit(method, left, right, valid, valid, [lc], [rc], relation=relation)
                            matched = True
                    if not matched:
                        methods["shared_decimal_tail"]["tested"] += 1
                        tail_rows, relationship = _decimal_tail(a, b, config)
                        if tail_rows:
                            positions = [valid[i] for i in tail_rows]
                            emit("shared_decimal_tail", left, right, positions, positions, [lc], [rc], relation=relationship)
                        else:
                            skip("shared_decimal_tail", relationship["skip"])
                    else:
                        skip("shared_decimal_tail", "stronger_relation_reported")
                if reason:
                    skip("shared_decimal_tail", reason)
                lr = [i for i in range(count) if left.values[i][lc] is not None]
                rr = [i for i in range(count) if right.values[i][rc] is not None]
                av, bv = [left.values[i][lc] for i in lr], [right.values[i][rc] for i in rr]
                reason = ("valid_count_mismatch" if len(lr) != len(rr) else
                          "insufficient_rows" if len(lr) < config.min_rows else
                          "insufficient_coverage" if len(lr) / count < config.min_coverage else
                          "constant_or_low_distinctness" if min(len(set(av)), len(set(bv))) < config.min_distinct else "")
                if reason:
                    skip("reordered_column", reason)
                else:
                    methods["reordered_column"]["tested"] += 1
                    mapped = _mapped_rows(av, bv, lr, rr)
                    if mapped is not None and mapped != lr:
                        emit("reordered_column", left, right, lr, mapped, [lc], [rc],
                             ambiguous=len(set(av)) < len(av))

    # Index fixed-size exact seeds, plus full-height row-multisets. Signatures
    # are actual rational tuples, so hash collisions cannot create evidence.
    indexes: dict[tuple[str, str], dict[str, dict[Any, list[tuple[int, int, list[int]]]]]] = {}
    height, width = config.min_rows, config.block_columns
    participating = {table.key for pair in pairs for table in pair}
    for table in ordered:
        if table.key not in participating:
            continue
        index = {"exact_block": defaultdict(list), "reordered_block": defaultdict(list)}
        indexes[table.key] = index
        nrows, ncols = len(table.values), len(table.column_names)
        coverage["block_windows_total"] += max(0, nrows - height + 1) * max(0, ncols - width + 1) + max(0, ncols - width + 1)
        for method in ("exact_block", "reordered_block"):
            if coverage["block_windows_scanned"] >= config.max_block_windows:
                break
            for col in range(max(0, ncols - width + 1)):
                if coverage["block_windows_scanned"] >= config.max_block_windows:
                    break
                starts = range(max(0, nrows - height + 1)) if method == "exact_block" else (0,)
                for row in starts:
                    if coverage["block_windows_scanned"] >= config.max_block_windows:
                        break
                    coverage["block_windows_scanned"] += 1
                    row_ids = list(range(row, row + height)) if method == "exact_block" else list(range(nrows))
                    physical_cols = table.column_numbers[col:col + width]
                    physical_rows = [table.row_numbers[i] for i in row_ids]
                    if (any(b != a + 1 for a, b in zip(physical_cols, physical_cols[1:]))
                            or (method == "exact_block" and any(b != a + 1 for a, b in zip(physical_rows, physical_rows[1:])))):
                        skip(method, "noncontiguous_source_coordinates")
                        continue
                    complete = [i for i in row_ids if all(v is not None for v in table.values[i][col:col + width])]
                    if len(complete) < height or (method == "exact_block" and len(complete) != height):
                        skip(method, "insufficient_complete_rows")
                        continue
                    if method == "reordered_block" and len(complete) / nrows < config.min_coverage:
                        skip(method, "insufficient_coverage")
                        continue
                    block = [tuple(table.values[i][col:col + width]) for i in complete]
                    if any(len({values[j] for values in block}) < config.min_distinct for j in range(width)):
                        skip(method, "constant_or_low_distinctness")
                        continue
                    signature = tuple(block) if method == "exact_block" else tuple(sorted(block))
                    index[method][signature].append((row, col, complete))

    for left, right in pairs:
        covered_blocks: list[tuple[int, ...]] = []
        for method in ("exact_block", "reordered_block"):
            li, ri = indexes[left.key][method], indexes[right.key][method]
            for signature in li:
                if signature not in ri:
                    continue
                coverage["block_candidate_pairs"] += len(li[signature]) * len(ri[signature])
                if coverage["block_candidates_examined"] >= config.max_block_candidates:
                    continue
                for lrow, lcol, lr in li[signature]:
                    if coverage["block_candidates_examined"] >= config.max_block_candidates:
                        break
                    for rrow, rcol, rr in ri[signature]:
                        if coverage["block_candidates_examined"] >= config.max_block_candidates:
                            break
                        coverage["block_candidates_examined"] += 1
                        methods[method]["tested"] += 1
                        if method == "reordered_block":
                            if len(left.values) != len(right.values):
                                skip(method, "row_count_mismatch")
                                continue
                            a = [tuple(left.values[i][lcol:lcol + width]) for i in lr]
                            b = [tuple(right.values[i][rcol:rcol + width]) for i in rr]
                            mapped = _mapped_rows(a, b, lr, rr)
                            if mapped is not None and mapped != lr:
                                emit(method, left, right, lr, mapped,
                                     list(range(lcol, lcol + width)), list(range(rcol, rcol + width)),
                                     ambiguous=len(set(a)) < len(a))
                        else:
                            if any(top <= lrow and lrow + height <= end
                                   and col <= lcol and lcol + width <= last
                                   and lrow - top == rrow - other_top
                                   and lcol - col == rcol - other_col
                                   for top, end, col, last, other_top, other_col in covered_blocks):
                                skip(method, "overlapping_seed_already_reported")
                                continue
                            # Extend a seed vertically and horizontally, but
                            # never cross a missing/nonfinite/non-numeric cell.
                            top_l, top_r, end_l, end_r = lrow, rrow, lrow + height, rrow + height
                            def equal_row(a: int, b: int) -> bool:
                                x, y = left.values[a][lcol:lcol + width], right.values[b][rcol:rcol + width]
                                return None not in x and x == y
                            while (top_l > 0 and top_r > 0
                                   and left.row_numbers[top_l] == left.row_numbers[top_l - 1] + 1
                                   and right.row_numbers[top_r] == right.row_numbers[top_r - 1] + 1
                                   and equal_row(top_l - 1, top_r - 1)):
                                top_l -= 1
                                top_r -= 1
                            while (end_l < len(left.values) and end_r < len(right.values)
                                   and left.row_numbers[end_l] == left.row_numbers[end_l - 1] + 1
                                   and right.row_numbers[end_r] == right.row_numbers[end_r - 1] + 1
                                   and equal_row(end_l, end_r)):
                                end_l += 1
                                end_r += 1
                            c_left, c_right, last_left, last_right = lcol, rcol, lcol + width, rcol + width
                            def equal_col(a: int, b: int) -> bool:
                                return all(left.values[i][a] is not None and left.values[i][a] == right.values[j][b]
                                           for i, j in zip(range(top_l, end_l), range(top_r, end_r)))
                            while (c_left > 0 and c_right > 0
                                   and left.column_numbers[c_left] == left.column_numbers[c_left - 1] + 1
                                   and right.column_numbers[c_right] == right.column_numbers[c_right - 1] + 1
                                   and equal_col(c_left - 1, c_right - 1)):
                                c_left -= 1
                                c_right -= 1
                            while (last_left < len(left.column_names) and last_right < len(right.column_names)
                                   and left.column_numbers[last_left] == left.column_numbers[last_left - 1] + 1
                                   and right.column_numbers[last_right] == right.column_numbers[last_right - 1] + 1
                                   and equal_col(last_left, last_right)):
                                last_left += 1
                                last_right += 1
                            covered_blocks.append((top_l, end_l, c_left, last_left, top_r, c_right))
                            emit(method, left, right, list(range(top_l, end_l)), list(range(top_r, end_r)),
                                 list(range(c_left, last_left)), list(range(c_right, last_right)))

    if coverage["column_pairs_examined"] < coverage["column_pairs_total"]:
        coverage["limit_reasons"].append("max_column_pairs")
    if coverage["block_windows_scanned"] < coverage["block_windows_total"]:
        coverage["limit_reasons"].append("max_block_windows")
    if coverage["block_candidates_examined"] < coverage["block_candidate_pairs"]:
        coverage["limit_reasons"].append("max_block_candidates")
    if coverage["findings_omitted"]:
        coverage["limit_reasons"].append("max_findings")
    coverage["pattern_checks_evaluated"] = sum(item["tested"] for item in methods.values())
    coverage["multiple_comparisons"] = {
        "adjustment": "not_applicable_no_p_values", "significance_claimed": False,
        "note": "跨文件/列/窗口搜索越多，巧合关系越多；未计算概率、显著性或造假评分。",
    }
    source_coverage = [table.coverage() for table in ordered]
    from src.research.integrity_review import finding as common_finding
    common = [common_finding(
        rule=item["method"], version=METHOD_VERSION,
        locations=[item["left"], item["right"]],
        evidence={key: item[key] for key in ("relationship", "alignment", "mapping_ambiguous_due_to_ties", "matched_rows")},
        applicability=["Finite numeric cells in the declared source ranges", "Row ordinals do not establish biological sample identity"],
        counterexamples=list(NORMAL_EXPLANATIONS), coverage=item["coverage"], summary=CAUTION)
        for item in findings]
    return {
        "method_version": METHOD_VERSION, "parameters": asdict(config), "sources": source_coverage,
        "common_findings": common,
        "findings": findings, "coverage": coverage,
        "coverage_status": "partial" if coverage["limit_reasons"] or any(t.truncation_reasons for t in ordered) else
                           "not_applicable" if not pairs else "complete_for_declared_methods",
        "limitations": [CAUTION, "未命中不等于数据没有问题；缺失值未补齐，常量和低变化列不提供复用证据。",
                        "按行序号比较不证明样本身份相同；重排序映射只由数值构造。",
                        "数值精确相同不表示原始文本/格式相同；浮点输入已受上游解析精度限制。",
                        "未检测任意子集重排、转置、非线性关系或不同时间采样的动态对齐。"],
    }


__all__ = ["METHOD_VERSION", "IntegrityConfig", "IntegrityTable", "IntegrityInputLimitError",
           "table_from_rows", "table_from_frame", "audit_numeric_reuse"]

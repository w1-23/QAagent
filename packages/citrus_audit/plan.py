"""AuditPlanV1 policy and bounded comparisons over authorized table snapshots.

The plan language stays backward compatible. In addition to exact keyed sample
alignment and exact finite rectangles, `window_search` accepts several
explicitly authorized rectangles per side and performs a bounded,
deterministic, hash-seeded search over contiguous windows. Hash ordering only
selects which windows and pairs fit the declared budget; a candidate becomes a
lead only after the same exact rational comparison (with the declared unit
conversion and tolerances) used by finite rectangles. Hash collisions cannot
create evidence and no receipt asserts misconduct.
"""
from collections import Counter
from fractions import Fraction

from .integrity import METHODS, _hash, _location, _number, _rational

VERSION = "audit-plan/1"
MODES = frozenset({"experimental_quality", "research_integrity"})
LEADS = frozenset({"sample_alignment", "finite_rectangle", "window_search"})
QUALITY_DEFAULT = ("empty_table", "empty_column", "missing_values", "missing_cell", "mixed_numeric_text",
                   "non_numeric_cell", "duplicate_key", "duplicate_row", "required_field_absent",
                   "out_of_range", "logic_conflict", "expected_experiment_structure", "iqr_outlier", "iqr_insufficient",
                   "unequal_group_size", "unequal_replicates", "duplicate_composite_key")
WINDOW_SEARCH_DEFAULT_SEED = "audit-plan/1"
MAX_RECTANGLES = 8
MAX_RECTANGLE_ROWS = 1024
DEFAULT_MAX_WINDOWS = 10_000
DEFAULT_MAX_PAIRS = 2_000
MAX_MAX_WINDOWS = 100_000
MAX_MAX_PAIRS = 50_000
MAX_REPORTED_MATCHES = 20
WINDOW_SEARCH_CAUTION = "窗口匹配仅为人工复核线索，不证明复制来源、研究结论错误或科研不端。"


def _labels(value, limit):
    return (isinstance(value, list) and 0 < len(value) <= limit
            and all(isinstance(v, str) and 0 < len(v.strip()) <= 200 for v in value)
            and len(set(value)) == len(value))


def _rows(value):
    return (isinstance(value, list) and 3 <= len(value) <= 32
            and all(type(row) is int and 1 <= row <= 1_048_576 for row in value)
            and len(set(value)) == len(value))


def _rect_rows(value):
    """Authorized rectangle rows: ordered, unique, original 1-based coordinates."""
    return (isinstance(value, list) and 3 <= len(value) <= MAX_RECTANGLE_ROWS
            and all(type(row) is int and 1 <= row <= 1_048_576 for row in value)
            and all(later > earlier for earlier, later in zip(value, value[1:])))


def _window(value):
    return (isinstance(value, dict) and set(value) == {"rows", "columns"}
            and type(value["rows"]) is int and 3 <= value["rows"] <= 32
            and type(value["columns"]) is int and 2 <= value["columns"] <= 8)


def _rectangles(value):
    """Validate multiple disjoint rectangles; return normalized copies or None."""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_RECTANGLES:
        return None
    rectangles, occupied = [], set()
    for rectangle in value:
        if (not isinstance(rectangle, dict) or set(rectangle) != {"rows", "columns"}
                or not _rect_rows(rectangle.get("rows"))
                or not _labels(rectangle.get("columns"), 8) or len(rectangle["columns"]) < 2):
            return None
        cells = {(row, column) for row in rectangle["rows"] for column in rectangle["columns"]}
        if len(cells) != len(rectangle["rows"]) * len(rectangle["columns"]) or occupied & cells:
            return None
        occupied |= cells
        rectangles.append({"rows": list(rectangle["rows"]), "columns": list(rectangle["columns"])})
    return rectangles


def _int_cap(value, default, maximum):
    if value is None:
        return default
    return value if type(value) is int and 1 <= value <= maximum else None


def parse_plan(raw):
    if not isinstance(raw, dict) or set(raw) - {"schema", "mode", "cross_file_methods", "comparisons", "parameter_source"}:
        raise ValueError("Invalid audit_plan fields")
    if raw.get("schema") != VERSION or raw.get("mode") not in MODES:
        raise ValueError("audit_plan needs audit-plan/1 and a supported mode")
    methods, pairs = raw.get("cross_file_methods", []), raw.get("comparisons", [])
    if not isinstance(methods, list) or any(not isinstance(m, str) or m not in METHODS for m in methods):
        raise ValueError("Choose cross-file methods from the stable catalogue")
    if not isinstance(pairs, list) or len(pairs) > 12:
        raise ValueError("At most 12 explicit comparisons per audit")
    if raw["mode"] != "research_integrity" and (methods or pairs):
        raise ValueError("Explicit cross-source comparisons require research_integrity")
    parameter_source = raw.get("parameter_source", "provided_bindings")
    if parameter_source not in {"provided_bindings", "experimental_protocol", "instrument_metadata"}:
        raise ValueError("Unknown parameter source")
    normalized = []
    for comparison in pairs:
        if not isinstance(comparison, dict) or comparison.get("method") not in LEADS:
            raise ValueError("Unknown audit comparison")
        method = comparison["method"]
        fields = {"method", "left", "right", "units", "right_factor_to_left",
                  "absolute_tolerance", "relative_tolerance"}
        if method == "window_search":
            fields |= {"window", "max_windows", "max_pairs", "seed"}
        if set(comparison) - fields:
            raise ValueError("Unknown audit comparison")
        units = comparison.get("units")
        if (not isinstance(units, list) or len(units) != 2 or any(not isinstance(unit, str)
                or not 0 < len(unit.strip()) <= 64 or unit.strip().lower() in {"unknown", "n/a", "?"} for unit in units)):
            raise ValueError("Comparisons require two known units, or 1 for dimensionless values")
        units = [unit.strip() for unit in units]
        if units[0] != units[1] and "right_factor_to_left" not in comparison:
            raise ValueError("Different units require an explicit conversion factor")
        if method == "window_search":
            window = comparison.get("window")
            if not _window(window):
                raise ValueError("window_search needs window rows 3-32 and columns 2-8")
            sides = []
            for side in ("left", "right"):
                ref = comparison.get(side)
                if (not isinstance(ref, dict) or set(ref) != {"file_index", "sheet", "rectangles"}
                        or type(ref["file_index"]) is not int or not 0 <= ref["file_index"] < 20
                        or not isinstance(ref["sheet"], str) or len(ref["sheet"]) > 250):
                    raise ValueError("Use original sheet names and explicit authorized rectangles")
                rectangles = _rectangles(ref["rectangles"])
                if rectangles is None:
                    raise ValueError("Rectangles need ordered 1-based rows and 2-8 distinct columns without overlap")
                if not any(len(rect["rows"]) >= window["rows"] and len(rect["columns"]) >= window["columns"]
                           for rect in rectangles):
                    raise ValueError("The window must fit inside an authorized rectangle on both sides")
                sides.append({"file_index": ref["file_index"], "sheet": ref["sheet"], "rectangles": rectangles})
            if (sides[0]["file_index"], sides[0]["sheet"]) == (sides[1]["file_index"], sides[1]["sheet"]):
                raise ValueError("Two distinct table selections must have matching comparison dimensions")
            max_windows = _int_cap(comparison.get("max_windows"), DEFAULT_MAX_WINDOWS, MAX_MAX_WINDOWS)
            max_pairs = _int_cap(comparison.get("max_pairs"), DEFAULT_MAX_PAIRS, MAX_MAX_PAIRS)
            seed = comparison.get("seed", WINDOW_SEARCH_DEFAULT_SEED)
            if max_windows is None or max_pairs is None:
                raise ValueError("Window and pair caps must be positive bounded integers")
            if not isinstance(seed, str) or not 0 < len(seed.strip()) <= 64:
                raise ValueError("The window search seed must be 1-64 characters")
            left, right = sides
        else:
            selection = "key_columns" if method == "sample_alignment" else "rows"
            sides = []
            for side in ("left", "right"):
                ref = comparison.get(side)
                if (not isinstance(ref, dict) or set(ref) != {"file_index", "sheet", "columns", selection}
                        or type(ref["file_index"]) is not int or not 0 <= ref["file_index"] < 20
                        or not isinstance(ref["sheet"], str) or len(ref["sheet"]) > 250
                        or not _labels(ref["columns"], 8)
                        or (selection == "key_columns" and (not _labels(ref[selection], 4) or set(ref[selection]) & set(ref["columns"])))
                        or (selection == "rows" and (not _rows(ref[selection]) or len(ref["columns"]) < 2))):
                    raise ValueError("Use original sheet names, exact columns and explicit keys or source rows")
                sides.append(ref)
            left, right = sides
            if ((left["file_index"], left["sheet"]) == (right["file_index"], right["sheet"])
                    or len(left["columns"]) != len(right["columns"]) or len(left[selection]) != len(right[selection])):
                raise ValueError("Two distinct table selections must have matching comparison dimensions")
        numbers = {}
        for key, default in (("right_factor_to_left", "1"), ("absolute_tolerance", "0"), ("relative_tolerance", "0")):
            value, _ = _number(comparison.get(key, default))
            if value is None or value < 0 or (key == "right_factor_to_left" and (not value or (units[0] == units[1] and value != 1))):
                raise ValueError("Invalid conversion or tolerance")
            numbers[key] = _rational(value)
        entry = {**comparison, **numbers, "units": units}
        if method == "window_search":
            entry.update(left=left, right=right, window={"rows": window["rows"], "columns": window["columns"]},
                         max_windows=max_windows, max_pairs=max_pairs, seed=seed.strip())
        normalized.append(entry)
    return {"schema": VERSION, "mode": raw["mode"], "cross_file_methods": list(dict.fromkeys(methods)),
            "comparisons": normalized, "parameter_source": parameter_source}


def key_columns(frame, names):
    """Exact textual identity; duplicate or missing keys are not guessed."""
    if any(list(frame.columns).count(name) != 1 for name in names):
        return None
    keys = []
    for row in frame[list(names)].itertuples(index=False, name=None):
        key = tuple(str(value) if value is not None else "" for value in row)
        keys.append(key if all(v.strip() and len(v) <= 256 and v.casefold() not in {"<na>", "nan", "nat"} for v in key) else None)
    counts = Counter(key for key in keys if key is not None)
    return {key: i for i, key in enumerate(keys) if key is not None and counts[key] == 1}, len(keys) - sum(v == 1 for v in counts.values())


def _confirm_cells(left, right, lr, rr, lc, rc, factor, absolute, relative):
    """The single exact comparison used by finite rectangles and window search."""
    a = [[left.values[r][c] for c in lc] for r in lr]
    b = [[right.values[r][c] for c in rc] for r in rr]
    omitted = sum(x is None or y is None for ar, br in zip(a, b) for x, y in zip(ar, br))
    cell_pairs = [(x, y * factor) for ar, br in zip(a, b) for x, y in zip(ar, br)
                  if x is not None and y is not None]
    pairs_digest = _hash([[_rational(x), _rational(y)] for x, y in cell_pairs])
    differences = sum(abs(x - y) > absolute + relative * max(abs(x), abs(y)) for x, y in cell_pairs)
    return omitted, cell_pairs, pairs_digest, differences


def _window_rank(seed, side, coordinates):
    """Deterministic hash seed for budgeted candidate selection; not evidence."""
    return _hash([VERSION, "window_search", seed, side, list(coordinates)])


def _authorized_windows(table, rectangles, window, max_windows, seed, side):
    """Enumerate and, above the cap, hash-select windows inside authorized rectangles."""
    rows_index = {row: i for i, row in enumerate(table.row_numbers)}
    columns_index = {}
    for i, name in enumerate(table.column_names):
        columns_index.setdefault(name, []).append(i)
    descriptors, total = [], 0
    for ridx, rectangle in enumerate(rectangles):
        column_positions = []
        for name in rectangle["columns"]:
            matches = columns_index.get(name, [])
            if len(matches) != 1:
                return None, "bound_columns_unavailable", None
            column_positions.append(matches[0])
        row_ordinals = []
        for row in rectangle["rows"]:
            position = rows_index.get(row)
            if position is None:
                return None, "bound_rows_unavailable", None
            row_ordinals.append(position)
        height, width = window["rows"], window["columns"]
        for top in range(len(row_ordinals) - height + 1):
            for left in range(len(column_positions) - width + 1):
                total += 1
                descriptors.append((ridx, top, left, tuple(row_ordinals[top:top + height]),
                                    tuple(column_positions[left:left + width])))
    truncated = total > max_windows
    if truncated:
        descriptors = sorted(descriptors, key=lambda item: (_window_rank(seed, side, item[:3]), item[:3]))[:max_windows]
        descriptors.sort(key=lambda item: item[:3])
    return descriptors, total, truncated


def _compare_window_search(receipt, binding, sides, found):
    """Budgeted hash-seeded window candidates, confirmed by the exact comparator.

    Candidate order is deterministic: windows whose unit-normalized content
    hashes match are examined first (hash collisions still need exact
    confirmation), then remaining position pairs in hash-seeded order. Only
    the configured budgets bound the search.
    """
    left, right = found
    window = binding["window"]
    factor, absolute, relative = (Fraction(binding[k]) for k in
                                  ("right_factor_to_left", "absolute_tolerance", "relative_tolerance"))
    left_windows, left_total, left_truncated = _authorized_windows(
        left, sides[0]["rectangles"], window, binding["max_windows"], binding["seed"], "left")
    if left_windows is None:
        receipt["reason"] = left_total
        return
    right_windows, right_total, right_truncated = _authorized_windows(
        right, sides[1]["rectangles"], window, binding["max_windows"], binding["seed"], "right")
    if right_windows is None:
        receipt["reason"] = right_total
        return

    def complete(table, descriptors):
        return [item for item in descriptors
                if all(table.values[r][c] is not None for r in item[3] for c in item[4])]

    def signature(table, descriptor, multiplier):
        return _hash([[_rational(table.values[r][c] * multiplier) for c in descriptor[4]]
                      for r in descriptor[3]])

    left_complete, right_complete = complete(left, left_windows), complete(right, right_windows)
    left_ranked = sorted(left_complete, key=lambda item: (_window_rank(binding["seed"], "left", item[:3]), item[:3]))
    right_ranked = sorted(right_complete, key=lambda item: (_window_rank(binding["seed"], "right", item[:3]), item[:3]))
    left_signatures = {item: signature(left, item, 1) for item in left_ranked}
    right_signatures = {item: signature(right, item, factor) for item in right_ranked}
    right_by_signature = {}
    for item in right_ranked:
        right_by_signature.setdefault(right_signatures[item], []).append(item)
    left_counts, right_counts = Counter(left_signatures.values()), Counter(right_signatures.values())
    exact_pairs_total = sum(count * right_counts.get(key, 0) for key, count in left_counts.items())

    def candidates():
        emitted = set()
        for ldesc in left_ranked:
            for rdesc in right_by_signature.get(left_signatures[ldesc], ()):
                key = (ldesc[:3], rdesc[:3])
                if key not in emitted:
                    emitted.add(key)
                    yield ldesc, rdesc, True
        for ldesc in left_ranked:
            for rdesc in right_ranked:
                key = (ldesc[:3], rdesc[:3])
                if key not in emitted:
                    emitted.add(key)
                    yield ldesc, rdesc, False

    coverage = receipt["coverage"]
    coverage.update(
        windows_total=left_total + right_total,
        windows_scanned=len(left_windows) + len(right_windows),
        windows_complete=len(left_complete) + len(right_complete),
        authorized_rectangles={"left": len(sides[0]["rectangles"]), "right": len(sides[1]["rectangles"])},
        candidate_pairs_total=len(left_complete) * len(right_complete),
        exact_signature_pairs_total=exact_pairs_total,
        candidate_pairs_examined=0,
        exact_signature_pairs_examined=0,
        cells_examined=0,
        missing_cells=0,
        candidate_order="exact_content_hash_then_hash_seeded_positions",
        candidate_signature="sha256_of_unit_normalized_exact_rationals;exact_confirmation_rejects_collisions",
    )
    limit_reasons = coverage["limit_reasons"]
    if left_truncated or right_truncated:
        limit_reasons.append("max_windows")
    limit_reasons.extend(sorted(set(left.truncation_reasons + right.truncation_reasons)))
    if not left_complete or not right_complete:
        receipt["reason"] = "insufficient_complete_windows"
        return
    examined, exact_examined = 0, 0
    matches, matches_omitted, candidate_digests = [], 0, []
    for ldesc, rdesc, from_signature in candidates():
        if examined >= binding["max_pairs"]:
            break
        examined += 1
        exact_examined += int(from_signature)
        lr, rr, lc, rc = ldesc[3], rdesc[3], ldesc[4], rdesc[4]
        _, cell_pairs, digest, differences = _confirm_cells(
            left, right, lr, rr, lc, rc, factor, absolute, relative)
        candidate_digests.append(digest)
        if differences or len({x for x, _ in cell_pairs}) < 3:
            continue
        if len(matches) >= MAX_REPORTED_MATCHES:
            matches_omitted += 1
            continue
        matches.append({
            "locations": [_location(left, lr, lc), _location(right, rr, rc)],
            "evidence": {"evaluated_pairs_sha256": digest, "cells_differing": 0,
                         "exact_decimal_match": all(x == y for x, y in cell_pairs),
                         "distinct_left_values": len({x for x, _ in cell_pairs}),
                         "tolerance_applied": bool(absolute or relative),
                         "window_rows": window["rows"], "window_columns": window["columns"]},
        })
    if examined < coverage["candidate_pairs_total"] and examined >= binding["max_pairs"]:
        limit_reasons.append("max_candidate_pairs")
    if matches_omitted:
        limit_reasons.append("max_reported_matches")
    coverage["candidate_pairs_examined"] = examined
    coverage["exact_signature_pairs_examined"] = exact_examined
    coverage["cells_examined"] = examined * window["rows"] * window["columns"]
    coverage["candidate_digests_sha256"] = _hash(candidate_digests)
    coverage["matched_windows"] = len(matches) + matches_omitted
    coverage["matches_reported"] = len(matches)
    evidence = {"window_search_match": bool(matches), "matched_windows": len(matches) + matches_omitted,
                "matches_reported": len(matches), "matches_omitted": matches_omitted,
                "candidate_pairs_examined": examined,
                "interpretation": "review_lead_only", "misconduct_verdict": False,
                "caveat": WINDOW_SEARCH_CAUTION}
    if matches:
        receipt["locations"] = matches[0]["locations"]
        evidence.update(matches[0]["evidence"])
        evidence["window_search_match"] = True
        evidence["matches"] = matches
    else:
        evidence.update(evaluated_pairs_sha256=coverage["candidate_digests_sha256"], cells_differing=None)
    receipt["evidence"] = evidence
    receipt["status"] = "partial" if limit_reasons else "complete"


def compare_bound(plan, snapshots):
    results = []
    for binding in plan["comparisons"]:
        method = binding["method"]
        receipt = {"method": method, "method_version": VERSION, "binding": binding, "status": "skipped",
                   "coverage": {"windows_scanned": 0, "candidate_pairs_examined": 0, "limit_reasons": []},
                   "interpretation_scope": "review_lead_only"}
        results.append(receipt)
        sides = [binding[name] for name in ("left", "right")]
        found = [snapshots.get((ref["file_index"], ref["sheet"])) for ref in sides]
        if any(value is None for value in found):
            receipt["reason"] = "bound_sheet_unavailable"
            continue
        (left, left_keys), (right, right_keys) = found
        if method == "window_search":
            _compare_window_search(receipt, binding, sides, (left, right))
            continue
        if any(not set(ref["columns"]) <= set(table.column_names) or
               any(table.column_names.count(c) != 1 for c in ref["columns"])
               for ref, table in zip(sides, (left, right))):
            receipt["reason"] = "bound_columns_unavailable"
            continue
        lc, rc = [[table.column_names.index(c) for c in ref["columns"]] for ref, table in zip(sides, (left, right))]
        if method == "sample_alignment":
            a, b = [keys.get(tuple(ref["key_columns"])) for keys, ref in zip((left_keys, right_keys), sides)]
            if a is None or b is None:
                receipt["reason"] = "bound_keys_unavailable"
                continue
            a, ax = a
            b, bx = b
            ids = sorted(a.keys() & b.keys(), key=a.get)
            receipt["coverage"].update(matched_unique_keys=len(ids), left_unmatched=len(a) - len(ids),
                right_unmatched=len(b) - len(ids), left_nonunique_or_empty=ax, right_nonunique_or_empty=bx)
            if len(ids) > 512:
                receipt["coverage"]["limit_reasons"].append("sample_pair_limit")
            lr, rr = [a[key] for key in ids[:512]], [b[key] for key in ids[:512]]
        else:
            indexes = [{row: i for i, row in enumerate(t.row_numbers)} for t in (left, right)]
            if any(not set(ref["rows"]) <= index.keys() for ref, index in zip(sides, indexes)):
                receipt["reason"] = "bound_rows_unavailable"
                continue
            lr, rr = [[index[row] for row in ref["rows"]] for ref, index in zip(sides, indexes)]
        if len(lr) < 3:
            receipt["reason"] = "insufficient_aligned_rows"
            continue
        receipt["coverage"]["limit_reasons"].extend(sorted(set(left.truncation_reasons + right.truncation_reasons)))
        omitted, cell_pairs, pairs_digest, differences = _confirm_cells(
            left, right, lr, rr, lc, rc, factor=Fraction(binding["right_factor_to_left"]),
            absolute=Fraction(binding["absolute_tolerance"]), relative=Fraction(binding["relative_tolerance"]))
        receipt["coverage"].update(windows_scanned=2, candidate_pairs_examined=1)
        receipt["coverage"]["missing_cells"] = omitted
        if method == "finite_rectangle" and omitted:
            receipt["reason"] = "nonfinite_cell_in_rectangle"
            continue
        receipt["coverage"]["cells_examined"] = len(cell_pairs)
        if not cell_pairs:
            receipt["reason"] = "no_finite_pairs"
            continue
        receipt.update(status="partial" if omitted or receipt["coverage"]["limit_reasons"] else "complete",
                       locations=[_location(left, lr, lc), _location(right, rr, rc)],
                       evidence={"evaluated_pairs_sha256": pairs_digest, "cells_differing": differences,
                                 "finite_rectangle_match": method == "finite_rectangle" and bool(cell_pairs)
                                     and not differences and len({x for x, _ in cell_pairs}) >= 3})
    return results

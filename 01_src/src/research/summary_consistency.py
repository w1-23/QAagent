"""Necessary integer-summary conditions with explicit rounding intervals.

Passing these conditions does not prove a realizable sample or authentic data.
Numeric spreadsheet cells do not preserve displayed decimal precision: callers
must provide it, or the relevant check is explicitly skipped.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
from typing import Any

VERSION = "rounding-interval-v1"


def _interval(value: Any, places: int | None):
    if isinstance(value, bool):
        raise ValueError("boolean is not a measurement")
    text = str(value).strip()
    if len(text) > 256:
        raise ValueError("numeric token exceeds checking limit")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError("not numeric") from exc
    if not number.is_finite():
        raise ValueError("nonfinite measurement")
    if abs(number.as_tuple().exponent) > 100 or len(number.as_tuple().digits) > 100:
        raise ValueError("numeric precision exceeds checking limit")
    if places is None:
        if not isinstance(value, str):
            raise ValueError("reported precision unavailable")
        places = max(0, -number.as_tuple().exponent)
    if type(places) is not int or not 0 <= places <= 12:
        raise ValueError("reported decimals must be an integer in [0,12]")
    half = Decimal(5).scaleb(-places - 1)
    return number - half, number + half


def _contains_integer(low: Decimal, high: Decimal) -> bool:
    # Inclusive endpoints conservatively admit either tie-breaking convention.
    return low.to_integral_value(rounding=ROUND_CEILING) <= high.to_integral_value(rounding=ROUND_FLOOR)


def _square_bounds(low: Decimal, high: Decimal):
    return (Decimal(0) if low <= 0 <= high else min(low * low, high * high),
            max(low * low, high * high))


def check_integer_summary(mean: Any, n: Any, sd: Any = None, *,
                          mean_decimals: int | None = None,
                          sd_decimals: int | None = None,
                          sd_kind: str = "") -> list[dict]:
    """Return executed/skipped checks with bounds, never a misconduct verdict."""
    checks = []
    base = {"method_version": VERSION, "conclusion_scope": "necessary_condition_only",
            "caveat": "需确认整数观测、独立样本量、未加权算术均值及报告精度；不据此认定造假。"}
    with localcontext() as context:
        context.prec = 60
        try:
            count = Decimal(str(n))
            if not count.is_finite() or count < 1 or count != count.to_integral_value() or count > 10**9:
                raise ValueError("invalid sample count")
            mean_low, mean_high = _interval(mean, mean_decimals)
            if max(abs(mean_low), abs(mean_high)) > Decimal("1e20"):
                raise ValueError("summary magnitude exceeds checking limit")
        except (ValueError, InvalidOperation) as exc:
            return [{**base, "check": "integer_mean_inconsistent", "status": "not_checked", "reason": str(exc)}]
        low, high = count * mean_low, count * mean_high
        checks.append({**base, "check": "integer_mean_inconsistent", "status": "checked",
                       "inconsistent": not _contains_integer(low, high),
                       "sum_interval": [str(low), str(high)],
                       "mean_interval": [str(mean_low), str(mean_high)]})
        if sd is None:
            return checks
        try:
            if sd_kind not in {"sample", "population"}:
                raise ValueError("SD convention unavailable")
            if sd_kind == "sample" and count < 2:
                raise ValueError("sample SD requires n >= 2")
            sd_low, sd_high = _interval(sd, sd_decimals)
            if Decimal(str(sd)) < 0 or sd_high > Decimal("1e20"):
                raise ValueError("invalid SD")
            sd_low = max(Decimal(0), sd_low)
        except (ValueError, InvalidOperation) as exc:
            checks.append({**base, "check": "integer_summary_inconsistent", "status": "not_checked", "reason": str(exc)})
            return checks
        mean_sq_low, mean_sq_high = _square_bounds(mean_low, mean_high)
        factor = count - 1 if sd_kind == "sample" else count
        q_low = factor * sd_low * sd_low + count * mean_sq_low
        q_high = factor * sd_high * sd_high + count * mean_sq_high
        checks.append({**base, "check": "integer_summary_inconsistent", "status": "checked",
                       "inconsistent": not _contains_integer(q_low, q_high),
                       "sum_of_squares_interval": [str(q_low), str(q_high)],
                       "sd_interval": [str(sd_low), str(sd_high)]})
    return checks


def compare_reported_summaries(tables, mappings: list[dict]) -> dict:
    """Recompute explicitly bound raw/report cells; never infer their meaning.

    Mappings name file indexes/sheets, raw_column and reported_row/columns.
    Precision and equal units/sample-unit confirmation are required for measured
    summaries. n is an exact count. Missing values are disclosed, not imputed.
    """
    from src.research.integrity import _location
    from src.research.integrity_review import finding
    if not isinstance(mappings, list) or len(mappings) > 50:
        raise ValueError("reported_summaries must contain at most 50 explicit bindings")
    checks, findings = [], []
    version = "raw-reported-summary/1"

    def select(index, sheet):
        if type(index) is not int or index < 0:
            raise ValueError("file index must be a nonnegative integer")
        candidates = [t for t in tables if t.source_id.startswith(f"attachment:{index}:")
                      and (not sheet or t.sheet == sheet)]
        if len(candidates) != 1:
            raise ValueError("select one snapshotted sheet explicitly")
        if candidates[0].truncation_reasons:
            raise ValueError("source snapshot is truncated; select a smaller complete input")
        return candidates[0]

    for binding in mappings:
        receipt = {"status": "not_checked", "binding": binding, "method_version": version}
        try:
            if not isinstance(binding, dict):
                raise ValueError("summary binding must be an object")
            raw = select(binding.get("raw_file_index"), binding.get("raw_sheet", ""))
            report = select(binding.get("reported_file_index"), binding.get("reported_sheet", ""))
            col = raw.column_names.index(binding["raw_column"])
            report_row = binding["reported_row"]
            if type(report_row) is not int:
                raise ValueError("reported_row is an original 1-based row number")
            rr = report.row_numbers.index(report_row)
            selected = binding.get("raw_rows")
            if selected is not None and (not isinstance(selected, list) or not selected
                    or len(selected) > 500 or any(type(row) is not int for row in selected)
                    or len(selected) != len(set(selected))):
                raise ValueError("raw_rows must list unique original row numbers")
            rows = [raw.row_numbers.index(row) for row in selected] if selected is not None else list(range(len(raw.values)))
            valid = [i for i in rows if raw.values[i][col] is not None]
            if not valid:
                raise ValueError("no finite raw observations")
            columns = binding.get("columns")
            if not isinstance(columns, dict) or not columns or set(columns) - {"mean", "n", "sd", "se"}:
                raise ValueError("columns maps n/mean/sd/se to actual reported column names")
            with localcontext() as context:
                context.prec = 60
                values = [Decimal(raw.values[i][col].numerator) / Decimal(raw.values[i][col].denominator) for i in valid]
                count = len(values)
                mean = sum(values) / count
                expected = {"n": Decimal(count), "mean": mean}
                if count >= 2:
                    sd = (sum((value - mean) ** 2 for value in values) / (count - 1)).sqrt()
                    expected.update(sd=sd, se=sd / Decimal(count).sqrt())
                coverage = {"selected_rows": len(rows), "used_rows": count,
                            "excluded_rows": [raw.row_numbers[i] for i in rows if i not in valid],
                            "scope": "explicit raw rows and reported cells only",
                            "report_text_caption_ocr": "not_checked", "p_values": "not_checked_without_design_and_test_binding"}
                results = []
                for metric, name in columns.items():
                    result = {"metric": metric, "status": "not_checked"}
                    try:
                        rc = report.column_names.index(name)
                        observed = report.values[rr][rc]
                        if observed is None or metric not in expected:
                            raise ValueError("summary unavailable or insufficient raw observations")
                        if binding.get("sample_unit_confirmed") is not True:
                            raise ValueError("confirm that raw rows represent the reported experimental/sample unit")
                        if metric != "n" and binding.get("same_units_confirmed") is not True:
                            raise ValueError("confirm matching units for raw and reported measurements")
                        if metric in {"sd", "se"} and binding.get("sd_kind") != "sample":
                            raise ValueError("only explicitly declared sample SD/SE is compared")
                        reported = Decimal(observed.numerator) / Decimal(observed.denominator)
                        if metric == "n":
                            low = high = reported
                        else:
                            places = (binding.get("decimals") or {}).get(metric)
                            if places is None:
                                raise ValueError("actual reported decimal precision required")
                            low, high = _interval(reported, places)
                        mismatch = not low <= expected[metric] <= high
                        result.update(status="checked", discrepancy=mismatch, recomputed=str(expected[metric]),
                                      reported=str(reported), admitted_interval=[str(low), str(high)])
                        if mismatch:
                            findings.append(finding(
                                rule="reported_" + metric + "_differs_from_raw", version=version,
                                locations=[_location(raw, valid, [col]), _location(report, [rr], [rc])],
                                evidence=result.copy(), applicability=["Explicit source/row/metric binding", "Declared sample unit, precision and matching units"],
                                counterexamples=["Different subsets or exclusion policies", "Weighted means or a different SD convention", "OCR or transcription errors; unit conversion"],
                                coverage=coverage, summary="报告值与所选原始数据的重算值不一致；需人工确认口径。"))
                    except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
                        result["reason"] = str(exc)
                    results.append(result)
                receipt.update(status="checked", results=results, coverage=coverage)
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            receipt["reason"] = str(exc)
        checks.append(receipt)
    return {"method_version": version, "checks": checks, "common_findings": findings,
            "coverage": {"requested_bindings": len(mappings),
                         "completed_bindings": sum(c["status"] == "checked" and all(r["status"] == "checked" for r in c.get("results", [])) for c in checks),
                         "unbound_report_text_captions": "not_checked", "p_values": "not_checked_without_test_design_binding"}}

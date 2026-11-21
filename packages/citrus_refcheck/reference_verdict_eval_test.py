"""Offline pytest gates and metrics for CitrusScholar ReferenceVerdictV1.

The corpus lives in ``reference_verdict_fixtures.py``.  Every case is a fixed
``entry`` / ``result`` pair with an independently written expected label.
This module never opens a socket, calls a provider, reads product state, or
writes files.  It can run under pytest::

    python -m pytest packages/citrus_refcheck/reference_verdict_eval_test.py

or directly when pytest is unavailable::

    PYTHONPATH=packages python3 packages/citrus_refcheck/reference_verdict_eval_test.py
"""
from __future__ import annotations

import json
import socket
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from unittest import mock

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from citrus_refcheck import reference_verdict_fixtures as fixture_data
from citrus_refcheck.reference_verdict_fixtures import AUDIT_ID, CASES
from citrus_refcheck.verdict import RULE_VERSION, project_verdict

REQUIRED_CLASSES = {
    "online_print": 5,
    "year_uncertainty": 5,
    "author_initials": 5,
    "journal_alias": 5,
    "article_page": 5,
    "distinct_versions": 5,
    "chinese_no_doi": 5,
    "ambiguous_homonym": 5,
    "provider_conflict": 5,
    "wrong_doi": 5,
    "timeout_404": 5,
    "incomplete": 5,
}
DECISIONS = {"verified", "correction_required", "not_confirmed"}
IDENTITIES = {"confirmed", "ambiguous", "unresolved"}
NOTES = {
    "verified": "已确认引用身份，未发现有证据支持的关键差错。",
    "correction_required": "登记证据支持更正至少一项已提交的书目信息。",
    "not_confirmed": "现有证据不足以确认引用；未核实不等于不存在。",
}
VALID_FIELDS = {"doi", "title", "authors", "year", "journal", "volume",
                "issue", "pages", "article_number"}
COVERAGE_KEYS = {"queried_sources", "successful_sources", "failed_sources",
                 "omitted_sources", "query_time", "stop_reason"}
GATE_CATEGORIES = {"identity", "decision", "variants", "material", "unknowns",
                   "coverage", "contract", "structure"}


def _pairs(rows, keys):
    return sorted(tuple(str(row.get(key, "")) for key in keys) for row in rows)


def _project(case):
    """Run one projection with networking disabled at the process boundary."""
    with mock.patch.object(socket, "socket",
                           side_effect=AssertionError("network used")), \
         mock.patch.object(socket, "create_connection",
                           side_effect=AssertionError("network used")):
        return project_verdict(case["entry"], case["result"], audit_id=AUDIT_ID)


@lru_cache(maxsize=1)
def _evaluate_all():
    with mock.patch.object(socket, "socket",
                           side_effect=AssertionError("network used")), \
         mock.patch.object(socket, "create_connection",
                           side_effect=AssertionError("network used")):
        return {case["id"]: project_verdict(case["entry"], case["result"],
                                            audit_id=AUDIT_ID)
                for case in CASES}


def _case_mismatches(case, actual):
    expected = case["expected"]
    cid = case["id"]
    rows = []

    def add(category, detail):
        rows.append({"case": cid, "class": case["class"],
                     "category": category, "detail": detail})

    if actual.get("identity") != expected["identity"]:
        add("identity", f"expected {expected['identity']!r} "
                        f"got {actual.get('identity')!r}")
    if actual.get("decision") != expected["decision"]:
        add("decision", f"expected {expected['decision']!r} "
                        f"got {actual.get('decision')!r}")
    variants = _pairs(actual.get("benign_variants") or [],
                      ("field", "variant_type"))
    if variants != expected["benign_variants"]:
        add("variants", f"expected {expected['benign_variants']!r} "
                        f"got {variants!r}")
    material = _pairs(actual.get("material_differences") or [],
                      ("field", "reason"))
    if material != expected["material_differences"]:
        add("material", f"expected {expected['material_differences']!r} "
                        f"got {material!r}")
    unknowns = _pairs(actual.get("critical_unknowns") or [],
                      ("field", "reason"))
    if unknowns != expected["critical_unknowns"]:
        add("unknowns", f"expected {expected['critical_unknowns']!r} "
                        f"got {unknowns!r}")
    if actual.get("coverage") != expected["coverage"]:
        add("coverage", f"expected {expected['coverage']!r} "
                        f"got {actual.get('coverage')!r}")
    if actual.get("rule_version") != expected["rule_version"]:
        add("contract", f"expected rule_version {expected['rule_version']!r} "
                        f"got {actual.get('rule_version')!r}")
    ref = actual.get("raw_audit_ref") or {}
    if (ref.get("audit_id") != expected["audit_id"]
            or ref.get("record_id") != expected["record_id"]):
        add("contract", f"expected raw_audit_ref "
                        f"{(expected['audit_id'], expected['record_id'])!r} "
                        f"got {(ref.get('audit_id'), ref.get('record_id'))!r}")
    if actual.get("note") != NOTES[expected["decision"]]:
        add("contract", "note does not match the labelled decision")
    for row in actual.get("benign_variants") or []:
        if not row.get("evidence_refs") or not row.get("explanation"):
            add("structure", f"benign variant lacks evidence: {row!r}")
    for row in actual.get("material_differences") or []:
        if not row.get("evidence_refs"):
            add("structure", f"material difference lacks evidence: {row!r}")
        if not row.get("submitted") or not row.get("registered"):
            add("structure", f"material difference lacks values: {row!r}")
    for row in actual.get("critical_unknowns") or []:
        if not row.get("missing_evidence"):
            add("structure", f"critical unknown lacks missing_evidence: {row!r}")
    return rows


def _mismatches(*categories):
    selected = []
    verdicts = _evaluate_all()
    for case in CASES:
        for row in _case_mismatches(case, verdicts[case["id"]]):
            if not categories or row["category"] in categories:
                selected.append(row)
    return selected


def _known_gap_rows(rows):
    gaps = {case["id"]: case["expected"] for case in CASES
            if case["expected"].get("known_gap")}
    known, unexpected = [], []
    for row in rows:
        gap = gaps.get(row["case"])
        if gap and row["category"] in set(gap["known_gap_categories"]):
            known.append(row)
        else:
            unexpected.append(row)
    return known, unexpected


def _unexpected(*categories):
    return _known_gap_rows(_mismatches(*categories))[1]


def _report(rows):
    lines = [f"{row['case']} ({row['class']}) [{row['category']}]: "
             f"{row['detail']}" for row in rows]
    return f"{len(rows)} verdict gate mismatch(es):\n" + "\n".join(lines)


def compute_metrics():
    verdicts = _evaluate_all()
    per_class = defaultdict(lambda: {"cases": 0, "identity_ok": 0,
                                     "decision_ok": 0, "exact": 0,
                                     "mismatches": 0})
    confusion = Counter()
    mismatches = []
    exact = 0
    expected_conflicts, reported_conflicts, normal_variants = set(), set(), set()
    for case in CASES:
        actual = verdicts[case["id"]]
        expected = case["expected"]
        row = per_class[case["class"]]
        row["cases"] += 1
        row["identity_ok"] += actual.get("identity") == expected["identity"]
        row["decision_ok"] += actual.get("decision") == expected["decision"]
        case_mismatches = _case_mismatches(case, actual)
        mismatches.extend(case_mismatches)
        row["mismatches"] += len(case_mismatches)
        row["exact"] += not case_mismatches
        exact += not case_mismatches
        confusion[f"{expected['decision']}->{actual.get('decision')}"] += 1
        cid = case["id"]
        expected_conflicts.update((cid, field, reason) for field, reason in expected["material_differences"])
        reported_conflicts.update((cid, field, reason) for field, reason in
                                  _pairs(actual["material_differences"], ("field", "reason")))
        normal_variants.update((cid, field) for field, _ in expected["benign_variants"])
    conflict_matches = len(expected_conflicts & reported_conflicts)
    variant_false_alarms = len(normal_variants & {(cid, field) for cid, field, _ in reported_conflicts})
    total = len(CASES)
    known_rows, unexpected_rows = _known_gap_rows(mismatches)
    gap_reasons = {case["id"]: case["expected"].get("known_gap", "")
                   for case in CASES}
    known_gaps = {}
    for row in known_rows:
        entry = known_gaps.setdefault(row["case"], {
            "case": row["case"], "categories": set(), "details": {},
            "reason": gap_reasons[row["case"]]})
        entry["categories"].add(row["category"])
        entry["details"][row["category"]] = row["detail"]
    for entry in known_gaps.values():
        entry["categories"] = sorted(entry["categories"])
    documented_gap_ids = {case["id"] for case in CASES
                          if case["expected"].get("known_gap")}
    for row in per_class.values():
        row["exact_rate"] = round(row["exact"] / row["cases"], 4)
        row["identity_accuracy"] = round(row["identity_ok"] / row["cases"], 4)
        row["decision_accuracy"] = round(row["decision_ok"] / row["cases"], 4)
    return {
        "cases": total,
        "classes": len(per_class),
        "exact_matches": exact,
        "exact_match_rate": round(exact / total, 4),
        "identity_accuracy": round(
            sum(row["identity_ok"] for row in per_class.values()) / total, 4),
        "decision_accuracy": round(
            sum(row["decision_ok"] for row in per_class.values()) / total, 4),
        "material_conflict_precision": round(conflict_matches / len(reported_conflicts), 4),
        "material_conflict_recall": round(conflict_matches / len(expected_conflicts), 4),
        "material_conflict_matches": conflict_matches,
        "material_conflicts_expected": len(expected_conflicts),
        "material_conflicts_reported": len(reported_conflicts),
        "benign_variant_false_alarm_rate": round(variant_false_alarms / len(normal_variants), 4),
        "benign_variant_false_alarms": variant_false_alarms,
        "benign_variant_fields": len(normal_variants),
        "mismatch_count": len(mismatches),
        "unexpected_mismatch_count": len(unexpected_rows),
        "known_gap_count": len(known_gaps),
        "known_gaps": [known_gaps[key] for key in sorted(known_gaps)],
        "resolved_known_gaps": sorted(documented_gap_ids - set(known_gaps)),
        "per_class": {name: dict(values)
                      for name, values in sorted(per_class.items())},
        "decision_confusion": dict(sorted(confusion.items())),
    }


def test_fixture_corpus_shape():
    assert len(CASES) >= 60, f"need 60+ fixtures, found {len(CASES)}"
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids)), "fixture ids must be unique"
    counts = Counter(case["class"] for case in CASES)
    assert set(counts) == set(REQUIRED_CLASSES), (
        f"classes differ: expected {sorted(REQUIRED_CLASSES)}, "
        f"got {sorted(counts)}")
    for name, minimum in REQUIRED_CLASSES.items():
        assert counts[name] >= minimum, (
            f"class {name!r} has {counts[name]}, need {minimum}")
    assert fixture_data.RULE_VERSION == RULE_VERSION
    for case in CASES:
        claims = case["entry"].get("claims")
        assert isinstance(claims, dict), f"{case['id']} claims not a dict"
        assert case["entry"].get("claims_sha256"), f"{case['id']} claims hash"
        expected = case["expected"]
        assert expected["identity"] in IDENTITIES
        assert expected["decision"] in DECISIONS
        assert expected["rule_version"] == RULE_VERSION
        assert set(expected["coverage"]) == COVERAGE_KEYS
        gap_categories = set(expected.get("known_gap_categories") or ())
        if expected.get("known_gap"):
            assert gap_categories and gap_categories <= GATE_CATEGORIES, (
                f"{case['id']} known gap has invalid categories")
        else:
            assert not gap_categories, (
                f"{case['id']} categories without a known_gap reason")
        for field, _variant in expected["benign_variants"]:
            assert field in VALID_FIELDS
        for field, _reason in expected["material_differences"]:
            assert field in VALID_FIELDS
        for field, _reason in expected["critical_unknowns"]:
            assert field in VALID_FIELDS | {"identity", "citation"}
        for record in case["result"]["facts"]["records"]:
            assert record.get("record_sha256"), (
                f"{case['id']} record lacks record_sha256")
            assert record.get("source"), f"{case['id']} record lacks source"


def test_fixture_labels_are_independent():
    source = Path(fixture_data.__file__).read_text(encoding="utf-8")
    for forbidden in ("from citrus_refcheck.verdict",
                      "import citrus_refcheck.verdict",
                      "project_verdict(",
                      "socket", "requests", "httpx"):
        assert forbidden not in source, (
            f"fixture module must not reference {forbidden!r}")


def test_projection_is_deterministic_and_offline():
    for case in CASES:
        assert _project(case) == _project(case), (
            f"{case['id']} projection is nondeterministic")


def test_identity_and_decision_gates():
    rows = _unexpected("identity", "decision")
    assert not rows, _report(rows)


def test_variant_and_material_gates():
    rows = _unexpected("variants", "material")
    assert not rows, _report(rows)


def test_unknown_coverage_and_contract_gates():
    rows = _unexpected("unknowns", "coverage", "contract", "structure")
    assert not rows, _report(rows)


def test_reference_verdict_metrics():
    metrics = compute_metrics()
    assert metrics["mismatch_count"] == 0, _report(_mismatches())
    assert metrics["material_conflict_precision"] == metrics["material_conflict_recall"] == 1
    assert metrics["benign_variant_false_alarm_rate"] == 0
    assert metrics["cases"] >= 60
    assert metrics["classes"] == len(REQUIRED_CLASSES)
    assert metrics["exact_matches"] == metrics["cases"]
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))


def _run_all_tests():
    names = sorted(name for name, value in globals().items()
                   if name.startswith("test_") and callable(value))
    failures = []
    for name in names:
        try:
            globals()[name]()
        except Exception as error:  # noqa: BLE001 - report every gate failure
            failures.append((name, error))
    metrics = compute_metrics()
    print(json.dumps({"metrics": metrics}, ensure_ascii=False,
                     sort_keys=True, indent=2))
    for name, error in failures:
        print(f"FAIL {name}: {type(error).__name__}: {error}")
    print(f"{len(names) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all_tests())

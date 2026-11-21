"""ReferenceVerdictV1: a conservative projection of retained registry facts."""
from collections import defaultdict
import re

from .compare import compare_fields
from .providers import normalize_doi, norm_title

RULE_VERSION = "reference-verdict/1"
_DIRECT_REGISTRIES = frozenset({"Crossref", "DataCite", "arXiv"})
_NOTES = {
    "verified": "已确认引用身份，未发现有证据支持的关键差错。",
    "correction_required": "登记证据支持更正至少一项已提交的书目信息。",
    "not_confirmed": "现有证据不足以确认引用；未核实不等于不存在。",
}


def _evidence(record):
    return {key: record[key] for key in ("source", "lookup_kind", "record_sha256", "url") if record.get(key)}


def _record_key(row):
    if row.get("doi"):
        return "doi", normalize_doi(row["doi"])
    if row.get("arxiv_id"):
        return "arxiv", row["arxiv_id"]
    return "url", row.get("url") or norm_title(row.get("title"))


def _name_matches(supplied, full):
    parts = norm_title(full).split()
    if len(parts) < 2:
        return False
    tokens = re.findall(r"[^\W\d_]+", supplied)
    for surname, given in ((tokens[:1], tokens[1:]), (tokens[-1:], tokens[:-1])):
        if not surname or not given or not all(len(t) == 1 or (t.isupper() and len(t) <= 4) for t in given):
            continue
        initials = "".join(given).casefold()
        for expected, names in ((parts[-1:], parts[:-1]), (parts[:1], parts[1:])):
            if norm_title(surname[0]) == expected[0] and initials == "".join(t[0] for t in names):
                return True
    return False


def _abbreviated_authors(submitted, registered):
    truncated = bool(re.search(r"\bet\s+al\b", submitted, re.I))
    submitted = re.sub(r"\bet\s+al\.?", "", submitted, flags=re.I).strip(" ,;.")
    left = re.split(r"\s*;\s*|\s+and\s+", submitted)
    right = re.split(r"\s*;\s*|\s+and\s+", registered)
    if not 0 < len(left) <= len(right) or (not truncated and len(left) != len(right)):
        return False
    return all(norm_title(a) == norm_title(b) or _name_matches(a, b) for a, b in zip(left, right))


def _variant(field, observation, record):
    if observation["input_certainty"] != "submitted":
        return None
    a, b = observation["submitted"], observation["registered"]
    if not a or not b or a == b:
        return None
    if field == "author" or field == "authors":
        if _abbreviated_authors(a, b):
            return "documented_author_abbreviation"
    if field == "journal" and norm_title(a) in {norm_title(v) for v in record.get("journal_aliases", [])}:
        return "registry_journal_alias"
    if field == "year":
        years = {item.get("kind"): str(item.get("year")) for item in record.get("years", [])}
        online, printed = years.get("published-online"), years.get("published-print")
        if online and printed and online != printed and {a, b} == {online, printed}:
            return "online_print_year"
    return None


def project_verdict(entry, result, *, audit_id):
    claims = entry.get("claims") or {}
    facts = result.get("facts") or {}
    records = [r for r in facts.get("records", []) if r.get("record_sha256")]
    providers = facts.get("providers") or {}
    doi, arxiv = normalize_doi(claims.get("doi")), claims.get("arxiv_id")
    uncertain_fields = entry.get("uncertain_fields", [])
    checks = [(r, compare_fields(claims, r, uncertain_fields=uncertain_fields)) for r in records]
    direct = [(r, f) for r, f in checks if ((doi and normalize_doi(r.get("doi")) == doi)
              or (not doi and arxiv and r.get("arxiv_id") == arxiv))]
    title_matches = defaultdict(list)
    for r, fields in checks:
        if (fields["title"]["status"] == "matched"
                and (fields["authors"]["status"] == "matched" or _variant("authors", fields["authors"], r))
                and fields["year"]["status"] == "matched"):
            title_matches[_record_key(r)].append((r, fields))
    identity, selected = "unresolved", []
    direct_sources = {r.get("source") for r, _ in direct}
    anchored = bool(direct_sources & _DIRECT_REGISTRIES or len(direct_sources - {None, ""}) >= 2)
    if anchored:
        # An identifier returned by its registry establishes the target, but
        # cannot on its own resolve a conflicting submitted title.
        selected = direct
        if not claims.get("title") or ({f["title"]["status"] for _, f in direct} <= {"matched", "missing"}
                                      and any(f["title"]["status"] == "matched" for _, f in direct)):
            identity = "confirmed"
        elif len(title_matches) > 1 or (title_matches and not any(_record_key(r) in title_matches for r, _ in direct)):
            identity = "ambiguous"
        elif any(f["authors"]["status"] == f["year"]["status"] == "matched" for _, f in direct):
            identity = "confirmed"
    elif len(title_matches) > 1:
        identity = "ambiguous"
    elif not (doi or arxiv) and len(title_matches) == 1:
        selected = next(iter(title_matches.values()))
        if len({r.get("source") for r, _ in selected} - {None, ""}) >= 2:
            identity = "confirmed"

    differences, variants, unknowns = [], [], []
    if identity != "confirmed":
        unknowns.append({"field": "identity", "missing_evidence": "unique_registry_identity", "reason": identity})
    if entry.get("parse_status") == "invalid_row" or uncertain_fields:
        unknowns.append({"field": "citation", "missing_evidence": "complete_parsed_claims", "reason": "parse_uncertain"})
    if identity == "confirmed":
        for field in compare_fields(claims, None):
            observations = [(r, f[field], _variant(field, f[field], r)) for r, f in selected if f[field]["submitted"]]
            if not observations:
                continue
            accepted = [(r, obs, kind) for r, obs, kind in observations if kind or obs["status"] == "matched"]
            conflicts = [(r, obs) for r, obs, kind in observations if not kind and obs["status"] == "conflicting"]
            if conflicts and not accepted:
                r, obs = conflicts[0]
                values = {norm_title(v["registered"]) for _, v in conflicts}
                if len(values) == 1 and (r.get("source") in _DIRECT_REGISTRIES or
                        len({row.get("source") for row, _ in conflicts} - {None, ""}) >= 2):
                    differences.append({"field": field, "submitted": obs["submitted"], "registered": obs["registered"],
                                        "evidence_refs": [_evidence(row) for row, _ in conflicts],
                                        "reason": obs["reason"] or "different_registry_value"})
                    continue
            if not conflicts and accepted:
                for r, obs, kind in accepted:
                    if kind:
                        variants.append({"field": field, "variant_type": kind, "evidence_refs": [_evidence(r)],
                                         "explanation": f"{obs['submitted']} / {obs['registered']}"})
                        break
                continue
            unknowns.append({"field": field, "missing_evidence": "consistent_registry_field",
                             "reason": "registry_disagreement" if conflicts and accepted else observations[0][1]["reason"]})

    # Prove a wrong submitted DOI only with an independently corroborated
    # alternative plus a direct record clearly naming a different object.
    if anchored and len(title_matches) == 1 and doi and "doi" not in uncertain_fields:
        alternatives = next(iter(title_matches.values()))
        alternate = normalize_doi(alternatives[0][0].get("doi"))
        related = (any(alternate == normalize_doi(value) for row, _ in direct
                       for value in (row.get("related_dois") or []))
                   or any(doi == normalize_doi(value) for row, _ in alternatives
                          for value in (row.get("related_dois") or [])))
        if (alternate and alternate != doi and len({r.get("source") for r, _ in alternatives} - {None, ""}) >= 2
                and all(f["title"]["status"] == "conflicting" for _, f in direct)
                and not related):
            differences.append({"field": "doi", "submitted": doi, "registered": alternate,
                                "evidence_refs": [_evidence(r) for r, _ in direct + alternatives],
                                "reason": "identifier_names_different_work"})
    decision = "correction_required" if differences else "verified" if identity == "confirmed" and not unknowns else "not_confirmed"
    return {"identity": identity, "material_differences": differences, "benign_variants": variants,
            "critical_unknowns": unknowns, "decision": decision, "note": _NOTES[decision],
            "rule_version": RULE_VERSION, "raw_audit_ref": {"audit_id": audit_id, "record_id": entry.get("record_id")},
            "coverage": {"queried_sources": sorted(providers),
                         "successful_sources": sorted(k for k, v in providers.items() if v in {"ok", "not_found"}),
                         "failed_sources": sorted(k for k, v in providers.items() if v == "error"),
                         "omitted_sources": sorted(set(facts.get("planned_sources", [])) - providers.keys()),
                         "query_time": facts.get("query_time"), "stop_reason": result.get("stop_reason") or result.get("reason")}}

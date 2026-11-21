"""Fixed offline labelled fixtures for ReferenceVerdictV1.

Each case mirrors the persisted ``entry`` / ``result`` shapes used by
``src.research.reference_audit`` and carries an independently written
expected label.  The module uses only hashlib/json: it imports no provider
transport and performs no I/O, so the corpus is deterministic and offline.
"""
from __future__ import annotations

import hashlib
import json

RULE_VERSION = "reference-verdict/1"
AUDIT_ID = "0" * 31 + "1"

D1 = "10.1000/alpha"
D2 = "10.1000/beta"
D3 = "10.1000/gamma"
T1 = "A Study of Example Systems"
A1 = "John Smith"
J1 = "Journal of Example Studies"
UNRELATED = "Completely Unrelated Topic in Another Field"


def _sha(value):
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _claims(*, doi=None, arxiv_id=None, title=None, authors=None, year=None,
            journal=None, venue=None, volume=None, issue=None, pages=None,
            article_number=None):
    claims = {}
    for key, value in (("doi", doi), ("arxiv_id", arxiv_id), ("title", title),
                       ("authors", authors), ("year", year),
                       ("journal", journal), ("venue", venue),
                       ("volume", volume), ("issue", issue), ("pages", pages),
                       ("article_number", article_number)):
        if value is not None:
            claims[key] = value
    return claims


def _record(source, *, title, authors="", year="", doi="", venue="",
            volume="", issue="", pages="", article_number="", years=None,
            journal_aliases=None, related_dois=None, url="",
            arxiv_id="", lookup_kind="doi"):
    record = {
        "source": source,
        "lookup_kind": lookup_kind,
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "venue": venue,
        "volume": volume,
        "issue": issue,
        "pages": pages,
        "article_number": article_number,
        "years": list(years or []),
        "url": url or (f"https://doi.org/{doi}" if doi
                       else f"https://example.invalid/{source.lower().replace(' ', '-')}"),
    }
    if journal_aliases is not None:
        record["journal_aliases"] = list(journal_aliases)
    if related_dois is not None:
        record["related_dois"] = list(related_dois)
    record["record_sha256"] = _sha(record)
    return record


def _entry(claims, *, uncertain_fields=(), parse_status="parsed", record_id="rec"):
    return {
        "record_id": record_id,
        "parse_status": parse_status,
        "uncertain_fields": list(uncertain_fields),
        "claims": dict(claims),
        "claims_sha256": _sha(claims),
    }


def _result(records, providers, *, planned_sources=(), stop_reason="",
            query_time=0.0):
    errors = {name: "fixture_error" for name, state in providers.items()
              if state == "error"}
    return {
        "status": "fixture",
        "stop_reason": stop_reason,
        "facts": {
            "records": list(records),
            "providers": dict(providers),
            "errors": errors,
            "http_evidence": [],
            "planned_sources": list(planned_sources),
            "query_time": query_time,
        },
    }


def _coverage(providers, planned_sources, stop_reason):
    return {
        "queried_sources": sorted(providers),
        "successful_sources": sorted(name for name, state in providers.items()
                                     if state in {"ok", "not_found"}),
        "failed_sources": sorted(name for name, state in providers.items()
                                 if state == "error"),
        "omitted_sources": sorted(set(planned_sources) - set(providers)),
        "query_time": 0.0,
        "stop_reason": stop_reason or None,
    }


def _case(cid, cls, *, claims, records=(), providers=(), planned_sources=(),
          stop_reason="", uncertain_fields=(), parse_status="parsed",
          identity, decision, variants=(), material=(), unknowns=(),
          record_id=None, known_gap="", known_gap_categories=()):
    providers = dict(providers)
    planned_sources = tuple(planned_sources) or tuple(providers)
    rid = record_id or cid
    return {
        "id": cid,
        "class": cls,
        "entry": _entry(claims, uncertain_fields=uncertain_fields,
                        parse_status=parse_status, record_id=rid),
        "result": _result(records, providers, planned_sources=planned_sources,
                          stop_reason=stop_reason),
        "expected": {
            "identity": identity,
            "decision": decision,
            "benign_variants": sorted(variants),
            "material_differences": sorted(material),
            "critical_unknowns": sorted(unknowns),
            "coverage": _coverage(providers, planned_sources, stop_reason),
            "rule_version": RULE_VERSION,
            "audit_id": AUDIT_ID,
            "record_id": rid,
            "known_gap": known_gap,
            "known_gap_categories": list(known_gap_categories),
        },
    }


def _doi_case(cid, cls, *, claims, records, providers=None,
              planned_sources=None, **expected):
    providers = providers or {"Crossref:doi": "ok"}
    if planned_sources is None:
        planned_sources = tuple(providers)
    return _case(cid, cls, claims=claims, records=records, providers=providers,
                 planned_sources=planned_sources, **expected)


CASES = [
    # ------------------------------------------------------------------
    # explicit online / print publication years
    # ------------------------------------------------------------------
    _doi_case(
        "op1", "online_print",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2024"),
        records=[_record("Crossref", title=T1, authors=A1, year="2023", doi=D1,
                         venue=J1,
                         years=[{"kind": "published-online", "year": "2024"},
                                {"kind": "published-print", "year": "2023"}])],
        identity="confirmed", decision="verified",
        variants=[("year", "online_print_year")]),
    _doi_case(
        "op2", "online_print",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2023"),
        records=[_record("Crossref", title=T1, authors=A1, year="2024", doi=D1,
                         venue=J1,
                         years=[{"kind": "published-online", "year": "2024"},
                                {"kind": "published-print", "year": "2023"}])],
        identity="confirmed", decision="verified",
        variants=[("year", "online_print_year")]),
    _doi_case(
        "op3", "online_print",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2024"),
        records=[_record("Crossref", title=T1, authors=A1, year="2024", doi=D1,
                         venue=J1,
                         years=[{"kind": "published-online", "year": "2024"},
                                {"kind": "published-print", "year": "2024"}])],
        identity="confirmed", decision="verified"),
    _doi_case(
        "op4", "online_print",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2024"),
        records=[_record("Crossref", title=T1, authors=A1, year="2023", doi=D1,
                         venue=J1,
                         years=[{"kind": "published-online", "year": "2024"}])],
        identity="confirmed", decision="verified"),
    _doi_case(
        "op5", "online_print",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2023"),
        records=[_record("Crossref", title=T1, authors=A1, year="2023", doi=D1,
                         venue=J1,
                         years=[{"kind": "published-online", "year": "2024"},
                                {"kind": "published-print", "year": "2023"}])],
        identity="confirmed", decision="verified"),
    _doi_case(
        "op6", "online_print",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2025"),
        records=[_record("Crossref", title=T1, authors=A1, year="2023", doi=D1,
                         venue=J1,
                         years=[{"kind": "published-online", "year": "2024"},
                                {"kind": "published-print", "year": "2023"}])],
        identity="confirmed", decision="correction_required",
        material=[("year", "different_registry_value")]),

    # ------------------------------------------------------------------
    # year uncertainty (one-year drift without documented pair)
    # ------------------------------------------------------------------
    _doi_case(
        "yu1", "year_uncertainty",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2021"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        identity="confirmed", decision="not_confirmed",
        unknowns=[("year", "possible_online_print_difference_not_documented")]),
    _doi_case(
        "yu2", "year_uncertainty",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2019"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        identity="confirmed", decision="not_confirmed",
        unknowns=[("year", "possible_online_print_difference_not_documented")]),
    _doi_case(
        "yu3", "year_uncertainty",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2022"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        identity="confirmed", decision="correction_required",
        material=[("year", "different_registry_value")]),
    _doi_case(
        "yu4", "year_uncertainty",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        uncertain_fields=("year",),
        identity="confirmed", decision="not_confirmed",
        unknowns=[("citation", "parse_uncertain"),
                  ("year", "heuristic_input_field_requires_confirmation")]),
    _doi_case(
        "yu5", "year_uncertainty",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2021"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        uncertain_fields=("year",),
        identity="confirmed", decision="not_confirmed",
        unknowns=[("citation", "parse_uncertain"),
                  ("year", "heuristic_input_field_requires_confirmation")]),

    # ------------------------------------------------------------------
    # author initials and abbreviations
    # ------------------------------------------------------------------
    _doi_case(
        "ai1", "author_initials",
        claims=_claims(doi=D1, title=T1, authors="J. Smith", year="2020"),
        records=[_record("Crossref", title=T1, authors="John Smith", year="2020", doi=D1)],
        identity="confirmed", decision="verified",
        variants=[("authors", "documented_author_abbreviation")]),
    _doi_case(
        "ai2", "author_initials",
        claims=_claims(doi=D1, title=T1, authors="Smith J", year="2020"),
        records=[_record("Crossref", title=T1, authors="John Smith", year="2020", doi=D1)],
        identity="confirmed", decision="verified",
        variants=[("authors", "documented_author_abbreviation")]),
    _doi_case(
        "ai3", "author_initials",
        claims=_claims(doi=D1, title=T1, authors="J. Smith; A. Doe", year="2020"),
        records=[_record("Crossref", title=T1, authors="John Smith; Alice Doe",
                         year="2020", doi=D1)],
        identity="confirmed", decision="verified",
        variants=[("authors", "documented_author_abbreviation")]),
    _doi_case(
        "ai4", "author_initials",
        claims=_claims(doi=D1, title=T1, authors="Wang Y", year="2020"),
        records=[_record("Crossref", title=T1, authors="Wang X", year="2020", doi=D1)],
        identity="confirmed", decision="correction_required",
        material=[("authors", "different_authors")]),
    _doi_case(
        "ai5", "author_initials",
        claims=_claims(doi=D1, title=T1, authors="Kim SS", year="2020"),
        records=[_record("Crossref", title=T1, authors="Sang Suk Kim",
                         year="2020", doi=D1)],
        identity="confirmed", decision="verified",
        variants=[("authors", "documented_author_abbreviation")]),
    _doi_case(
        "ai6", "author_initials",
        claims=_claims(doi=D1, title=T1, authors="Kim S", year="2020"),
        records=[_record("Crossref", title=T1, authors="Sang Suk Kim",
                         year="2020", doi=D1)],
        identity="confirmed", decision="not_confirmed",
        unknowns=[("authors", "author_order_count_or_transliteration_requires_review")]),

    # ------------------------------------------------------------------
    # journal aliases and venue fallback
    # ------------------------------------------------------------------
    _doi_case(
        "ja1", "journal_alias",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       journal="J. Comput. Chem."),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         venue="Journal of Computational Chemistry",
                         journal_aliases=["J. Comput. Chem."])],
        identity="confirmed", decision="verified",
        variants=[("journal", "registry_journal_alias")]),
    _doi_case(
        "ja2", "journal_alias",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020", journal="PNAS"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         venue="Proceedings of the National Academy of Sciences",
                         journal_aliases=["PNAS"])],
        identity="confirmed", decision="verified",
        variants=[("journal", "registry_journal_alias")]),
    _doi_case(
        "ja3", "journal_alias",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020", venue=J1),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         venue=J1)],
        identity="confirmed", decision="verified"),
    _doi_case(
        "ja4", "journal_alias",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020", journal=J1),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         venue="Different Review Quarterly")],
        identity="confirmed", decision="correction_required",
        material=[("journal", "different_journal")]),
    _doi_case(
        "ja5", "journal_alias",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       journal="J. Example Stud."),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                    venue="Journal of Example Studies",
                    journal_aliases=["J. Example Stud."]),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D1,
                    venue="Different Review Quarterly"),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="not_confirmed",
        unknowns=[("journal", "registry_disagreement")]),
    _doi_case(
        "ja6", "journal_alias",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       journal="j. comput. chem."),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         venue="Journal of Computational Chemistry",
                         journal_aliases=["J. Comput. Chem."])],
        identity="confirmed", decision="verified",
        variants=[("journal", "registry_journal_alias")]),

    # ------------------------------------------------------------------
    # article number / page semantics
    # ------------------------------------------------------------------
    _doi_case(
        "ap1", "article_page",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       pages="62-64"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         pages="61-63")],
        identity="confirmed", decision="correction_required",
        material=[("pages", "page_range_differs_article_number_not_interchangeable")]),
    _doi_case(
        "ap2", "article_page",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       pages="62-64"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         pages="", article_number="e12345")],
        identity="confirmed", decision="not_confirmed",
        unknowns=[("pages", "provider_field_missing")]),
    _doi_case(
        "ap3", "article_page",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       article_number="e12345"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         article_number="e12345")],
        identity="confirmed", decision="verified"),
    _doi_case(
        "ap4", "article_page",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       article_number="e12345"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         pages="e12345", article_number="")],
        identity="confirmed", decision="not_confirmed",
        unknowns=[("article_number", "provider_field_missing")]),
    _doi_case(
        "ap5", "article_page",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       pages="62-64"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         pages="62")],
        identity="confirmed", decision="correction_required",
        material=[("pages", "page_range_differs_article_number_not_interchangeable")]),
    _doi_case(
        "ap6", "article_page",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       article_number="e12345"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         article_number="e99999")],
        identity="confirmed", decision="correction_required",
        material=[("article_number", "different_registry_value")]),

    # ------------------------------------------------------------------
    # distinct versions / preprints vs journal versions
    # ------------------------------------------------------------------
    _doi_case(
        "dv1", "distinct_versions",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="verified"),
    _case(
        "dv2", "distinct_versions",
        claims=_claims(arxiv_id="2401.00001", title=T1, authors=A1, year="2020"),
        records=[
            _record("arXiv", title=T1, authors=A1, year="2020",
                    arxiv_id="2401.00001", lookup_kind="arxiv_id"),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2,
                    lookup_kind="title"),
        ],
        providers={"arXiv:arxiv_id": "ok", "Crossref:title": "ok"},
        identity="confirmed", decision="verified"),
    _doi_case(
        "dv3", "distinct_versions",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=UNRELATED, authors=A1, year="2020", doi=D1,
                    related_dois=[D2]),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous")]),
    _case(
        "dv4", "distinct_versions",
        claims=_claims(title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                    lookup_kind="title"),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2,
                    lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous")]),
    _case(
        "dv5", "distinct_versions",
        claims=_claims(title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2,
                    lookup_kind="title"),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2,
                    lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="confirmed", decision="verified"),
    _case(
        "dv6", "distinct_versions",
        claims=_claims(title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2019", doi=D1,
                    lookup_kind="title"),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2,
                    lookup_kind="title"),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2,
                    lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="confirmed", decision="verified"),

    # ------------------------------------------------------------------
    # Chinese-language references without a DOI
    # ------------------------------------------------------------------
    _case(
        "zh1", "chinese_no_doi",
        claims=_claims(title="中文示例研究", authors="张三; 李四", year="2021"),
        records=[
            _record("Crossref", title="中文示例研究", authors="张三; 李四",
                    year="2021", doi=D3, lookup_kind="title"),
            _record("OpenAlex", title="中文示例研究", authors="张三; 李四",
                    year="2021", doi=D3, lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="confirmed", decision="verified"),
    _case(
        "zh2", "chinese_no_doi",
        claims=_claims(title="中文示例研究", authors="张三; 李四", year="2021"),
        records=[_record("Crossref", title="中文示例研究", authors="张三; 李四",
                         year="2021", doi=D3, lookup_kind="title")],
        providers={"Crossref:title": "ok"},
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),
    _case(
        "zh3", "chinese_no_doi",
        claims=_claims(title="中文示例研究", authors="张三; 李四", year="2021"),
        records=[
            _record("Crossref", title="中文示例研究", authors="张三; 李四",
                    year="2021", doi=D1, lookup_kind="title"),
            _record("OpenAlex", title="中文示例研究", authors="张三; 李四",
                    year="2021", doi=D2, lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous")]),
    _case(
        "zh4", "chinese_no_doi",
        claims=_claims(title="中文示例研究", authors="张三", year="2021"),
        records=[
            _record("Crossref", title="中文示例研究", authors="张三",
                    year="2021", doi=D3, lookup_kind="title"),
            _record("OpenAlex", title="中文示例研究", authors="张三",
                    year="2021", doi=D3, lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="confirmed", decision="verified"),
    _case(
        "zh5", "chinese_no_doi",
        claims=_claims(title="中文示例研究", year="2021"),
        records=[
            _record("Crossref", title="中文示例研究", authors="张三",
                    year="2021", doi=D3, lookup_kind="title"),
            _record("OpenAlex", title="中文示例研究", authors="张三",
                    year="2021", doi=D3, lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),
    _case(
        "zh6", "chinese_no_doi",
        claims=_claims(title="中文示例研究", authors="张三; 李四", year="2021"),
        records=[
            _record("Crossref", title="中文示例研究", authors="张三; 李四",
                    year="2019", doi=D3, lookup_kind="title"),
            _record("OpenAlex", title="中文示例研究", authors="张三; 李四",
                    year="2019", doi=D3, lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),

    # ------------------------------------------------------------------
    # ambiguous homonyms
    # ------------------------------------------------------------------
    _case(
        "ah1", "ambiguous_homonym",
        claims=_claims(title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                    lookup_kind="title"),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2,
                    lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous")]),
    _doi_case(
        "ah2", "ambiguous_homonym",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="verified"),
    _doi_case(
        "ah3", "ambiguous_homonym",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=UNRELATED, authors=A1, year="2020", doi=D1),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D3),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous")]),
    _case(
        "ah4", "ambiguous_homonym",
        claims=_claims(title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                    lookup_kind="title"),
            _record("OpenAlex", title=T1, authors="Different Person",
                    year="2020", doi=D2, lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),
    _case(
        "ah5", "ambiguous_homonym",
        claims=_claims(title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                    lookup_kind="title"),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D1,
                    lookup_kind="title"),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2,
                    lookup_kind="title"),
        ],
        providers={"Crossref:title": "ok", "OpenAlex:title": "ok"},
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous")]),
    _doi_case(
        "ah6", "ambiguous_homonym",
        claims=_claims(doi=D1),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="verified"),

    # ------------------------------------------------------------------
    # conflicting providers on the same identifier
    # ------------------------------------------------------------------
    _doi_case(
        "pc1", "provider_conflict",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020", journal=J1),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                    venue=J1),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D1,
                    venue="Different Review Quarterly"),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="not_confirmed",
        unknowns=[("journal", "registry_disagreement")]),
    _doi_case(
        "pc2", "provider_conflict",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1),
            _record("OpenAlex", title=T1, authors=A1, year="2018", doi=D1),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="not_confirmed",
        unknowns=[("year", "registry_disagreement")]),
    _doi_case(
        "pc3", "provider_conflict",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1),
            _record("OpenAlex", title=UNRELATED, authors=A1, year="2020", doi=D1),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="not_confirmed",
        unknowns=[("title", "registry_disagreement")]),
    _doi_case(
        "pc4", "provider_conflict",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020",
                       pages="62-64"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                    pages="62-64"),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D1,
                    pages="61-63"),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="not_confirmed",
        unknowns=[("pages", "registry_disagreement")]),
    _doi_case(
        "pc5", "provider_conflict",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                    volume="1"),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D1,
                    volume="2"),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="verified"),
    _doi_case(
        "pc6", "provider_conflict",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D1),
            _record("OpenAlex", title=T1, authors="Different Person",
                    year="2020", doi=D1),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="confirmed", decision="not_confirmed",
        unknowns=[("authors", "registry_disagreement")]),

    # ------------------------------------------------------------------
    # wrong submitted DOI
    # ------------------------------------------------------------------
    _doi_case(
        "wd1", "wrong_doi",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=UNRELATED, authors=A1, year="2020", doi=D1),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="ambiguous", decision="correction_required",
        material=[("doi", "identifier_names_different_work")],
        unknowns=[("identity", "ambiguous")]),
    _doi_case(
        "wd2", "wrong_doi",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=UNRELATED, authors=A1, year="2020", doi=D1),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2),
        ],
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous")]),
    _doi_case(
        "wd3", "wrong_doi",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=UNRELATED, authors=A1, year="2020", doi=D1),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        uncertain_fields=("doi",),
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous"),
                  ("citation", "parse_uncertain")]),
    _doi_case(
        "wd4", "wrong_doi",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title="A Study of Example System",
                    authors=A1, year="2020", doi=D1),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous")]),
    _doi_case(
        "wd5", "wrong_doi",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=UNRELATED, authors=A1, year="2020", doi=D1),
            _record("OpenAlex", title=UNRELATED, authors=A1, year="2020", doi=D1),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="ambiguous", decision="correction_required",
        material=[("doi", "identifier_names_different_work")],
        unknowns=[("identity", "ambiguous")]),
    _doi_case(
        "wd6", "wrong_doi",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[
            _record("Crossref", title=UNRELATED, authors=A1, year="2020", doi=D1),
            _record("Crossref", title=T1, authors=A1, year="2020", doi=D2,
                    related_dois=[D1]),
            _record("OpenAlex", title=T1, authors=A1, year="2020", doi=D2,
                    related_dois=[D1]),
        ],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "ok"},
        identity="ambiguous", decision="not_confirmed",
        unknowns=[("identity", "ambiguous")],
        known_gap=(
            "ReferenceVerdictV1 checks related_dois only on direct records; "
            "when the alternative DOI's record declares that it is a version "
            "of the submitted DOI (related_dois=[submitted]), the verdict still "
            "reports identifier_names_different_work for a distinct version."),
        known_gap_categories=("decision", "material", "contract")),

    # ------------------------------------------------------------------
    # timeout and 404 coverage
    # ------------------------------------------------------------------
    _doi_case(
        "to1", "timeout_404",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[],
        providers={"Crossref:doi": "error", "OpenAlex:doi": "error"},
        stop_reason="deadline",
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),
    _doi_case(
        "to2", "timeout_404",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[],
        providers={"Crossref:doi": "not_found"},
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),
    _doi_case(
        "to3", "timeout_404",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        providers={"Crossref:doi": "ok", "OpenAlex:doi": "error"},
        stop_reason="deadline",
        identity="confirmed", decision="verified"),
    _doi_case(
        "to4", "timeout_404",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[],
        providers={"Crossref:doi": "not_found", "OpenAlex:doi": "not_found"},
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),
    _doi_case(
        "to5", "timeout_404",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[],
        providers={},
        planned_sources=("Crossref:doi", "OpenAlex:doi"),
        stop_reason="deadline",
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),
    _doi_case(
        "to6", "timeout_404",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[],
        providers={"Crossref:doi": "error"},
        stop_reason="timeout",
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),

    # ------------------------------------------------------------------
    # incomplete rows / uncertain input fields
    # ------------------------------------------------------------------
    _doi_case(
        "inc1", "incomplete",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        parse_status="invalid_row",
        identity="confirmed", decision="not_confirmed",
        unknowns=[("citation", "parse_uncertain")]),
    _doi_case(
        "inc2", "incomplete",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        uncertain_fields=("title",),
        identity="confirmed", decision="not_confirmed",
        unknowns=[("citation", "parse_uncertain"),
                  ("title", "heuristic_input_field_requires_confirmation")]),
    _doi_case(
        "inc3", "incomplete",
        claims=_claims(doi=D1, title=T1),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        identity="confirmed", decision="verified"),
    _doi_case(
        "inc4", "incomplete",
        claims=_claims(doi=D1),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        identity="confirmed", decision="verified"),
    _doi_case(
        "inc5", "incomplete",
        claims=_claims(doi=D1, title=T1),
        records=[],
        providers={"Crossref:doi": "not_found"},
        parse_status="invalid_row",
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved"),
                  ("citation", "parse_uncertain")]),
    _case(
        "inc6", "incomplete",
        claims=_claims(title=T1),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1,
                         lookup_kind="title")],
        providers={"Crossref:title": "ok"},
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),
    _doi_case(
        "inc7", "incomplete",
        claims=_claims(doi=D1, title=T1, authors=A1, year="2020"),
        records=[_record("Crossref", title=T1, authors=A1, year="2020", doi=D1)],
        uncertain_fields=("authors",),
        identity="confirmed", decision="not_confirmed",
        unknowns=[("citation", "parse_uncertain"),
                  ("authors", "heuristic_input_field_requires_confirmation")]),
    _doi_case(
        "inc8", "incomplete",
        claims={},
        records=[],
        providers={"Crossref:doi": "not_found"},
        identity="unresolved", decision="not_confirmed",
        unknowns=[("identity", "unresolved")]),
]

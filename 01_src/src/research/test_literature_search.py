"""Unit tests for the scholarly identity search capability (no network)."""
from __future__ import annotations

import pytest

from src.research import literature as lit


def _rec(source="Crossref", title="Metabolomic Profiling of Citrus unshiu", doi="10.3390/plants11070967",
         authors="Kim SS; Lee J", year="2022", kind="journal-article", arxiv_id=""):
    return lit._rec(source, title=title, authors=authors, year=year, venue="Plants",
                    doi=doi, arxiv_id=arxiv_id, kind=kind)


@pytest.mark.parametrize("container, expected_state", [({}, "missing"), ({"title": "Plants"}, "matched")])
def test_datacite_publisher_is_not_a_journal(monkeypatch, container, expected_state):
    from src.research.reference_audit import compare_fields
    payload = {"data": {"attributes": {
        "doi": "10.1234/fixture", "titles": [{"title": "Fixture article"}],
        "publisher": "Fixture Publishing Company", "container": container}}}
    monkeypatch.setattr(lit, "_get", lambda *a, **k: (200, payload, ""))
    status, records, error = lit._datacite_work("10.1234/fixture")
    assert status == 200 and not error
    assert records[0]["venue"] == container.get("title", "")
    assert compare_fields({"journal": "Plants"}, records[0])["journal"]["status"] == expected_state


def test_doi_hit_marks_identifier_match(monkeypatch):
    monkeypatch.setattr(lit, "_crossref_work", lambda doi: (200, [_rec()], ""))
    monkeypatch.setattr(lit, "_openalex_work", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_epmc_doi", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_datacite_work", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_s2_doi", lambda doi: (404, [], ""))

    result = lit.search_literature(doi="10.3390/plants11070967")
    assert result["errors"] == {}
    best = result["candidates"][0]
    assert best["identifier_match"] is True
    assert best["field_conflicts"] == []
    assert best["title_similarity"] >= 0.9


def _controlled_doi_futures(monkeypatch, *, hit=True, completed_error=False):
    """Control provider completion without sleeping or running network threads."""
    from concurrent.futures import Future

    first, sibling, pending = Future(), Future(), Future()
    if hit:
        first.set_result((200, [_rec()], ""))
    if completed_error:
        sibling.set_result((503, [], "http_503"))
    submitted = iter([first, sibling, pending])

    class Executor:
        def __init__(self, **kwargs):
            pass

        def submit(self, fn):
            return next(submitted)

        def shutdown(self, **kwargs):
            pass

    monkeypatch.setattr(lit, "ThreadPoolExecutor", Executor)
    monkeypatch.setattr(lit, "wait", lambda futures, **kwargs:
                        ({first}, set(futures) - {first}) if hit else (set(), set(futures)))
    monkeypatch.setattr(lit, "_datacite_work", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_s2_doi", lambda doi: (404, [], ""))
    return pending


def test_doi_hit_does_not_invent_sibling_deadlines(monkeypatch):
    pending = _controlled_doi_futures(monkeypatch)
    result = lit.search_literature(doi="10.3390/plants11070967")
    assert result["candidates"][0]["identifier_match"]
    assert result["errors"] == {}
    assert pending.cancelled()
    assert "OpenAlex" in result["notes"][0]
    assert "不计为超时或已完成覆盖" in result["notes"][0]


def test_doi_hit_keeps_completed_sibling_failure(monkeypatch):
    _controlled_doi_futures(monkeypatch, completed_error=True)
    result = lit.search_literature(doi="10.3390/plants11070967")
    assert result["errors"] == {"OpenAlex": "http_503"}
    assert result["candidates"]


def test_real_search_deadline_still_reports_all_pending_sources(monkeypatch):
    _controlled_doi_futures(monkeypatch, hit=False)
    result = lit.search_literature(doi="10.3390/plants11070967")
    assert result["errors"] == {"Crossref": "deadline", "OpenAlex": "deadline",
                                "Europe PMC": "deadline"}
    assert not result["candidates"]


def test_doi_pointing_to_other_paper_is_a_conflict(monkeypatch):
    other = _rec(title="Salt tolerance mechanisms in citrus rootstocks", doi="10.3390/plants11070967",
                 year="2019")
    monkeypatch.setattr(lit, "_crossref_work", lambda doi: (200, [other], ""))
    monkeypatch.setattr(lit, "_openalex_work", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_epmc_doi", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_datacite_work", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_s2_doi", lambda doi: (404, [], ""))

    result = lit.search_literature(doi="10.3390/plants11070967",
                                   query="Metabolomic Profiling of Citrus unshiu",
                                   authors="Kim SS", year="2022")
    best = result["candidates"][0]
    assert best["identifier_match"] is True
    assert any(item.startswith("title:") for item in best["field_conflicts"])
    assert any(item.startswith("year:") for item in best["field_conflicts"])


def test_title_only_search_returns_candidates_without_confirmation(monkeypatch):
    monkeypatch.setattr(lit, "_crossref_work", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_crossref_search", lambda query, rows=8: (200, [_rec()], ""))
    monkeypatch.setattr(lit, "_openalex_search", lambda query, per_page=8: (200, [], ""))
    monkeypatch.setattr(lit, "_epmc_search", lambda query, page_size=8: (200, [], ""))
    monkeypatch.setattr(lit, "_pubmed_search", lambda query, retmax=6: (200, [], ""))
    result = lit.search_literature(query="Metabolomic Profiling of Citrus unshiu")
    best = result["candidates"][0]
    assert best["doi"] == "10.3390/plants11070967"
    assert best["identifier_match"] is False


def test_truncated_title_is_not_a_match(monkeypatch):
    junk = _rec(title="Gold Nugget mandarin postharvest storage", doi="10.1016/j.foodchem.2020.1")
    monkeypatch.setattr(lit, "_crossref_search", lambda query, rows=8: (200, [junk], ""))
    monkeypatch.setattr(lit, "_openalex_search", lambda query, per_page=8: (200, [], ""))
    monkeypatch.setattr(lit, "_epmc_search", lambda query, page_size=8: (200, [], ""))
    monkeypatch.setattr(lit, "_pubmed_search", lambda query, retmax=6: (200, [], ""))
    result = lit.search_literature(query="Gold Nug")
    assert result["candidates"][0]["identifier_match"] is False
    assert result["candidates"][0]["title_similarity"] < 0.6


def test_arxiv_identifier_returns_preprint(monkeypatch):
    preprint = lit._rec("arXiv", title="Deep learning for citrus disease detection",
                        authors="Zhang Y", year="2022", kind="preprint",
                        arxiv_id="2201.00001", doi="", url="http://arxiv.org/abs/2201.00001",
                        extra="journal_ref:Frontiers in Plant Science")
    monkeypatch.setattr(lit, "_arxiv_by_id", lambda arxiv_id: (200, [preprint], ""))
    monkeypatch.setattr(lit, "_datacite_arxiv", lambda arxiv_id: (404, [], ""))
    result = lit.search_literature(arxiv_id="2201.00001")
    best = result["candidates"][0]
    assert best["type"] == "preprint"
    assert best["arxiv_id"] == "2201.00001"
    assert best["identifier_match"] is True


def test_all_sources_failing_reports_errors_without_raising(monkeypatch):
    monkeypatch.setattr(lit, "_crossref_search", lambda query, rows=8: (0, [], "timeout"))
    monkeypatch.setattr(lit, "_openalex_search", lambda query, per_page=8: (0, [], "timeout"))
    monkeypatch.setattr(lit, "_epmc_search", lambda query, page_size=8: (0, [], "timeout"))
    monkeypatch.setattr(lit, "_pubmed_search", lambda query, retmax=6: (0, [], "timeout"))
    result = lit.search_literature(query="some citrus paper title that does not exist")
    assert result["candidates"] == []
    assert set(result["errors"]) >= {"Crossref", "OpenAlex", "Europe PMC", "PubMed"}


def test_chinese_title_triggers_translation(monkeypatch):
    monkeypatch.setattr(lit, "_translate", lambda query: "citrus rootstock salt tolerance")
    captured = {}

    def fake_search(query, rows=8):
        captured.setdefault("queries", []).append(query)
        return 200, [], ""

    monkeypatch.setattr(lit, "_crossref_search", fake_search)
    monkeypatch.setattr(lit, "_openalex_search", lambda query, per_page=8: (200, [], ""))
    monkeypatch.setattr(lit, "_epmc_search", lambda query, page_size=8: (200, [], ""))
    monkeypatch.setattr(lit, "_pubmed_search", lambda query, retmax=6: (200, [], ""))
    result = lit.search_literature(query="柑橘砧木耐盐性研究进展")
    assert result["translated"] == "citrus rootstock salt tolerance"
    assert any("citrus rootstock" in q for q in captured["queries"])
    assert not any("柑橘砧木" in q for q in captured["queries"])
    assert result["original_query"] == "柑橘砧木耐盐性研究进展"


def test_web_fallback_only_used_when_registries_are_empty(monkeypatch):
    monkeypatch.setattr(lit, "_crossref_search", lambda query, rows=8: (200, [], ""))
    monkeypatch.setattr(lit, "_openalex_search", lambda query, per_page=8: (200, [], ""))
    monkeypatch.setattr(lit, "_epmc_search", lambda query, page_size=8: (200, [], ""))
    monkeypatch.setattr(lit, "_pubmed_search", lambda query, retmax=6: (200, [], ""))
    monkeypatch.setattr(lit, "_web_candidate",
                        lambda query, year: ([_rec(source="Web search")], ""))
    result = lit.search_literature(query="Metabolomic Profiling of Citrus unshiu during storage",
                                   web_fallback=True)
    assert result["candidates"][0]["source"] == "Web search"
    assert result["notes"]


FULL_TITLE = ("Citrus unshiu peel extract: Antioxidant Effect and Sensory Quality")
REWRITTEN = ("Citrus unshiu peel extract: Antioxidant Effect and Sensory")


def _doi_only(monkeypatch, record):
    monkeypatch.setattr(lit, "_crossref_work", lambda doi: (200, [record], ""))
    monkeypatch.setattr(lit, "_openalex_work", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_epmc_doi", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_datacite_work", lambda doi: (404, [], ""))
    monkeypatch.setattr(lit, "_s2_doi", lambda doi: (404, [], ""))


def test_rewritten_title_after_identifier_hit_is_a_conflict(monkeypatch):
    """RF069: DOI is correct but the citing text dropped the tail of the title."""
    record = _rec(title=FULL_TITLE)
    _doi_only(monkeypatch, record)
    result = lit.search_literature(doi="10.3390/plants11070967", claimed_title=REWRITTEN)
    best = result["candidates"][0]
    assert best["identifier_match"] is True
    assert best["title_similarity"] > 0.9          # near-identical, must still conflict
    assert any(item.startswith("title_variant:") for item in best["field_conflicts"])


def test_claimed_title_is_used_when_only_a_doi_is_supplied(monkeypatch):
    """A bare DOI leaves no title to compare unless claimed_title is honoured."""
    _doi_only(monkeypatch, _rec(title=FULL_TITLE))
    result = lit.search_literature(doi="10.3390/plants11070967", claimed_title=REWRITTEN)
    best = result["candidates"][0]
    assert best["title_similarity"] < 1.0
    assert best["field_conflicts"]


def test_identical_title_with_punctuation_noise_is_not_a_conflict(monkeypatch):
    noisy = "Citrus unshiu peel extract - antioxidant effect, and sensory quality!"
    _doi_only(monkeypatch, _rec(title=FULL_TITLE))
    result = lit.search_literature(doi="10.3390/plants11070967", claimed_title=noisy)
    best = result["candidates"][0]
    assert best["identifier_match"] is True
    assert best["field_conflicts"] == []


def test_missing_claimed_title_is_not_invented(monkeypatch):
    _doi_only(monkeypatch, _rec(title=FULL_TITLE))
    result = lit.search_literature(doi="10.3390/plants11070967")
    best = result["candidates"][0]
    assert best["field_conflicts"] == []


def test_arxiv_identifier_falls_back_to_datacite_when_the_api_times_out(monkeypatch):
    """arXiv serialises requests; its deadline kill must not erase the paper."""
    # mimics _datacite_arxiv: same record with the arXiv id backfilled
    preprint = _rec("DataCite (arXiv DOI)", title="Deep learning for citrus disease detection",
                    doi="10.48550/arXiv.2201.00001", authors="Zhang Y", year="2022",
                    kind="preprint", arxiv_id="2201.00001")
    monkeypatch.setattr(lit, "_arxiv_by_id", lambda arxiv_id: (0, [], "deadline"))
    monkeypatch.setattr(lit, "_datacite_arxiv", lambda arxiv_id: (200, [preprint], ""))
    result = lit.search_literature(arxiv_id="2201.00001")
    best = result["candidates"][0]
    assert best["identifier_match"] is True
    assert best["arxiv_id"] == "2201.00001"
    assert "DataCite" in best["source"]


def test_datacite_arxiv_backfills_identifier_on_the_record(monkeypatch):
    monkeypatch.setattr(lit, "_datacite_work",
                        lambda doi: (200, [_rec("DataCite", title="A preprint title",
                                                doi=doi, kind="preprint")], ""))
    status, records, _ = lit._datacite_arxiv("2201.00002")
    assert status == 200
    assert records[0]["arxiv_id"] == "2201.00002"
    assert records[0]["doi"] == lit.normalize_doi("10.48550/arXiv.2201.00002")
    assert records[0]["source"] == "DataCite (arXiv DOI)"

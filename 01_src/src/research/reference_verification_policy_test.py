"""S2 reference verification authorization/cache tests: entirely offline mocks."""
from __future__ import annotations

import io
import json
import socket
from copy import deepcopy

import httpx
import pytest
from openpyxl import load_workbook

from src.core import tracing
from src.research import tools


DOI = "10.1000/synthetic-s2"
TITLE = "Synthetic reference for checking metadata comparisons"
CLAIM = {"doi": DOI, "title": TITLE, "authors": "Alice Smith", "year": "2020"}


def _record(**changes):
    record = tools._lit._rec("Crossref", title=TITLE, authors="Alice Smith",
                             year="2020", doi=DOI, venue="Synthetic Journal")
    record.update(changes)
    return record


@pytest.fixture(autouse=True)
def isolated_verification(tmp_path, monkeypatch):
    previous = tracing.web_search_enabled()
    tracing.set_web_search_enabled(True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tools, "_VERIFY_MEMO", {})
    attempts = []

    def no_external_io(*args, **kwargs):
        attempts.append("external IO")
        raise AssertionError("This test must not perform external IO")

    def unmocked_provider(*args, **kwargs):
        raise AssertionError("Provider must be explicitly mocked")

    monkeypatch.setattr(httpx, "get", no_external_io)
    monkeypatch.setattr(socket.socket, "connect", no_external_io)
    monkeypatch.setattr(tools._lit, "lookup_doi", unmocked_provider)
    monkeypatch.setattr(tools._lit, "search_literature", unmocked_provider)
    try:
        yield
    finally:
        tracing.set_web_search_enabled(previous)
        assert attempts == []


@pytest.mark.parametrize("raw", [CLAIM, {"title": TITLE},
                                  {"doi": "arxiv:2401.00001", "title": TITLE}])
@pytest.mark.parametrize("allow_web", [True, False])
def test_offline_gate_precedes_doi_registry_and_web_io(raw, allow_web):
    tracing.set_web_search_enabled(False)
    row = tools._verify_reference_cached(raw, allow_web)
    assert row["status"] == "not_checked"
    assert row["reason"] == "network_disabled"
    assert row["input_title"] == TITLE
    assert tools._VERIFY_MEMO == {}


def test_offline_batch_preserves_request_context_in_each_worker(monkeypatch):
    tracing.set_web_search_enabled(False)
    captured = []
    monkeypatch.setattr(tools, "get_request_tables", lambda: [])

    def save(filename, content, mime, **kwargs):
        captured.append(content)
        return "/mock/reference-report.xlsx"

    monkeypatch.setattr(tools, "_save_artifact", save)
    text = tools.verify_uploaded_references.func(
        references_json=json.dumps([dict(CLAIM, doi=f"10.1000/synthetic-{i}") for i in range(7)]),
        batch_size=2, allow_web=True)
    assert "not_checked 7" in text
    assert "分 4 批" in text
    sheet = load_workbook(io.BytesIO(captured[0])).active
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 7
    assert all(row[1] == "not_checked" for row in rows)
    assert all("未启用联网" in row[12] for row in rows)
    assert tracing.web_search_enabled() is False


def test_allow_web_false_still_allows_authorized_doi_lookup(monkeypatch):
    calls = []

    def lookup(doi):
        calls.append(doi)
        return "Crossref", _record(), ""

    monkeypatch.setattr(tools._lit, "lookup_doi", lookup)
    row = tools._verify_reference_cached(CLAIM, False)
    assert calls == [DOI]
    assert row["status"] == "metadata_matched"


@pytest.mark.parametrize("change,conflict", [({"authors": "Robert Jones"}, "authors"),
    ({"title": "Completely unrelated paper about quantum field theory"}, "title:"),
    ({"year": "2010"}, "year:")])
def test_same_doi_reuses_metadata_but_recompares_current_claim(monkeypatch, change, conflict):
    calls = []
    provider_record = _record()
    original = deepcopy(provider_record)

    def lookup(doi):
        calls.append(doi)
        return "Crossref", provider_record, ""

    monkeypatch.setattr(tools._lit, "lookup_doi", lookup)
    first = tools._verify_reference_cached(CLAIM, True)
    altered = dict(CLAIM, **change, citation="Current citation, not the cached one")
    second = tools._verify_reference_cached(altered, True)
    third = tools._verify_reference_cached(CLAIM, True)
    assert calls == [DOI]
    assert first["status"] == third["status"] == "metadata_matched"
    assert second["status"] == "metadata_mismatch"
    assert conflict in second["field_conflicts"]
    assert second["input_authors"] == altered["authors"]
    assert second["input_title"] == altered["title"]
    assert second["input_year"] == altered["year"]
    assert second["citation"] == altered["citation"]
    assert provider_record == original
    entry = next(iter(tools._VERIFY_MEMO.values()))[1]
    assert set(entry) == {"candidates", "errors"}
    assert not ({"status", "field_conflicts", "identifier_match", "title_similarity"}
                & set(entry["candidates"][0]))


def test_doi_normalization_shares_only_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr(tools._lit, "lookup_doi",
                        lambda doi: (calls.append(doi) or "Crossref", _record(), ""))
    first = tools._verify_reference_cached(dict(CLAIM, doi="https://doi.org/" + DOI.upper()), True)
    second = tools._verify_reference_cached(dict(CLAIM, doi="doi: " + DOI, authors="Robert Jones"), False)
    assert calls == [DOI]
    assert first["status"] == "metadata_matched"
    assert second["status"] == "metadata_mismatch"


def test_offline_request_does_not_reuse_online_verdict_or_poison_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(tools._lit, "lookup_doi",
                        lambda doi: (calls.append(doi) or "Crossref", _record(), ""))
    assert tools._verify_reference_cached(CLAIM, True)["status"] == "metadata_matched"
    tracing.set_web_search_enabled(False)
    offline = tools._verify_reference_cached(dict(CLAIM, authors="Robert Jones"), True)
    assert offline["status"] == "not_checked"
    assert offline["reason"] == "network_disabled"
    tracing.set_web_search_enabled(True)
    assert tools._verify_reference_cached(dict(CLAIM, authors="Robert Jones"), True)["status"] == "metadata_mismatch"
    assert calls == [DOI]


def test_cache_expiry_refetches_provider_metadata(monkeypatch):
    clock = [100.0]
    calls = []
    monkeypatch.setattr(tools.time, "time", lambda: clock[0])
    monkeypatch.setattr(tools._lit, "lookup_doi",
                        lambda doi: (calls.append(doi) or "Crossref", _record(), ""))
    tools._verify_reference_cached(CLAIM, True)
    clock[0] += tools._VERIFY_MEMO_TTL_SEC - 1
    tools._verify_reference_cached(CLAIM, True)
    assert len(calls) == 1
    clock[0] += 2
    tools._verify_reference_cached(CLAIM, True)
    assert len(calls) == 2


def test_network_errors_are_not_cached_as_missing_literature(monkeypatch):
    calls = []

    def lookup(doi):
        calls.append(doi)
        return ("", None, "timeout") if len(calls) == 1 else ("Crossref", _record(), "")

    monkeypatch.setattr(tools._lit, "lookup_doi", lookup)
    monkeypatch.setattr(tools._lit, "search_literature",
                        lambda **kwargs: {"candidates": [], "errors": {"Crossref": "timeout"}})
    first = tools._verify_reference_cached(CLAIM, True)
    assert first["status"] == "network_error"
    assert tools._VERIFY_MEMO == {}
    second = tools._verify_reference_cached(CLAIM, True)
    assert second["status"] == "metadata_matched"
    assert len(calls) == 2


def test_search_cache_strips_stale_comparisons_and_reannotates(monkeypatch):
    calls = []
    provider = _record(source="arXiv", doi="", arxiv_id="2401.00001", type="preprint",
                       identifier_match=False, title_similarity=0.0, field_conflicts=["stale"])

    def search(**kwargs):
        calls.append(kwargs)
        return {"candidates": [provider], "errors": {}}

    monkeypatch.setattr(tools._lit, "search_literature", search)
    claim = dict(CLAIM, doi="arxiv:2401.00001")
    first = tools._verify_reference_cached(claim, True)
    second = tools._verify_reference_cached(dict(claim, citation="New citation"), True)
    assert len(calls) == 1
    assert first["status"] == second["status"] == "metadata_matched"
    assert second["field_conflicts"] == ""
    assert second["citation"] == "New citation"
    assert provider["field_conflicts"] == ["stale"]


def test_search_cache_is_separated_by_web_fallback_option(monkeypatch):
    calls = []

    def search(**kwargs):
        calls.append(kwargs["web_fallback"])
        return {"candidates": [_record(source="Web search" if kwargs["web_fallback"] else "Crossref")],
                "errors": {}}

    monkeypatch.setattr(tools._lit, "search_literature", search)
    claim = {"title": TITLE, "authors": "Alice Smith", "year": "2020"}
    assert tools._verify_reference_cached(claim, True)["status"] == "web_candidate"
    assert tools._verify_reference_cached(claim, False)["status"] == "title_candidate"
    assert calls == [True, False]


def test_search_keys_keep_full_title_authors_and_arxiv_identifier():
    base = {"kind": "search", "title": "same prefix " * 25, "authors": "Alice Smith",
            "year": "2020", "arxiv_id": "2401.00001", "allow_web": False}
    keys = {tools._verify_memo_key(base)}
    for changes in ({"title": base["title"] + "different tail"}, {"authors": "Robert Jones"},
                    {"arxiv_id": "2401.00002"}, {"allow_web": True}):
        keys.add(tools._verify_memo_key(dict(base, **changes)))
    assert len(keys) == 5

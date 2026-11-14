"""Batch reference verification: batching, table shape, backward compatibility."""
from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook

from src.research import tools as research_tools

LEGACY_COLUMNS = ["DOI", "状态", "登记库", "输入标题", "登记标题", "输入作者", "登记作者",
                  "输入年份", "登记年份", "期刊", "标题相似度", "来源链接", "说明"]
NEW_COLUMNS = ["文献类型", "arXiv ID", "命中来源", "证据链接", "字段冲突"]


@pytest.fixture()
def captured(monkeypatch):
    state = {"files": []}

    def fake_save(filename, content, mime, **kwargs):
        state["files"].append({"filename": filename, "content": content, "mime": mime})
        return "/api/v2/session/s/artifacts/1"

    def fake_verify(raw, allow_web=True):
        index = int(str(raw.get("title", "0")).split("-")[-1])
        status = ["metadata_matched", "metadata_mismatch", "title_candidate",
                  "not_found_in_checked_registries", "network_error"][index % 5]
        return {"doi": raw.get("doi", ""), "status": status, "provider": "Crossref",
                "input_title": raw.get("title", ""), "metadata_title": "Registry title",
                "input_authors": "", "metadata_authors": "", "input_year": "", "metadata_year": "",
                "metadata_journal": "", "title_similarity": 0.9, "source_url": "https://doi.org/x",
                "detail": "stub", "literature_type": "journal-article", "arxiv_id": "",
                "evidence_source": "Crossref", "evidence_url": "https://doi.org/x",
                "field_conflicts": ""}

    monkeypatch.setattr(research_tools, "_save_artifact", fake_save)
    monkeypatch.setattr(research_tools, "_verify_reference_record", fake_verify)
    return state


def _refs(count: int) -> str:
    import json
    return json.dumps([{"doi": f"10.1000/x{i}", "title": f"Paper-{i}"} for i in range(count)])


def test_56_references_split_into_six_batches(captured):
    text = research_tools.verify_uploaded_references.func(references_json=_refs(56))
    assert "分 6 批" in text
    assert text.count("批：") == 6
    assert captured["files"][0]["filename"].endswith(".xlsx")


def test_table_keeps_legacy_columns_then_appends_new_ones(captured):
    research_tools.verify_uploaded_references.func(references_json=_refs(3))
    workbook = load_workbook(io.BytesIO(captured["files"][0]["content"]))
    sheet = workbook.active
    header = [cell.value for cell in sheet[1]]
    assert header[:13] == LEGACY_COLUMNS
    assert header[13:] == NEW_COLUMNS
    assert sheet.max_row == 4


def test_batch_size_does_not_change_status_distribution(captured):
    small = research_tools.verify_uploaded_references.func(references_json=_refs(20), batch_size=5)
    big = research_tools.verify_uploaded_references.func(references_json=_refs(20), batch_size=20)
    assert "分 4 批" in small and "分 1 批" in big
    counts_small = small.split("：", 1)[1].split("。", 1)[0]
    counts_big = big.split("：", 1)[1].split("。", 1)[0]
    assert counts_small == counts_big


def test_references_are_capped_and_reported(captured):
    text = research_tools.verify_uploaded_references.func(references_json=_refs(105), batch_size=50)
    assert "已处理 101 条" in text
    assert "另有 5 条未核验" in text



def test_arxiv_prefixed_doi_is_verified_as_an_arxiv_id(monkeypatch):
    """引用清单把 arXiv 写成 doi: arxiv:xxxx 时必须按 arXiv 号核验，不能当 DOI 去查。"""
    seen = {}

    def fake_search(**kwargs):
        seen.update(kwargs)
        return {"candidates": [{"identifier_match": True, "source": "arXiv",
                                "title": "Citrus: Leveraging Expert Cognitive Pathways",
                                "title_similarity": 1.0, "field_conflicts": "",
                                "arxiv_id": "2502.18274", "doi": "", "authors": "W",
                                "year": "2025", "venue": "", "url": "u",
                                "type": "preprint"}],
                "errors": {}}

    monkeypatch.setattr(research_tools._lit, "search_literature", fake_search)
    row = research_tools._verify_reference_record(
        {"doi": "arxiv:2502.18274",
         "title": "Citrus: Leveraging Expert Cognitive Pathways"})
    assert seen["arxiv_id"] == "2502.18274"
    assert seen["doi"] == ""
    assert row["status"] == "metadata_matched"
    assert row["arxiv_id"] == "2502.18274"


def test_real_doi_is_still_verified_as_a_doi(monkeypatch):
    seen = {}

    def fake_search(**kwargs):
        seen.update(kwargs)
        return {"candidates": [], "errors": {}}

    monkeypatch.setattr(research_tools._lit, "search_literature", fake_search)
    monkeypatch.setattr(research_tools._lit, "lookup_doi", lambda doi: ("", None, ""))
    research_tools._verify_reference_record(
        {"doi": "https://doi.org/10.3390/foods11111550", "title": "T"})
    assert seen["arxiv_id"] == ""
    assert seen["doi"] == "10.3390/foods11111550"


def test_arxiv_hint_parser_covers_urls_bare_ids_and_plain_dois():
    parse = research_tools._split_arxiv_hint
    assert parse("arxiv:2502.18274") == "2502.18274"
    assert parse("arXiv: 2309.05645v3") == "2309.05645"
    assert parse("https://arxiv.org/abs/2105.01553v2") == "2105.01553"
    assert parse("2309.05645v3") == "2309.05645"
    assert parse("10.3390/foods11111550") == ""
    assert parse("", None) == ""

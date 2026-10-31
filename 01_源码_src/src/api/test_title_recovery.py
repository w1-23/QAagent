# -*- coding: utf-8 -*-
"""题名回填（v9.9, R17）测试：纯函数规则 + sidecar 合并后的读取优先级。

只测规则与读取路径，不联网：真实回填由 `experiments/corpus_meta/recover_titles.py`
在服务器上跑，结果落在 `metadata_index.json`，这里验证"合并后谁优先"。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from src.core import corpus_source

ROOT = pathlib.Path(__file__).resolve().parents[2]          # 01_源码_src
RECOVER = ROOT / "experiments" / "corpus_meta" / "recover_titles.py"


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rec():
    return _load("recover_titles", RECOVER)


# ── 纯函数：查询词选择 ────────────────────────────────────────────────────
def test_rare_words_prefers_rare_long_terms(rec):
    text = ("Here we show that CsLOB1 and the effector PthA4 activate CsERF027 "
            "during citrus canker; CsERF027 then regulates CsRAP2.3.")
    words = rec.rare_words(text, n=5)
    assert words, "expected some query words"
    # 停用词与短词不得入选
    for w in words:
        assert len(w) > 4
        assert w not in rec.STOPWORDS
    # 只出现一次的长词应排在前面
    assert words[0] in {"cslob1", "ptha4", "cserf027", "csrap2.3", "regulates", "citrus", "canker"}


def test_rare_words_empty_text(rec):
    assert rec.rare_words("", n=8) == []
    assert rec.rare_words("the of and in", n=8) == []


# ── 纯函数：证据重叠 ──────────────────────────────────────────────────────
def test_ngram_overlap_detects_shared_run(rec):
    source = ("Viroids are naked RNAs that do not code for any known protein "
              "and yet are able to infect plants causing severe diseases.")
    candidate = ("ABSTRACT: Viroids are naked RNAs that do not code for any "
                 "known protein and yet are able to infect plants.")
    gram = rec.ngram_overlap(source, candidate)
    assert gram
    assert len(gram.split()) == 8


def test_ngram_overlap_rejects_unrelated(rec):
    assert rec.ngram_overlap("alpha beta gamma delta epsilon zeta eta theta",
                             "nothing in common here at all with that text") == ""


def test_title_overlap_ratio(rec):
    body = "citrus canker resistance is conferred by citph4 activating a protease"
    assert rec.title_overlap("CitPH4 Confers Resistance to Citrus Canker", body) >= 0.8
    assert rec.title_overlap("Something Entirely Unrelated Here", body) < 0.4


def test_distinctive_phrase_skips_leading_stopwords(rec):
    phrase = rec.distinctive_phrase("Here we present a genome assembly of citrus")
    assert phrase.split()[0].lower() not in rec.STOPWORDS


# ── sidecar 合并后的读取优先级 ────────────────────────────────────────────
def test_recovered_title_wins_over_internal_key(tmp_path, monkeypatch):
    key = "paper_3fff7da9_000047_D_2015_Molecular_biolog"
    sidecar = {
        "version": 1,
        "index": {
            key: {
                "title": "A Phylogenetic Analysis of 34 Chloroplast Genomes",
                "year": "2015",
                "journal": "Molecular biology and evolution",
                "doi": "10.1093/molbev/msv082",
                "title_source": "europepmc",
                "title_confidence": 0.95,
                "title_evidence": "abstract_ngram:viroids are naked rnas",
            }
        },
    }
    (tmp_path / "metadata_index.json").write_text(
        json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    corpus_source.clear_meta_cache()
    monkeypatch.setattr(corpus_source, "DATA_DIR", str(tmp_path))
    try:
        meta = corpus_source.load_metadata()
        assert key in meta
        entry = corpus_source.meta_for(key)
        assert entry["title"].startswith("A Phylogenetic Analysis")
        assert entry["doi"] == "10.1093/molbev/msv082"
        assert entry["title_source"] == "europepmc"
        assert corpus_source._meta_url(entry) == "https://doi.org/10.1093/molbev/msv082"
    finally:
        corpus_source.clear_meta_cache()


def test_missing_sidecar_degrades_to_empty(tmp_path, monkeypatch):
    corpus_source.clear_meta_cache()
    try:
        monkeypatch.setattr(corpus_source, "DATA_DIR", str(tmp_path))
        assert corpus_source.load_metadata() == {}
        assert corpus_source.meta_for("paper_unknown_000000_A_2020_X") == {}
    finally:
        corpus_source.clear_meta_cache()


def test_low_confidence_recovery_is_still_preferred_when_present(tmp_path, monkeypatch):
    """回填只写"有证据"的记录；写了就说明至少经过 8-gram 或重叠校验。"""
    key = "paper_d930661f_000061_D_2025_Plant_biotechnol"
    sidecar = {"index": {key: {"title": "Recovered Title", "title_source": "crossref",
                               "title_confidence": 0.6}}}
    (tmp_path / "metadata_index.json").write_text(
        json.dumps(sidecar), encoding="utf-8")
    corpus_source.clear_meta_cache()
    monkeypatch.setattr(corpus_source, "DATA_DIR", str(tmp_path))
    try:
        assert corpus_source.meta_for(key)["title"] == "Recovered Title"
    finally:
        corpus_source.clear_meta_cache()

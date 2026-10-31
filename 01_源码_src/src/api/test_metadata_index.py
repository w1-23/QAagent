# -*- coding: utf-8 -*-
"""元数据索引（v9.8, R16）测试：sidecar 加载、题名回填、构建规则。

这些测试只读 sidecar 与规则函数，不做网络/嵌入；构建脚本用独立模块导入，
不依赖它所在的实验目录被加进 sys.path。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

from src.core import corpus_source

ROOT = pathlib.Path(__file__).resolve().parents[2]          # 01_源码_src
BUILD = ROOT / "experiments" / "corpus_meta" / "build_metadata_index.py"
INDEX_FILE = pathlib.Path(corpus_source.DATA_DIR) / "metadata_index.json"
KEY_TITLED = "paper_d3855c0c_000001_A_2018_Nature_Genomics_"
KEY_UNTITLED = "paper_65bfd947_000517_F_2014_BMC_microbiology"


def _load_build_module():
    spec = importlib.util.spec_from_file_location("build_metadata_index", BUILD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build():
    return _load_build_module()


# ── 构建规则（纯函数） ───────────────────────────────────────────────────
def test_internal_key_detection(build):
    assert build.is_internal_key("593_paper_1c63bb9b_000593_F_2018_BMC_Plant_Biolog")
    assert build.is_internal_key("paper_45bbcac3_000004_B_2017_Nature_Genetics_")
    assert not build.is_internal_key("Genomics of the origin and evolution of Citrus")
    assert not build.is_internal_key("")


def test_parse_key_meta_year_and_journal(build):
    meta = build.parse_key_meta("paper_45bbcac3_000004_B_2017_Nature_Genetics_")
    assert meta["year"] == "2017"
    assert meta["journal"] == "Nature Genetics"
    assert build.parse_key_meta("doc_88-3_mandarin_hybrid_CRC_3992")["year"] == ""


def test_title_from_section_rejects_generic_sections(build):
    assert build.title_from_section("Abstract") == ""
    assert build.title_from_section("INTRODUCTION") == ""
    assert build.title_from_section("ARTIGOS") == ""
    assert build.title_from_section("摘要") == ""
    assert build.title_from_section(
        "Genomics of the origin and evolution of Citrus"
    ) == "Genomics of the origin and evolution of Citrus"


def test_title_from_section_rejects_editorial_noise(build):
    assert build.title_from_section("Academic Editor: Artur Valente") == ""
    assert build.title_from_section("Corresponding Author: Yasir Iftikhar") == ""
    assert build.title_from_section("someone@example.org") == ""


def test_title_from_text_prefers_markdown_heading(build):
    text = "# Agrobacterium Transformation Method (Chemical Transformation Method)\n\nLi Yawei"
    assert build.title_from_text(text).startswith("Agrobacterium Transformation Method")


def test_title_from_text_rejects_short_first_line(build):
    assert build.title_from_text("Short\nmore text") == ""


# ── sidecar 加载 ────────────────────────────────────────────────────────
def test_load_metadata_reads_index(tmp_path):
    path = tmp_path / "metadata_index.json"
    path.write_text(json.dumps({"version": 1, "index": {
        "paper_x_000001_A_2020_Nature_": {"title": "T", "year": "2020"}}}),
        encoding="utf-8")
    rows = corpus_source.load_metadata(path)
    assert rows["paper_x_000001_A_2020_Nature_"]["title"] == "T"


def test_load_metadata_tolerates_broken_file(tmp_path):
    path = tmp_path / "metadata_index.json"
    path.write_text("{not json", encoding="utf-8")
    assert corpus_source.load_metadata(path) == {}
    assert corpus_source.load_metadata(tmp_path / "missing.json") == {}


def test_load_metadata_caches_by_mtime(tmp_path):
    path = tmp_path / "metadata_index.json"
    path.write_text(json.dumps({"index": {"k": {"title": "v1"}}}), encoding="utf-8")
    first = corpus_source.load_metadata(path)
    second = corpus_source.load_metadata(path)
    assert first == second
    path.write_text(json.dumps({"index": {"k": {"title": "v2-longer"}}}), encoding="utf-8")
    third = corpus_source.load_metadata(path)
    assert third["k"]["title"] == "v2-longer"


# ── 真实 sidecar + 真实语料的回填效果 ────────────────────────────────────
@pytest.mark.skipif(not INDEX_FILE.exists(), reason="本机未生成 metadata_index.json")
def test_real_index_has_wide_title_coverage():
    stats = corpus_source.meta_stats()
    assert stats["documents"] >= 3000
    assert stats["titled"] / max(1, stats["documents"]) > 0.6


@pytest.mark.skipif(not INDEX_FILE.exists(), reason="本机未生成 metadata_index.json")
def test_describe_uses_backfilled_title():
    desc = corpus_source.describe(KEY_TITLED)
    assert desc is not None
    assert desc["title_label"].lower().startswith("genomics of the origin")
    assert desc["year"] == "2018"
    assert desc["journal"] == "Nature Genomics"


@pytest.mark.skipif(not INDEX_FILE.exists(), reason="本机未生成 metadata_index.json")
def test_describe_keeps_honest_fallback_when_title_missing():
    desc = corpus_source.describe(KEY_UNTITLED)
    assert desc is not None
    assert desc["title_label"].startswith("未具题名文献（")
    assert "paper_" not in desc["title_label"]
    assert desc["year"] == "2014"


@pytest.mark.skipif(not INDEX_FILE.exists(), reason="本机未生成 metadata_index.json")
def test_sidecar_carries_variety_metadata_for_ucr():
    desc = corpus_source.describe("doc_88-3_mandarin_hybrid_CRC_3992")
    assert desc is not None
    assert desc["registry"] == "CRC 3992"
    assert desc["species"].startswith("Citrus reticulata")

"""内部记录键可读化（v9.7）契约测试。"""
from __future__ import annotations

from src.core.record_label import (
    display_label,
    is_internal_record_key,
    parse_record_key,
    scrub_internal_keys,
)

# 生产语料里的真实样本（`cqa_literature/chunks.jsonl` 的 title 字段）
REAL_KEY = "593_paper_1c63bb9b_000593_F_2018_BMC_Plant_Biolog"
REAL_ROW = {
    "paper_id": "paper_1c63bb9b_000593_F_2018_BMC_Plant_Biolog",
    "chunk_index": 0,
    "title": REAL_KEY,
    "section_name": "Abstract",
    "year": "",
    "doi": "",
    "corpus_family": "literature",
}


def test_detects_internal_key_with_and_without_prefix():
    assert is_internal_record_key(REAL_KEY)
    assert is_internal_record_key("paper_1c63bb9b_000593_F_2018_BMC_Plant_Biolog")
    assert is_internal_record_key(REAL_KEY + "_")
    assert not is_internal_record_key("MITE insertion and polyembryony in citrus")
    assert not is_internal_record_key("")

def test_parses_year_journal_and_doc_number():
    info = parse_record_key(REAL_KEY)
    assert info is not None
    assert info["year"] == "2018"
    assert info["doc_no"] == "593"
    assert info["journal"] == "BMC_Plant_Biolog"

def test_parse_returns_none_for_human_title():
    assert parse_record_key("Citrus genomics 2022") is None

def test_display_label_never_exposes_internal_key():
    label = display_label(REAL_ROW, with_section=True)
    assert "paper_" not in label
    assert "未具题名文献" in label          # 明确标注题名缺失，不伪造题名
    assert "2018" in label
    assert "Abstract" in label             # 真实片段用 section 区分，不合并掉

def test_display_label_keeps_real_title():
    row = {"title": "MITE insertion drives polyembryony", "section_name": "Results"}
    label = display_label(row, with_section=True)
    assert label.startswith("MITE insertion drives polyembryony")
    assert "Results" in label

def test_display_label_ignores_generic_section_as_title():
    row = {"title": "", "section_name": "Abstract",
           "paper_id": "paper_deadbeef_000001_A_2021_Journal_of_Experimental_Botany"}
    label = display_label(row)
    assert "未具题名文献" in label
    assert "Abstract" not in label

def test_same_paper_different_chunks_stay_distinguishable():
    first = display_label({**REAL_ROW, "section_name": "Abstract", "chunk_index": 0},
                          with_section=True)
    second = display_label({**REAL_ROW, "section_name": "Abstract", "chunk_index": 1},
                           with_section=True)
    assert first != second
    assert "#0" in first and "#1" in second

def test_scrub_replaces_leaked_key_with_readable_phrase():
    text = ("如需继续深挖，建议以记录 " + REAL_KEY + "（定位+MITE）为核心，"
            "再配合 paper_45bbcac3_000004_B_2017_Nature_Genetics_ 看跨属比较。")
    out = scrub_internal_keys(text)
    assert "paper_" not in out
    assert "一篇 2018 年 BMC Plant Biolog 文献" in out
    assert "一篇 2017 年 Nature Genetics 文献" in out
    assert "跨属比较" in out               # 正文其余内容逐字保留
    assert "（定位+MITE）" in out

def test_scrub_is_idempotent():
    text = "见 " + REAL_KEY + " 一文"
    once = scrub_internal_keys(text)
    assert scrub_internal_keys(once) == once

def test_scrub_leaves_clean_text_untouched():
    text = "MITE 插入与多胚性相关（DOI: 10.1186/s12870-018-1234-5）。"
    assert scrub_internal_keys(text) == text

def test_scrub_tolerates_empty_input():
    assert scrub_internal_keys("") == ""

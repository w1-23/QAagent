"""Product identity, positive intent, format routing, and concise Word delivery."""
import pytest
from langchain_core.messages import ToolMessage
from src.graph.export_contract import deliverable_contract, deliverable_match_map, forced_export_plan
from src.graph.document_delivery import compact_word_delivery


@pytest.mark.parametrize("query,formats", [
    ("读取上传的 PDF，生成 DOCX 报告", [["docx"]]),
    ("生成 HTML 报告，不要导出 XLSX", [["html"]]),
    ("不要再导出 Word 文档", []),
    ("生成 HTML 报告和 DOCX 报告", [["html"], ["docx"]]),
    ("生成 HTML 或 DOCX 报告", [["html", "docx"]]),
    ("生成 RNA-seq 样本核查表 CSV", [["csv"]]),
    ("画一张流程图 SVG", [["svg"]]),
    ("如何绘制流程图？", []),
    ("不要画图", []),
])
def test_positive_products(query, formats):
    contract = deliverable_contract(query)
    assert [r["formats"] for r in contract] == formats
    assert len({r["product_id"] for r in contract}) == len(contract)
    if "样本核查表" in query:
        assert contract[0]["kinds"] == ["table_file"]


def artifact(identity, filename="report.docx", tool="word_document"):
    return {"valid": True, "request_matches": True, "artifact_id": identity,
            "filename": filename, "producer_tool": tool,
            "link": "/api/v2/session/session-contract/artifacts/" + identity}


def test_registry_identity_and_original_indices():
    word = {"kinds": ["report_docx"], "formats": ["docx"]}
    first = artifact("a" * 32)
    second = artifact("b" * 32)  # same filename, distinct immutable bytes/identity
    validations = [{"valid": False}, first, dict(first), second]
    mapping = deliverable_match_map(validations, [word, dict(word)])
    assert set(mapping.values()) == {1, 3}
    assert len(deliverable_match_map([first, dict(first)], [word, word])) == 1
    flexible = {"kinds": [], "formats": ["docx", "xlsx"]}
    xlsx = artifact("c" * 32, "data.xlsx", "export_table")
    assert deliverable_match_map([first, xlsx], [flexible, word]) == {1: 0, 0: 1}


def test_csv_is_not_a_corpus_gap_and_requires_real_rows():
    req = deliverable_contract("生成 CSV 表格")[0]
    assert forced_export_plan(req, "", "只有叙述", [], {})[2] == "missing_tabular_data"
    name, args, reason = forced_export_plan(req, "", "|样本|值|\n|---|---|\n|合成项|1|", [], {})
    assert (name, reason) == ("export_table", "")
    assert args["rows_json"] == [["合成项", "1"]]
    assert args["output_format"] == "csv"


@pytest.mark.parametrize("fmt", ["ris", "bibtex"])
def test_bibliography_has_real_provenance(fmt):
    req = {"kinds": ["bibliography"], "formats": [fmt]}
    assert forced_export_plan(req, "", "无条目", [], {})[2] == "missing_bibliography_evidence"
    message = ToolMessage(content="", tool_call_id="search", name="search_literature",
        artifact={"candidates": [{"title": "Synthetic engineering record", "doi": "10.0000/example"}]})
    name, args, reason = forced_export_plan(req, "", "", [message], {})
    assert name == "export_literature_evidence" and not reason
    assert args["output_format"] == fmt
    assert args["evidence_rows_json"][0]["doi"] == "10.0000/example"


def test_compact_word_bubble_keeps_all_files_and_only_after_completion():
    requirements = deliverable_contract("生成 DOCX 报告和 XLSX 表格")
    word = artifact("a" * 32)
    table = artifact("b" * 32, "data.xlsx", "export_table")
    answer = "长篇正文。" * 1000
    assert compact_word_delivery(answer, [word], requirements) == answer
    compact = compact_word_delivery(answer, [word, table], requirements)
    assert len(compact) < 500
    assert word["link"] in compact and table["link"] in compact
    assert "长篇正文" not in compact

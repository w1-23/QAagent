"""Scene names must not force files; concrete output contracts stay strict."""
import pytest
from src.graph.export_contract import deliverable_contract, artifact_matches_requirement, forced_export_plan


@pytest.mark.parametrize("query", [
    "请写一份柑橘病害文献综述，给出来源", "请提供研究报告", "帮我写实验设计方案",
    "整理成表格在聊天里回答", "提供文献证据与参考文献表", "给出核验结果和问题清单",
    "读取上传的Excel，提供审查报告", "阅读PDF论文，写一份方法解读报告",
    "Write a literature review with a bibliography", "Prepare a research report",
])
def test_scene_answers_do_not_require_files(query):
    assert deliverable_contract(query) == []


@pytest.mark.parametrize("query", [
    "导出文献证据为XLSX", "请生成Word实验设计文件", "生成可下载的HTML报告",
    "导出报告", "制作CSV表格文件", "请绘制散点图", "Create a downloadable report",
])
def test_explicit_outputs_still_require_files(query):
    assert deliverable_contract(query)


def test_input_format_is_not_output_authorization():
    assert deliverable_contract("请读取上传的XLSX文件并写一份研究报告") == []


def test_concrete_renderer_retains_legacy_kind():
    validation = {"valid": True, "filename": "results.xlsx", "producer_tool": "export_table"}
    assert artifact_matches_requirement(validation, {"kinds": ["table_file"], "formats": ["xlsx"]})
    assert not artifact_matches_requirement(validation, {"kinds": ["bibliography"], "formats": ["xlsx"]})
    validation["producer_tool"] = "deliver_research_artifact"
    assert not artifact_matches_requirement(validation, {"kinds": ["bibliography"], "formats": ["xlsx"]})


def test_forced_plan_uses_canonical_delivery():
    name, args, reason = forced_export_plan(
        {"kinds": ["table_file"], "formats": ["csv"]}, "导出CSV表格", "| x | y |\n|---|---|\n|1|2|",
        [], {"deliver_research_artifact": object()})
    assert name == "deliver_research_artifact" and not reason
    assert args["kind"] == "table" and args["rows"] == [["1", "2"]]

"""Product identity, positive intent, format routing, and concise Word delivery."""
import pytest
from langchain_core.messages import ToolMessage
from src.graph.export_contract import (
    deliverable_contract,
    deliverable_export_requested,
    deliverable_match_map,
    forced_export_plan,
)
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


# ---------------------------------------------------------------------------
# Explicit carriers only. These are the real end-to-end prompts of the audited
# 40-task run (v8_work/02_P4/formal/*/run.json, prompt field, attachment tail
# removed). They are reproduced verbatim here so the contract cannot regress
# back to inventing a file for every 清单 in the prose.
# ---------------------------------------------------------------------------

FW08_PROMPT = (
    "我准备制定黄龙病现场筛查采样流程。请从原始研究中查找样本部位、保存条件与检测步骤，"
    "提供有出处的取舍清单及现场待验证事项。请把这份采样流程整理成一份可直接下载的 HTML 报告，"
    "每一步后面注明出处，并在回复里给出下载位置。")
FW11_PROMPT = (
    "这是我准备用于论文的柑橘补充工作簿（含 Table S1 代谢物强度与 Table S2 测序统计）。"
    "请先核查数据质量并标出需要复核的位置，再依据文件内已有的表题说明判断哪些信息文件本身没有提供、"
    "不能替作者补写，最后给出投稿前的复核清单。请把这份工作簿标注后另存为可直接下载的 XLSX，"
    "需要复核的位置指到工作表、行、列。另外写一份 HTML 报告，把复核清单和不能替作者补写的内容写进去，"
    "并在回复里给出下载位置。")
FW15_PROMPT = (
    "这是我准备投稿的柑橘甜橙果实转录组研究：稿件正文（Methods）和它的补充表 Table S1（RNA-seq 测序统计）"
    "都在附件里。请核对这张补充表与 Methods 中关于取样设计、生物学重复、测序统计口径的描述是否一致，"
    "检查表内可用于投稿前自检的一致性问题，输出一份带位置（工作表/行/列）的核验清单和结论。"
    "如果某处看起来可疑但其实与方法描述相符，请说明为什么不构成问题；如果确实没有发现数据记录错误，"
    "也要明确说明，并给出你据以判断的可核验证据。请把问题位置标在这份补充工作簿上并另存为可直接下载的标注版 XLSX，"
    "另外写一份 HTML 报告，把核对结论和判断依据写进去，并在回复里给出下载位置。")
FW18_PROMPT = (
    "实验室要决定下一轮柑橘黄龙病（HLB）检测采用哪种公开方法。附件是两篇公开研究：2018 年的 LAMP 检测法"
    "与 2023 年的田间实时 LAMP（rt-LAMP）检测法。请比较两者的样本制备、检测流程、靶标、报告指标与适用边界，"
    "形成内部方法选择前的核查清单。不要直接把不同样本条件下的检出率/灵敏度排名，若两者不可直接比较请说明原因；"
    "所有结论必须能追溯到具体文献中的句子或表格。请把五维对照整理成一份可直接下载的 HTML 报告。"
    "另外把可以对外、不含敏感信息的部分单独整理成一份 HTML 简报，并在回复里给出下载位置。")
FW20_PROMPT = (
    "我想根据这份柑橘补充工作簿决定后续还需要补测什么。请先审查 Table S1（代谢物强度）与 Table S2（测序统计）"
    "的数据质量与可用性，再核实相关文献，区分“记录/设计问题”与“可能的科学现象”，最后整理需补测的清单并给出证据依据。"
    "如果某处差异来自两份表的设计不同（例如每期重复数不同），请说明它属于设计差异而非记录错误。"
    "请把审查结论标注在这份工作簿上并另存为可直接下载的 XLSX，问题指到工作表、行、列。"
    "另外写一份 HTML 报告，把补测建议和文献依据写进去，并在回复里给出下载位置。")
R01_PROMPT = (
    "这份柠檬果皮发育的补充工作簿要投稿，请先做一遍数据审查：检查缺失、重复、越界与样本键一致性，"
    "输出标注副本与问题清单。 不要修改原始文件；如果本表确实没有问题，请明确说明“未发现该类问题”，不要编造。")
LN04_PROMPT = (
    "附件是本地语料库的 15 条文献记录。我的综述主题是“柑橘果实糖酸品质与成熟度的关系”。"
    "请按主题相关性筛出应优先阅读的文献，形成可直接交接给综述作者的清单（含 DOI、相关性理由、排除理由），"
    "不要把仅涉及基因组资源或抗病/胁迫的论文混入。")
LN07_PROMPT = (
    "附件是（BibTeX 内容，按纯文本 .txt 保存）我准备交接给合作者的 BibTeX 文件。"
    "请核对条目完整性（作者/题名/期刊/年份/DOI）、是否有重复条目，输出修正后的条目清单与修改说明。")


@pytest.mark.parametrize("prompt,expected", [
    (FW08_PROMPT, [("report_html", "html")]),
    (FW11_PROMPT, [("annotation_table", "xlsx"), ("report_html", "html")]),
    (FW15_PROMPT, [("annotation_table", "xlsx"), ("report_html", "html")]),
    (FW18_PROMPT, [("report_html", "html"), ("report_html", "html")]),
    (FW20_PROMPT, [("annotation_table", "xlsx"), ("report_html", "html")]),
    (R01_PROMPT, [("annotation_table", "xlsx")]),
    (LN04_PROMPT, [("generic_output", "")]),
    (LN07_PROMPT, [("generic_output", "")]),
])
def test_real_prompts_request_only_the_files_the_user_named(prompt, expected):
    contract = deliverable_contract(prompt)
    assert [(r["kinds"][0], (r["formats"] or [""])[0]) for r in contract] == expected


def test_annotated_workbook_is_not_an_ordinary_table():
    """「标注后另存为 XLSX」 is an annotation, even though 标注 precedes the verb."""
    for prompt in (FW11_PROMPT, FW20_PROMPT):
        first = deliverable_contract(prompt)[0]
        assert first["kinds"] == ["annotation_table"]
    # A plain table request must not be promoted to an annotation.
    assert deliverable_contract("生成 CSV 表格")[0]["kinds"] == ["table_file"]


def test_constraint_sentences_never_become_deliverables():
    contract = deliverable_contract(FW18_PROMPT)
    assert all("追溯" not in r["label"] for r in contract)
    assert all(r["kinds"] != ["generic_output"] for r in contract)
    # An obligation attached to a real request must not cancel the request.
    contract = deliverable_contract("请导出 XLSX，问题必须指到工作表、行、列")
    assert [{key: item[key] for key in ("kinds", "formats", "product_id", "label")}
            for item in contract] == [
        {"kinds": ["table_file"], "formats": ["xlsx"],
         "product_id": "p0_0", "label": "XLSX"}]
    assert contract[0]["quantity"] == 1
    assert contract[0]["body_source"] == "answer_or_tool_output"
    assert contract[0]["needs_clarification"] is False


def test_bare_nouns_survive_only_when_no_explicit_carrier_exists():
    """R01's 问题清单 is answer content; L-N04's 清单 is the whole deliverable."""
    assert len(deliverable_contract(R01_PROMPT)) == 1
    assert deliverable_contract(LN04_PROMPT)[0]["kinds"] == ["generic_output"]
    assert deliverable_contract(LN07_PROMPT)[0]["kinds"] == ["generic_output"]


def test_restatement_merges_but_two_named_products_do_not():
    assert len(deliverable_contract("生成实验设计，并整理成实验设计报告")) == 1
    assert len(deliverable_contract("请生成对照表报告，并提供一份脱敏对外简报")) == 2
    assert len(deliverable_contract(FW18_PROMPT)) == 2


@pytest.mark.parametrize("prompt,scopes", [
    (FW18_PROMPT, ["shared", "distinct"]),
    (FW11_PROMPT, [None, "shared"]),
    ("请生成对照表报告，并提供一份脱敏对外简报", ["shared", "distinct"]),
])
def test_report_body_scope_marks_products_that_need_their_own_text(prompt, scopes):
    assert [r.get("body_scope") for r in deliverable_contract(prompt)] == scopes


# ---------------------------------------------------------------------------
# Generic product matrix. These prompts are deliberately unrelated to the
# audited task set: they exercise quantity, conjunction, audience, restatement,
# negation, discussion, and same/different body ownership.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("prompt,count", [
    ("分别生成一份 HTML 病害报告和一份 HTML 营养报告。", 2),
    ("生成 HTML 病害报告和 HTML 营养报告。", 2),
    ("分别生成一份 HTML 病害报告、一份 HTML 营养报告。", 2),
    ("请导出三份 HTML 报告，分别面向研发、销售和客户。", 3),
    ("三份 HTML 报告，分别面向研发、销售和客户。", 3),
    ("生成三份面向研发、销售和客户的 HTML 报告。", 3),
    ("Create an HTML report for finance and a separate HTML report for operations.", 2),
    ("生成两份 HTML 报告，内容相同。", 2),
    ("生成两份 HTML 报告。", 2),
])
def test_generic_product_quantity_and_identity(prompt, count):
    contract = deliverable_contract(prompt)
    assert len(contract) == count
    assert len({r["product_id"] for r in contract}) == count
    assert all(r["kinds"] == ["report_html"] for r in contract)
    assert all(r["formats"] == ["html"] for r in contract)
    assert all(r["quantity"] == 1 for r in contract)


def test_different_products_never_share_one_body():
    contract = deliverable_contract("生成 HTML 病害报告和 HTML 营养报告。")
    assert [r["body_scope"] for r in contract] == ["shared", "distinct"]
    assert contract[0]["body_source"] == "answer_or_tool_output"
    assert contract[1]["body_source"] == "product_specific"

    english = deliverable_contract(
        "Create an HTML report for finance and a separate HTML report for operations.")
    assert [r["body_scope"] for r in english] == ["shared", "distinct"]

    audiences = deliverable_contract("三份 HTML 报告，分别面向研发、销售和客户。")
    assert [r["body_scope"] for r in audiences] == ["shared", "distinct", "distinct"]
    assert all(r["needs_clarification"] is False for r in audiences)


def test_explicit_same_body_allows_shared_content():
    contract = deliverable_contract("生成两份 HTML 报告，内容相同。")
    assert [r["body_scope"] for r in contract] == ["shared", "shared"]
    assert all(r["content_same"] is True for r in contract)
    assert all(r["needs_clarification"] is False for r in contract)


def test_ambiguous_quantity_is_kept_and_flagged_not_dropped():
    contract = deliverable_contract("生成两份 HTML 报告。")
    assert len(contract) == 2
    assert all(r["needs_clarification"] is True for r in contract)


@pytest.mark.parametrize("prompt,expected", [
    ("这三张表分别是 A、B、C，请生成一份 HTML 报告。", 1),
    ("五维对照整理成一份可直接下载的 HTML 报告。", 1),
    ("请导出三份 HTML 报告，分别面向研发、销售和客户。", 3),
])
def test_input_counts_and_adjectives_are_not_product_quantities(prompt, expected):
    assert len(deliverable_contract(prompt)) == expected


def test_negation_and_discussion_still_do_not_create_products():
    contract = deliverable_contract("不要生成 HTML 报告，生成 XLSX 表格")
    assert [(r["kinds"], r["formats"]) for r in contract] == [
        (["table_file"], ["xlsx"])]
    assert deliverable_contract("如何生成 HTML 报告？") == []
    assert deliverable_contract("不要画图，只给文字结论") == []


def test_specific_no_format_carrier_survives_an_explicit_format():
    contract = deliverable_contract("生成 HTML 报告和参考文献表")
    assert [r["kinds"] for r in contract] == [["report_html"], ["bibliography"]]
    assert contract[1]["formats"] == []


def test_same_format_different_audience_is_not_a_restatement():
    contract = deliverable_contract("生成一份中文 HTML 报告和一份英文 HTML 报告")
    assert len(contract) == 2
    assert [r["body_scope"] for r in contract] == ["shared", "distinct"]


def test_quoted_body_and_labels_are_not_extra_deliverables():
    """A body quoted after 正文： mentions .docx but is not a second request."""
    prompt = (
        "请只使用 Word 文档工具，把下面这段现成文字制作成一份 Word 文档并交付下载，"
        "不要检索，不要扩写，不要生成其它格式。标题：《冒烟测试文档》。"
        "第一节标题：测试范围。正文原样写入：本文件由冒烟测试自动生成，"
        "用于验证 Word 导出链路。完成后告诉我实际文件名。")
    contract = deliverable_contract(prompt)
    assert len(contract) == 1
    assert contract[0]["formats"] == ["docx"]
    assert contract[0]["body_scope"] == "shared"


def test_export_verb_inside_supplied_body_is_not_a_command():
    assert deliverable_export_requested("把这段文字存档。正文：请导出 Word 报告。") is False
    assert deliverable_export_requested("请导出 Word 报告。") is True


def test_tool_choice_and_export_noun_compounds_do_not_add_products():
    contract = deliverable_contract(
        "请生成一份 Word 文档，使用 Word 文档工具完成，并在正文中说明导出链路的限制。")
    assert len(contract) == 1
    assert contract[0]["formats"] == ["docx"]


def test_english_body_payload_does_not_invent_a_second_product():
    contract = deliverable_contract(
        'Create a Word document. Body: "The audit found three export issues '
        'in the report pipeline."')
    assert len(contract) == 1
    assert contract[0]["formats"] == ["docx"]

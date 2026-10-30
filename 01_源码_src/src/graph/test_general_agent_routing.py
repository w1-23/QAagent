from src.graph.general_agent_graph import (
    _explicit_data_audit,
    _explicit_export_request,
    _explicit_local_tool_request,
    _explicit_note_save_request,
    _local_corpus_matches,
    _scoped_toolset,
    _source_scope,
)
from src.retrieval.multi_retriever import local_corpus_family


def test_local_corpus_match_is_limited_to_citrus_topics():
    assert _local_corpus_matches("Find citrus papers on Huanglongbing resistance")
    assert _local_corpus_matches("检索柑橘黄龙病抗性相关文献")
    assert not _local_corpus_matches("检索 PaperConan 的数据审查方法")
    assert not _local_corpus_matches("Why is this orange-colored?")


def test_corpus_family_uses_current_normalized_categories_and_legacy_metadata():
    assert local_corpus_family({"category": "ucr_varieties", "source_type": "ucr"}) == "ucr_varieties"
    assert local_corpus_family({"category": "deng_xiuxin", "source_type": "deng"}) == "deng_variety_book"
    assert local_corpus_family({"category": "cgi_methods", "source_type": "methods"}) == "experiment_methods"
    assert local_corpus_family({"category": "english_literature", "source_type": "literature"}) == "literature"
    assert local_corpus_family({"category": "translated", "source_type": "translated"}) == "literature"
    assert local_corpus_family({"_batch": "citrus_varieties1"}) == "ucr_varieties"


def test_source_scope_still_respects_explicit_local_and_web_constraints():
    assert _source_scope("只查本地柑橘文献") == "local"
    assert _source_scope("只查网页上的柑橘文献") == "web"
    assert _source_scope("只从本地柑橘文献语料检索关于黄龙病抗性的英文文献") == "local"
    assert _source_scope("只从网页搜索黄龙病抗性相关论文") == "web"


def test_english_explicit_local_tool_instruction_is_enforced():
    query = "Use the local_hybrid_search tool to look up UCR accession CRC 4079"
    assert _explicit_local_tool_request(query)
    assert _local_corpus_matches(query)
    assert "local_hybrid_search" in _tool_names(query)
    assert not _explicit_local_tool_request("Do not use local_hybrid_search for this question")


def test_data_audit_router_requires_positive_data_review_intent():
    assert _explicit_data_audit("请帮我审查这份实验数据")
    assert _explicit_data_audit("检查表格有没有异常值")
    assert not _explicit_data_audit("只告诉我附件中的 DOI 和标题；不要审查数值或生成报告")
    assert not _explicit_data_audit("附件只是参考，不需要分析里面的表格")
    assert _explicit_data_audit("不要审查附件，但请检查这份数据是否有异常")


def _tool_names(query: str, scope: str = "auto", **kwargs) -> set[str]:
    tools, _ = _scoped_toolset(scope, query=query, **kwargs)
    return {tool.name for tool in tools}


def test_tool_visibility_follows_the_environment_not_the_wording():
    """Phase 2A removed the keyword narrowing layer.

    可见性现在是环境与用户显式约束的函数：没有附件只是不出现表格工具，而不是
    “除非命中数据审查关键词否则不给任何研究工具”。旧契约断言“任何一次收窄都应
    少于 17 个工具”，Phase 2A 之后不再成立，故改写为环境断言。
    """
    # 纯社交寒暄仍然不检索（这是用户可见的策略，不是关键词收窄），
    # 但其余能力照常下发，模型不再只剩"凭空回答"一条路。
    social = _tool_names("你好")
    assert "deepseek_search" not in social
    assert "draw_svg" in social
    assert "deepseek_search" in _tool_names("柑橘病害有哪些")

    audit = _tool_names("审查该实验表有无异常", has_table=True)
    assert {"inspect_uploaded_table", "audit_uploaded_data"} <= audit

    without_table = _tool_names("审查该实验表有无异常")
    assert not (without_table & {"inspect_uploaded_table", "audit_uploaded_data"})

    literature = _tool_names("检索柑橘黄龙病抗性相关文献")
    assert {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper",
            "local_hybrid_search"}.issubset(literature)

    web_only = _tool_names("只查网页上的黄龙病抗性研究", scope="web")
    assert "local_hybrid_search" not in web_only
    assert "deepseek_search" in web_only

    local_only = _tool_names("只查本地库的黄龙病资料", scope="local")
    assert "local_hybrid_search" in local_only
    assert not (local_only & {"deepseek_search", "deepseek_fetch",
                             "deepseek_fetch_paper"})

    visual = _tool_names("画一个柑橘病害分布图", visualization_enabled=True)
    assert "draw_svg" in visual


def test_note_writes_are_the_only_capability_behind_an_explicit_request():
    """Exports are advertised unconditionally; a note write is an account mutation.

    Until Phase 2B enforces authorization in the executor, save_research_note is
    the single tool still held back when the user did not ask for it.
    """
    assert _explicit_note_save_request("把这段结论保存到课题记录里")
    assert "save_research_note" in _tool_names("把这段结论保存到课题记录里")
    assert "save_research_note" not in _tool_names("帮我看看这篇文献的方法")

    # Export tools are visible without a keyword gate; the model decides.
    assert "export_research_notes" in _tool_names("导出课题记录")
    assert "export_literature_evidence" in _tool_names("把文献证据导出为 RIS")
    assert _explicit_export_request("导出为 BibTeX")

import pytest

from src.core import tracing
from src.graph import tool_availability
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


@pytest.fixture(autouse=True)
def healthy_runtime(monkeypatch):
    monkeypatch.setattr(tracing, "get_session_id", lambda: "test-routing-session")
    monkeypatch.setattr(tracing, "web_search_enabled", lambda: True)
    monkeypatch.setattr(tool_availability, "provider_denials", lambda config: frozenset())
    monkeypatch.setenv("CITRUS_RESEARCH_TOOLS_ENABLED", "true")


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
    少于全部工具”，Phase 2A 之后不再成立，故改写为环境断言。
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


def test_note_writes_are_visible_but_need_explicit_executor_authorization():
    """Visibility and authorization are separate layers.

    The note writer is advertised whenever the environment can run it. The
    request plan records consent only when the user actually asked to save, and
    the executor rejects the write otherwise.
    """
    from src.graph.request_plan import build_request_plan

    assert _explicit_note_save_request("把这段结论保存到课题记录里")
    assert "save_research_note" in _tool_names("把这段结论保存到课题记录里")
    assert "save_research_note" in _tool_names("帮我看看这篇文献的方法")
    assert build_request_plan("把这段结论保存到课题记录里").executor_authorizations == (
        "save_research_note",)
    assert build_request_plan("帮我看看这篇文献的方法").executor_authorizations == ()

    # Export tools are visible without a keyword gate; the model decides.
    assert "export_research_notes" in _tool_names("导出课题记录")
    assert "export_literature_evidence" in _tool_names("把文献证据导出为 RIS")
    assert _explicit_export_request("导出为 BibTeX")


# Frozen prompts read from the real 240-task routing set. These test only the
# pure parser and tool binding; no benchmark runner or service is invoked.
_REAL_LOCAL_PROMPTS = [
    ("TD044", "请在库内检索影响柑橘果皮类胡萝卜素积累的研究，回答时注明文献标题。"),
    ("TD045", "本地资料里有没有柑橘砧木盐胁迫与叶片Na+积累的实验？请定位到文献。"),
    ("TD046", "只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。"),
    ("TD047", "为我找出两篇库内关于柑橘采后酸度变化的论文，并说明各自处理条件。"),
    ("TD052", "请你作为柑橘实验室科研助手，请在库内检索影响柑橘果皮类胡萝卜素积累的研究，回答时注明文献标题。"),
    ("TD053", "请你作为柑橘实验室科研助手，本地资料里有没有柑橘砧木盐胁迫与叶片Na+积累的实验？请定位到文献。"),
    ("TD054", "请你作为柑橘实验室科研助手，只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。"),
    ("TD061", "我正在整理实验记录，本地资料里有没有柑橘砧木盐胁迫与叶片Na+积累的实验？请定位到文献。"),
    ("TD062", "我正在整理实验记录，只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。"),
    ("TD063", "我正在整理实验记录，为我找出两篇库内关于柑橘采后酸度变化的论文，并说明各自处理条件。"),
    ("TD070", "我只需要可以核查的结果，只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。"),
    ("TD071", "我只需要可以核查的结果，为我找出两篇库内关于柑橘采后酸度变化的论文，并说明各自处理条件。"),
    ("TD072", "我只需要可以核查的结果，不需要即时新闻；从本地柑橘研究语料中找出RT-qPCR内参基因筛选的实验证据。"),
    ("TD078", "我希望结果便于写论文，只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。"),
]


@pytest.mark.parametrize("task_id,query", _REAL_LOCAL_PROMPTS, ids=[x[0] for x in _REAL_LOCAL_PROMPTS])
def test_real_local_requests_have_one_consistent_scope_and_required_tool(task_id, query):
    from src.graph.retrieval_router import (
        LOCAL_GROUP, decide_retrieval, parse_request_constraints, required_route_groups,
    )
    scope = _source_scope(query)
    assert scope == "local", task_id
    constraints = parse_request_constraints(query)
    assert constraints.requested_mode == "local"
    assert constraints.source_policy == "required"
    decision = decide_retrieval(query, scope, local_corpus_match=False)
    assert required_route_groups(decision) == (LOCAL_GROUP,)
    tools = _tool_names(query, scope=scope)
    assert "local_hybrid_search" in tools
    assert not tools.intersection({"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper", "search_literature"})


@pytest.mark.parametrize("query,expected", [
    ("不要联网，只用本地资料核对柑橘品种。", "local"),
    ("不查本地，请联网检索黄龙病论文。", "web"),
    ("同时检索本地库和网页，然后对比。", "fusion"),
    ("请解释仅本地检索是什么意思。", "auto"),
    ("Use only the local corpus for this question.", "local"),
])
def test_source_scope_is_shared_by_binding_and_constraints(query, expected):
    from src.graph.retrieval_router import parse_request_constraints
    assert _source_scope(query) == expected
    parsed = parse_request_constraints(query)
    assert parsed.requested_mode == expected
    assert parsed.source_policy != "deny"


def test_banning_all_search_still_wins_over_a_local_scope():
    from src.graph.retrieval_router import decide_retrieval, parse_request_constraints
    query = "仅用本地资料，不要检索，直接整理上文。"
    assert parse_request_constraints(query).source_policy == "deny"
    assert decide_retrieval(query, "local").policy == "deny"
    assert "local_hybrid_search" not in _tool_names(query, scope="local")


@pytest.mark.parametrize("query", [
    "请解读附件论文的方法与局限。",
    "请使用附件完成实验对照，并核实关键出版信息。",
    "不要只读附件，请补充证据。",
])
def test_uploaded_paper_itself_does_not_remove_search(query):
    from src.graph.general_agent_graph import _read_uploaded_paper_without_search
    assert not _read_uploaded_paper_without_search(query, "auto", True)
    tools = _tool_names(query, has_paper_attachment=True)
    assert {"local_hybrid_search", "deepseek_search", "deepseek_fetch_paper"} <= tools


@pytest.mark.parametrize("query", ["只阅读附件论文。", "仅根据我上传的附件回答。", "不要检索，只解读附件。"])
def test_explicit_paper_only_restriction_removes_search(query):
    from src.graph.general_agent_graph import _read_uploaded_paper_without_search
    assert _read_uploaded_paper_without_search(query, "auto", True)
    assert not _tool_names(query, has_paper_attachment=True).intersection({
        "local_hybrid_search", "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"})


_LN06_PUBLICATION_REQUEST = (
    "本地语料有 15 篇文献 PDF。请确认这 15 篇是否都能找到正式出版记录"
    "（期刊、年份、卷期页或文章号），输出出版记录表；"
    "如某篇无法找到正式记录，必须明确指出而不是猜测。")


def test_ln06_publication_record_check_keeps_the_tools_needed_to_verify():
    from src.graph.general_agent_graph import (
        _explicit_reference_check, _read_uploaded_paper_without_search,
    )
    assert _explicit_reference_check(_LN06_PUBLICATION_REQUEST)
    assert not _read_uploaded_paper_without_search(_LN06_PUBLICATION_REQUEST, "auto", True)
    tools = _tool_names(_LN06_PUBLICATION_REQUEST, has_paper_attachment=True)
    assert {"verify_uploaded_references", "search_literature", "deepseek_fetch_paper"} <= tools


@pytest.mark.parametrize("query,expected", [
    ("这里有参考文献，请核对这些记录是否准确。", True),
    ("这里有参考文献。请核对这些记录是否准确。", True),
    ("这里有参考文献，不要核验。", False),
    ("这里有参考文献。不要核验这些记录。", False),
    ("这里有参考文献，并检查表格是否有缺失值。", False),
    ("这里有参考文献。然后解释试验设计。请核对这些记录。", False),
])
def test_reference_context_is_bounded_and_negation_aware(query, expected):
    from src.graph.general_agent_graph import _explicit_reference_check
    assert _explicit_reference_check(query) is expected

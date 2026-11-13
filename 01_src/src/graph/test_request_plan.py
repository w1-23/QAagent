"""RequestPlan is one deterministic, serializable composition of request facts."""
from __future__ import annotations

import json

from src.graph.request_plan import RequestFacts, build_request_plan


def test_request_plan_is_deterministic_and_json_serializable():
    query = "生成三份 HTML 报告，分别面向研发、销售和客户。"
    first = build_request_plan(query)
    second = build_request_plan(query)
    assert first == second
    assert first.schema_version == 1
    assert len(first.deliverables) == 3
    assert first.retrieval_decision["policy"] == "advisory"
    assert first.required_tool_groups == ()
    payload = first.as_dict()
    json.dumps(payload, ensure_ascii=False)
    assert tuple(payload["visible_tools"]) == first.visible_tools


def test_data_audit_denies_retrieval_capabilities_not_just_tool_names():
    plan = build_request_plan(
        "请审查上传表格里的缺失值和重复记录",
        RequestFacts(attachments=1, has_session=True),
    )
    assert "network" in plan.capability_denials
    assert "local_corpus" in plan.capability_denials
    assert "deepseek_search" not in plan.visible_tools
    assert "local_hybrid_search" not in plan.visible_tools
    assert "audit_uploaded_data" in plan.visible_tools


def test_explicit_note_authorization_is_part_of_the_plan():
    assert build_request_plan("把这段结论保存到课题记录里").executor_authorizations == (
        "save_research_note",)
    assert build_request_plan("导出课题记录").executor_authorizations == ()
    assert build_request_plan("帮我看看这篇文献的方法").executor_authorizations == ()


def test_explicit_source_scope_becomes_a_required_route_not_a_topic_guess():
    plan = build_request_plan("只查本地库里的黄龙病资料")
    assert plan.selected_scope == "local"
    assert plan.retrieval_decision["policy"] == "required"
    assert plan.required_tool_groups
    assert "deepseek_search" not in plan.visible_tools


def test_visualization_is_an_environment_fact():
    without = build_request_plan("画一个流程图", RequestFacts(visualization=False))
    with_it = build_request_plan("画一个流程图", RequestFacts(visualization=True))
    assert "draw_svg" not in without.visible_tools
    assert "draw_svg" in with_it.visible_tools
    assert "visualization" in without.capability_denials


def test_candidate_pool_is_a_runtime_fact_not_a_topic_guess():
    """候选池是运行事实：本轮预取出了池，候选工具才可见。"""
    without = build_request_plan("柑橘黄龙病有哪些防治方法")
    with_pool = build_request_plan(
        "柑橘黄龙病有哪些防治方法", RequestFacts(has_candidate_pool=True))
    candidate_tools = {"inspect_candidates", "read_candidate", "search_candidates"}
    assert not (candidate_tools & set(without.visible_tools))
    assert candidate_tools <= set(with_pool.visible_tools)
    # 池只影响候选工具，不影响本地检索本身。
    assert "local_hybrid_search" in without.visible_tools
    assert "local_hybrid_search" in with_pool.visible_tools

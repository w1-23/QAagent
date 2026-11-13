"""Phase 2A: tool visibility is an environment question, not a keyword one.

The contract under test: the model is offered every tool that can actually run
in this request, and a tool disappears only because a declared precondition is
unmet or because the user stated a source constraint. Nothing here asserts that
a particular question "should" surface a particular tool - that is the model's
decision - except for the regression that started this work: a twelve-character
question that used to lose both retrieval tools.
"""

from types import SimpleNamespace

import pytest

from src.core import tracing
from src.graph import general_agent_graph, tool_availability
from src.graph.general_agent_graph import _scoped_toolset
from src.graph.tool_availability import provider_denials as configured_provider_denials
from src.graph.tool_availability import (
    KNOWN_SCOPES,
    SCOPE_LOCAL,
    SCOPE_WEB,
    available_tool_names,
    explain,
    runtime_env,
)


@pytest.fixture(autouse=True)
def healthy_runtime(monkeypatch):
    monkeypatch.setattr(tracing, "get_session_id", lambda: "test-visibility-session")
    monkeypatch.setattr(tracing, "web_search_enabled", lambda: True)
    monkeypatch.setattr(tool_availability, "provider_denials", lambda config: frozenset())
    monkeypatch.setenv("CITRUS_RESEARCH_TOOLS_ENABLED", "true")


def names(query: str = "", scope: str = "auto", **kwargs) -> set[str]:
    tools, _schemas = _scoped_toolset(scope, query=query, **kwargs)
    return {tool.name for tool in tools}


WEB = {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper", "search_literature"}
LOCAL = {"local_hybrid_search"}
TABLE = {"inspect_uploaded_table", "audit_uploaded_data"}
CANDIDATE = {"inspect_candidates", "read_candidate", "search_candidates"}


def test_healthy_environment_offers_every_tool_including_the_note_writer():
    all_tools = set(available_tool_names(runtime_env(attachments=2, account_files=1)))
    assert len(all_tools) == 20, sorted(all_tools)

    offered = names("柑橘病害有哪些", has_table=True, has_account_files=True)
    # Visibility is environmental. save_research_note stays visible; the
    # executor rejects it unless the request plan records explicit consent.
    assert offered == all_tools

    # Without an attachment or a library file, exactly the gated tools drop out.
    bare = names("柑橘病害有哪些")
    assert offered - bare == {"inspect_uploaded_table", "audit_uploaded_data",
                              "read_account_file"}


def test_candidate_tools_need_a_pool_for_this_request():
    """候选工具只在"本轮已有候选池"时下发；没有池时模型只能用 local_hybrid_search。"""
    without_pool = names("柑橘病害有哪些", has_table=True, has_account_files=True)
    assert not (without_pool & CANDIDATE)

    with_pool = names("柑橘病害有哪些", has_table=True, has_account_files=True,
                      has_candidate_pool=True)
    assert CANDIDATE <= with_pool
    assert with_pool - without_pool == CANDIDATE

    # 明确禁止检索时，候选工具一并消失：没有池也不该有池。
    denied = names("不要检索，直接基于常识回答", has_candidate_pool=True)
    assert not (denied & CANDIDATE)

    reasons = explain(runtime_env(candidate_pool=False))
    assert "候选池" in reasons["inspect_candidates"]
    assert reasons["inspect_candidates"] != ""


def test_a_short_question_no_longer_loses_both_retrieval_tools():
    """The bug: 12 characters, no keyword, therefore no search tools at all."""
    offered = names("柑橘木虱的防治措施有哪些")
    assert WEB <= offered
    assert LOCAL <= offered


def test_local_only_scope_removes_network_tools_and_nothing_else():
    offered = names("只查本地库里的黄龙病资料", scope=SCOPE_LOCAL)
    assert not (offered & WEB)
    assert not (offered & {"fetch_and_audit_open_supplements"})
    assert LOCAL <= offered
    # Non-retrieval capabilities stay: the user constrained the source, not the
    # work that may be done with the result.
    assert {"draw_svg", "export_literature_evidence", "recall_research_notes"} <= offered


def test_web_only_scope_removes_the_local_corpus_only():
    offered = names("只查网页上的黄龙病资料", scope=SCOPE_WEB)
    assert not (offered & LOCAL)
    assert WEB <= offered
    assert "fetch_and_audit_open_supplements" in offered


def test_fusion_scope_keeps_both_families():
    offered = names("同时检索本地库和网页，然后对比", scope="fusion")
    assert WEB <= offered
    assert LOCAL <= offered


def test_missing_attachment_hides_only_the_table_tools():
    without = names("审查这个实验表有没有异常")
    with_table = names("审查这个实验表有没有异常", has_table=True)
    assert not (without & TABLE)
    assert TABLE <= with_table
    assert with_table - without == TABLE


def test_account_file_tool_follows_the_library_not_the_wording():
    assert "read_account_file" not in names("看看我之前上传的资料")
    assert "read_account_file" in names("看看我之前上传的资料", has_account_files=True)


def test_explicit_no_retrieval_removes_both_families():
    offered = names("不要检索，直接基于常识回答")
    assert not (offered & WEB)
    assert not (offered & LOCAL)
    # Execution-side tools are not retrieval and must survive.
    assert {"draw_svg", "export_literature_evidence"} <= offered


def test_saving_a_note_is_visible_but_executor_authorization_is_explicit():
    from src.graph.request_plan import build_request_plan

    # Visibility no longer guesses intent; the executor checks the plan.
    assert "save_research_note" in names("帮我看看这篇文献的方法")
    assert "save_research_note" in names("把这段结论保存到课题记录里")
    assert build_request_plan("帮我看看这篇文献的方法").executor_authorizations == ()
    assert build_request_plan("把这段结论保存到课题记录里").executor_authorizations == (
        "save_research_note",)


def test_denied_names_are_never_offered():
    env = runtime_env(denied={"deepseek_search", "draw_svg"})
    offered = set(available_tool_names(env))
    assert "deepseek_search" not in offered
    assert "draw_svg" not in offered
    assert "deepseek_fetch" in offered


def test_explicit_constraints_are_explained_with_the_users_own_reason():
    reasons = explain(runtime_env(source_scope=SCOPE_LOCAL))
    assert "只用本地资料" in reasons["deepseek_search"]
    assert reasons["local_hybrid_search"] == ""
    reasons = explain(runtime_env(retrieval=False))
    assert "不检索" in reasons["local_hybrid_search"]


def test_unknown_scope_falls_back_to_auto_instead_of_hiding_tools():
    env = runtime_env(source_scope="something-else")
    assert env.source_scope in KNOWN_SCOPES
    assert env.source_scope == "auto"
    offered = set(available_tool_names(env))
    assert WEB <= offered and LOCAL <= offered


@pytest.mark.parametrize("provider,deepseek_key,zhipu_key,hidden", [
    ("deepseek", "", "", {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"}),
    ("zhipu_web_search", "", "test-key", {"deepseek_fetch", "deepseek_fetch_paper"}),
    ("zhipu_web_search", "test-key", "", {"deepseek_search"}),
])
def test_missing_provider_configuration_hides_only_its_tools(
        monkeypatch, provider, deepseek_key, zhipu_key, hidden):
    monkeypatch.setattr(tool_availability, "provider_denials", configured_provider_denials)
    monkeypatch.setattr(general_agent_graph, "settings", SimpleNamespace(
        RESOLVED_WEB_SEARCH_PROVIDER=provider,
        RESOLVED_DEEPSEEK_WEB_API_KEY=deepseek_key,
        RESOLVED_ZHIPU_API_KEY=zhipu_key,
    ))
    offered = names("检索柑橘黄龙病文献", has_table=True, has_account_files=True)
    expected = set(available_tool_names(runtime_env(attachments=1, account_files=1)))
    assert offered == expected - hidden
    assert "search_literature" in offered


def test_missing_session_hides_tools_that_write_artifacts(monkeypatch):
    monkeypatch.setattr(tracing, "get_session_id", lambda: "")
    offered = names("把这段结论保存到课题记录里", has_table=True, has_account_files=True)
    expected = available_tool_names(runtime_env(session=False, attachments=1, account_files=1))
    assert offered == set(expected)
    assert not (offered & {"word_document", "export_table", "audit_uploaded_data",
                           "save_research_note"})


def test_disabled_web_search_hides_network_tools(monkeypatch):
    monkeypatch.setattr(tracing, "web_search_enabled", lambda: False)
    offered = names("检索柑橘黄龙病文献")
    assert not (offered & (WEB | {"fetch_and_audit_open_supplements"}))
    assert LOCAL <= offered


def test_disabled_research_tools_preserves_the_base_tools(monkeypatch):
    monkeypatch.setenv("CITRUS_RESEARCH_TOOLS_ENABLED", "false")
    assert names("检索柑橘文献", has_table=True, has_account_files=True) == (
        WEB | LOCAL | {"draw_svg", "read_account_file"})

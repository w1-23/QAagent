"""Phase 2A: tool visibility is an environment question, not a keyword one.

The contract under test: the model is offered every tool that can actually run
in this request, and a tool disappears only because a declared precondition is
unmet or because the user stated a source constraint. Nothing here asserts that
a particular question "should" surface a particular tool - that is the model's
decision - except for the regression that started this work: a twelve-character
question that used to lose both retrieval tools.
"""

from src.graph.general_agent_graph import _scoped_toolset
from src.graph.tool_availability import (
    KNOWN_SCOPES,
    SCOPE_LOCAL,
    SCOPE_WEB,
    available_tool_names,
    explain,
    runtime_env,
)


def names(query: str = "", scope: str = "auto", **kwargs) -> set[str]:
    tools, _schemas = _scoped_toolset(scope, query=query, **kwargs)
    return {tool.name for tool in tools}


WEB = {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"}
LOCAL = {"local_hybrid_search"}
TABLE = {"inspect_uploaded_table", "audit_uploaded_data"}


def test_healthy_environment_offers_every_tool_but_unrequested_note_write():
    all_tools = set(available_tool_names(runtime_env(attachments=2, account_files=1)))
    assert len(all_tools) == 17, sorted(all_tools)

    offered = names("柑橘病害有哪些", has_table=True, has_account_files=True)
    # save_research_note is the one tool held back on purpose until Phase 2B
    # enforces its authorization in the executor.
    assert offered == all_tools - {"save_research_note"}

    # Without an attachment or a library file, exactly the gated tools drop out.
    bare = names("柑橘病害有哪些")
    assert offered - bare == {"inspect_uploaded_table", "audit_uploaded_data",
                              "read_account_file"}


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


def test_saving_a_note_still_needs_the_user_to_ask():
    assert "save_research_note" not in names("帮我看看这篇文献的方法")
    assert "save_research_note" in names("把这段结论保存到课题记录里")


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

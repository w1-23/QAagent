"""Production policy preserves generic capabilities and explicit source limits."""
import pytest

from src.agent.host_policy import allowed_presets
from src.graph import tool_availability


@pytest.fixture
def readiness(monkeypatch):
    def ready(**facts):
        return tool_availability.Env(**facts, local_corpus=True, script_sandbox=True)
    monkeypatch.setattr(tool_availability, "runtime_env", ready)
    monkeypatch.setattr(tool_availability, "provider_denials", lambda _: frozenset())
    monkeypatch.setenv("CITRUS_RESEARCH_TOOLS_ENABLED", "true")
    from src.core import tracing
    monkeypatch.setattr(tracing, "web_search_enabled", lambda: True)


@pytest.mark.parametrize("query,local,web", [
    ("联网查一下这些方法", True, True),
    ("请查询本地知识库", True, True),
    ("只用联网资料", False, True),
    ("不要联网，只查本地库", True, False),
    ("不要检索，解释上传的数据", False, False),
])
def test_explicit_source_limits_leave_file_and_workspace_available(readiness, query, local, web):
    names = allowed_presets({"query": query, "session_id": "synthetic",
        "account_files": [{"id": "synthetic"}]}, has_tables=True, has_pool=True)
    assert ("local_hybrid_search" in names) is local
    assert ("deepseek_search" in names) is web
    assert {"read_account_file", "read_tool_result", "research_workspace"} <= names


def test_disabling_research_presets_retains_available_generic_readers(readiness, monkeypatch):
    monkeypatch.setenv("CITRUS_RESEARCH_TOOLS_ENABLED", "false")
    names = allowed_presets({"query": "read a file", "session_id": "synthetic",
        "account_files": [{"id": "synthetic"}]}, has_tables=True, has_pool=False)
    assert {"deepseek_search", "deepseek_fetch", "read_account_file", "read_tool_result"} <= names
    assert not {"research_workspace", "audit_uploaded_data", "create_research_figure"} & names

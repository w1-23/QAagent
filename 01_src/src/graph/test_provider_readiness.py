"""Provider configuration checks are offline and separate search from fetch."""
from types import SimpleNamespace
import pytest
from src.graph.tool_availability import provider_denials


@pytest.mark.parametrize("provider,search,fetch,expected", [
    ("zhipu_web_search", "configured", "", {"deepseek_fetch", "deepseek_fetch_paper"}),
    ("zhipu_web_search", "", "configured", {"deepseek_search"}),
    ("deepseek", "", "configured", set()),
    ("deepseek", "configured", "", {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"}),
])
def test_only_missing_provider_capabilities_are_hidden(provider, search, fetch, expected):
    config = SimpleNamespace(RESOLVED_WEB_SEARCH_PROVIDER=provider,
        RESOLVED_ZHIPU_API_KEY=search, RESOLVED_DEEPSEEK_WEB_API_KEY=fetch)
    assert provider_denials(config) == expected
    assert "search_literature" not in provider_denials(config)

from src.config import settings
from src.tools import get_tool_names
from src.tools.deepseek_search_fetch import _fetch_timeout, _search_timeout
from src.tools.registry import (
    _tool_exec_timeout,
    get_tool_spec,
    _TOOL_SPECS,
    ToolSpec,
    register_tool,
)
from src.tools.deepseek_search_fetch import _public_http_url_error


def test_search_and_fetch_use_distinct_http_budgets(monkeypatch):
    monkeypatch.setattr(settings, "WEB_SEARCH_TIMEOUT", 90)
    monkeypatch.setattr(settings, "SEARCH_FETCH_TIMEOUT_SEC", 45)

    assert _search_timeout() == 90
    assert _fetch_timeout() == 45


def test_outer_tool_wait_cannot_expire_before_http_budget(monkeypatch):
    monkeypatch.setattr(settings, "TOOL_EXEC_TIMEOUT_SEC", 60)
    monkeypatch.setattr(settings, "WEB_SEARCH_TIMEOUT", 90)
    monkeypatch.setattr(settings, "SEARCH_FETCH_TIMEOUT_SEC", 45)
    monkeypatch.setattr(settings, "TOOL_TIMEOUTS", {
        # Lower operator overrides are raised to preserve the timeout contract.
        "deepseek_search": 30,
        "deepseek_fetch": 20,
        "deepseek_fetch_paper": 20,
    })

    assert _tool_exec_timeout("deepseek_search") == 100
    assert _tool_exec_timeout("deepseek_fetch") == 60
    assert _tool_exec_timeout("deepseek_fetch_paper") == 60


def test_active_registry_has_unique_names_and_specs_for_every_tool():
    names = get_tool_names()
    assert len(names) == len(set(names))
    assert all(get_tool_spec(name) is not None for name in names)


def test_active_tool_write_scopes_match_actual_side_effects():
    assert "draw_svg" in get_tool_names()
    assert get_tool_spec("draw_svg").is_readonly is True

    for name in (
        "audit_uploaded_data", "verify_uploaded_references",
        "fetch_and_audit_open_supplements", "export_literature_evidence",
        "export_paper_analysis", "export_experiment_design",
        "export_research_notes", "export_corpus_gap_report",
    ):
        spec = get_tool_spec(name)
        assert spec is not None
        assert spec.is_readonly is False
        assert spec.write_scope == "session_artifact"

    save_note = get_tool_spec("save_research_note")
    assert save_note is not None
    assert save_note.is_readonly is False
    assert save_note.write_scope == "account_record"


def test_analysis_category_does_not_bypass_write_sandbox(monkeypatch):
    import pytest
    from langchain_core.tools import StructuredTool
    from src.tools.registry import _check_tool_sandbox

    tool = StructuredTool.from_function(
        lambda value="": value,
        name="custom_analysis_writer",
        description="test-only tool",
    )
    monkeypatch.setitem(_TOOL_SPECS, tool.name, ToolSpec(
        func=tool, category="analysis", is_readonly=False))
    monkeypatch.setattr(settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(settings, "PERMISSION_MODE", "deny")

    # Exercise the deny branch without opening a Windows asyncio event loop;
    # the current host can hang while creating its socketpair self-pipe.
    pending = _check_tool_sandbox(tool.name, {})
    with pytest.raises(StopIteration) as completed:
        pending.send(None)
    reason = completed.value.value
    assert reason and "拒绝" in reason


def test_registry_has_no_old_yaml_name_dependency():
    # The code-owned declaration imports/registers the active names directly.
    # This guards against silently falling back to stale config registrations.
    import src.tools as tools_module
    from pathlib import Path
    import yaml

    assert len(tools_module._TOOL_REGISTRY_BY_NAME) == len(get_tool_names())
    assert set(tools_module._TOOL_REGISTRY_BY_NAME) == set(get_tool_names())
    config_path = Path(__file__).resolve().parents[3] / "02_配置_config" / "config.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "tools" not in config
    assert not ({"provider", "api_style", "responses_path", "anthropic_path"}
                & set(config.get("web_search", {})))
    assert not hasattr(settings, "WEB_SEARCH_API_STYLE")


def test_public_fetch_url_guard_rejects_private_and_malformed_targets(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
    ])
    assert _public_http_url_error("https://example.org/paper") is None
    for target in (
        "http://127.0.0.1/admin", "http://10.0.0.2/", "http://169.254.169.254/",
        "http://localhost/", "file:///etc/passwd", "http://user:pass@example.org/",
        "https://example.org:99999/", "http://router.local/",
    ):
        assert _public_http_url_error(target), target


def test_fetch_paper_blocks_private_user_url_before_provider_call(monkeypatch):
    from src.tools import deepseek_search_fetch as web_tools

    called = []
    monkeypatch.setattr(web_tools, "_enabled", lambda: True)
    monkeypatch.setattr(web_tools, "_fetch_url", lambda *_a, **_kw: called.append(True))
    output = web_tools.deepseek_fetch_paper.func(
        doi="", pmid="", title="", urls=["http://127.0.0.1/private"])

    assert "WEB_FETCH_BLOCKED" in output[0]
    assert called == []


def test_disabled_fetch_does_not_resolve_user_hostname(monkeypatch):
    import socket
    from src.tools import deepseek_search_fetch as web_tools

    def forbidden_dns(*_args, **_kwargs):
        raise AssertionError("hostname resolution must not run when web tools are disabled")

    monkeypatch.setattr(web_tools, "_enabled", lambda: False)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden_dns)

    output = web_tools.deepseek_fetch.func("https://example.org/article")

    assert "WEB_DISABLED" in output[0]


def test_registry_requires_explicit_side_effect_classification():
    import pytest
    from langchain_core.tools import StructuredTool

    tool = StructuredTool.from_function(
        lambda value="": value,
        name="unclassified_writer",
        description="test-only tool",
    )
    with pytest.raises(ValueError, match="explicit write scope"):
        register_tool(tool, category="analysis")
    with pytest.raises(ValueError, match="cannot declare a write scope"):
        register_tool(tool, category="analysis", is_readonly=True,
                      write_scope="session_artifact")

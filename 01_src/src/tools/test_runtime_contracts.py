import pytest

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


def test_note_writer_requires_request_scoped_executor_authorization(monkeypatch):
    import asyncio
    from src.core.tracing import (
        clear_request_authorizations, set_request_authorizations,
    )
    from src.tools.registry import _check_tool_sandbox

    monkeypatch.setattr(settings, "SANDBOX_ENABLED", True)
    monkeypatch.setattr(settings, "PERMISSION_MODE", "deny")
    try:
        clear_request_authorizations()
        pending = _check_tool_sandbox("save_research_note", {})
        with pytest.raises(StopIteration) as completed:
            pending.send(None)
        assert "需要用户在本轮明确请求" in completed.value.value

        set_request_authorizations({"save_research_note"})
        assert asyncio.run(_check_tool_sandbox("save_research_note", {})) is None
    finally:
        clear_request_authorizations()


def test_registry_has_no_old_yaml_name_dependency():
    # The code-owned declaration imports/registers the active names directly.
    # This guards against silently falling back to stale config registrations.
    import src.tools as tools_module
    from pathlib import Path
    import yaml

    assert len(tools_module._TOOL_REGISTRY_BY_NAME) == len(get_tool_names())
    assert set(tools_module._TOOL_REGISTRY_BY_NAME) == set(get_tool_names())
    config_path = Path(__file__).resolve().parents[3] / "02_config" / "config.yaml"
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


def test_wrong_keyword_is_rejected_before_dispatch_with_legal_names():
    """审核包里的 deepseek_fetch(doi=…) 曾变成裸 TypeError + "检查参数是否合理"。"""
    import asyncio

    from src.tools import deepseek_fetch, deepseek_fetch_paper
    from src.tools.registry import _run_single_tool

    message = asyncio.run(_run_single_tool(
        deepseek_fetch, {"id": "call-1", "name": "deepseek_fetch",
                         "args": {"doi": "10.0000/synthetic"}}))
    text = str(message.content)
    assert text.startswith("[ERR_ARG]")
    assert "doi" in text and "url" in text
    assert "deepseek_fetch_paper" in text
    assert "检查参数是否合理" not in text
    assert "本次未执行" in text
    # 正确的工具确实接受 doi。
    from src.tools.argument_check import check_tool_arguments
    assert check_tool_arguments(deepseek_fetch_paper, "deepseek_fetch_paper",
                                {"doi": "10.0000/synthetic"}) == ""


def test_missing_required_argument_names_the_argument():
    from src.tools import export_table
    from src.tools.argument_check import check_tool_arguments

    problem = check_tool_arguments(export_table, "export_table", {})
    assert problem.startswith("[ERR_ARG]") and "缺少必填参数" in problem


def test_type_error_from_a_tool_is_classified_as_an_argument_fault():
    from src.tools import deepseek_fetch
    from src.tools.registry import _classify_error

    text = _classify_error(
        TypeError("deepseek_fetch() got an unexpected keyword argument 'doi'"),
        deepseek_fetch)
    assert text.startswith("[ERR_ARG]")
    assert "url" in text and "本次未执行" in text


def test_internal_type_error_does_not_claim_the_tool_was_never_executed():
    from src.tools import deepseek_fetch
    from src.tools.registry import _classify_error
    text = _classify_error(TypeError("unsupported operand type(s) for +: 'int' and 'str'"),
                           deepseek_fetch)
    assert not text.startswith("[ERR_ARG]")
    assert "本次未执行" not in text


def test_dsml_recovery_uses_the_same_argument_checker():
    from src.graph.general_agent_graph import _parse_dsml_tool_calls
    from src.tools import deepseek_fetch

    tools = {"deepseek_fetch": deepseek_fetch}
    bad = ('<｜｜DSML｜｜invoke name="deepseek_fetch">'
           '<｜｜DSML｜｜parameter name="doi" string="true">10.0000/x'
           '</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke>')
    detected, calls = _parse_dsml_tool_calls(bad, tools)
    assert detected is True and calls == []


def test_timeout_message_gives_a_concrete_resume_position():
    from src.tools.registry import _timeout_message

    verify = _timeout_message("verify_uploaded_references", 60,
                              {"file_index": 0, "start_index": 20, "max_rows": 10})
    assert "start_index=20" in verify and "max_rows=10" in verify
    assert "start_index=30" not in verify, "超时无法证明当前窗口完成，不能跳过"
    assert "6 小时" in verify
    first = _timeout_message("verify_uploaded_references", 60, {})
    assert "start_index=0" in first
    read = _timeout_message("read_account_file", 60,
                            {"file_id": "a" * 32, "start_char": 9000})
    assert "start_char=9000" in read
    assert "start_char=18000" not in read, "尚未收到的页必须保留"
    other = _timeout_message("draw_svg", 60, {})
    assert "缩小" in other


def test_request_deadline_comes_from_one_place(monkeypatch):
    import math

    from src.graph.general_agent_graph import (
        _request_timeout_disabled,
        request_deadline_from_settings,
    )

    monkeypatch.setattr(settings, "AGENT_REQUEST_TIMEOUT_SEC", 0)
    assert _request_timeout_disabled() is True
    assert math.isinf(request_deadline_from_settings())

    monkeypatch.setattr(settings, "AGENT_REQUEST_TIMEOUT_SEC", 240)
    assert _request_timeout_disabled() is False
    import time as _time
    assert 235 <= request_deadline_from_settings() - _time.monotonic() <= 241


def test_api_layer_no_longer_hardcodes_a_180_second_ceiling():
    import pathlib

    source = (pathlib.Path(__file__).resolve().parents[1] / "api" / "main.py").read_text(
        encoding="utf-8")
    assert 'AGENT_REQUEST_TIMEOUT_SEC", 180' not in source
    assert "request_deadline_from_settings()" in source


def test_large_account_file_page_keeps_its_real_continuation(monkeypatch):
    import asyncio

    from src.tools import account_files
    from src.tools.registry import _run_single_tool

    file_id = "a" * 32
    source = "甲" * 20_000 + "后半段证据" * 1000
    lookups = []

    def owned_file(fid, uid, touch=True):
        lookups.append((fid, uid, touch))
        return {"path": "account.txt", "filename": "account.txt"}

    monkeypatch.setattr("src.core.tracing.get_user_id", lambda: "owner")
    monkeypatch.setattr("src.session.files.get_account_file", owned_file)
    monkeypatch.setattr(account_files, "_extract_text", lambda *_: (source, ""))

    async def read_pages():
        pages = []
        for offset in (0, 20_000):
            message = await _run_single_tool(account_files.read_account_file, {
                "id": f"account-page-{offset}", "name": "read_account_file",
                "args": {"file_id": file_id, "start_char": offset,
                         "max_chars": 20_000},
            })
            pages.append(str(message.content))
        return pages

    first, second = asyncio.run(read_pages())
    assert "甲" * 20_000 in first
    assert "从字符位置 20000 继续读取" in first
    assert source[20_000:] in second
    assert "继续读取" not in second
    assert lookups == [(file_id, "owner", True)] * 2
    for text in (first, second):
        assert "read_local_file" not in text
        assert "tmp/" not in text


@pytest.mark.parametrize("result_kind", ["plain", "artifact", "tuple", "tool_message"])
@pytest.mark.parametrize("raw_chars", [60_000, 200_500])
def test_large_result_is_recorded_before_the_model_copy_is_clamped(
        monkeypatch, tmp_path, result_kind, raw_chars):
    import asyncio
    import hashlib
    import sqlite3

    from langchain_core.messages import ToolMessage
    from langchain_core.tools import StructuredTool

    from src.core import progress_bus, tool_results, turn_source_ledger
    from src.graph import general_agent_graph as graph
    from src.tools import registry

    raw = "头\n" + "中" * (raw_chars - 4) + "\n尾"
    call_id = "large-result-call"
    tool_name = "offload_contract"

    def output():
        if result_kind in {"artifact", "tuple"}:
            return raw, {}
        if result_kind == "tool_message":
            return ToolMessage(content=raw, tool_call_id=call_id, name=tool_name)
        return raw

    tool = StructuredTool.from_function(
        output, name=tool_name, description="Offline result-retention contract",
        response_format=("content_and_artifact" if result_kind == "artifact"
                         else "content"))
    monkeypatch.setitem(registry._TOOL_SPECS, tool_name,
                        ToolSpec(func=tool, is_readonly=True))
    offload_dir = tmp_path / "offload"
    monkeypatch.setattr(registry, "OFFLOAD_DIR", offload_dir)
    monkeypatch.setattr(registry, "_offload_created_files", [])
    evidence_db = tmp_path / "evidence.db"
    monkeypatch.setattr(tool_results, "_connect", lambda: sqlite3.connect(evidence_db))
    monkeypatch.setattr(tool_results, "_schema_ready", False)
    monkeypatch.setattr(turn_source_ledger, "record_tool_sources", lambda *a, **k: 0)
    progress_bus.set_request_queue(asyncio.Queue())
    progress_bus.set_turn_scope("offload-session", "offload-request")
    try:
        messages = asyncio.run(graph._run_regular_calls(
            [{"id": call_id, "name": tool_name, "args": {}}], {tool_name: tool},
            completion_callback=graph._emit_completed_tool_step))
    finally:
        progress_bus.clear_turn_scope()
        progress_bus.clear_request_queue()

    # This is the same evidence record read by the existing tool-result endpoint.
    row = tool_results.fetch(call_id)
    assert row is not None and row["session_id"] == "offload-session"
    assert row["result_chars"] == len(raw)
    assert row["full_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if raw_chars <= tool_results.MAX_CHARS:
        assert row["result_text"] == raw and row["truncated"] == 0
    else:
        assert row["truncated"] == 1
        assert row["result_text"].startswith("头\n")
        assert row["result_text"].endswith("\n尾")

    model_text = str(messages[0].content)
    assert len(model_text) <= tool_results.MODEL_MAX_CHARS
    assert "tool_call_id" in model_text and "证据库" in model_text
    assert "read_local_file" not in model_text
    assert "read_account_file" not in model_text
    assert "tmp/" not in model_text
    assert str(offload_dir) not in model_text
    assert registry.get_offload_file_list() == []
    assert not offload_dir.exists()

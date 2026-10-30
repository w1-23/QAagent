"""工具事件载荷安全回归（审计缺口 G1）。

为什么需要：工具事件会直接推给浏览器并落进历史。一旦有人往载荷里加
"原始参数"或"工具输出"，检索式、DOI、正文摘要、密钥就会跨出服务端边界。
本测试把三类事件的字段集合钉死为白名单，任何新增字段都会在此失败，
迫使改动者明确回答："这个字段能否给客户端看？"
"""

import re

import src.core.progress_bus as pb


def _capture(monkeypatch):
    events = []
    monkeypatch.setattr(
        pb, "emit_encoded",
        lambda name, payload: events.append((name, dict(payload))),
    )
    return events


def test_tool_call_start_payload_is_whitelisted(monkeypatch):
    events = _capture(monkeypatch)
    pb.emit_tool_call_start(
        "deepseek_search",
        {"query": "内部检索式-不可外泄", "api_key": "sk-should-not-leak"},
        "call-1",
    )
    assert len(events) == 1
    name, payload = events[0]
    assert name == "tool_call_start"
    assert set(payload) == {"display_name", "action_hint", "tool_call_id", "started_at_ms"}
    # 设计如此：action_hint 只取白名单字段（query/url 站点名/file 名…），
    # 让用户看到"查了什么"；但其余参数（api_key 等）绝不出现在事件里。
    blob = repr(payload)
    assert "sk-should-not-leak" not in blob
    assert "api_key" not in blob
    assert "内部检索式-不可外泄" in payload["action_hint"]
    assert len(payload["action_hint"]) <= 80
    assert payload["tool_call_id"] == "call-1"
    assert isinstance(payload["started_at_ms"], int)


def test_url_args_are_reduced_to_host(monkeypatch):
    events = _capture(monkeypatch)
    pb.emit_tool_call_start(
        "deepseek_fetch",
        {"url": "https://example.org/paper?token=secret123"},
        "call-url",
    )
    _, payload = events[-1]
    assert "secret123" not in repr(payload)
    assert "example.org" in payload["action_hint"]


def test_tool_result_payload_is_whitelisted_and_sanitized(monkeypatch):
    events = _capture(monkeypatch)
    pb.emit_tool_call_start("deepseek_search", {"query": "q"}, "call-2")
    record = pb.emit_tool_result(
        "deepseek_search",
        "工具完整输出正文-不可外泄",
        "call-2",
        is_error=False,
        summary="找到 3 条资料，见 https://example.org/x 与 /home/WZQ/CitrusQA/a.txt",
        action_hint="检索\n黄龙病  防控",
    )
    name, payload = events[-1]
    assert name == "tool_result"
    assert set(payload) == {
        "display_name", "action_hint", "tool_call_id", "status",
        "reason_code", "summary", "elapsed_ms", "completed_at_ms",
    }
    blob = repr(payload)
    assert "工具完整输出正文-不可外泄" not in blob
    assert "https://example.org" not in payload["summary"]
    assert "/home/WZQ/" not in payload["summary"]
    assert payload["action_hint"] == "检索 黄龙病 防控"
    assert payload["status"] == "complete"
    assert payload["elapsed_ms"] >= 0
    assert record["tool_call_id"] == "call-2"


def test_unknown_status_is_normalized_to_failed(monkeypatch):
    events = _capture(monkeypatch)
    pb.emit_tool_result("draw_svg", "", "call-3", outcome={"status": "bogus_status"})
    _, payload = events[-1]
    assert payload["status"] == "failed"


def test_reason_code_is_clipped(monkeypatch):
    events = _capture(monkeypatch)
    pb.emit_tool_result("draw_svg", "", "call-4", outcome={"reason_code": "x" * 200})
    _, payload = events[-1]
    assert len(payload["reason_code"]) == 64


def test_journal_records_terminal_status(monkeypatch):
    _capture(monkeypatch)
    pb.set_request_journal([])
    try:
        pb.emit_tool_result("draw_svg", "", "call-5", is_error=True,
                            outcome={"status": "failed", "reason_code": "svg_invalid"})
        journal = pb.get_request_journal()
    finally:
        pb.clear_request_journal()
    assert journal and journal[-1]["status"] == "failed"
    assert journal[-1]["reason"] == "svg_invalid"

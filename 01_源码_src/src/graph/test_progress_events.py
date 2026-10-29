import asyncio
import json

import pytest
from langchain_core.messages import ToolMessage

from src.core.progress_bus import (
    clear_request_queue,
    emit_tool_call_start,
    emit_tool_result,
    set_request_queue,
)
from src.graph.general_agent_graph import _run_regular_calls, _safe_persisted_trace


def test_tool_events_are_correlated_and_do_not_expose_arguments_or_output():
    queue = asyncio.Queue()
    set_request_queue(queue)
    try:
        emit_tool_call_start("deepseek_search", {"query": "private query"}, "call-1")
        emit_tool_result(
            "deepseek_search", "private page body", "call-1",
            summary="找到 2 条网页资料",
            outcome={"status": "complete", "reason_code": ""})
        events = [json.loads(queue.get_nowait()["data"]),
                  json.loads(queue.get_nowait()["data"])]
    finally:
        clear_request_queue()

    assert [event["tool_call_id"] for event in events] == ["call-1", "call-1"]
    assert events[0]["display_name"] == "网页搜索"
    assert events[1]["status"] == "complete"
    assert events[1]["summary"] == "找到 2 条网页资料"
    serialized = json.dumps(events, ensure_ascii=False)
    assert "private query" not in serialized
    assert "private page body" not in serialized


def test_history_redaction_keeps_only_execution_metadata_for_local_evidence():
    message = ToolMessage(
        content="private evidence text",
        tool_call_id="search-1",
        name="local_hybrid_search",
        artifact={
            "main_results": [{"text": "private corpus content"}],
            "_citrus_execution": {
                "status": "complete", "reason_code": "", "summary": "找到 1 条候选资料",
                "elapsed_ms": 42, "completed_at_ms": 123456789,
            },
        })

    sanitized = _safe_persisted_trace([message])[0]
    assert "private evidence text" not in sanitized.content
    assert sanitized.artifact == {"_citrus_execution": message.artifact["_citrus_execution"]}
    assert "main_results" not in sanitized.artifact


def test_parallel_tool_events_follow_completion_order_but_results_keep_call_order(monkeypatch):
    import src.tools.registry as registry

    async def fake_run(tool, call):
        await asyncio.sleep(0.04 if call["id"] == "slow" else 0.005)
        return ToolMessage(content="ok", tool_call_id=call["id"], name=call["name"])

    monkeypatch.setattr(registry, "_run_single_tool", fake_run)
    callback_order = []
    calls = [
        {"id": "slow", "name": "slow_tool", "args": {}},
        {"id": "fast", "name": "fast_tool", "args": {}},
    ]
    async def run():
        return await _run_regular_calls(
            calls, {"slow_tool": object(), "fast_tool": object()},
            completion_callback=lambda call, message: callback_order.append(call["id"]))

    results = asyncio.run(run())

    assert callback_order == ["fast", "slow"]
    assert [message.tool_call_id for message in results] == ["slow", "fast"]

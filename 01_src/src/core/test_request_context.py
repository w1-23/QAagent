"""Synthetic request-boundary regressions; no external model or user material."""
import asyncio
import contextvars
import json
import time

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from src.config import settings
from src.core.request_context import ContextEpoch, estimate_request, model_request, message_dict, text_tokens
from src.graph.request_plan import bounded_tool_context


def test_converted_request_counts_schemas_arguments_and_images_once():
    calls = [{"id": "a", "type": "function", "function": {"name": "fixture", "arguments": json.dumps({"script": "synthetic " * 1000})}}]
    payload = {"messages": [{"role": "assistant", "content": "", "tool_calls": calls},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}}]}],
        "tools": [{"type": "function", "function": {"name": "fixture", "description": "s" * 8000}}], "max_tokens": 128}
    value = estimate_request(payload)
    assert value["components"]["tool_arguments"] == text_tokens(json.dumps(calls, ensure_ascii=False, separators=(",", ":")))
    assert value["components"]["tool_schemas"] > 1900
    assert value["components"]["images"] > 0
    assert value["estimated_total_tokens"] == sum(value["components"].values()) + 128


def test_schemas_cannot_bypass_window_and_user_requirement_is_untouched(monkeypatch):
    class Model:
        kwargs = {"tools": [{"description": "X" * 30000}]}
        max_tokens = 128
    messages = [HumanMessage(content="Keep every observation and unit mg/L.")]
    view, status = bounded_tool_context(messages, max_tokens=5000, output_tokens=128, model=Model())
    assert not status["fits"] and view[0] is messages[0]
    assert status["estimate_scope"] == "full_request_estimate"
    assert status["components"]["tool_schemas"] > 7000


def test_epoch_freezes_folded_prefix_and_does_not_rewrite_on_append(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_WORKING_MAX_INPUT_TOKENS", 8000)
    monkeypatch.setattr(settings, "CONTEXT_WORKING_SOFT_TOKENS", 4500)
    monkeypatch.setattr(settings, "CONTEXT_SAFETY_MARGIN_TOKENS", 0)
    source = [SystemMessage(content="preserve rules"), HumanMessage(content="Plot all rows, no imputation."),
        AIMessage(content="", tool_calls=[{"id": "script", "name": "run_research_figure_script", "args": {"script": "fixture = 1\n" * 4000}}]),
        ToolMessage(content=json.dumps({"status": "complete", "result_refs": [{"id": "fixture-result"}], "warning": "synthetic warning"}), name="run_research_figure_script", tool_call_id="script")]
    phase = ContextEpoch()
    first, info = bounded_tool_context(source, max_tokens=8000, epoch=phase)
    assert info["fits"] and phase.number == 1
    assert "fixture = 1" not in json.dumps([message_dict(m) for m in first])
    old = [message_dict(m) for m in first]
    source.append(AIMessage(content="Next step"))
    second, info = bounded_tool_context(source, max_tokens=8000, epoch=phase)
    assert [message_dict(m) for m in second[:len(first)]] == old
    assert phase.number == 1


def test_thirty_synthetic_turns_keep_requests_bounded_and_skill_unchanged():
    from src.prompts.loader import assemble_system_prompt
    skill = assemble_system_prompt(mode="expert", query="", available_tools={"run_research_figure_script"})
    source = [SystemMessage(content=skill)]
    phase = ContextEpoch()
    largest = 0
    for index in range(30):
        source.extend([HumanMessage(content=f"Synthetic turn {index}; all rows, unit mg/L, no significance claims."),
            AIMessage(content="", tool_calls=[{"id": f"call-{index}", "name": "run_research_figure_script", "args": {"script": "fixture=1\n" * 10000}}]),
            ToolMessage(name="run_research_figure_script", tool_call_id=f"call-{index}", content=json.dumps({"status": "complete", "result_refs": [{"id": f"result-{index}"}], "warnings": ["synthetic only"]}))])
        view, status = bounded_tool_context(source, max_tokens=1000000, output_tokens=2048, epoch=phase)
        assert status["fits"], status
        assert view[0].content == skill and f"Synthetic turn {index}" in str([m.content for m in view])
        largest = max(largest, status["estimated_input_tokens"])
    assert largest <= settings.CONTEXT_WORKING_MAX_INPUT_TOKENS
    assert phase.number > 0


def test_real_sdk_conversion_and_transport_observation_use_mock_http(monkeypatch):
    from src.core.llm_pool import _ObservedChatOpenAI
    from src.core import task_events, turn_context, progress_bus
    seen, captured = [], []
    def receive(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"id": "fixture-completion", "object": "chat.completion", "created": 1,
            "model": "fixture-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": "Synthetic done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 123, "completion_tokens": 2, "total_tokens": 125}})
    monkeypatch.setattr(turn_context, "record", lambda snapshot, **kw: captured.append((snapshot, kw)))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            model = _ObservedChatOpenAI(model="fixture-model", api_key="synthetic-unused", base_url="http://127.0.0.1:9/v1", http_async_client=http, max_tokens=128, max_retries=0)
            messages = [HumanMessage(content="Synthetic private body must not be captured")]
            progress_bus.set_turn_scope("synthetic-session", "synthetic-request")
            try:
                _, estimated = model_request(model, messages)
                assert not captured and not seen  # preflight does not send/record.
                response = await task_events.run_model_attempt(model, messages, lambda: model.ainvoke(messages))
                assert response.content == "Synthetic done"
            finally:
                progress_bus.clear_turn_scope()
    asyncio.run(scenario())
    assert len(seen) == len(captured) == 1
    snapshot, fields = captured[0]
    assert snapshot["scope"] == "physical_attempt" and snapshot["attempt_id"]
    assert fields["usage"]["input_tokens"] == 123
    assert "Synthetic private body" not in json.dumps(snapshot)


def test_final_budget_hold_and_reported_vs_unknown_totals(tmp_path, monkeypatch):
    from src.core import jobs, tracing, task_events as events
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.sqlite3")
    job = jobs.claim_job("synthetic-session", "synthetic-request", owner_user_id=1)
    events.init_budget(job.job_id, time.time() + 60, max_calls=3, max_tokens=1000)
    events.protect_finalization(job.job_id, 500, calls=2)
    first = events.reserve_attempt(job.job_id, "supervisor", 100)
    events.finish_attempt(first, {"input_tokens": 30, "output_tokens": 20, "total_tokens": 50})
    with pytest.raises(events.TaskConflict, match="reserved:finalization"):
        events.reserve_attempt(job.job_id, "reflection", 10)
    events.release_finalization(job.job_id)
    final = events.reserve_attempt(job.job_id, "final_synthesis", 200)
    events.finish_attempt(final, error="SyntheticTimeout")
    budget = events.projection(job.job_id)["budget"]
    assert budget["reported_total_tokens"] == 50 and budget["unknown_reserved_tokens"] == 200
    assert budget["reported_attempts"] == budget["unknown_attempts"] == 1
    assert budget["tokens"] == 250


def test_cumulative_usage_does_not_expand_single_request_limit(tmp_path, monkeypatch):
    from src.core import jobs, task_events as events
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.sqlite3")
    job = jobs.claim_job("synthetic-session", "synthetic-300k", owner_user_id=1)
    events.init_budget(job.job_id, time.time() + 60, max_calls=40, max_tokens=400000)
    for _ in range(30):
        attempt = events.reserve_attempt(job.job_id, "fixture", 10000)
        events.finish_attempt(attempt, {"input_tokens": 9000, "output_tokens": 1000, "total_tokens": 10000})
    assert events.projection(job.job_id)["budget"]["reported_total_tokens"] == 300000
    _, result = bounded_tool_context([HumanMessage(content="Synthetic next question")], max_tokens=1000000, output_tokens=2048)
    assert result["input_limit"] == settings.CONTEXT_WORKING_MAX_INPUT_TOKENS
    assert result["estimated_input_tokens"] < 100


def test_ordered_parameter_fingerprint_is_distinct_from_canonical_audit_hash():
    from src.core.turn_context import capture_request
    one = capture_request({"model": "fixture", "messages": [{"role": "user", "content": "synthetic"}]})
    two = capture_request({"messages": [{"role": "user", "content": "synthetic"}], "model": "fixture"})
    assert one["hash"] == two["hash"]
    assert one["ordered_parameters"]["hash"] != two["ordered_parameters"]["hash"]
    assert "synthetic" not in json.dumps(one)


def test_historical_evidence_is_data_and_does_not_create_a_user_turn():
    from src.core.context_manager import assemble_supervisor_messages
    source = {"messages": [HumanMessage(content="earlier actual request")],
              "history_evidence_block": "Synthetic historical evidence"}
    current = HumanMessage(content="current request")
    messages, trace_start = assemble_supervisor_messages("rules", source, current)
    assert isinstance(messages[-2], AIMessage)
    assert messages[-2].additional_kwargs["context_origin"] == "historical_evidence"
    assert [m.content for m in messages if isinstance(m, HumanMessage)] == ["earlier actual request", "current request"]
    assert messages[trace_start] is current


def test_explicit_zero_usage_releases_reservation(tmp_path, monkeypatch):
    from src.core import jobs, task_events as events
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.sqlite3")
    job = jobs.claim_job("synthetic-session", "synthetic-zero", owner_user_id=1)
    events.init_budget(job.job_id, time.time() + 60, max_calls=2, max_tokens=1000)
    attempt = events.reserve_attempt(job.job_id, "fixture", 500)
    events.finish_attempt(attempt, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    budget = events.projection(job.job_id)["budget"]
    assert budget["tokens"] == budget["unknown_reserved_tokens"] == 0
    assert budget["reported_attempts"] == 1 and budget["unknown_attempts"] == 0


def test_long_single_turn_compacts_without_waiting_for_another_user():
    source = [SystemMessage(content="Synthetic rules"), HumanMessage(content="Keep mg/L and all rows.")]
    phase = ContextEpoch()
    for index in range(40):
        source.extend([
            AIMessage(content="", tool_calls=[{"id": f"single-{index}", "name": "fixture",
                "args": {"script": "synthetic=1\n" * 12000}}]),
            ToolMessage(content=json.dumps({"status": "complete", "warning": "synthetic only"}),
                name="fixture", tool_call_id=f"single-{index}")])
        view, status = bounded_tool_context(source, max_tokens=1000000, output_tokens=2048, epoch=phase)
        assert status["fits"] and status["estimated_input_tokens"] <= 64000
        assert any(m.content == "Keep mg/L and all rows." for m in view)
    assert phase.number > 1


def test_thinking_recovery_is_two_physical_attempts_with_same_output_cap(tmp_path, monkeypatch):
    from src.core import jobs, tracing, task_events as events, turn_context, progress_bus
    from src.core.llm_pool import _ObservedChatOpenAI, _ThinkingOffWrapper
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.sqlite3")
    job = jobs.claim_job("synthetic-session", "synthetic-fallback", owner_user_id=1)
    events.init_budget(job.job_id, time.time() + 60, max_calls=5, max_tokens=10000)
    monkeypatch.setattr(tracing, "get_job_id", lambda: job.job_id)
    seen, captured = [], []
    monkeypatch.setattr(turn_context, "record", lambda snapshot, **kw: captured.append((snapshot, kw)))

    def receive(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            return httpx.Response(422, json={"error": {"message": "synthetic thinking rejected", "type": "invalid_request_error"}})
        return httpx.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 1,
            "model": "fixture-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101}})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as http:
            common = dict(model="fixture-model", api_key="synthetic-unused", base_url="http://127.0.0.1:9/v1",
                          http_async_client=http, max_tokens=128, max_retries=0)
            primary = _ObservedChatOpenAI(**common, extra_body={"thinking": {"type": "disabled"}})
            fallback = _ObservedChatOpenAI(**common)
            wrapped = _ThinkingOffWrapper(primary, fallback)
            messages = [HumanMessage(content="Synthetic recovery")]
            progress_bus.set_turn_scope("synthetic-session", "synthetic-fallback")
            try:
                result = await events.run_model_attempt(wrapped, messages, lambda: wrapped.ainvoke(messages), purpose="final_synthesis")
                assert result.content == "done"
            finally:
                progress_bus.clear_turn_scope()
    asyncio.run(scenario())
    assert len(seen) == len(captured) == 2
    assert all(body.get("max_tokens", body.get("max_completion_tokens")) == 128 for body in seen)
    assert "thinking" in seen[0] and "thinking" not in seen[1]
    assert {snapshot["purpose"] for snapshot, _ in captured} == {"final_synthesis", "thinking_parameter_fallback"}
    assert len({snapshot["attempt_id"] for snapshot, _ in captured}) == 2
    assert len({snapshot["logical_call_id"] for snapshot, _ in captured}) == 1
    budget = events.projection(job.job_id)["budget"]
    assert budget["reported_total_tokens"] == 101
    assert budget["reported_attempts"] == budget["unknown_attempts"] == 1
    assert budget["unknown_reserved_tokens"] > 0

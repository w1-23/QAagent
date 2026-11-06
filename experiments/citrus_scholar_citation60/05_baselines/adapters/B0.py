"""B0: direct frozen-model diagnostic with no retrieval, files, or tools."""
from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapter_common import (  # noqa: E402
    budget_result,
    load_provider_settings,
    provider_call,
    response_content,
    response_usage,
    terminal_status_after_gates,
    utc_now,
)
from source_scope import resolve_source_scope, validate_tool_trajectory  # noqa: E402


def run_case(case: dict, config: dict) -> dict:
    base_url, api_key, model = load_provider_settings(config)
    scope_policy = resolve_source_scope(case.get("source_scope"))
    started = time.monotonic()
    started_utc = utc_now()
    client_request_id = str(case.get("run_id") or uuid.uuid4().hex)
    call = provider_call(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a careful citrus-science research assistant. "
                    "You have no retrieval, file, web, or tool access. Do not "
                    "pretend that you searched or read a source. If evidence "
                    "is unavailable, say so directly and do not invent facts."
                ),
            },
            {"role": "user", "content": str(case["prompt"])},
        ],
        config=config,
        user_agent="CitrusScholarExperiment/B0",
    )
    raw_response = call["raw_response"]
    answer = response_content(raw_response)
    tokens_in, tokens_out = response_usage(raw_response)
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    usage = raw_response.get("usage") if isinstance(raw_response, dict) else {}
    cost = usage.get("cost") if isinstance(usage, dict) else None
    budget = budget_result(
        model_calls=1,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        retrieval_calls=0,
        context_tokens=0,
        tool_calls=0,
        elapsed_ms=elapsed_ms,
        config=config,
    )
    semantic_status = "completed" if answer.strip() else "no_results"
    terminal_status, failure_class, gate_errors = terminal_status_after_gates(
        semantic_status,
        budget=budget,
        gate_errors=validate_tool_trajectory(scope_policy, []),
    )
    response_id = str(
        raw_response.get("id") or raw_response.get("request_id") or ""
    )
    return {
        "status": terminal_status,
        "semantic_status": semantic_status,
        "failure_class": failure_class,
        "errors": gate_errors,
        "final_answer": answer,
        "raw_answer": answer,
        "response_model": str(raw_response.get("model") or ""),
        "response_id": response_id,
        "provider_response_id": response_id,
        "requested_model": model,
        "client_request_id": client_request_id,
        "server_request_id": None,
        "session_id": None,
        "source_scope_policy": scope_policy.as_dict(),
        "source_scope_validation": {"status": "PASS", "errors": []},
        "raw_response": raw_response,
        "raw_response_sha256": call["raw_response_sha256"],
        "request_body": call["request_body"],
        "request_sha256": call["request_sha256"],
        "response_headers": call["response_headers"],
        "provider_http_status": call["http_status"],
        "tool_calls": [],
        "citations": [],
        "artifact_paths": [],
        "artifact_sha256": {},
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "api_cost": cost,
        "list_cost": usage.get("list_cost") if isinstance(usage, dict) else None,
        "allowance": usage.get("allowance") if isinstance(usage, dict) else None,
        "elapsed_ms": elapsed_ms,
        "model_calls": 1,
        "total_input_tokens": tokens_in,
        "total_output_tokens": tokens_out,
        "retrieval_calls": 0,
        "context_tokens_used": 0,
        "tool_call_count": 0,
        "budget_status": budget["status"],
        "budget": budget,
        "started_utc": started_utc,
        "finished_utc": utc_now(),
        "implementation_notes": (
            "No retrieval, attachment, parser, web, or research-tool access; "
            "B0 is a descriptive model-only diagnostic."
        ),
    }

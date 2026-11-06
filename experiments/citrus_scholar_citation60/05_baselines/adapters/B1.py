"""B1: fair one-pass RAG comparator using the real CitrusScholar retrieval stack."""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapter_common import (  # noqa: E402
    authenticate_experiment_account,
    attachment_payload,
    budget_result,
    citation_list,
    load_provider_settings,
    provider_call,
    response_content,
    response_usage,
    terminal_status_after_gates,
    utc_now,
)
from source_scope import (  # noqa: E402
    resolve_source_scope,
    validate_tool_trajectory,
)


def _project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "01_src" / "src").is_dir():
            return parent
    raise RuntimeError("cannot locate the CitrusScholar source checkout")


def _attachment_context(case: dict, source_root: Path) -> str:
    attachments = attachment_payload(case)
    if not attachments:
        return ""
    sys.path.insert(0, str(source_root / "01_src"))
    from src.api.main import _prepare_chat_attachments  # noqa: PLC0415

    context, _images, _tables = _prepare_chat_attachments(attachments)
    return str(context or "")


def _tool_invoke(tool, arguments: dict) -> tuple[str, dict]:
    raw = tool.invoke(arguments)
    if isinstance(raw, tuple) and len(raw) == 2:
        return str(raw[0]), dict(raw[1] or {})
    return str(raw), {}


def _run_authenticated_local_search(
    *,
    auth: dict,
    client_request_id: str,
    query: str,
    families: list[str],
) -> tuple[str, dict]:
    """Run real local retrieval under the account returned by /api/v2/auth/me."""
    from src.core import tracing  # noqa: PLC0415
    from src.tools.deepseek_search_fetch import local_hybrid_search  # noqa: PLC0415

    tracing.set_user_id(int(auth["user_id"]))
    tracing.set_user_role(str(auth.get("role") or ""))
    tracing.set_session_id("")
    tracing.set_client_request_id(client_request_id)
    try:
        return _tool_invoke(
            local_hybrid_search,
            {
                "query": query,
                "corpus_types": families,
                "limit": 8,
            },
        )
    finally:
        tracing.set_user_id(None)
        tracing.set_user_role("")
        tracing.set_session_id("")
        tracing.set_client_request_id("")


def run_case(case: dict, config: dict) -> dict:
    source_root = _project_root()
    sys.path.insert(0, str(source_root / "01_src"))
    from src.retrieval.corpus_registry import load_corpus_registry  # noqa: PLC0415
    from src.tools.deepseek_search_fetch import (  # noqa: PLC0415
        deepseek_fetch,
        deepseek_search,
    )

    base_url, api_key, model = load_provider_settings(config)
    started = time.monotonic()
    started_utc = utc_now()
    client_request_id = str(case.get("run_id") or uuid.uuid4().hex)
    scope_policy = resolve_source_scope(case.get("source_scope"))
    auth = authenticate_experiment_account(
        config,
        user_agent="CitrusScholarExperiment/B1",
    )
    registry = load_corpus_registry()
    families = sorted(registry.family_ids)
    if scope_policy.local_selector:
        requested_family = scope_policy.local_selector.casefold()
        if requested_family not in families:
            raise RuntimeError(
                f"source_scope local selector is not a registered corpus family: "
                f"{scope_policy.local_selector}"
            )
        families = [requested_family]
    evidence_sections: list[str] = []
    tool_calls: list[dict] = []
    citation_rows: list[dict] = []
    retrieval_calls = 0
    if scope_policy.allow_local:
        local_text, local_artifact = _run_authenticated_local_search(
            auth=auth,
            client_request_id=client_request_id,
            query=str(case["prompt"]),
            families=families,
        )
        retrieval_calls += 1
        local_status = str(local_artifact.get("local_status") or "unknown")
        tool_calls.append(
            {
                "tool_name": "local_hybrid_search",
                "tool_group": "LOCAL",
                "status": (
                    "completed"
                    if local_status == "hit"
                    else "no_results"
                    if local_status in {"miss", "zero_hit"}
                    else "failed"
                ),
                "result": local_artifact,
            }
        )
        evidence_sections.append("<local_evidence>\n" + local_text + "\n</local_evidence>")
        citation_rows.extend(
            row for row in local_artifact.get("main_results", [])
            if isinstance(row, dict)
        )
    if scope_policy.allow_web:
        web_text, web_artifact = _tool_invoke(
            deepseek_search,
            {
                "query": str(case["prompt"]),
                "count": 8,
                "need_content": False,
            },
        )
        retrieval_calls += 1
        web_rows = [
            row for row in web_artifact.get("web_results", [])
            if isinstance(row, dict)
        ]
        tool_calls.append(
            {
                "tool_name": "deepseek_search",
                "tool_group": "WEB",
                "status": "completed" if web_rows else "failed",
                "result": web_artifact,
            }
        )
        evidence_sections.append("<web_evidence>\n" + web_text + "\n</web_evidence>")
        citation_rows.extend(web_rows)
        fetchable = [
            str(row.get("url") or "") for row in web_rows[:2] if row.get("url")
        ]
        for url in fetchable:
            fetch_text, fetch_artifact = _tool_invoke(deepseek_fetch, {"url": url})
            retrieval_calls += 1
            fetched_rows = [
                row for row in fetch_artifact.get("web_results", [])
                if isinstance(row, dict)
            ]
            tool_calls.append(
                {
                    "tool_name": "deepseek_fetch",
                    "tool_group": "WEB",
                    "status": "completed" if fetched_rows else "failed",
                    "result": fetch_artifact,
                }
            )
            evidence_sections.append(
                f"<fetched_web_evidence url={json.dumps(url)}>\n"
                + fetch_text
                + "\n</fetched_web_evidence>"
            )
            citation_rows.extend(fetched_rows)
    attachment_text = (
        _attachment_context(case, source_root)
        if scope_policy.allow_attachment
        else ""
    )
    if attachment_text:
        evidence_sections.append(
            "<attachment_evidence>\n" + attachment_text + "\n</attachment_evidence>"
        )

    caps = config.get("b1_b2_shared_resource_caps") or {}
    context_text = "\n\n".join(evidence_sections)
    context_cap_raw = caps.get("max_context_tokens_per_run")
    context_cap = int(context_cap_raw) if context_cap_raw is not None else 16000
    max_chars = max(1, context_cap * 4)
    truncated = len(context_text) > max_chars
    context_text = context_text[:max_chars]
    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful citrus-science research assistant. Use only "
                "the supplied evidence. Cite material claims with the numbered "
                "evidence or source URLs. If the evidence is insufficient, state "
                "the limitation instead of inventing an answer."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{case['prompt']}\n\nRetrieved evidence:\n"
                f"{context_text or '[no evidence retrieved]'}"
            ),
        },
    ]
    call = provider_call(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=messages,
        config=config,
        user_agent="CitrusScholarExperiment/B1",
    )
    raw_response = call["raw_response"]
    answer = response_content(raw_response)
    tokens_in, tokens_out = response_usage(raw_response)
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    usage = raw_response.get("usage") if isinstance(raw_response, dict) else {}
    response_id = str(
        raw_response.get("id") or raw_response.get("request_id") or ""
    )
    budget = budget_result(
        model_calls=1,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        retrieval_calls=retrieval_calls,
        context_tokens=tokens_in,
        tool_calls=len(tool_calls),
        elapsed_ms=elapsed_ms,
        config=config,
    )
    scope_errors = validate_tool_trajectory(scope_policy, tool_calls)
    semantic_status = "completed" if answer.strip() else "no_results"
    terminal_status, failure_class, gate_errors = terminal_status_after_gates(
        semantic_status,
        budget=budget,
        gate_errors=scope_errors,
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
        "auth_flow": auth["auth_flow"],
        "authenticated_user_id_sha256": auth["identity_sha256"],
        "authenticated_role": auth["role"],
        "auth_me_sha256": auth["auth_me_sha256"],
        "source_scope_policy": scope_policy.as_dict(),
        "source_scope_validation": {
            "status": "PASS" if not scope_errors else "FAIL",
            "errors": scope_errors,
        },
        "raw_response": raw_response,
        "raw_response_sha256": call["raw_response_sha256"],
        "request_body": call["request_body"],
        "request_sha256": call["request_sha256"],
        "response_headers": call["response_headers"],
        "provider_http_status": call["http_status"],
        "tool_calls": tool_calls,
        "citations": citation_list(citation_rows),
        "artifact_paths": [],
        "artifact_sha256": {},
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "api_cost": usage.get("cost") if isinstance(usage, dict) else None,
        "list_cost": usage.get("list_cost") if isinstance(usage, dict) else None,
        "allowance": usage.get("allowance") if isinstance(usage, dict) else None,
        "elapsed_ms": elapsed_ms,
        "model_calls": 1,
        "total_input_tokens": tokens_in,
        "total_output_tokens": tokens_out,
        "retrieval_calls": retrieval_calls,
        "context_tokens_used": tokens_in,
        "context_truncated": truncated,
        "tool_call_count": len(tool_calls),
        "budget_status": budget["status"],
        "budget": budget,
        "started_utc": started_utc,
        "finished_utc": utc_now(),
        "implementation_notes": (
            "One deterministic retrieval/context/model pass using the same "
            "authenticated experiment account, registered corpora, parser, "
            "local search implementation, model and resource caps as B2. "
            "No Agent tool-orchestration loop."
        ),
    }

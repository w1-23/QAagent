"""B1: fair one-pass RAG comparator using the real CitrusScholar retrieval stack."""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapter_common import (  # noqa: E402
    attachment_payload,
    budget_result,
    citation_list,
    load_provider_settings,
    provider_call,
    response_content,
    response_usage,
    utc_now,
)


def _project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "01_src" / "src").is_dir():
            return parent
    raise RuntimeError("cannot locate the CitrusScholar source checkout")


def _scope_allows(scope: str, token: str) -> bool:
    value = str(scope or "").casefold()
    if token == "local":
        return any(marker in value for marker in ("local", "本地", "语料", "文獻", "文献"))
    return any(marker in value for marker in ("web", "联网", "網絡", "网络"))


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


def run_case(case: dict, config: dict) -> dict:
    source_root = _project_root()
    sys.path.insert(0, str(source_root / "01_src"))
    from src.retrieval.corpus_registry import load_corpus_registry  # noqa: PLC0415
    from src.tools.deepseek_search_fetch import (  # noqa: PLC0415
        deepseek_fetch,
        deepseek_search,
        local_hybrid_search,
    )

    base_url, api_key, model = load_provider_settings(config)
    started = time.monotonic()
    started_utc = utc_now()
    client_request_id = str(case.get("run_id") or uuid.uuid4().hex)
    scope = str(case.get("source_scope") or "auto")
    use_local = bool(scope) and not (
        _scope_allows(scope, "web") and not _scope_allows(scope, "local")
    )
    use_web = _scope_allows(scope, "web") or scope.casefold() in {
        "auto",
        "general",
        "fusion",
        "as_stated",
        "自动",
    }
    registry = load_corpus_registry()
    families = sorted(registry.family_ids)
    evidence_sections: list[str] = []
    tool_calls: list[dict] = []
    citation_rows: list[dict] = []
    retrieval_calls = 0
    if use_local:
        local_text, local_artifact = _tool_invoke(
            local_hybrid_search,
            {
                "query": str(case["prompt"]),
                "corpus_types": families,
                "limit": 8,
            },
        )
        retrieval_calls += 1
        local_status = str(local_artifact.get("local_status") or "unknown")
        tool_calls.append(
            {
                "tool_name": "local_hybrid_search",
                "tool_group": "LOCAL",
                "status": "completed" if local_status == "hit" else "failed",
                "result": local_artifact,
            }
        )
        evidence_sections.append("<local_evidence>\n" + local_text + "\n</local_evidence>")
        citation_rows.extend(
            row for row in local_artifact.get("main_results", [])
            if isinstance(row, dict)
        )
    if use_web:
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
    attachment_text = _attachment_context(case, source_root)
    if attachment_text:
        evidence_sections.append(
            "<attachment_evidence>\n" + attachment_text + "\n</attachment_evidence>"
        )

    caps = config.get("b1_b2_shared_resource_caps") or {}
    context_cap = int(caps.get("max_context_tokens_per_run") or 16000)
    context_text = "\n\n".join(evidence_sections)
    max_chars = max(1, context_cap * 4)
    truncated = len(context_text) > max_chars
    context_text = context_text[:max_chars]
    context_tokens = (len(context_text) + 3) // 4
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
    return {
        "status": "completed" if answer.strip() else "no_results",
        "final_answer": answer,
        "raw_answer": answer,
        "response_model": str(raw_response.get("model") or ""),
        "response_id": response_id,
        "requested_model": model,
        "client_request_id": client_request_id,
        "server_request_id": response_id,
        "session_id": None,
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
        "context_tokens_used": context_tokens,
        "context_truncated": truncated,
        "tool_call_count": len(tool_calls),
        "budget_status": budget_result(
            model_calls=1,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            retrieval_calls=retrieval_calls,
            context_tokens=context_tokens,
            tool_calls=len(tool_calls),
            elapsed_ms=elapsed_ms,
            config=config,
        ),
        "started_utc": started_utc,
        "finished_utc": utc_now(),
        "implementation_notes": (
            "One deterministic retrieval/context/model pass using the same "
            "registered corpora, parser, local search implementation, model "
            "and resource caps as B2. No Agent tool-orchestration loop."
        ),
    }

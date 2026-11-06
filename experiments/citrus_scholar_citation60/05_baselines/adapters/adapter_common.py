"""Shared, credential-free HTTP and evidence helpers for experiment adapters."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TERMINAL_STATUSES = {
    "completed",
    "partial",
    "failed",
    "no_results",
    "cancelled",
    "skipped",
}

RESOURCE_ACCOUNTING_UNKNOWN = "RESOURCE_ACCOUNTING_UNKNOWN"
BUDGET_VIOLATION = "BUDGET_VIOLATION"
WITHIN_BUDGET = "WITHIN_BUDGET"

RETRIEVAL_TOOL_NAMES = frozenset(
    {
        "local_hybrid_search",
        "local_bm25_search",
        "local_dense_search",
        "deepseek_search",
        "deepseek_fetch",
        "deepseek_fetch_paper",
    }
)

_AUTH_CACHE: dict[str, dict[str, Any]] = {}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def env_required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def safe_env(name: str) -> str:
    return os.environ.get(name, "").strip()


def secure_endpoint(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme == "https" and parsed.hostname:
        return base_url.rstrip("/")
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        return base_url.rstrip("/")
    raise RuntimeError("remote experiment endpoints must use HTTPS")


def chat_completions_url(base_url: str) -> str:
    base = secure_endpoint(base_url)
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def safe_headers(headers: dict[str, str]) -> dict[str, str]:
    allowed = {
        "content-type",
        "user-agent",
        "x-request-id",
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "retry-after",
    }
    return {
        str(key).lower(): str(value)
        for key, value in headers.items()
        if str(key).lower() in allowed
    }


def provider_call(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    config: dict[str, Any],
    user_agent: str,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(config.get("temperature", 0.0)),
        "top_p": float(config.get("top_p", 1.0)),
        "max_tokens": int(config.get("max_output_tokens") or 4096),
        "stream": False,
    }
    if config.get("seed") is not None:
        payload["seed"] = config.get("seed")
    endpoint = chat_completions_url(base_url)
    request_bytes = canonical_json(payload)
    request = urllib.request.Request(
        endpoint,
        data=request_bytes,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(
            request,
            timeout=float(config.get("timeout_seconds") or 120),
        ) as response:
            response_bytes = response.read(32 * 1024 * 1024)
            status = int(response.status)
            headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        response_bytes = exc.read(32 * 1024 * 1024)
        status = int(exc.code)
        headers = dict(exc.headers.items()) if exc.headers else {}
    except Exception as exc:
        raise RuntimeError(
            f"provider transport failure ({type(exc).__name__}); "
            "credentials were not logged"
        ) from exc
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    try:
        parsed = json.loads(response_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    if status != 200 or not isinstance(parsed, dict):
        raise RuntimeError(f"provider HTTP rejection or non-JSON response: HTTP {status}")
    return {
        "endpoint_path": urllib.parse.urlsplit(endpoint).path,
        "http_status": status,
        "response_headers": safe_headers(headers),
        "raw_response": parsed,
        "raw_response_sha256": sha256_bytes(response_bytes),
        "request_body": payload,
        "request_sha256": sha256_bytes(request_bytes),
        "elapsed_ms": elapsed_ms,
    }


def response_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        if isinstance(choices[0].get("text"), str):
            return choices[0]["text"]
    return ""


def response_usage(response: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None, None
    incoming = usage.get("prompt_tokens", usage.get("input_tokens"))
    outgoing = usage.get("completion_tokens", usage.get("output_tokens"))
    return (
        int(incoming) if isinstance(incoming, int) else None,
        int(outgoing) if isinstance(outgoing, int) else None,
    )


def file_descriptor(path: Path, root: Path) -> dict[str, str]:
    resolved = path.resolve()
    root = root.resolve()
    if not resolved.is_relative_to(root):
        raise RuntimeError("evidence file escapes the configured evidence root")
    return {
        "path": resolved.relative_to(root).as_posix(),
        "sha256": sha256_file(resolved),
    }


def write_once(path: Path, data: bytes) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
    return {"path": path.as_posix(), "sha256": sha256_bytes(data)}


def write_json_once(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    cookie: str = "",
    origin: str = "",
    timeout: float = 120,
    user_agent: str,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url,
        data=canonical_json(payload),
        headers={
            "Content-Type": "application/json",
            "User-Agent": user_agent,
            **({"Cookie": cookie} if cookie else {}),
            **({"Origin": origin} if origin else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()) if exc.headers else {}, exc.read()
    except Exception as exc:
        raise RuntimeError(
            f"experiment API transport failure ({type(exc).__name__})"
        ) from exc


def get_json(
    url: str,
    *,
    cookie: str,
    origin: str = "",
    timeout: float = 120,
    user_agent: str,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            **({"Cookie": cookie} if cookie else {}),
            **({"Origin": origin} if origin else {}),
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()) if exc.headers else {}, exc.read()
    except Exception as exc:
        raise RuntimeError(
            f"experiment API transport failure ({type(exc).__name__})"
        ) from exc


def post_stream(
    url: str,
    payload: dict[str, Any],
    *,
    cookie: str,
    origin: str = "",
    timeout: float,
    user_agent: str,
) -> tuple[int, dict[str, str], bytes]:
    """Capture an app SSE response body byte-for-byte."""
    request = urllib.request.Request(
        url,
        data=canonical_json(payload),
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
            **({"Cookie": cookie} if cookie else {}),
            **({"Origin": origin} if origin else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), dict(response.headers.items()), response.read(
                64 * 1024 * 1024
            )
    except urllib.error.HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()) if exc.headers else {}, exc.read(
            64 * 1024 * 1024
        )
    except Exception as exc:
        raise RuntimeError(
            f"experiment SSE transport failure ({type(exc).__name__})"
        ) from exc


def get_bytes(
    url: str,
    *,
    cookie: str,
    origin: str = "",
    timeout: float,
    user_agent: str,
) -> tuple[int, dict[str, str], bytes]:
    """GET a binary response while preserving status and safe response headers."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            **({"Cookie": cookie} if cookie else {}),
            **({"Origin": origin} if origin else {}),
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), dict(response.headers.items()), response.read(
                64 * 1024 * 1024
            )
    except urllib.error.HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()) if exc.headers else {}, exc.read(
            16 * 1024 * 1024
        )
    except Exception as exc:
        raise RuntimeError(
            f"experiment binary transport failure ({type(exc).__name__})"
        ) from exc


def cookie_header(headers: dict[str, str]) -> str:
    for name, value in headers.items():
        if name.lower() != "set-cookie":
            continue
        first = str(value).split(";", 1)[0].strip()
        if first:
            return first
    return ""


def _parse_sse_raw(data: bytes) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_name = ""
    event_id = ""
    data_lines: list[str] = []
    sequence = 0
    for raw_line in data.decode("utf-8", errors="replace").splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        if not line:
            if data_lines:
                sequence += 1
                payload_text = "\n".join(data_lines)
                try:
                    payload: Any = json.loads(payload_text)
                except json.JSONDecodeError:
                    payload = {"unparsed_data": payload_text}
                events.append(
                    {
                        "sequence": sequence,
                        "event": event_name or "message",
                        "id": event_id,
                        "data": payload,
                    }
                )
            event_name = ""
            event_id = ""
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = value
        elif field == "id":
            event_id = value
        elif field == "data":
            data_lines.append(value)
    return events


def parse_sse_bytes(data: bytes) -> list[dict[str, Any]]:
    return sse_events_with_hashes(data)


def sse_events_with_hashes(data: bytes) -> list[dict[str, Any]]:
    """Parse an SSE body and hash each complete event block."""
    blocks: list[bytes] = []
    current: list[bytes] = []
    for line in data.splitlines(keepends=True):
        if line.rstrip(b"\r\n"):
            current.append(line)
        elif current:
            blocks.append(b"".join(current))
            current = []
    if current:
        blocks.append(b"".join(current))
    parsed = _parse_sse_raw(data)
    for event, block in zip(parsed, blocks):
        event["raw_event_sha256"] = sha256_bytes(block)
    return parsed


def attachment_payload(case: dict[str, Any]) -> list[dict[str, str]]:
    raw = str(case.get("attachment") or "").strip()
    if not raw:
        return []
    search_root = Path(
        os.environ.get("CITRUS_EXPERIMENT_INPUT_ROOT", "")
        or Path.cwd()
    ).resolve()
    items: list[dict[str, str]] = []
    for token in raw.replace(",", "|").split("|"):
        name = token.strip()
        if not name:
            continue
        matches = list(search_root.rglob(name)) if search_root.is_dir() else []
        if len(matches) != 1:
            raise RuntimeError(f"attachment {name!r} must resolve to exactly one input file")
        path = matches[0].resolve()
        if not path.is_relative_to(search_root):
            raise RuntimeError("attachment path escapes configured input root")
        content = path.read_bytes()
        items.append(
            {
                "name": path.name,
                "type": "application/octet-stream",
                "data": "data:application/octet-stream;base64,"
                + base64.b64encode(content).decode("ascii"),
            }
        )
    return items


def random_order_seed(config: dict[str, Any]) -> str:
    seed = int(config.get("seed") or 20261104)
    return hashlib.sha256(f"{seed}:{os.getpid()}:{time.time_ns()}".encode()).hexdigest()


def load_provider_settings(config: dict[str, Any]) -> tuple[str, str, str]:
    base_url = env_required(str(config.get("base_url_env") or "CITRUS_AGENT_BASE_URL"))
    api_key = env_required(str(config.get("api_key_env") or "CITRUS_AGENT_API_KEY"))
    model = env_required(str(config.get("model_id_env") or "CITRUS_AGENT_MODEL_ID"))
    if model != str(config.get("candidate_model_id") or model):
        raise RuntimeError("model environment differs from frozen candidate model")
    return base_url, api_key, model


def _account_identity(user: dict[str, Any]) -> dict[str, Any]:
    try:
        user_id = int(user.get("user_id"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("authenticated account has no valid user_id") from exc
    if user_id <= 0:
        raise RuntimeError("authenticated account has no valid user_id")
    identity_bytes = canonical_json(user)
    return {
        "user_id": user_id,
        "role": str(user.get("role") or ""),
        "identity_sha256": sha256_bytes(identity_bytes),
    }


def authenticate_experiment_account(
    config: dict[str, Any],
    *,
    user_agent: str,
) -> dict[str, Any]:
    """Authenticate once in memory; share the real account with B1 and B2."""
    base_url = env_required(
        str(config.get("experiment_base_url_env") or "CITRUS_EXPERIMENT_BASE_URL")
    )
    username = env_required(
        str(config.get("experiment_username_env") or "CITRUS_EXPERIMENT_USERNAME")
    )
    password = env_required(
        str(config.get("experiment_password_env") or "CITRUS_EXPERIMENT_PASSWORD")
    )
    parsed = urllib.parse.urlsplit(base_url)
    if not (
        parsed.scheme == "https"
        or (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
    ):
        raise RuntimeError("remote experiment app URL must use HTTPS")
    base_url = base_url.rstrip("/")
    cache_key = sha256_bytes(
        canonical_json(
            {
                "base_url": base_url,
                "username": username,
                "password_sha256": sha256_bytes(password.encode("utf-8")),
            }
        )
    )
    cached = _AUTH_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    timeout = float(config.get("timeout_seconds") or 600)
    status, headers, _body = post_json(
        base_url + "/api/v2/auth/login",
        {"username": username, "password": password, "avatar_preset": "01"},
        origin=base_url,
        timeout=timeout,
        user_agent=user_agent,
    )
    if status != 200:
        raise RuntimeError(f"experiment login rejected: HTTP {status}")
    cookie = cookie_header(headers)
    if not cookie:
        raise RuntimeError("experiment authentication cookie missing")
    me_status, _me_headers, me_body = get_json(
        base_url + "/api/v2/auth/me",
        cookie=cookie,
        origin=base_url,
        timeout=timeout,
        user_agent=user_agent,
    )
    if me_status != 200:
        raise RuntimeError(f"experiment identity read rejected: HTTP {me_status}")
    try:
        me_payload = json.loads(me_body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("experiment identity response is not valid JSON") from exc
    user = me_payload.get("user") if isinstance(me_payload, dict) else None
    if not isinstance(user, dict):
        raise RuntimeError("experiment identity response has no account object")
    identity = _account_identity(user)
    auth = {
        "base_url": base_url,
        "cookie": cookie,
        "user": user,
        "user_id": identity["user_id"],
        "role": identity["role"],
        "identity_sha256": identity["identity_sha256"],
        "auth_me_sha256": sha256_bytes(me_body),
        "auth_flow": "login_then_api_v2_auth_me",
        "cookie_persisted": False,
    }
    _AUTH_CACHE[cache_key] = dict(auth)
    return auth


def clear_experiment_auth_cache() -> None:
    """Test helper; production code never persists this in-memory cache."""
    _AUTH_CACHE.clear()


def budget_result(
    *,
    model_calls: int | None,
    tokens_in: int | None,
    tokens_out: int | None,
    retrieval_calls: int | None,
    context_tokens: int | None,
    tool_calls: int | None,
    elapsed_ms: float | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    caps = config.get("b1_b2_shared_resource_caps") or {}
    checks = (
        (model_calls, caps.get("max_model_calls_per_run"), "model_calls"),
        (tokens_in, caps.get("max_total_input_tokens_per_run"), "input_tokens"),
        (tokens_out, caps.get("max_total_output_tokens_per_run"), "output_tokens"),
        (retrieval_calls, caps.get("max_retrieval_calls_per_run"), "retrieval_calls"),
        (context_tokens, caps.get("max_context_tokens_per_run"), "context_tokens"),
        (tool_calls, caps.get("max_tool_calls_per_run"), "tool_calls"),
        (
            elapsed_ms / 1000 if elapsed_ms is not None else None,
            caps.get("max_elapsed_seconds_per_run"),
            "elapsed_seconds",
        ),
    )
    unknown = sorted(
        metric
        for actual, cap, metric in checks
        if cap is not None and actual is None
    )
    violations = sorted(
        metric
        for actual, cap, metric in checks
        if actual is not None
        and cap is not None
        and float(actual) > float(cap)
    )
    status = (
        BUDGET_VIOLATION
        if violations
        else RESOURCE_ACCOUNTING_UNKNOWN
        if unknown
        else WITHIN_BUDGET
    )
    return {
        "status": status,
        "violations": violations,
        "unknown_metrics": unknown,
        "caps": {
            metric: cap
            for _actual, cap, metric in checks
            if cap is not None
        },
    }


def terminal_status_after_gates(
    semantic_status: str,
    *,
    budget: dict[str, Any],
    gate_errors: Iterable[str] = (),
) -> tuple[str, str | None, list[str]]:
    errors = sorted({str(value) for value in gate_errors if str(value)})
    if errors:
        return "failed", "SOURCE_SCOPE_VIOLATION", errors
    if budget.get("status") == BUDGET_VIOLATION:
        return "failed", BUDGET_VIOLATION, []
    if budget.get("status") == RESOURCE_ACCOUNTING_UNKNOWN:
        return "failed", RESOURCE_ACCOUNTING_UNKNOWN, []
    return semantic_status, None, []


def aggregate_provider_usage(responses: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Sum usage across every captured Provider call in one run."""
    rows = [row for row in responses if isinstance(row, dict)]
    token_rows = [response_usage(row) for row in rows]
    prompt_values = [value for value, _ in token_rows]
    completion_values = [value for _, value in token_rows]
    total_in = (
        sum(int(value) for value in prompt_values if value is not None)
        if prompt_values and all(value is not None for value in prompt_values)
        else None
    )
    total_out = (
        sum(int(value) for value in completion_values if value is not None)
        if completion_values and all(value is not None for value in completion_values)
        else None
    )

    def summed_usage_field(name: str) -> int | float | None:
        values = []
        for row in rows:
            usage = row.get("usage")
            value = usage.get(name) if isinstance(usage, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(value)
            else:
                return None
        return sum(values) if values else None

    return {
        "model_calls": len(rows),
        "tokens_in": total_in,
        "tokens_out": total_out,
        "api_cost": summed_usage_field("cost"),
        "list_cost": summed_usage_field("list_cost"),
        "allowance": summed_usage_field("allowance"),
        "usage_complete": (
            bool(rows)
            and all(value is not None for value in prompt_values)
            and all(value is not None for value in completion_values)
        ),
    }


def retrieval_tool_call_count(tool_events: Iterable[dict[str, Any]]) -> int:
    return sum(
        1
        for event in tool_events
        if isinstance(event, dict)
        and str(event.get("tool_name") or "") in RETRIEVAL_TOOL_NAMES
    )


def citation_list(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        citations.append(
            {
                key: row.get(key)
                for key in (
                    "title",
                    "doi",
                    "url",
                    "source",
                    "source_type",
                    "chunk_id",
                    "source_id",
                    "corpus_id",
                    "record",
                    "section",
                    "page",
                )
                if row.get(key) not in (None, "")
            }
        )
    return citations

"""B2: the complete CitrusScholar workflow through the real app API and SSE."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapter_common import (  # noqa: E402
    aggregate_provider_usage,
    authenticate_experiment_account,
    attachment_payload,
    budget_result,
    get_bytes,
    get_json,
    parse_sse_bytes,
    post_json,
    post_stream,
    retrieval_tool_call_count,
    response_content,
    response_usage,
    sha256_bytes,
    sha256_file,
    terminal_status_after_gates,
    utc_now,
    write_json_once,
    write_once,
)
from source_scope import (  # noqa: E402
    resolve_allowed_corpus_family_ids,
    resolve_source_scope,
    validate_tool_trajectory,
)


TERMINAL_TOOL_STATUSES = {
    "complete": "completed",
    "completed": "completed",
    "partial": "partial",
    "no_results": "no_results",
    "failed": "failed",
    "not_run": "skipped",
    "skipped": "skipped",
    "cancelled": "cancelled",
}
ARTIFACT_PATTERN = re.compile(
    r"/api/v2/session/([A-Za-z0-9_-]{8,128})/artifacts/([a-f0-9]{32})"
)
LOCATOR_KEYS = {
    "source_id",
    "chunk_id",
    "corpus_id",
    "document_id",
    "locator",
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _json_or_text(data: bytes) -> Any:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _event_data(event: dict[str, Any]) -> Any:
    return event.get("data") if isinstance(event, dict) else None


def _done_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if str(event.get("event") or "").lower() != "done":
            continue
        data = _event_data(event)
        if isinstance(data, dict):
            return data
    return {}


def _job_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if str(event.get("event") or "").lower() != "job":
            continue
        data = _event_data(event)
        if isinstance(data, dict):
            return data
    return {}


def _text_payload(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for event in events:
        if str(event.get("event") or "").lower() != "text":
            continue
        data = _event_data(event)
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            parts.append(data["content"])
    return "".join(parts)


def _capture_root() -> Path:
    raw = _env("CITRUS_EXPERIMENT_CAPTURE_ROOT")
    if not raw:
        raise RuntimeError("CITRUS_EXPERIMENT_CAPTURE_ROOT is required for B2")
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("CITRUS_EXPERIMENT_CAPTURE_ROOT must be an existing directory")
    return root


def _capture_dir(root: Path, run_id: str, attempt: int = 1) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", run_id):
        raise RuntimeError("run_id is unsafe for a capture path")
    path = (root / run_id / f"attempt_{attempt:02d}").resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("capture path escaped the configured root")
    if path.exists():
        raise RuntimeError(f"capture attempt already exists: {run_id}/{attempt}")
    path.mkdir(parents=True)
    return path


def _load_capture_index(
    root: Path,
    *,
    run_id: str,
    client_request_id: str,
    session_id: str,
    job_id: str,
    expected_answer: str,
    expected_model: str,
) -> tuple[dict[str, Any] | None, str | None]:
    index_env = _env("CITRUS_EXPERIMENT_CAPTURE_INDEX")
    if not index_env:
        return None, "provider_capture_index_not_configured"
    index_path = Path(index_env).expanduser().resolve()
    if not index_path.is_relative_to(root):
        return None, "provider_capture_index_outside_root"
    if not index_path.is_file():
        return None, "provider_capture_index_missing"
    matches: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        index_path.read_text(encoding="utf-8-sig").splitlines(),
        1,
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None, f"provider_capture_index_invalid_json:{line_number}"
        if not isinstance(row, dict):
            return None, f"provider_capture_index_non_object:{line_number}"
        if (
            str(row.get("run_id") or "") == run_id
            and str(row.get("client_request_id") or "") == client_request_id
            and str(row.get("session_id") or "") == session_id
            and str(row.get("job_id") or "") == job_id
        ):
            matches.append(row)
    if not matches:
        return None, "provider_capture_index_match_count:0"
    validated: list[dict[str, Any]] = []
    for row in matches:
        for field in (
            "server_request_id",
            "run_id",
            "client_request_id",
            "session_id",
            "job_id",
            "raw_response_path",
            "raw_response_sha256",
            "request_body_path",
            "request_sha256",
            "provider_response_id",
        ):
            if not str(row.get(field) or "").strip():
                return None, f"provider_capture_index_field_missing:{field}"
        if str(row.get("status") or "") != "CAPTURED":
            return None, "provider_capture_record_not_captured"
        if str(row.get("run_id")) != run_id:
            return None, "provider_capture_index_run_id_mismatch"
        if str(row.get("client_request_id")) != client_request_id:
            return None, "provider_capture_index_client_request_id_mismatch"
        if str(row.get("session_id")) != session_id:
            return None, "provider_capture_index_session_id_mismatch"
        if str(row.get("job_id")) != job_id:
            return None, "provider_capture_index_job_id_mismatch"
        for field in ("raw_response_path", "request_body_path"):
            candidate = Path(str(row[field])).expanduser()
            resolved = (
                candidate
                if candidate.is_absolute()
                else root / candidate
            ).resolve()
            if not resolved.is_relative_to(root):
                return None, f"provider_capture_path_outside_root:{field}"
            if not resolved.is_file():
                return None, f"provider_capture_file_missing:{field}"
            row[f"{field}_resolved"] = resolved
        if (
            sha256_file(row["raw_response_path_resolved"])
            != row["raw_response_sha256"]
        ):
            return None, "provider_capture_raw_response_sha_mismatch"
        if sha256_file(row["request_body_path_resolved"]) != row["request_sha256"]:
            return None, "provider_capture_request_sha_mismatch"
        raw_payload = _json_or_text(
            row["raw_response_path_resolved"].read_bytes()
        )
        request_payload = _json_or_text(
            row["request_body_path_resolved"].read_bytes()
        )
        if not isinstance(raw_payload, dict):
            return None, "provider_capture_raw_response_not_object"
        if not isinstance(request_payload, dict):
            return None, "provider_capture_request_body_not_object"
        captured_answer = response_content(raw_payload)
        captured_model = str(raw_payload.get("model") or "").strip()
        captured_id = str(
            raw_payload.get("id") or raw_payload.get("request_id") or ""
        ).strip()
        if captured_id != str(row["provider_response_id"]):
            return None, "provider_capture_response_id_mismatch"
        if expected_model and captured_model != expected_model:
            return None, "provider_capture_response_model_mismatch"
        if (
            str(request_payload.get("model") or "").strip()
            != str(row.get("requested_model") or "").strip()
        ):
            return None, "provider_capture_requested_model_mismatch"
        row["raw_response_payload"] = raw_payload
        row["request_body_payload"] = request_payload
        row["captured_answer"] = captured_answer
        row["captured_model"] = captured_model
        row["captured_response_id"] = captured_id
        validated.append(row)
    answer_matches = [
        row for row in validated if row["captured_answer"] == expected_answer
    ]
    if len(answer_matches) != 1:
        return None, f"provider_capture_answer_match_count:{len(answer_matches)}"
    row = answer_matches[0]
    if not row["captured_answer"]:
        return None, "provider_capture_answer_empty"
    if str(row["server_request_id"]) == str(row["client_request_id"]):
        return None, "provider_capture_server_and_client_request_ids_equal"
    row["index_path"] = index_path
    row["index_sha256"] = sha256_file(index_path)
    row["run_capture_records"] = validated
    return row, None


def _load_tool_registry() -> dict[str, dict[str, str]]:
    raw_path = _env("CITRUS_TOOL_REGISTRY_PATH")
    if not raw_path:
        raise RuntimeError("CITRUS_TOOL_REGISTRY_PATH is required for B2")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError("configured tool registry does not exist")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = payload.get("tools") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("tool registry must contain a tools list")
    registry: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("tool_name") or "").strip()
        if not name:
            continue
        registry[name] = {
            "tool_group": str(row.get("tool_group") or row.get("group") or ""),
            "tool_category": str(row.get("tool_category") or row.get("category") or ""),
            "registration_version": str(row.get("registration_version") or ""),
        }
    if len(registry) != 17 or any(
        not values["tool_group"]
        or not values["tool_category"]
        or not values["registration_version"]
        for values in registry.values()
    ):
        raise RuntimeError("tool registry must contain 17 fully identified tools")
    return registry


def _flatten_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_flatten_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_flatten_dicts(child))
    return found


def _citations_from_payload(value: Any) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _flatten_dicts(value):
        if not any(
            row.get(key) not in (None, "", [], {})
            for key in (
                "title",
                "doi",
                "url",
                "source_id",
                "chunk_id",
                "corpus_id",
                "record",
            )
        ):
            continue
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        citations.append(row)
    return citations


def _locator_rows(value: Any) -> list[dict[str, Any]]:
    return [
        row
        for row in _flatten_dicts(value)
        if any(row.get(key) not in (None, "", [], {}) for key in LOCATOR_KEYS)
    ]


def _tool_events(
    *,
    history: dict[str, Any],
    session_id: str,
    base_url: str,
    cookie: str,
    timeout: float,
    capture_dir: Path,
    capture_root: Path,
    registry: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    turns = history.get("turns")
    if not isinstance(turns, list) or not turns:
        return [], []
    target_turn = turns[-1] if isinstance(turns[-1], dict) else {}
    trace = target_turn.get("trace") if isinstance(target_turn, dict) else None
    tool_events: list[dict[str, Any]] = []
    retrieval_events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in trace if isinstance(trace, list) else []:
        if not isinstance(entry, dict):
            continue
        call_id = str(entry.get("tool_call_id") or "").strip()
        if not call_id or call_id in seen_ids:
            continue
        seen_ids.add(call_id)
        status, _headers, body = get_json(
            base_url
            + f"/api/v2/session/{session_id}/tool-result/{call_id}",
            cookie=cookie,
            timeout=timeout,
            user_agent="CitrusScholarExperiment/B2",
        )
        if status != 200:
            raise RuntimeError(f"tool-result HTTP {status} for {call_id}")
        detail = json.loads(body.decode("utf-8-sig"))
        tool_name = str(detail.get("tool_name") or entry.get("name") or "tool")
        registered = registry.get(tool_name, {})
        result_text = str(detail.get("result_text") or "")
        result_value: Any
        try:
            result_value = json.loads(result_text)
        except json.JSONDecodeError:
            result_value = {"result_text": result_text}
        result_rel = (
            f"{capture_dir.relative_to(capture_root).as_posix()}"
            f"/tool_results/{call_id}.json"
        )
        result_bytes = _json_bytes(result_value)
        write_once(capture_root / result_rel, result_bytes)
        try:
            arguments: Any = json.loads(str(detail.get("args_json") or "{}"))
        except json.JSONDecodeError:
            arguments = {"unparsed_args": str(detail.get("args_json") or "")}
        tool_event = {
            "tool_name": tool_name,
            "tool_group": registered.get("tool_group", ""),
            "tool_category": registered.get("tool_category", ""),
            "registration_version": registered.get("registration_version", ""),
            "tool_call_id": call_id,
            "arguments": arguments,
            "status": TERMINAL_TOOL_STATUSES.get(
                str(detail.get("status") or entry.get("status") or ""),
                "failed",
            ),
            "elapsed_ms": int(detail.get("elapsed_ms") or entry.get("elapsed_ms") or 0),
            "result": result_value,
            "result_ref": result_rel,
            "result_sha256": sha256_bytes(result_bytes),
            "truncated": bool(detail.get("truncated")),
            "full_result_sha256": str(detail.get("full_sha256") or ""),
        }
        tool_events.append(tool_event)
        rows = _locator_rows(result_value)
        if rows:
            retrieval_events.append(
                {
                    "sequence": len(retrieval_events) + 1,
                    "tool_name": tool_name,
                    "tool_call_id": call_id,
                    "query": arguments.get("query") if isinstance(arguments, dict) else None,
                    "results": rows,
                }
            )
    retrieval_rel = (
        f"{capture_dir.relative_to(capture_root).as_posix()}/retrieval_events.json"
    )
    write_once(capture_root / retrieval_rel, _json_bytes(retrieval_events))
    return tool_events, retrieval_events


def _download_artifacts(
    *,
    texts: list[str],
    session_id: str,
    base_url: str,
    cookie: str,
    timeout: float,
    capture_dir: Path,
    capture_root: Path,
) -> tuple[list[str], dict[str, str]]:
    refs = sorted(
        {
            match.group(0)
            for text in texts
            for match in ARTIFACT_PATTERN.finditer(text)
            if match.group(1) == session_id
        }
    )
    paths: list[str] = []
    hashes: dict[str, str] = {}
    for ref in refs:
        status, _headers, data = get_bytes(
            base_url + ref,
            cookie=cookie,
            timeout=timeout,
            user_agent="CitrusScholarExperiment/B2",
        )
        if status != 200 or not data:
            raise RuntimeError(f"artifact download failed: HTTP {status}")
        artifact_id = ref.rsplit("/", 1)[-1]
        relative = (
            f"{capture_dir.relative_to(capture_root).as_posix()}"
            f"/artifacts/{artifact_id}.bin"
        )
        write_once(capture_root / relative, data)
        digest = sha256_bytes(data)
        paths.append(relative)
        hashes[relative] = digest
    return paths, hashes


def _backend_logs(
    *,
    bound_ids: set[str],
    required_id: str,
    capture_dir: Path,
    capture_root: Path,
) -> list[dict[str, str]]:
    raw = _env("CITRUS_EXPERIMENT_BACKEND_LOG_PATHS")
    if not raw:
        return []
    refs: list[dict[str, str]] = []
    sensitive = re.compile(
        r"(?i)\b(authorization|cookie|api[_-]?key|password|token)\b"
        r"(\s*[:=]\s*)[^\s,;]+"
    )
    for index, token in enumerate(raw.split(os.pathsep), start=1):
        path = Path(token).expanduser().resolve()
        if not path.is_file():
            raise RuntimeError("configured backend log does not exist")
        text = path.read_text(encoding="utf-8", errors="replace")
        observed = sorted(value for value in bound_ids if value and value in text)
        if required_id not in text or not observed:
            raise RuntimeError(
                "configured backend log is not bound to the server request"
            )
        matching_lines = [
            sensitive.sub(r"\1\2[REDACTED]", line)
            for line in text.splitlines()
            if any(value and value in line for value in bound_ids)
        ]
        if not matching_lines:
            raise RuntimeError("configured backend log has no bound record")
        relative = (
            f"{capture_dir.relative_to(capture_root).as_posix()}"
            f"/logs/backend_{index:02d}.log"
        )
        snapshot = ("\n".join(matching_lines) + "\n").encode("utf-8")
        write_once(capture_root / relative, snapshot)
        refs.append(
            {
                "path": relative,
                "sha256": sha256_bytes(snapshot),
                "reference": "backend_log_matched_ids:" + ",".join(observed),
                "source_path": str(path),
                "source_sha256": sha256_file(path),
            }
        )
    return refs


def _failed_result(
    *,
    run_id: str,
    client_request_id: str,
    session_id: str,
    started_utc: str,
    started_monotonic: float,
    blocker: str,
    final_answer: str = "",
    raw_answer: str = "",
) -> dict[str, Any]:
    return {
        "status": "failed",
        "final_answer": final_answer,
        "raw_answer": raw_answer,
        "failure_class": blocker,
        "errors": [blocker],
        "run_id": run_id,
        "client_request_id": client_request_id,
        "server_request_id": "",
        "session_id": session_id,
        "requested_model": "",
        "response_model": "",
        "response_id": "",
        "request_body": {},
        "request_sha256": "",
        "raw_response": {},
        "raw_response_sha256": "",
        "provider_http_status": 0,
        "tool_calls": [],
        "citations": [],
        "artifact_paths": [],
        "artifact_sha256": {},
        "tokens_in": None,
        "tokens_out": None,
        "api_cost": None,
        "list_cost": None,
        "allowance": None,
        "elapsed_ms": round((time.monotonic() - started_monotonic) * 1000, 1),
        "model_calls": None,
        "total_input_tokens": None,
        "total_output_tokens": None,
        "retrieval_calls": None,
        "context_tokens_used": None,
        "tool_call_count": None,
        "budget_status": "UNKNOWN",
        "sse_events": [],
        "retrieval_events": [],
        "sse_sha256": "",
        "history_sha256": "",
        "backend_log_refs": [],
        "started_utc": started_utc,
        "finished_utc": utc_now(),
        "implementation_notes": (
            "The real app call was fail-closed because a required observation "
            "or capture binding was unavailable."
        ),
    }


def run_case(case: dict, config: dict) -> dict:
    started = time.monotonic()
    started_utc = utc_now()
    run_id = str(case.get("run_id") or uuid.uuid4().hex)
    client_request_id = run_id
    timeout = float(config.get("timeout_seconds") or 600)
    capture_root = _capture_root()
    scope_policy = resolve_source_scope(case.get("source_scope"))
    allowed_families = resolve_allowed_corpus_family_ids(
        case.get("allowed_corpus_family_ids"),
        scope_policy,
    )
    try:
        auth = authenticate_experiment_account(
            config,
            user_agent="CitrusScholarExperiment/B2",
        )
    except Exception as exc:
        return _failed_result(
            run_id=run_id,
            client_request_id=client_request_id,
            session_id="",
            started_utc=started_utc,
            started_monotonic=started,
            blocker=f"experiment_authentication_failed:{type(exc).__name__}",
        )
    base_url = str(auth["base_url"])
    cookie = str(auth["cookie"])

    status, _headers, body = post_json(
        base_url + "/api/v1/session/new",
        {},
        cookie=cookie,
        origin=base_url,
        timeout=timeout,
        user_agent="CitrusScholarExperiment/B2",
    )
    if status != 200:
        return _failed_result(
            run_id=run_id,
            client_request_id=client_request_id,
            session_id="",
            started_utc=started_utc,
            started_monotonic=started,
            blocker=f"experiment_new_session_http_{status}",
        )
    session_id = str(json.loads(body.decode("utf-8-sig")).get("session_id") or "")
    if not session_id:
        raise RuntimeError("new session response has no session_id")
    capture_dir = _capture_dir(capture_root, run_id)
    write_once(
        capture_dir / "auth_flow.json",
        _json_bytes(
            {
                "auth_flow": auth["auth_flow"],
                "authenticated_user_id_sha256": auth["identity_sha256"],
                "authenticated_role": auth["role"],
                "auth_me_sha256": auth["auth_me_sha256"],
                "cookie_received": True,
                "cookie_persisted": False,
                "new_session_http_status": status,
                "session_id": session_id,
            }
        ),
    )

    chat_payload = {
        "session_id": session_id,
        "query": str(case["prompt"]),
        "client_request_id": client_request_id,
        "attachments": attachment_payload(case),
    }
    app_request_bytes = _json_bytes(chat_payload)
    write_once(capture_dir / "request.json", app_request_bytes)
    status, _response_headers, sse_bytes = post_stream(
        base_url + "/api/v2/chat",
        chat_payload,
        cookie=cookie,
        origin=base_url,
        timeout=timeout,
        user_agent="CitrusScholarExperiment/B2",
    )
    write_once(capture_dir / "sse.txt", sse_bytes)
    if status != 200:
        return _failed_result(
            run_id=run_id,
            client_request_id=client_request_id,
            session_id=session_id,
            started_utc=started_utc,
            started_monotonic=started,
            blocker=f"experiment_chat_http_{status}",
        )
    events = parse_sse_bytes(sse_bytes)
    done = _done_payload(events)
    job = _job_payload(events)
    final_answer = str(done.get("answer") or "") or _text_payload(events)
    job_id = str(done.get("job_id") or job.get("job_id") or "")

    status, _headers, history_bytes = get_json(
        base_url + f"/api/v2/session/{session_id}/messages?limit=200",
        cookie=cookie,
        timeout=timeout,
        user_agent="CitrusScholarExperiment/B2",
    )
    if status != 200:
        raise RuntimeError(f"history read failed: HTTP {status}")
    write_once(capture_dir / "history.json", history_bytes)
    history = json.loads(history_bytes.decode("utf-8-sig"))
    persisted_answer = ""
    history_request_id = ""
    turns = history.get("turns")
    if isinstance(turns, list) and turns and isinstance(turns[-1], dict):
        answer = turns[-1].get("answer")
        if isinstance(answer, dict):
            persisted_answer = str(answer.get("content") or "")
        history_request_id = str(turns[-1].get("request_id") or "")

    status, _headers, citations_bytes = get_json(
        base_url + f"/api/v2/session/{session_id}/citations",
        cookie=cookie,
        timeout=timeout,
        user_agent="CitrusScholarExperiment/B2",
    )
    if status != 200:
        raise RuntimeError(f"citation read failed: HTTP {status}")
    write_once(capture_dir / "citations.json", citations_bytes)
    citations_payload = json.loads(citations_bytes.decode("utf-8-sig"))
    citation_payloads = [
        _event_data(event)
        for event in events
        if str(event.get("event") or "").lower() == "citations"
    ]
    citations = _citations_from_payload(citation_payloads)
    citations.extend(_citations_from_payload(citations_payload))

    registry = _load_tool_registry()
    tool_events, retrieval_events = _tool_events(
        history=history,
        session_id=session_id,
        base_url=base_url,
        cookie=cookie,
        timeout=timeout,
        capture_dir=capture_dir,
        capture_root=capture_root,
        registry=registry,
    )
    tool_texts = [
        json.dumps(event.get("result"), ensure_ascii=False, default=str)
        for event in tool_events
    ]
    artifact_paths, artifact_sha256 = _download_artifacts(
        texts=[sse_bytes.decode("utf-8", errors="replace"), final_answer, *tool_texts],
        session_id=session_id,
        base_url=base_url,
        cookie=cookie,
        timeout=timeout,
        capture_dir=capture_dir,
        capture_root=capture_root,
    )

    capture, capture_error = _load_capture_index(
        capture_root,
        run_id=run_id,
        client_request_id=client_request_id,
        session_id=session_id,
        job_id=job_id,
        expected_answer=final_answer,
        expected_model=str(
            config.get("actual_model_id")
            or config.get("candidate_model_id")
            or ""
        ),
    )
    if capture_error or capture is None:
        return _failed_result(
            run_id=run_id,
            client_request_id=client_request_id,
            session_id=session_id,
            started_utc=started_utc,
            started_monotonic=started,
            blocker=capture_error or "provider_capture_index_missing",
            final_answer=final_answer,
            raw_answer="",
        )

    raw_response = capture["raw_response_payload"]
    raw_answer = capture["captured_answer"]
    response_id = capture["captured_response_id"]
    response_model = capture["captured_model"]
    tokens_in, tokens_out = response_usage(raw_response)
    request_body = capture["request_body_payload"]
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    capture_records = capture.get("run_capture_records") or [capture]
    provider_usage = aggregate_provider_usage(
        [
            record["raw_response_payload"]
            for record in capture_records
            if isinstance(record.get("raw_response_payload"), dict)
        ]
    )
    provider_capture_records = [
        {
            "capture_id": str(record.get("capture_id") or ""),
            "provider_response_id": str(record.get("provider_response_id") or ""),
            "requested_model": str(record.get("requested_model") or ""),
            "response_model": str(record.get("response_model") or ""),
            "request_sha256": str(record.get("request_sha256") or ""),
            "raw_response_sha256": str(record.get("raw_response_sha256") or ""),
            "raw_response_path": str(record.get("raw_response_path") or ""),
            "http_status": int(record.get("http_status") or 0),
            "usage": (
                record["raw_response_payload"].get("usage")
                if isinstance(record.get("raw_response_payload"), dict)
                and isinstance(record["raw_response_payload"].get("usage"), dict)
                else None
            ),
        }
        for record in capture_records
    ]
    retrieval_calls = retrieval_tool_call_count(tool_events)
    scope_errors = validate_tool_trajectory(
        scope_policy,
        tool_events,
        allowed_corpus_family_ids=allowed_families,
    )
    server_request_id = str(capture["server_request_id"])
    backend_log_refs = _backend_logs(
        bound_ids={
            server_request_id,
            client_request_id,
            run_id,
            job_id,
            response_id,
        },
        required_id=server_request_id,
        capture_dir=capture_dir,
        capture_root=capture_root,
    )
    capture_prefix = capture_dir.relative_to(capture_root).as_posix()
    semantic_status = (
        "completed"
        if final_answer.strip() and persisted_answer == final_answer
        else "partial"
        if final_answer.strip()
        else "no_results"
    )
    budget = budget_result(
        model_calls=provider_usage["model_calls"],
        tokens_in=provider_usage["tokens_in"],
        tokens_out=provider_usage["tokens_out"],
        retrieval_calls=retrieval_calls,
        context_tokens=provider_usage["tokens_in"],
        tool_calls=len(tool_events),
        elapsed_ms=elapsed_ms,
        config=config,
    )
    terminal_status, failure_class, gate_errors = terminal_status_after_gates(
        semantic_status,
        budget=budget,
        gate_errors=scope_errors,
    )
    result = {
        "status": terminal_status,
        "semantic_status": semantic_status,
        "failure_class": failure_class,
        "errors": gate_errors,
        "final_answer": final_answer,
        "raw_answer": raw_answer,
        "run_id": run_id,
        "client_request_id": client_request_id,
        "server_request_id": server_request_id,
        "session_id": session_id,
        "job_id": job_id,
        "requested_model": str(
            request_body.get("model") if isinstance(request_body, dict) else ""
        ),
        "response_model": response_model,
        "response_id": response_id,
        "provider_response_id": response_id,
        "provider": "Alibaba Cloud Bailian",
        "auth_flow": auth["auth_flow"],
        "authenticated_user_id_sha256": auth["identity_sha256"],
        "authenticated_role": auth["role"],
        "auth_me_sha256": auth["auth_me_sha256"],
        "source_scope_policy": scope_policy.as_dict(),
        "allowed_corpus_family_ids": allowed_families,
        "source_scope_validation": {
            "status": "PASS" if not scope_errors else "FAIL",
            "errors": scope_errors,
        },
        "request_body": request_body,
        "request_sha256": str(capture["request_sha256"]),
        "app_request": chat_payload,
        "app_request_sha256": sha256_bytes(app_request_bytes),
        "app_request_ref": f"{capture_prefix}/request.json",
        "raw_response_ref": str(capture["raw_response_path"]),
        "raw_response": raw_response,
        "raw_response_sha256": str(capture["raw_response_sha256"]),
        "provider_http_status": 200,
        "response_headers": {},
        "tool_calls": tool_events,
        "citations": citations,
        "artifact_paths": artifact_paths,
        "artifact_sha256": artifact_sha256,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "api_cost": provider_usage["api_cost"],
        "list_cost": provider_usage["list_cost"],
        "allowance": provider_usage["allowance"],
        "elapsed_ms": elapsed_ms,
        "model_calls": provider_usage["model_calls"],
        "total_input_tokens": provider_usage["tokens_in"],
        "total_output_tokens": provider_usage["tokens_out"],
        "retrieval_calls": retrieval_calls,
        "context_tokens_used": provider_usage["tokens_in"],
        "context_token_metric": "sum_provider_prompt_tokens_proxy",
        "tool_call_count": len(tool_events),
        "budget_status": budget["status"],
        "budget": budget,
        "provider_usage": provider_usage,
        "provider_capture_records": provider_capture_records,
        "sse_events": events,
        "retrieval_events": retrieval_events,
        "sse_ref": f"{capture_prefix}/sse.txt",
        "sse_sha256": sha256_bytes(sse_bytes),
        "history_ref": f"{capture_prefix}/history.json",
        "history_sha256": sha256_bytes(history_bytes),
        "history_answer": persisted_answer,
        "history_session_id": str(history.get("session_id") or ""),
        "history_request_id": history_request_id,
        "history_job_id": job_id,
        "citations_ref": f"{capture_prefix}/citations.json",
        "citations_sha256": sha256_bytes(citations_bytes),
        "provider_capture_index_ref": capture["index_path"]
        .resolve()
        .relative_to(capture_root)
        .as_posix()
        if capture["index_path"].resolve().is_relative_to(capture_root)
        else str(capture["index_path"]),
        "provider_capture_index_sha256": capture["index_sha256"],
        "backend_log_refs": backend_log_refs,
        "evidence_root": str(capture_root),
        "started_utc": started_utc,
        "finished_utc": utc_now(),
        "implementation_notes": (
            "Real login, new session, chat SSE, history readback, citation "
            "readback, tool-result readback, artifact download, and provider "
            "capture binding. No credential is persisted."
        ),
    }
    write_json_once(capture_dir / "result.json", result)
    return result

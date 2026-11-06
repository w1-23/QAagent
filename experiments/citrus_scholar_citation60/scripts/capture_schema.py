"""Fail-closed validation for one adapter execution record."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


TERMINAL_STATUSES = {
    "completed",
    "partial",
    "failed",
    "no_results",
    "cancelled",
    "skipped",
}
SEMANTIC_STATUSES = {"completed", "partial", "no_results"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
RETRIEVAL_TOOL_NAMES = {
    "local_hybrid_search",
    "local_bm25_search",
    "local_dense_search",
    "deepseek_search",
    "deepseek_fetch",
    "deepseek_fetch_paper",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_reference(
    response: Mapping[str, Any],
    ref_field: str,
    sha_field: str,
    errors: list[str],
) -> Path | None:
    root_text = str(response.get("evidence_root") or "").strip()
    if not root_text:
        errors.append("b2_evidence_root_missing")
        return None
    root = Path(root_text).expanduser().resolve()
    if not root.is_dir():
        errors.append("b2_evidence_root_invalid")
        return None
    ref_text = str(response.get(ref_field) or "").strip()
    if not ref_text:
        errors.append(f"b2_{ref_field}_missing")
        return None
    candidate = Path(ref_text).expanduser()
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not path.is_relative_to(root):
        errors.append(f"b2_{ref_field}_outside_root")
        return None
    if not path.is_file() or path.stat().st_size <= 0:
        errors.append(f"b2_{ref_field}_file_missing")
        return None
    expected = str(response.get(sha_field) or "")
    if not SHA256.fullmatch(expected):
        errors.append(f"b2_{sha_field}_invalid")
    elif _sha256_file(path) != expected:
        errors.append(f"b2_{sha_field}_mismatch")
    return path


def _verify_item_reference(
    root: Path,
    path_text: str,
    sha_text: str,
    *,
    label: str,
    errors: list[str],
) -> Path | None:
    """Verify an evidence item whose path is relative to the capture root."""
    if not path_text:
        errors.append(f"{label}_path_missing")
        return None
    candidate = Path(path_text).expanduser()
    if candidate.is_absolute():
        errors.append(f"{label}_path_not_relative")
        return None
    path = (root / candidate).resolve()
    if not path.is_relative_to(root):
        errors.append(f"{label}_outside_root")
        return None
    if not path.is_file() or path.stat().st_size <= 0:
        errors.append(f"{label}_file_missing")
        return None
    if not SHA256.fullmatch(sha_text):
        errors.append(f"{label}_sha_invalid")
    elif _sha256_file(path) != sha_text:
        errors.append(f"{label}_sha_mismatch")
    return path


def _validate_b2_provider_capture(
    response: Mapping[str, Any],
    index_path: Path,
    errors: list[str],
) -> None:
    """Cross-bind the B2 response to the loopback provider-capture index."""
    root_text = str(response.get("evidence_root") or "").strip()
    if not root_text:
        errors.append("b2_evidence_root_missing")
        return
    root = Path(root_text).expanduser().resolve()
    try:
        raw_lines = index_path.read_text(encoding="utf-8-sig").splitlines()
        records = [
            json.loads(line)
            for line in raw_lines
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"b2_provider_capture_index_unparseable:{type(exc).__name__}")
        return
    if not all(isinstance(record, dict) for record in records):
        errors.append("b2_provider_capture_index_non_object")
        return
    matches = [
        record
        for record in records
        if str(record.get("run_id") or "") == str(response.get("run_id") or "")
        and str(record.get("client_request_id") or "")
        == str(response.get("client_request_id") or "")
        and str(record.get("server_request_id") or "")
        == str(response.get("server_request_id") or "")
        and str(record.get("session_id") or "")
        == str(response.get("session_id") or "")
        and str(record.get("job_id") or "") == str(response.get("job_id") or "")
    ]
    if not matches:
        errors.append("b2_provider_capture_match_count:0")
        return
    final_record: dict[str, Any] | None = None
    for index, record in enumerate(matches):
        for field in (
            "provider_response_id",
            "requested_model",
            "response_model",
            "request_sha256",
            "raw_response_sha256",
            "raw_response_path",
            "request_body_path",
        ):
            if not str(record.get(field) or "").strip():
                errors.append(f"b2_provider_capture_field_missing:{index}:{field}")
        _verify_item_reference(
            root,
            str(record.get("raw_response_path") or ""),
            str(record.get("raw_response_sha256") or ""),
            label=f"b2_provider_capture_raw_response:{index}",
            errors=errors,
        )
        request_path = _verify_item_reference(
            root,
            str(record.get("request_body_path") or ""),
            str(record.get("request_sha256") or ""),
            label=f"b2_provider_capture_request:{index}",
            errors=errors,
        )
        if request_path is not None:
            try:
                archived_request = json.loads(
                    request_path.read_text(encoding="utf-8-sig")
                )
            except Exception:
                archived_request = None
            if not isinstance(archived_request, dict):
                errors.append(f"b2_provider_capture_request_invalid:{index}")
        if str(record.get("provider_response_id") or "") == str(
            response.get("response_id") or ""
        ):
            if final_record is not None:
                errors.append("b2_provider_capture_final_record_duplicate")
            final_record = record
    if final_record is None:
        errors.append("b2_provider_capture_final_response_missing")
    else:
        if str(final_record.get("request_sha256") or "") != str(
            response.get("request_sha256") or ""
        ):
            errors.append("b2_provider_capture_request_sha_mismatch")
        if str(final_record.get("raw_response_sha256") or "") != str(
            response.get("raw_response_sha256") or ""
        ):
            errors.append("b2_provider_capture_raw_response_sha_mismatch")
        if str(final_record.get("requested_model") or "") != str(
            response.get("requested_model") or ""
        ):
            errors.append("b2_provider_capture_requested_model_mismatch")
        if str(final_record.get("response_model") or "") != str(
            response.get("response_model") or ""
        ):
            errors.append("b2_provider_capture_response_model_mismatch")
        request_path = _verify_item_reference(
            root,
            str(final_record.get("request_body_path") or ""),
            str(final_record.get("request_sha256") or ""),
            label="b2_provider_capture_final_request",
            errors=errors,
        )
        if request_path is not None:
            try:
                archived_request = json.loads(
                    request_path.read_text(encoding="utf-8-sig")
                )
            except Exception:
                archived_request = None
            if archived_request != response.get("request_body"):
                errors.append("b2_provider_capture_request_body_differs_from_record")
    records = response.get("provider_capture_records")
    if isinstance(records, list) and len(records) != len(matches):
        errors.append("b2_provider_capture_record_count_mismatch")


def _validate_b2_sse(response: Mapping[str, Any], errors: list[str]) -> None:
    events = response.get("sse_events")
    if not isinstance(events, list) or not events:
        errors.append("b2_sse_events_missing")
        return
    normalized: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"b2_sse_event_invalid:{index}")
            continue
        if not isinstance(event.get("sequence"), int):
            errors.append(f"b2_sse_sequence_invalid:{index}")
        if not str(event.get("event") or "").strip():
            errors.append(f"b2_sse_event_name_missing:{index}")
        normalized.append(event)
    if [event.get("sequence") for event in normalized] != list(
        range(1, len(normalized) + 1)
    ):
        errors.append("b2_sse_sequence_not_contiguous")
    done_events = [
        event
        for event in normalized
        if str(event.get("event") or "").lower() == "done"
    ]
    if len(done_events) != 1:
        errors.append("b2_sse_done_count_not_one")
    elif not isinstance(done_events[0].get("data"), dict):
        errors.append("b2_sse_done_payload_invalid")
    start_events = [
        event
        for event in normalized
        if str(event.get("event") or "").lower() == "answer_start"
    ]
    if len(start_events) != 1:
        errors.append("b2_sse_answer_start_count_not_one")
    text_events = [
        event
        for event in normalized
        if str(event.get("event") or "").lower() == "text"
    ]
    if not text_events:
        errors.append("b2_sse_text_events_missing")
    if text_events:
        text = "".join(
            str((event.get("data") or {}).get("content") or "")
            for event in text_events
            if isinstance(event.get("data"), dict)
        )
        if text != str(response.get("final_answer") or ""):
            errors.append("b2_sse_text_differs_from_final")
    if done_events and isinstance(done_events[0].get("data"), dict):
        done = done_events[0]["data"]
        if str(done.get("answer") or "") != str(
            response.get("final_answer") or ""
        ):
            errors.append("b2_sse_done_answer_differs_from_final")
        if str(done.get("session_id") or "") != str(
            response.get("session_id") or ""
        ):
            errors.append("b2_sse_done_session_id_mismatch")
        if str(done.get("job_id") or "") != str(response.get("job_id") or ""):
            errors.append("b2_sse_done_job_id_mismatch")
    job_events = [
        event
        for event in normalized
        if str(event.get("event") or "").lower() == "job"
    ]
    if len(job_events) != 1:
        errors.append("b2_sse_job_count_not_one")
    elif isinstance(job_events[0].get("data"), dict):
        job = job_events[0]["data"]
        if str(job.get("session_id") or "") != str(
            response.get("session_id") or ""
        ):
            errors.append("b2_sse_job_session_id_mismatch")
        if str(job.get("job_id") or "") != str(response.get("job_id") or ""):
            errors.append("b2_sse_job_id_mismatch")


def validate_capture(
    response: Mapping[str, Any],
    *,
    condition: str,
    require_raw_provider: bool = True,
) -> list[str]:
    """Return deterministic schema errors; never coerce missing evidence to success."""
    errors: list[str] = []
    status = str(response.get("status") or "")
    if status not in TERMINAL_STATUSES:
        errors.append("status_invalid")
    if condition not in {"B0", "B1", "B2"}:
        errors.append("condition_invalid")
    for field in ("final_answer", "raw_answer"):
        if not isinstance(response.get(field), str):
            errors.append(f"{field}_not_string")
    for field in ("tool_calls", "citations", "artifact_paths"):
        if not isinstance(response.get(field), list):
            errors.append(f"{field}_not_list")
    if not isinstance(response.get("artifact_sha256"), dict):
        errors.append("artifact_sha256_not_object")

    raw_response_present = isinstance(response.get("raw_response"), dict) and bool(
        response.get("raw_response")
    )
    validate_provider = require_raw_provider and (
        status in SEMANTIC_STATUSES or raw_response_present
    )
    if not validate_provider:
        return errors

    if status in {"completed", "partial"} and not str(
        response.get("final_answer") or ""
    ).strip():
        errors.append("completed_answer_empty")
    for field in (
        "requested_model",
        "response_model",
        "response_id",
        "provider_response_id",
    ):
        if not str(response.get(field) or "").strip():
            errors.append(f"{field}_missing")
    if str(response.get("provider_response_id") or "") != str(
        response.get("response_id") or ""
    ):
        errors.append("provider_response_id_differs_from_response_id")
    for field in ("request_body", "raw_response"):
        if not isinstance(response.get(field), (dict, list)):
            errors.append(f"{field}_missing")
    for field in ("request_sha256", "raw_response_sha256"):
        if not SHA256.fullmatch(str(response.get(field) or "")):
            errors.append(f"{field}_invalid")
    for field in ("tokens_in", "tokens_out"):
        value = response.get(field)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{field}_invalid")
    if not isinstance(response.get("provider_http_status"), int):
        errors.append("provider_http_status_missing")
    if status == "completed" and not str(response.get("raw_answer") or "").strip():
        errors.append("raw_answer_empty")

    budget_status = str(response.get("budget_status") or "")
    if budget_status not in {
        "WITHIN_BUDGET",
        "BUDGET_VIOLATION",
        "RESOURCE_ACCOUNTING_UNKNOWN",
        "UNKNOWN",
    }:
        errors.append("budget_status_invalid")
    budget = response.get("budget")
    if not isinstance(budget, dict):
        errors.append("budget_evidence_missing")
    else:
        if budget.get("status") != budget_status:
            errors.append("budget_status_differs_from_evidence")
        if status == "completed" and budget_status != "WITHIN_BUDGET":
            errors.append("completed_without_within_budget")
        if failure_class := str(response.get("failure_class") or ""):
            if failure_class == "BUDGET_VIOLATION" and budget_status != "BUDGET_VIOLATION":
                errors.append("budget_failure_class_mismatch")
            if (
                failure_class == "RESOURCE_ACCOUNTING_UNKNOWN"
                and budget_status != "RESOURCE_ACCOUNTING_UNKNOWN"
            ):
                errors.append("resource_unknown_failure_class_mismatch")

    scope_policy = response.get("source_scope_policy")
    scope_validation = response.get("source_scope_validation")
    if not isinstance(scope_policy, dict) or not scope_policy:
        errors.append("source_scope_policy_missing")
    if not isinstance(scope_validation, dict):
        errors.append("source_scope_validation_missing")
    elif scope_validation.get("status") not in {"PASS", "FAIL"}:
        errors.append("source_scope_validation_status_invalid")
    elif status == "completed" and scope_validation.get("status") != "PASS":
        errors.append("completed_with_source_scope_failure")
    allowed_families = response.get("allowed_corpus_family_ids")
    if (
        not isinstance(allowed_families, list)
        or any(not isinstance(item, str) or not item.strip() for item in allowed_families)
        or len({item.strip().casefold() for item in allowed_families})
        != len(allowed_families)
    ):
        errors.append("allowed_corpus_family_ids_invalid")
    elif isinstance(scope_policy, dict):
        if scope_policy.get("allow_local") is True and not allowed_families:
            errors.append("allowed_corpus_family_ids_empty_for_local_scope")
        if scope_policy.get("allow_local") is False and allowed_families:
            errors.append("allowed_corpus_family_ids_nonempty_for_nonlocal_scope")

    model_calls = response.get("model_calls")
    if not isinstance(model_calls, int) or isinstance(model_calls, bool) or model_calls < 1:
        errors.append("model_calls_invalid")
    for field in ("total_input_tokens", "total_output_tokens"):
        value = response.get(field)
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{field}_invalid")
    retrieval_calls = response.get("retrieval_calls")
    if (
        not isinstance(retrieval_calls, int)
        or isinstance(retrieval_calls, bool)
        or retrieval_calls < 0
    ):
        errors.append("retrieval_calls_invalid")
    else:
        tool_calls = response.get("tool_calls")
        if isinstance(tool_calls, list):
            actual_retrieval_calls = sum(
                1
                for event in tool_calls
                if isinstance(event, dict)
                and str(event.get("tool_name") or "") in RETRIEVAL_TOOL_NAMES
            )
            if retrieval_calls != actual_retrieval_calls:
                errors.append("retrieval_calls_differ_from_tool_trajectory")
    context_tokens = response.get("context_tokens_used")
    if (
        context_tokens is not None
        and (
            not isinstance(context_tokens, int)
            or isinstance(context_tokens, bool)
            or context_tokens < 0
        )
    ):
        errors.append("context_tokens_used_invalid")
    if budget_status == "WITHIN_BUDGET" and context_tokens is None:
        errors.append("context_tokens_used_missing")
    if (
        condition in {"B1", "B2"}
        and response.get("context_token_metric")
        != "sum_provider_prompt_tokens_proxy"
    ):
        errors.append("context_token_metric_invalid")

    if condition == "B2":
        for field in (
            "server_request_id",
            "client_request_id",
            "session_id",
            "run_id",
            "job_id",
            "sse_ref",
            "sse_sha256",
            "history_ref",
            "history_sha256",
            "app_request_ref",
            "app_request_sha256",
            "raw_response_ref",
            "citations_ref",
            "citations_sha256",
            "provider_capture_index_ref",
            "provider_capture_index_sha256",
            "history_session_id",
            "history_request_id",
        ):
            if not str(response.get(field) or "").strip():
                errors.append(f"b2_{field}_missing")
        if response.get("sse_sha256") and not SHA256.fullmatch(
            str(response.get("sse_sha256"))
        ):
            errors.append("b2_sse_sha256_invalid")
        if response.get("history_sha256") and not SHA256.fullmatch(
            str(response.get("history_sha256"))
        ):
            errors.append("b2_history_sha256_invalid")
        if not isinstance(response.get("sse_events"), list):
            errors.append("b2_sse_events_missing")
        if not isinstance(response.get("retrieval_events"), list):
            errors.append("b2_retrieval_events_missing")
        capture_records = response.get("provider_capture_records")
        if not isinstance(capture_records, list) or not capture_records:
            errors.append("b2_provider_capture_records_missing")
        elif isinstance(model_calls, int) and len(capture_records) != model_calls:
            errors.append("b2_provider_capture_record_count_differs_from_model_calls")
        if str(response.get("server_request_id") or "") == str(
            response.get("client_request_id") or ""
        ):
            errors.append("b2_server_and_client_request_ids_equal")
        if str(response.get("history_answer") or "") != str(
            response.get("final_answer") or ""
        ):
            errors.append("b2_history_answer_differs_from_final")
        if str(response.get("history_session_id") or "") != str(
            response.get("session_id") or ""
        ):
            errors.append("b2_history_session_id_mismatch")
        if str(response.get("history_request_id") or "") != str(
            response.get("client_request_id") or ""
        ):
            errors.append("b2_history_request_id_mismatch")
        app_request = response.get("app_request")
        if not isinstance(app_request, dict):
            errors.append("b2_app_request_missing")
        else:
            if str(app_request.get("client_request_id") or "") != str(
                response.get("client_request_id") or ""
            ):
                errors.append("b2_app_request_client_request_id_mismatch")
            if str(app_request.get("session_id") or "") != str(
                response.get("session_id") or ""
            ):
                errors.append("b2_app_request_session_id_mismatch")
        backend_refs = response.get("backend_log_refs")
        if not isinstance(backend_refs, list) or not backend_refs:
            errors.append("b2_backend_log_refs_missing")
        else:
            root_text = str(response.get("evidence_root") or "").strip()
            root = Path(root_text).expanduser().resolve() if root_text else None
            bound = {
                str(value)
                for value in (
                    response.get("server_request_id"),
                    response.get("client_request_id"),
                    response.get("run_id"),
                    response.get("job_id"),
                    response.get("response_id"),
                )
                if str(value or "").strip()
            }
            for index, item in enumerate(backend_refs):
                if not isinstance(item, dict):
                    errors.append(f"b2_backend_log_ref_invalid:{index}")
                    continue
                if root is None:
                    errors.append("b2_evidence_root_missing")
                    break
                path_text = str(item.get("path") or "").strip()
                digest = str(item.get("sha256") or "")
                path = _verify_item_reference(
                    root,
                    path_text,
                    digest,
                    label=f"b2_backend_log:{index}",
                    errors=errors,
                )
                if path is None:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                if str(response.get("server_request_id") or "") not in text:
                    errors.append(f"b2_backend_log_server_id_not_observed:{index}")
                if not any(value in text for value in bound):
                    errors.append(f"b2_backend_log_not_bound:{index}")
        _validate_b2_sse(response, errors)
        verified_paths: dict[str, Path | None] = {}
        for ref_field, sha_field in (
            ("app_request_ref", "app_request_sha256"),
            ("sse_ref", "sse_sha256"),
            ("history_ref", "history_sha256"),
            ("citations_ref", "citations_sha256"),
        ):
            verified_paths[ref_field] = _verify_reference(
                response,
                ref_field,
                sha_field,
                errors,
            )
        capture_index_path = _verify_reference(
            response,
            "provider_capture_index_ref",
            "provider_capture_index_sha256",
            errors,
        )
        if capture_index_path is not None:
            _validate_b2_provider_capture(response, capture_index_path, errors)
        raw_response = response.get("raw_response")
        raw_root_text = str(response.get("evidence_root") or "").strip()
        raw_root = (
            Path(raw_root_text).expanduser().resolve()
            if raw_root_text
            else None
        )
        raw_ref = str(response.get("raw_response_ref") or "").strip()
        if raw_root is not None and raw_ref:
            raw_path = _verify_item_reference(
                raw_root,
                raw_ref,
                str(response.get("raw_response_sha256") or ""),
                label="b2_raw_response",
                errors=errors,
            )
            if raw_path is not None:
                try:
                    archived_raw = json.loads(
                        raw_path.read_text(encoding="utf-8-sig")
                    )
                except Exception:
                    archived_raw = None
                if archived_raw != raw_response:
                    errors.append("b2_raw_response_file_differs_from_record")
        app_request_path = verified_paths.get("app_request_ref")
        if app_request_path is not None:
            try:
                archived_request = json.loads(
                    app_request_path.read_text(encoding="utf-8-sig")
                )
            except Exception:
                archived_request = None
            if archived_request != app_request:
                errors.append("b2_app_request_file_differs_from_record")
        if not isinstance(raw_response, dict):
            errors.append("b2_raw_response_not_object")
        elif not str(response.get("raw_response_ref") or "").strip():
            errors.append("b2_raw_response_ref_missing")
    elif condition == "B1":
        if response.get("server_request_id") not in (None, ""):
            errors.append("b1_server_request_id_must_be_not_applicable")
    return errors

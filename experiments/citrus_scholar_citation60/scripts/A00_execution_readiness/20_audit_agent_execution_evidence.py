#!/usr/bin/env python3
"""Audit a real Agent execution without treating normal corpus access as leakage."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PROTOCOL_VERSION = "4.4.0"

ALLOWED_REQUEST_KEYS = {
    "request_id",
    "server_request_id",
    "client_request_id",
    "run_id",
    "session_id",
    "question_id",
    "condition",
    "question",
    "system_prompt",
    "source_scope",
    "attachments",
    "research_constraints",
    "tool_permissions",
    "model_config",
    "session_mode",
    "conversation_history",
    "question_count",
    "execution_mode",
    "workflow_id",
    "started_utc",
}

REQUIRED_REQUEST_KEYS = {
    "server_request_id",
    "client_request_id",
    "run_id",
    "session_id",
    "question_id",
    "condition",
    "question",
    "source_scope",
    "tool_permissions",
    "model_config",
    "session_mode",
    "conversation_history",
    "question_count",
}

# Study-control material must not be injected into the Agent. Full corpus
# chunks, source IDs and retrieval locators remain valid normal inputs/results.
FORBIDDEN_INPUT_KEYS = {
    "gold_answer",
    "reference_answer",
    "expected_answer",
    "answer_key",
    "scoring_mapping",
    "score_mapping",
    "expert_score",
    "expert_scores",
    "judge_score",
    "judge_scores",
    "other_condition_answer",
    "other_condition_answers",
    "blind_id",
    "blind_map",
}

DENIED_TOOL_PATH_MARKERS = {
    "06_runs/raw",
    "06_runs/judge_packets",
    "06_runs/judge_blinding",
    "06_runs/judge_requests",
    "06_runs/judge_raw",
    "07_scoring",
}

TRACE_IDENTITY_FIELDS = {
    "request_id",
    "server_request_id",
    "client_request_id",
    "run_id",
    "session_id",
    "question_id",
    "condition",
}

TERMINAL_STATUSES = {
    "completed",
    "partial",
    "failed",
    "no_results",
    "cancelled",
    "skipped",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA256_PATTERN.fullmatch(value))


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_evidence_path(
    evidence_root: Path | None,
    reference: Any,
) -> Path | None:
    if evidence_root is None or not isinstance(reference, str) or not reference.strip():
        return None
    root = evidence_root.expanduser().resolve()
    raw_path = reference.split("#", 1)[0]
    candidate = Path(raw_path).expanduser()
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (root / candidate).resolve()
    )
    if not is_relative_to(resolved, root):
        return None
    return resolved


def audit_reference_file(
    *,
    evidence_root: Path | None,
    reference: Any,
    declared_sha256: Any,
    label: str,
    errors: list[str],
    allow_fragment: bool = True,
) -> tuple[Path | None, bytes | None]:
    if evidence_root is None:
        errors.append(f"evidence_root_missing:{label}")
        return None, None
    if not allow_fragment and isinstance(reference, str) and "#" in reference:
        errors.append(f"reference_fragment_not_allowed:{label}")
        return None, None
    path = resolve_evidence_path(evidence_root, reference)
    if path is None:
        errors.append(f"reference_path_invalid:{label}")
        return None, None
    if not path.is_file():
        errors.append(f"reference_file_missing:{label}")
        return path, None
    if not valid_sha256(declared_sha256):
        errors.append(f"reference_sha256_invalid:{label}")
        return path, None
    data = path.read_bytes()
    if sha256_bytes(data) != str(declared_sha256):
        errors.append(f"reference_sha256_mismatch:{label}")
    return path, data


def parse_capture(data: bytes) -> Any:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    rows: list[Any] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            return text
    return rows


def extract_response_content(captured: Any) -> str | None:
    if isinstance(captured, str):
        return captured
    if not isinstance(captured, dict):
        return None
    for key in ("raw_answer", "content", "output_text"):
        value = captured.get(key)
        if isinstance(value, str) and value.strip():
            return value
    choices = captured.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            candidates = (
                message.get("content") if isinstance(message, dict) else None,
                choice.get("text"),
            )
            for candidate in candidates:
                if isinstance(candidate, str) and candidate.strip():
                    return candidate
                if isinstance(candidate, list):
                    parts = [
                        str(part.get("text") or "")
                        for part in candidate
                        if isinstance(part, dict)
                    ]
                    joined = "".join(parts)
                    if joined.strip():
                        return joined
    output = captured.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                parts.extend(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict)
                )
        joined = "".join(parts)
        if joined.strip():
            return joined
    return None


def response_envelope_value(captured: Any, *keys: str) -> Any:
    if not isinstance(captured, dict):
        return None
    for key in keys:
        value = captured.get(key)
        if value not in (None, ""):
            return value
    return None


def capture_event_list(data: bytes) -> list[Any] | None:
    captured = parse_capture(data)
    if isinstance(captured, dict) and isinstance(captured.get("events"), list):
        return list(captured["events"])
    if isinstance(captured, list):
        return captured
    text = data.decode("utf-8-sig", errors="replace")
    events: list[Any] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            return None
    return events or None


def compare_event_capture(
    captured: list[Any] | None,
    expected: Sequence[Any],
    *,
    label: str,
    errors: list[str],
) -> None:
    if captured is None:
        errors.append(f"{label}_capture_format_invalid")
        return
    if len(captured) != len(expected):
        errors.append(f"{label}_capture_event_count_mismatch")
    for index, (actual, declared) in enumerate(zip(captured, expected)):
        if actual != declared:
            errors.append(f"{label}_capture_event_mismatch:{index}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_question_catalog(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    question_column = next(
        (name for name in ("完整题目", "question", "prompt") if rows and name in rows[0]),
        None,
    )
    id_column = next(
        (name for name in ("题号", "question_id", "case_id") if rows and name in rows[0]),
        None,
    )
    if question_column is None or id_column is None:
        raise ValueError("question catalog needs an ID and question column")
    return {
        row[id_column].strip(): row[question_column].strip()
        for row in rows
        if row.get(id_column) and row.get(question_column)
    }


def flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from flatten_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from flatten_strings(child)


def forbidden_key_paths(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            current = f"{prefix}.{key}" if prefix else str(key)
            if normalized in FORBIDDEN_INPUT_KEYS:
                found.append(current)
            found.extend(forbidden_key_paths(child, current))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(forbidden_key_paths(child, f"{prefix}[{index}]"))
    return found


def identity_mismatches(
    value: Any,
    request: Mapping[str, Any],
    prefix: str = "",
) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            current = f"{prefix}.{key}" if prefix else str(key)
            if (
                normalized in TRACE_IDENTITY_FIELDS
                and isinstance(child, (str, int))
                and str(child) != str(request.get(normalized, ""))
            ):
                found.append(f"trace_nested_identity_mismatch:{current}")
            found.extend(identity_mismatches(child, request, current))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(
                identity_mismatches(child, request, f"{prefix}[{index}]")
            )
    return found


def path_markers_in_text(value: str) -> set[str]:
    normalized = value.replace("\\", "/").lower()
    return {
        marker
        for marker in DENIED_TOOL_PATH_MARKERS
        if marker.lower() in normalized
    }


def audit_tool_arguments(value: Any, prefix: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            current = f"{prefix}.{key}" if prefix else str(key)
            for text in flatten_strings(child):
                markers = path_markers_in_text(text)
                if markers:
                    errors.append(
                        "tool_argument_reaches_control_material:"
                        f"{current}:{','.join(sorted(markers))}"
                    )
            errors.extend(audit_tool_arguments(child, current))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(
                audit_tool_arguments(child, f"{prefix}[{index}]")
            )
    return errors


def audit_request(
    request: Mapping[str, Any],
    *,
    catalog: Mapping[str, str] | None = None,
    expected_model_config: Mapping[str, Any] | None = None,
    expected_condition: str | None = None,
    max_prompt_chars: int = 20000,
) -> list[str]:
    errors: list[str] = []
    keys = set(request)
    unknown = keys - ALLOWED_REQUEST_KEYS
    missing = REQUIRED_REQUEST_KEYS - keys
    if unknown:
        errors.append(f"request_unknown_keys:{','.join(sorted(unknown))}")
    if missing:
        errors.append(f"request_missing_keys:{','.join(sorted(missing))}")
    for field in ("server_request_id", "client_request_id", "run_id", "session_id"):
        if not str(request.get(field) or "").strip():
            errors.append(f"request_identifier_empty:{field}")
    if request.get("request_id") not in (None, "") and str(
        request.get("request_id")
    ) != str(request.get("server_request_id") or ""):
        errors.append("request_id_differs_from_server_request_id")
    errors.extend(
        f"forbidden_request_field:{path}"
        for path in forbidden_key_paths(request)
    )

    if request.get("session_mode") != "NEW":
        errors.append("session_mode_must_be_NEW")
    if request.get("conversation_history") not in (None, [], "", {}):
        errors.append("conversation_history_not_empty")
    if request.get("question_count") not in (1, "1"):
        errors.append("question_count_must_equal_one")

    condition = str(request.get("condition") or "")
    if condition not in {"B0", "B1", "B2"}:
        errors.append("condition_not_B0_B1_B2")
    if expected_condition and condition != expected_condition:
        errors.append("condition_mismatch")

    question = request.get("question")
    if not isinstance(question, str) or not question.strip():
        errors.append("question_missing")
    elif len(question) > max_prompt_chars:
        errors.append("question_too_long")
    elif "\nQ" in question or "\n问题" in question:
        errors.append("possible_multiple_questions")

    question_id = str(request.get("question_id") or "")
    if catalog:
        catalog_question = catalog.get(question_id)
        if catalog_question is None:
            errors.append("question_not_in_catalog")
        elif isinstance(question, str) and question.strip() != catalog_question:
            errors.append("question_text_differs_from_catalog")

    model_config = request.get("model_config")
    if not isinstance(model_config, dict) or not model_config:
        errors.append("model_config_missing")
    elif expected_model_config is not None and dict(model_config) != dict(
        expected_model_config
    ):
        errors.append("model_config_differs_from_frozen")

    permissions = request.get("tool_permissions")
    if not isinstance(permissions, (list, dict)) or not permissions:
        errors.append("tool_permissions_missing")
    return errors


def _list_field(trace: Mapping[str, Any], name: str) -> list[Any] | None:
    value = trace.get(name)
    return value if isinstance(value, list) else None


def _has_terminal_sse(events: Sequence[Any]) -> bool:
    terminal_names = {"done", "end", "completed", "cancelled", "error"}
    for event in events:
        if isinstance(event, dict):
            name = str(event.get("event") or event.get("type") or "").lower()
        else:
            name = str(event).lower()
        if name in terminal_names:
            return True
    return False


def _done_answer(events: Sequence[Any]) -> str | None:
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        name = str(event.get("event") or event.get("type") or "").lower()
        if name != "done":
            continue
        for candidate in (
            event.get("answer"),
            event.get("done_answer"),
            event.get("data", {}).get("answer")
            if isinstance(event.get("data"), dict)
            else None,
            event.get("done", {}).get("answer")
            if isinstance(event.get("done"), dict)
            else None,
        ):
            if isinstance(candidate, str) and candidate:
                return candidate
    return None


def _retrieval_has_source_locator(events: Sequence[Any]) -> bool:
    locator_keys = {"source_id", "chunk_id", "locator", "corpus_id", "document_id"}
    for event in events:
        if not isinstance(event, dict):
            continue
        results = event.get("results") or event.get("hits") or event.get("sources")
        if not isinstance(results, list) or not results:
            continue
        for result in results:
            if isinstance(result, dict) and any(
                result.get(key) not in (None, "", [], {})
                for key in locator_keys
            ):
                return True
    return False


def audit_tool_payload(value: Any, prefix: str) -> list[str]:
    errors: list[str] = []
    for text in flatten_strings(value):
        markers = path_markers_in_text(text)
        if markers:
            errors.append(
                "tool_payload_reaches_control_material:"
                f"{prefix}:{','.join(sorted(markers))}"
            )
    return errors


def model_identity_errors(
    trace: Mapping[str, Any],
    request: Mapping[str, Any],
    expected_model_config: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    provider_id = str(expected_model_config.get("provider_id") or "")
    provider_names = {
        provider_id,
        str(expected_model_config.get("provider_display_name") or ""),
        str(expected_model_config.get("agent_provider") or ""),
    } - {""}
    trace_provider = str(trace.get("provider") or "")
    if not provider_id:
        errors.append("frozen_provider_id_missing")
    elif trace_provider not in provider_names:
        errors.append("trace_provider_differs_from_frozen_config")

    request_model = (
        request.get("model_config", {}).get("model")
        if isinstance(request.get("model_config"), dict)
        else None
    )
    candidate = expected_model_config.get("candidate_model_id")
    allowed_models = {
        str(value)
        for value in expected_model_config.get("allowed_response_models", [])
        if str(value).strip()
    }
    if candidate:
        allowed_models.add(str(candidate))
    requested_model = trace.get("requested_model")
    actual_model = trace.get("actual_model")
    if not candidate:
        errors.append("frozen_agent_model_id_missing")
    if request_model != candidate:
        errors.append("request_model_differs_from_frozen_config")
    if requested_model != request_model:
        errors.append("trace_requested_model_differs_from_request")
    if not allowed_models:
        errors.append("frozen_allowed_response_models_missing")
    elif actual_model not in allowed_models:
        errors.append("trace_actual_model_not_in_frozen_allowlist")
    return errors


def stage1_tool_registry(
    manifest: Mapping[str, Any],
) -> tuple[dict[str, dict[str, str]], list[str]]:
    errors: list[str] = []
    guard = manifest.get("execution_guard")
    if not isinstance(guard, dict):
        return {}, ["stage1_execution_guard_missing"]
    rows = guard.get("tool_registry")
    if not isinstance(rows, list) or len(rows) != 17:
        errors.append("stage1_tool_registry_mapping_incomplete")
        return {}, errors
    registry: dict[str, dict[str, str]] = {}
    versions: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"stage1_tool_registry_entry_invalid:{index}")
            continue
        name = str(row.get("tool_name") or "").strip()
        group = str(row.get("tool_group") or "").strip()
        category = str(row.get("tool_category") or "").strip()
        version = str(row.get("registration_version") or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,127}", name):
            errors.append(f"stage1_tool_registry_name_invalid:{index}")
        elif name in registry:
            errors.append(f"stage1_tool_registry_name_duplicate:{index}")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,31}", group):
            errors.append(f"stage1_tool_registry_group_invalid:{index}")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", category):
            errors.append(f"stage1_tool_registry_category_invalid:{index}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", version):
            errors.append(f"stage1_tool_registry_version_invalid:{index}")
        else:
            versions.add(version)
        if name:
            registry[name] = {
                "tool_group": group,
                "tool_category": category,
                "registration_version": version,
            }
    if len(versions) != 1:
        errors.append("stage1_tool_registry_version_not_single_or_missing")
    names = guard.get("tool_names")
    if len(
        {
            str(value).strip()
            for value in names or []
            if isinstance(value, str) and str(value).strip()
        }
    ) != 17:
        errors.append("stage1_tool_registry_names_not_unique")
    if not isinstance(names, list) or set(
        str(value).strip() for value in names if isinstance(value, str)
    ) != set(registry):
        errors.append("stage1_tool_registry_names_mismatch_mapping")
    if not SHA256_PATTERN.fullmatch(
        str(guard.get("tool_registration_evidence_sha256") or "")
    ):
        errors.append("stage1_tool_registry_evidence_sha_invalid")
    return registry, errors


def audit_stage1_binding(
    manifest: Mapping[str, Any],
    expected_model_config: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    _, registry_errors = stage1_tool_registry(manifest)
    errors.extend(registry_errors)
    if manifest.get("status") != "PASS":
        errors.append("stage1_manifest_not_pass")
    runtime = manifest.get("runtime_app_config")
    if not isinstance(runtime, dict):
        errors.append("stage1_runtime_app_config_missing")
        runtime = {}
    runtime_model = runtime.get("model", {}) if isinstance(runtime, dict) else {}
    if not isinstance(runtime_model, dict):
        errors.append("stage1_runtime_model_config_missing")
        runtime_model = {}
    if runtime_model.get("provider_id") != expected_model_config.get("provider_id"):
        errors.append("stage1_app_provider_differs_from_frozen")
    if runtime_model.get("main") != expected_model_config.get("candidate_model_id"):
        errors.append("stage1_app_model_differs_from_frozen")
    identities = manifest.get("provider_model_identity", {})
    agent_rows = (
        identities.get("agent_api_probe", [])
        if isinstance(identities, dict)
        else []
    )
    if not isinstance(agent_rows, list) or not agent_rows:
        errors.append("stage1_agent_provider_identity_missing")
    else:
        for row in agent_rows:
            if not isinstance(row, dict) or row.get("status") != "PASS":
                errors.append("stage1_agent_provider_identity_not_pass")
                continue
            if row.get("requested_model") != expected_model_config.get(
                "candidate_model_id"
            ):
                errors.append("stage1_agent_requested_model_mismatch")
            allowed = {
                str(value)
                for value in expected_model_config.get("allowed_response_models", [])
            }
            if str(expected_model_config.get("candidate_model_id") or ""):
                allowed.add(str(expected_model_config["candidate_model_id"]))
            if row.get("actual_model") not in allowed:
                errors.append("stage1_agent_actual_model_not_allowed")
    return sorted(set(errors))


def audit_session_registry(
    registry_path: Path | None,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    if registry_path is None:
        return {
            "status": "NOT_ASSESSED",
            "errors": ["session_registry_missing"],
            "current_row_count": 0,
            "registry_sha256": None,
            "duplicate_request_ids": [],
            "duplicate_server_request_ids": [],
            "duplicate_client_request_ids": [],
            "duplicate_session_ids": [],
            "duplicate_run_ids": [],
        }
    if not registry_path.is_file():
        return {
            "status": "BLOCKED",
            "errors": ["session_registry_file_missing"],
            "current_row_count": 0,
            "registry_sha256": None,
            "duplicate_request_ids": [],
            "duplicate_server_request_ids": [],
            "duplicate_client_request_ids": [],
            "duplicate_session_ids": [],
            "duplicate_run_ids": [],
        }
    registry_bytes = registry_path.read_bytes()
    registry_sha256 = sha256_bytes(registry_bytes)
    with io.StringIO(
        registry_bytes.decode("utf-8-sig"),
        newline="",
    ) as handle:
        rows = list(csv.DictReader(handle))
    required = {
        "run_id",
        "request_id",
        "server_request_id",
        "client_request_id",
        "session_id",
        "condition",
        "question_id",
        "created_for_run",
    }
    present = set(rows[0]) if rows else set()
    missing_columns = sorted(required - present)
    errors: list[str] = []
    if missing_columns:
        errors.append("session_registry_columns_missing:" + ",".join(missing_columns))
    request_counts: dict[str, int] = {}
    server_request_counts: dict[str, int] = {}
    client_request_counts: dict[str, int] = {}
    session_counts: dict[str, int] = {}
    run_counts: dict[str, int] = {}
    empty_request_rows = 0
    empty_server_request_rows = 0
    empty_client_request_rows = 0
    empty_session_rows = 0
    empty_run_rows = 0
    for row in rows:
        session_id = str(row.get("session_id") or "").strip()
        run_id = str(row.get("run_id") or "").strip()
        request_id = str(row.get("request_id") or "").strip()
        server_request_id = str(row.get("server_request_id") or "").strip()
        client_request_id = str(row.get("client_request_id") or "").strip()
        if request_id and server_request_id and request_id != server_request_id:
            errors.append("session_registry_request_alias_differs_from_server_id")
        if not request_id:
            empty_request_rows += 1
        else:
            request_counts[request_id] = request_counts.get(request_id, 0) + 1
        if not server_request_id:
            empty_server_request_rows += 1
        else:
            server_request_counts[server_request_id] = (
                server_request_counts.get(server_request_id, 0) + 1
            )
        if not client_request_id:
            empty_client_request_rows += 1
        else:
            client_request_counts[client_request_id] = (
                client_request_counts.get(client_request_id, 0) + 1
            )
        if session_id:
            session_counts[session_id] = session_counts.get(session_id, 0) + 1
        else:
            empty_session_rows += 1
        if run_id:
            run_counts[run_id] = run_counts.get(run_id, 0) + 1
        else:
            empty_run_rows += 1
    duplicate_requests = sorted(
        request_id for request_id, count in request_counts.items() if count != 1
    )
    duplicate_server_requests = sorted(
        request_id
        for request_id, count in server_request_counts.items()
        if count != 1
    )
    duplicate_client_requests = sorted(
        request_id
        for request_id, count in client_request_counts.items()
        if count != 1
    )
    duplicate_sessions = sorted(
        session_id for session_id, count in session_counts.items() if count != 1
    )
    duplicate_runs = sorted(
        run_id for run_id, count in run_counts.items() if count != 1
    )
    if empty_request_rows:
        errors.append("session_registry_empty_request_id")
    if empty_server_request_rows:
        errors.append("session_registry_empty_server_request_id")
    if empty_client_request_rows:
        errors.append("session_registry_empty_client_request_id")
    if empty_session_rows:
        errors.append("session_registry_empty_session_id")
    if empty_run_rows:
        errors.append("session_registry_empty_run_id")
    if duplicate_requests:
        errors.append("session_registry_duplicate_request_id")
    if duplicate_server_requests:
        errors.append("session_registry_duplicate_server_request_id")
    if duplicate_client_requests:
        errors.append("session_registry_duplicate_client_request_id")
    if duplicate_sessions:
        errors.append("session_registry_duplicate_session_id")
    if duplicate_runs:
        errors.append("session_registry_duplicate_run_id")
    current = [
        row
        for row in rows
        if str(row.get("server_request_id") or "").strip()
        == str(request.get("server_request_id") or "").strip()
    ]
    if len(current) != 1:
        errors.append("session_registry_current_request_row_not_unique")
    else:
        row = current[0]
        if str(row.get("run_id") or "").strip() != str(
            request.get("run_id") or ""
        ).strip():
            errors.append("session_registry_run_id_mismatch")
        if str(row.get("request_id") or "").strip() != str(
            request.get("request_id") or ""
        ).strip():
            errors.append("session_registry_request_id_mismatch")
        if str(row.get("client_request_id") or "").strip() != str(
            request.get("client_request_id") or ""
        ).strip():
            errors.append("session_registry_client_request_id_mismatch")
        if str(row.get("session_id") or "").strip() != str(
            request.get("session_id") or ""
        ).strip():
            errors.append("session_registry_session_id_mismatch")
        if str(row.get("condition") or "").strip() != str(
            request.get("condition") or ""
        ).strip():
            errors.append("session_registry_condition_mismatch")
        if str(row.get("question_id") or "").strip() != str(
            request.get("question_id") or ""
        ).strip():
            errors.append("session_registry_question_id_mismatch")
        if str(row.get("created_for_run") or "").lower() not in {"true", "1", "yes"}:
            errors.append("session_registry_current_session_not_new")
        previous = {
            value.strip()
            for value in str(row.get("previous_session_ids") or "").replace(
                "|", ";"
            ).split(";")
            if value.strip()
        }
        if str(request.get("session_id") or "") in previous:
            errors.append("session_registry_current_session_has_history")
    return {
        "status": "BLOCKED" if errors else "VERIFIED_CLEAN",
        "errors": sorted(set(errors)),
        "current_row_count": len(current),
        "row_count": len(rows),
        "registry_sha256": registry_sha256,
        "duplicate_request_ids": duplicate_requests,
        "duplicate_server_request_ids": duplicate_server_requests,
        "duplicate_client_request_ids": duplicate_client_requests,
        "duplicate_session_ids": duplicate_sessions,
        "duplicate_run_ids": duplicate_runs,
    }


def audit_trace(
    trace: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    request_sha256: str,
    evidence_root: Path | None = None,
    required_tool_groups: set[str] | None = None,
    require_corpus_access: bool = False,
    require_successful_completed_run: bool = False,
    expected_model_config: Mapping[str, Any] | None = None,
    stage1_manifest: Mapping[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    for field in TRACE_IDENTITY_FIELDS:
        if str(trace.get(field, "")) != str(request.get(field, "")):
            errors.append(f"trace_{field}_mismatch")
    for field in ("server_request_id", "client_request_id", "run_id", "session_id"):
        if not str(trace.get(field) or "").strip():
            errors.append(f"trace_identifier_empty:{field}")
    if trace.get("request_sha256") != request_sha256:
        errors.append("trace_request_sha256_mismatch")
    errors.extend(
        f"forbidden_trace_field:{path}"
        for path in forbidden_key_paths(
            {
                "request": trace.get("request", {}),
                "retrieval_events": trace.get("retrieval_events", []),
            }
        )
    )
    errors.extend(identity_mismatches(trace, request))

    status = str(trace.get("status") or "")
    if status not in TERMINAL_STATUSES:
        errors.append("trace_terminal_status_invalid")
    if require_successful_completed_run and status != "completed":
        errors.append("stage2_requires_completed_run")

    for field in (
        "provider",
        "requested_model",
        "actual_model",
        "response_id",
        "started_utc",
        "finished_utc",
    ):
        if trace.get(field) in (None, ""):
            errors.append(f"trace_{field}_missing")
    if expected_model_config is not None:
        errors.extend(
            model_identity_errors(trace, request, expected_model_config)
        )
    if stage1_manifest is not None and expected_model_config is not None:
        errors.extend(audit_stage1_binding(stage1_manifest, expected_model_config))
    registered_tool_registry: dict[str, dict[str, str]] = {}
    if stage1_manifest is not None:
        registered_tool_registry, registry_errors = stage1_tool_registry(
            stage1_manifest
        )
        errors.extend(registry_errors)

    if trace.get("answer_source") != "AGENT":
        errors.append("final_answer_source_not_AGENT")
    raw_answer = trace.get("raw_answer")
    final_answer = trace.get("final_answer")
    persisted_answer = trace.get("persisted_answer")
    if not isinstance(raw_answer, str) or not raw_answer.strip():
        errors.append("raw_agent_answer_missing")
    if not isinstance(final_answer, str) or not final_answer.strip():
        errors.append("final_agent_answer_missing")
    if final_answer != persisted_answer:
        errors.append("persisted_answer_differs_from_final")

    raw_response_path, raw_response_bytes = audit_reference_file(
        evidence_root=evidence_root,
        reference=trace.get("raw_response_ref"),
        declared_sha256=trace.get("raw_response_sha256"),
        label="raw_agent_response",
        errors=errors,
        allow_fragment=False,
    )
    if raw_response_bytes is not None and raw_response_path is not None:
        captured = parse_capture(raw_response_bytes)
        captured_answer = extract_response_content(captured)
        if captured_answer is None:
            errors.append("raw_response_file_content_missing")
        elif captured_answer != raw_answer:
            errors.append("raw_response_file_answer_differs_from_trace")
        captured_response_id = response_envelope_value(
            captured,
            "id",
            "request_id",
            "response_id",
        )
        if captured_response_id in (None, ""):
            errors.append("raw_response_file_id_missing")
        elif str(captured_response_id) != str(trace.get("response_id") or ""):
            errors.append("raw_response_file_id_differs_from_trace")
        captured_model = response_envelope_value(captured, "model")
        if captured_model in (None, ""):
            errors.append("raw_response_file_model_missing")
        elif str(captured_model) != str(trace.get("actual_model") or ""):
            errors.append("raw_response_file_model_differs_from_trace")

    for field in ("sse_events", "retrieval_events", "tool_events", "backend_log_refs"):
        if _list_field(trace, field) is None:
            errors.append(f"trace_{field}_missing")

    sse_events = _list_field(trace, "sse_events") or []
    _, sse_bytes = audit_reference_file(
        evidence_root=evidence_root,
        reference=trace.get("sse_ref"),
        declared_sha256=trace.get("sse_sha256"),
        label="sse_capture",
        errors=errors,
        allow_fragment=False,
    )
    if sse_bytes is not None:
        compare_event_capture(
            capture_event_list(sse_bytes),
            sse_events,
            label="sse",
            errors=errors,
        )
    if not any(
        isinstance(event, dict)
        and str(event.get("event") or event.get("type") or "").lower()
        in {"answer_start", "message", "text"}
        for event in sse_events
    ):
        errors.append("sse_start_event_missing")
    if not _has_terminal_sse(sse_events):
        errors.append("sse_terminal_event_missing")
    done_answer = _done_answer(sse_events)
    if done_answer is not None and done_answer != final_answer:
        errors.append("sse_done_answer_differs_from_final")

    citations = trace.get("citations")
    if not isinstance(citations, list):
        errors.append("trace_citations_missing")
    else:
        for index, citation in enumerate(citations):
            if not isinstance(citation, (str, dict)):
                errors.append(f"citation_invalid:{index}")

    artifact_paths = trace.get("artifact_paths")
    artifact_sha256 = trace.get("artifact_sha256")
    if not isinstance(artifact_paths, list):
        errors.append("trace_artifact_paths_missing")
    if not isinstance(artifact_sha256, dict):
        errors.append("trace_artifact_sha256_missing")
    if isinstance(artifact_paths, list) and isinstance(artifact_sha256, dict):
        for index, artifact_path in enumerate(artifact_paths):
            if not isinstance(artifact_path, str) or not artifact_path.strip():
                errors.append(f"artifact_path_invalid:{index}")
                continue
            digest = artifact_sha256.get(artifact_path)
            if not valid_sha256(digest):
                errors.append(f"artifact_sha256_missing_or_invalid:{index}")
                continue
            audit_reference_file(
                evidence_root=evidence_root,
                reference=artifact_path,
                declared_sha256=digest,
                label=f"artifact:{index}",
                errors=errors,
                allow_fragment=False,
            )

    tool_events = _list_field(trace, "tool_events") or []
    required_groups = set(required_tool_groups or ())
    if require_successful_completed_run and request.get("condition") == "B2":
        required_groups.add("LOCAL")
    completed_registered_events = [
        event
        for event in tool_events
        if isinstance(event, dict)
        and str(event.get("status") or "").lower() == "completed"
        and str(event.get("tool_name") or event.get("name") or "").strip()
        and (
            not registered_tool_registry
            or str(event.get("tool_name") or event.get("name"))
            in registered_tool_registry
        )
        and (
            not registered_tool_registry
            or str(event.get("tool_group") or event.get("group") or "")
            == registered_tool_registry[
                str(event.get("tool_name") or event.get("name"))
            ]["tool_group"]
        )
    ]
    observed_groups = {
        str(event.get("tool_group") or event.get("group"))
        for event in completed_registered_events
        if event.get("tool_group") or event.get("group")
    }
    if required_groups and not required_groups.issubset(observed_groups):
        missing = sorted(required_groups - observed_groups)
        errors.append("required_completed_tool_groups_missing:" + ",".join(missing))
    for index, event in enumerate(tool_events):
        if not isinstance(event, dict):
            errors.append(f"tool_event_not_object:{index}")
            continue
        tool_name = str(event.get("tool_name") or event.get("name") or "").strip()
        if not tool_name:
            errors.append(f"tool_event_name_missing:{index}")
        elif registered_tool_registry and tool_name not in registered_tool_registry:
            errors.append(f"tool_event_name_not_in_frozen_registry:{index}")
        if tool_name and registered_tool_registry and tool_name in registered_tool_registry:
            declared_group = str(
                event.get("tool_group") or event.get("group") or ""
            ).strip()
            registered_group = registered_tool_registry[tool_name]["tool_group"]
            if declared_group != registered_group:
                errors.append(f"tool_event_group_differs_from_registry:{index}")
        if str(event.get("status") or "").lower() not in {
            "completed",
            "failed",
            "cancelled",
            "skipped",
        }:
            errors.append(f"tool_event_status_invalid:{index}")
        errors.extend(
            audit_tool_arguments(
                event.get("arguments", {}),
                prefix=f"tool_events[{index}].arguments",
            )
        )
        errors.extend(
            audit_tool_payload(
                event.get("arguments", {}),
                f"tool_events[{index}].arguments",
            )
        )
        errors.extend(
            audit_tool_payload(
                event.get("result", {}),
                f"tool_events[{index}].result",
            )
        )
        inline_result = event.get("result")
        if inline_result is None:
            errors.append(f"tool_event_result_missing:{index}")
        _, result_bytes = audit_reference_file(
            evidence_root=evidence_root,
            reference=event.get("result_ref"),
            declared_sha256=event.get("result_sha256"),
            label=f"tool_result:{index}",
            errors=errors,
            allow_fragment=False,
        )
        if result_bytes is not None:
            captured_result = parse_capture(result_bytes)
            if captured_result != inline_result:
                errors.append(f"tool_result_file_differs_from_trace:{index}")
            errors.extend(
                audit_tool_payload(
                    captured_result,
                    f"tool_events[{index}].result_file",
                )
            )

    retrieval_events = _list_field(trace, "retrieval_events") or []
    if require_corpus_access and not retrieval_events:
        errors.append("normal_corpus_retrieval_not_observed")
    if require_corpus_access and not _retrieval_has_source_locator(retrieval_events):
        errors.append("retrieval_source_locator_missing")
    _, retrieval_bytes = audit_reference_file(
        evidence_root=evidence_root,
        reference=trace.get("retrieval_ref"),
        declared_sha256=trace.get("retrieval_sha256"),
        label="retrieval_capture",
        errors=errors,
        allow_fragment=False,
    )
    if retrieval_bytes is not None:
        captured_retrieval = capture_event_list(retrieval_bytes)
        compare_event_capture(
            captured_retrieval,
            retrieval_events,
            label="retrieval",
            errors=errors,
        )
        if captured_retrieval is not None and not _retrieval_has_source_locator(
            captured_retrieval
        ):
            errors.append("retrieval_capture_source_locator_missing")

    backend_logs = _list_field(trace, "backend_log_refs") or []
    if not backend_logs:
        errors.append("backend_log_refs_empty")
    backend_request_id_observed = False
    for index, item in enumerate(backend_logs):
        if isinstance(item, str):
            errors.append(f"backend_log_ref_string_not_allowed:{index}")
        elif isinstance(item, dict):
            reference = item.get("path")
            if not isinstance(reference, str) or not reference.strip():
                errors.append(f"backend_log_path_missing:{index}")
                continue
            if not valid_sha256(item.get("sha256")):
                errors.append(f"backend_log_sha256_missing:{index}")
                continue
            _, log_bytes = audit_reference_file(
                evidence_root=evidence_root,
                reference=reference,
                declared_sha256=item.get("sha256"),
                label=f"backend_log:{index}",
                errors=errors,
                allow_fragment=False,
            )
            if log_bytes is not None:
                log_text = log_bytes.decode("utf-8", errors="replace")
                bound_ids = {
                    str(value)
                    for value in (
                        request.get("request_id"),
                        request.get("server_request_id"),
                        request.get("client_request_id"),
                        request.get("run_id"),
                        trace.get("response_id"),
                    )
                    if str(value or "").strip()
                }
                if not any(value in log_text for value in bound_ids):
                    errors.append(f"backend_log_not_bound_to_run:{index}")
                if str(request.get("server_request_id") or "") in log_text:
                    backend_request_id_observed = True
        else:
            errors.append(f"backend_log_ref_invalid:{index}")
    if backend_logs and not backend_request_id_observed:
        errors.append("backend_log_request_id_not_observed")

    previous = _list_field(trace, "previous_session_ids") or []
    if request.get("session_id") in previous:
        errors.append("current_session_appears_in_previous_sessions")
    return errors


def build_report(
    *,
    request: Mapping[str, Any],
    trace: Mapping[str, Any],
    request_sha256: str,
    trace_sha256: str,
    errors: list[str],
    session_registry: Mapping[str, Any] | None = None,
    stage1_binding_errors: Sequence[str] = (),
) -> dict[str, Any]:
    registry = dict(session_registry or {})
    if not registry:
        registry = {
            "status": "NOT_ASSESSED",
            "errors": ["session_registry_missing"],
        }
    cross_condition_state = {
        "NOT_ASSESSED": "NOT_ASSESSED",
        "VERIFIED_CLEAN": "VERIFIED_CLEAN",
        "BLOCKED": "BLOCKED",
    }.get(str(registry.get("status")), "BLOCKED")
    blocking_errors = sorted(
        set(errors)
        | set(str(value) for value in registry.get("errors", []))
        | set(stage1_binding_errors)
    )
    if cross_condition_state != "VERIFIED_CLEAN":
        blocking_errors.append(
            "cross_condition_contamination_not_verified_clean"
            if cross_condition_state == "NOT_ASSESSED"
            else "cross_condition_contamination_blocked"
        )
    return {
        "schema_version": "1.0",
        "protocol_version": PROTOCOL_VERSION,
        "audit": "AGENT_EXECUTION_EVIDENCE",
        "status": "PASS" if not blocking_errors else "BLOCKED",
        "request_id": request.get("request_id"),
        "server_request_id": request.get("server_request_id"),
        "client_request_id": request.get("client_request_id"),
        "run_id": request.get("run_id"),
        "session_id": request.get("session_id"),
        "question_id": request.get("question_id"),
        "condition": request.get("condition"),
        "request_sha256": request_sha256,
        "trace_sha256": trace_sha256,
        "single_question_request": "question_count_must_equal_one" not in errors,
        "new_session": "session_mode_must_be_NEW" not in errors,
        "request_trace_sha_bound": "trace_request_sha256_mismatch" not in errors,
        "normal_corpus_access_allowed": True,
        "corpus_access_observed": bool(trace.get("retrieval_events")),
        "raw_agent_answer_captured": isinstance(trace.get("raw_answer"), str)
        and bool(str(trace.get("raw_answer")).strip()),
        "final_answer_matches_persisted": (
            "persisted_answer_differs_from_final" not in errors
        ),
        "sse_done_answer_matches_final": (
            "sse_done_answer_differs_from_final" not in errors
        ),
        "agent_answer_provenance_verified": (
            trace.get("answer_source") == "AGENT"
            and isinstance(trace.get("raw_answer"), str)
            and bool(str(trace.get("raw_answer")).strip())
            and "persisted_answer_differs_from_final" not in errors
        ),
        "backend_logs_linked": bool(trace.get("backend_log_refs")),
        "cross_condition_contamination": cross_condition_state,
        "session_registry_sha256": registry.get("registry_sha256"),
        "session_registry": registry,
        "stage1_model_cross_binding": (
            "PASS" if not stage1_binding_errors else "BLOCKED"
        ),
        "stage1_binding_errors": sorted(set(stage1_binding_errors)),
        "errors": sorted(set(blocking_errors)),
    }


def write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--question-catalog")
    parser.add_argument("--expected-model-config", required=True)
    parser.add_argument("--stage1-manifest", required=True)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--session-registry")
    parser.add_argument(
        "--expected-condition",
        required=True,
        choices=("B0", "B1", "B2"),
    )
    parser.add_argument("--required-tool-groups", default="")
    parser.add_argument("--require-corpus-access", action="store_true")
    args = parser.parse_args()

    request_path = Path(args.request).expanduser().resolve()
    trace_path = Path(args.trace).expanduser().resolve()
    stage1_path = Path(args.stage1_manifest).expanduser().resolve()
    evidence_root = Path(args.evidence_root).expanduser().resolve()
    request = load_json(request_path)
    trace = load_json(trace_path)
    if not isinstance(request, dict) or not isinstance(trace, dict):
        raise SystemExit("request and trace must be JSON objects")

    catalog = load_question_catalog(
        Path(args.question_catalog).expanduser().resolve()
        if args.question_catalog
        else None
    )
    expected_model = load_json(
        Path(args.expected_model_config).expanduser().resolve()
    )
    stage1_manifest = load_json(stage1_path)
    if not isinstance(expected_model, dict) or not isinstance(stage1_manifest, dict):
        raise SystemExit("model config and Stage 1 manifest must be JSON objects")
    required_groups = {
        item.strip()
        for item in args.required_tool_groups.split(",")
        if item.strip()
    }
    request_sha256 = sha256_file(request_path)
    stage1_binding_errors = audit_stage1_binding(
        stage1_manifest,
        expected_model,
    )
    errors = audit_request(
        request,
        catalog=catalog,
        expected_model_config=expected_model,
        expected_condition=args.expected_condition,
    )
    if args.expected_condition != "B2":
        errors.append("stage2_acceptance_requires_condition_B2")
    errors.extend(
        audit_trace(
            trace,
            request,
            request_sha256=request_sha256,
            evidence_root=evidence_root,
            required_tool_groups=required_groups or None,
            require_corpus_access=args.require_corpus_access,
            require_successful_completed_run=True,
            expected_model_config=expected_model,
            stage1_manifest=stage1_manifest,
        )
    )
    session_registry = audit_session_registry(
        Path(args.session_registry).expanduser().resolve()
        if args.session_registry
        else None,
        request,
    )
    errors.extend(str(value) for value in session_registry.get("errors", []))
    report = build_report(
        request=request,
        trace=trace,
        request_sha256=request_sha256,
        trace_sha256=sha256_file(trace_path),
        errors=errors,
        session_registry=session_registry,
        stage1_binding_errors=stage1_binding_errors,
    )
    write_new_json(Path(args.out).expanduser().resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

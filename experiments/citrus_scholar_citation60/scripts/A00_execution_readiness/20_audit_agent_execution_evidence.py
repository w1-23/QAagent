#!/usr/bin/env python3
"""Audit a real Agent execution without treating normal corpus access as leakage."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PROTOCOL_VERSION = "4.4.0"

ALLOWED_REQUEST_KEYS = {
    "request_id",
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
    "request_id",
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


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


def audit_trace(
    trace: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    request_sha256: str,
    required_tool_groups: set[str] | None = None,
    require_corpus_access: bool = False,
) -> list[str]:
    errors: list[str] = []
    for field in TRACE_IDENTITY_FIELDS:
        if str(trace.get(field, "")) != str(request.get(field, "")):
            errors.append(f"trace_{field}_mismatch")
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

    for field in ("sse_events", "retrieval_events", "tool_events", "backend_log_refs"):
        if _list_field(trace, field) is None:
            errors.append(f"trace_{field}_missing")

    sse_events = _list_field(trace, "sse_events") or []
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
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdefABCDEF" for character in digest)
            ):
                errors.append(f"artifact_sha256_missing_or_invalid:{index}")

    tool_events = _list_field(trace, "tool_events") or []
    observed_groups = {
        str(event.get("tool_group") or event.get("group"))
        for event in tool_events
        if isinstance(event, dict) and (event.get("tool_group") or event.get("group"))
    }
    if required_tool_groups and not required_tool_groups.issubset(observed_groups):
        missing = sorted(required_tool_groups - observed_groups)
        errors.append("required_tool_groups_missing:" + ",".join(missing))
    for index, event in enumerate(tool_events):
        if not isinstance(event, dict):
            errors.append(f"tool_event_not_object:{index}")
            continue
        if not str(event.get("tool_name") or event.get("name") or "").strip():
            errors.append(f"tool_event_name_missing:{index}")
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

    retrieval_events = _list_field(trace, "retrieval_events") or []
    if require_corpus_access and not retrieval_events:
        errors.append("normal_corpus_retrieval_not_observed")
    if require_corpus_access and not _retrieval_has_source_locator(retrieval_events):
        errors.append("retrieval_source_locator_missing")

    backend_logs = _list_field(trace, "backend_log_refs") or []
    if not backend_logs:
        errors.append("backend_log_refs_empty")
    for index, item in enumerate(backend_logs):
        if isinstance(item, str):
            if not item.strip():
                errors.append(f"backend_log_ref_empty:{index}")
        elif isinstance(item, dict):
            if not str(item.get("reference") or item.get("path") or "").strip():
                errors.append(f"backend_log_reference_missing:{index}")
            if not str(item.get("sha256") or "").strip():
                errors.append(f"backend_log_sha256_missing:{index}")
        else:
            errors.append(f"backend_log_ref_invalid:{index}")

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
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "protocol_version": PROTOCOL_VERSION,
        "audit": "AGENT_EXECUTION_EVIDENCE",
        "status": "PASS" if not errors else "BLOCKED",
        "request_id": request.get("request_id"),
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
        "cross_condition_contamination": False,
        "errors": sorted(set(errors)),
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
    parser.add_argument("--expected-model-config")
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
    request = load_json(request_path)
    trace = load_json(trace_path)
    if not isinstance(request, dict) or not isinstance(trace, dict):
        raise SystemExit("request and trace must be JSON objects")

    catalog = load_question_catalog(
        Path(args.question_catalog).expanduser().resolve()
        if args.question_catalog
        else None
    )
    expected_model = (
        load_json(Path(args.expected_model_config).expanduser().resolve())
        if args.expected_model_config
        else None
    )
    required_groups = {
        item.strip()
        for item in args.required_tool_groups.split(",")
        if item.strip()
    }
    request_sha256 = sha256_file(request_path)
    errors = audit_request(
        request,
        catalog=catalog,
        expected_model_config=expected_model,
        expected_condition=args.expected_condition,
    )
    errors.extend(
        audit_trace(
            trace,
            request,
            request_sha256=request_sha256,
            required_tool_groups=required_groups or None,
            require_corpus_access=args.require_corpus_access,
        )
    )
    report = build_report(
        request=request,
        trace=trace,
        request_sha256=request_sha256,
        trace_sha256=sha256_file(trace_path),
        errors=errors,
    )
    write_new_json(Path(args.out).expanduser().resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

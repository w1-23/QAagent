#!/usr/bin/env python3
"""Fail closed when an Agent request or tool trace crosses the study boundary."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping


FORBIDDEN_KEY_NAMES = {
    "target_chunk_id",
    "target_chunk_ids",
    "target_chunk_text",
    "chunk_text",
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

ALLOWED_REQUEST_KEYS = {
    "request_id",
    "run_id",
    "session_id",
    "question_id",
    "condition",
    "question",
    "system_prompt",
    "source_scope",
    "attachment",
    "research_constraints",
    "tool_permissions",
    "model_config",
    "session_mode",
    "conversation_history",
    "question_count",
    "conversation_history",
    "question_count",
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
}

DENIED_TOOL_PATH_MARKERS = {
    "03_benchmarks/citation60",
    "06_runs/raw",
    "06_runs/judge_packets",
    "06_runs/judge_blinding",
    "06_runs/judge_requests",
    "06_runs/judge_raw",
    "07_scoring",
}

PATH_ARGUMENT_KEYS = {
    "path",
    "paths",
    "file",
    "files",
    "filename",
    "directory",
    "directories",
    "glob",
    "cwd",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_json_or_jsonl(path: Path) -> list[Any]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    return value if isinstance(value, list) else [value]


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
    return {row[id_column].strip(): row[question_column].strip() for row in rows}


def load_hidden_tokens(path: Path | None, keys: Iterable[str]) -> set[str]:
    if path is None:
        return set()
    wanted = set(keys)
    tokens: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in wanted and isinstance(child, (str, int, float)):
                    candidate = str(child).strip()
                    if len(candidate) >= 8:
                        tokens.add(candidate)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for record in read_json_or_jsonl(path):
        visit(record)
    return tokens


def flatten_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from flatten_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from flatten_strings(child)


def forbidden_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            current = f"{prefix}.{key}" if prefix else str(key)
            if normalized in FORBIDDEN_KEY_NAMES:
                found.append(current)
            found.extend(forbidden_keys(child, current))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(forbidden_keys(child, f"{prefix}[{index}]"))
    return found


def identity_mismatches(
    value: Any,
    request: Mapping[str, Any],
    prefix: str = "",
) -> list[str]:
    fields = {
        "request_id",
        "session_id",
        "run_id",
        "question_id",
        "condition",
    }
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            current = f"{prefix}.{key}" if prefix else str(key)
            if (
                normalized in fields
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


def path_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def relative_markers(path_text: str) -> set[str]:
    normalized = path_text.replace("\\", "/").lower()
    return {
        marker
        for marker in DENIED_TOOL_PATH_MARKERS
        if marker.lower() in normalized
    }


def text_references_denied_root(
    value: str,
    denied_roots: Iterable[Path],
) -> bool:
    normalized = value.replace("\\", "/").lower()
    return any(
        str(root).replace("\\", "/").lower() in normalized
        for root in denied_roots
    )


def audit_tool_arguments(
    value: Any,
    *,
    denied_roots: Iterable[Path],
    prefix: str = "",
) -> list[str]:
    violations: list[str] = []
    denied = [root.resolve() for root in denied_roots]
    if isinstance(value, dict):
        for key, child in value.items():
            current = f"{prefix}.{key}" if prefix else str(key)
            for text in flatten_strings(child):
                if not text.strip():
                    continue
                markers = relative_markers(text)
                if markers:
                    violations.append(
                        f"tool_path_matches_protected_area:{current}:"
                        f"{','.join(sorted(markers))}"
                    )
                if text_references_denied_root(text, denied):
                    violations.append(
                        f"tool_argument_references_denied_root:{current}"
                    )
                if str(key).lower() in PATH_ARGUMENT_KEYS:
                    candidate = Path(text).expanduser()
                    if candidate.is_absolute() and any(
                        path_within(candidate, root) for root in denied
                    ):
                        violations.append(f"tool_path_inside_denied_root:{current}")
            violations.extend(
                audit_tool_arguments(
                    child,
                    denied_roots=denied,
                    prefix=current,
                )
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(
                audit_tool_arguments(
                    child,
                    denied_roots=denied,
                    prefix=f"{prefix}[{index}]",
                )
            )
    return violations


def audit_request(
    request: Mapping[str, Any],
    *,
    catalog: Mapping[str, str] | None = None,
    hidden_tokens: Iterable[str] = (),
    expected_model_config: Mapping[str, Any] | None = None,
    expected_condition: str | None = None,
    denied_roots: Iterable[Path] = (),
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
    for path in forbidden_keys(request):
        errors.append(f"forbidden_request_field:{path}")

    if request.get("session_mode") != "NEW":
        errors.append("session_mode_must_be_NEW")
    history = request.get("conversation_history")
    if history not in (None, [], "", {}):
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
            errors.append("question_not_in_frozen_catalog")
        elif isinstance(question, str) and question.strip() != catalog_question:
            errors.append("question_text_differs_from_frozen_catalog")

    model_config = request.get("model_config")
    if not isinstance(model_config, dict):
        errors.append("model_config_missing")
    elif expected_model_config is not None:
        if dict(model_config) != dict(expected_model_config):
            errors.append("model_config_differs_from_frozen")
    for forbidden in forbidden_keys(model_config):
        if forbidden not in {
            path for path in forbidden_keys(request) if path.startswith("model_config")
        }:
            errors.append(f"forbidden_model_config_field:{forbidden}")

    permissions = request.get("tool_permissions")
    if not isinstance(permissions, (list, dict)) or not permissions:
        errors.append("tool_permissions_missing")

    request_text = "\n".join(flatten_strings(request))
    denied = [root.resolve() for root in denied_roots]
    for value in flatten_strings(request):
        if text_references_denied_root(value, denied):
            errors.append("request_references_denied_root")
        if relative_markers(value):
            errors.append("request_references_protected_area")
    for token in hidden_tokens:
        if token and token in request_text:
            errors.append("hidden_reference_token_in_request")
            break
    return errors


def audit_trace(
    trace: Mapping[str, Any],
    request: Mapping[str, Any],
    *,
    request_sha256: str,
    denied_roots: Iterable[Path],
    allowed_tool_groups: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    if trace.get("request_sha256") != request_sha256:
        errors.append("trace_request_sha256_mismatch")
    if trace.get("request_id") != request.get("request_id"):
        errors.append("trace_request_id_mismatch")
    if trace.get("session_id") != request.get("session_id"):
        errors.append("trace_session_id_mismatch")
    if trace.get("run_id") != request.get("run_id"):
        errors.append("trace_run_id_mismatch")
    if trace.get("question_id") != request.get("question_id"):
        errors.append("trace_question_id_mismatch")
    if trace.get("condition") != request.get("condition"):
        errors.append("trace_condition_mismatch")
    for path in forbidden_keys(trace):
        if path.startswith(("tool_events", "request", "session")):
            errors.append(f"forbidden_trace_field:{path}")
    errors.extend(identity_mismatches(trace, request))
    events = trace.get("tool_events")
    if not isinstance(events, list):
        errors.append("tool_events_missing")
        return errors
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"tool_event_not_object:{index}")
            continue
        group = str(event.get("tool_group") or event.get("group") or "")
        if allowed_tool_groups is not None and group not in allowed_tool_groups:
            errors.append(f"tool_group_not_allowed:{index}:{group or 'EMPTY'}")
        for violation in audit_tool_arguments(
            event.get("arguments", {}),
            denied_roots=denied_roots,
            prefix=f"tool_events[{index}].arguments",
        ):
            errors.append(violation)
    return errors


def build_report(
    *,
    request: Mapping[str, Any],
    trace: Mapping[str, Any],
    request_sha256: str,
    trace_sha256: str,
    errors: list[str],
    l1_root: Path | None,
    repo_root: Path | None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "protocol_version": "4.3.3",
        "audit": "AGENT_INPUT_BOUNDARY",
        "status": "PASS" if not errors else "BLOCKED",
        "request_id": request.get("request_id"),
        "run_id": request.get("run_id"),
        "question_id": request.get("question_id"),
        "condition": request.get("condition"),
        "request_sha256": request_sha256,
        "trace_sha256": trace_sha256,
        "single_question_request": "question_count_must_equal_one" not in errors,
        "new_session_required": "session_mode_must_be_NEW" not in errors,
        "request_trace_sha_bound": "trace_request_sha256_mismatch" not in errors,
        "normal_platform_retrieval_allowed": True,
        "target_chunk_id_injection_allowed": False,
        "target_chunk_text_injection_allowed": False,
        "hidden_scoring_access_allowed": False,
        "l1_root_checked": str(l1_root) if l1_root else None,
        "repo_root_checked": str(repo_root) if repo_root else None,
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
    parser.add_argument("--question-catalog", required=True)
    parser.add_argument("--hidden-reference", required=True)
    parser.add_argument("--l1-root", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument(
        "--expected-condition",
        required=True,
        choices=("B0", "B1", "B2"),
    )
    parser.add_argument("--allowed-tool-groups", required=True)
    parser.add_argument("--expected-model-config", required=True)
    args = parser.parse_args()

    request_path = Path(args.request).expanduser().resolve()
    trace_path = Path(args.trace).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    request = load_json(request_path)
    trace = load_json(trace_path)
    if not isinstance(request, dict) or not isinstance(trace, dict):
        raise SystemExit("request and trace must be JSON objects")

    catalog = load_question_catalog(
        Path(args.question_catalog).expanduser().resolve()
        if args.question_catalog else None
    )
    hidden = load_hidden_tokens(
        Path(args.hidden_reference).expanduser().resolve()
        if args.hidden_reference else None,
        {
            "target_chunk_id",
            "target_chunk_text",
            "chunk_text",
            "gold_answer",
            "reference_answer",
            "expected_answer",
            "answer_key",
        },
    )
    expected_model = (
        load_json(Path(args.expected_model_config).expanduser().resolve())
        if args.expected_model_config else None
    )
    l1_root = Path(args.l1_root).expanduser().resolve() if args.l1_root else None
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else None
    denied_roots = [path for path in (l1_root, repo_root) if path]
    allowed_groups = {
        item.strip()
        for item in args.allowed_tool_groups.split(",")
        if item.strip()
    } or None
    request_sha256 = sha256_file(request_path)
    if allowed_groups is None:
        raise SystemExit("--allowed-tool-groups must not be empty")

    errors = audit_request(
        request,
        catalog=catalog,
        hidden_tokens=hidden,
        expected_model_config=expected_model,
        expected_condition=args.expected_condition,
        denied_roots=denied_roots,
    )
    errors.extend(
        audit_trace(
            trace,
            request,
            request_sha256=request_sha256,
            denied_roots=denied_roots,
            allowed_tool_groups=allowed_groups,
        )
    )
    report = build_report(
        request=request,
        trace=trace,
        request_sha256=request_sha256,
        trace_sha256=sha256_file(trace_path),
        errors=errors,
        l1_root=l1_root,
        repo_root=repo_root,
    )
    write_new_json(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

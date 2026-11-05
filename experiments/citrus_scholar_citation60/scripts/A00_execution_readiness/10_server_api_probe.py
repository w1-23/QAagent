#!/usr/bin/env python3
"""Execute declarative server/API probes without persisting credentials."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import socket
import ssl
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PROBE_KINDS = {
    "local_resources",
    "env_presence",
    "auth_flow",
    "http",
    "provider_http",
    "rate_limit",
    "model_identity",
}
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)^(authorization|proxy-authorization|cookie|set-cookie|password|"
    r"passwd|secret|token|access_token|refresh_token|id_token|auth_token|"
    r"api[_-]?key|x-api-key|credential|credentials)$"
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-ws-[A-Za-z0-9._-]{20,}"),
    re.compile(r"oc_sk_[A-Za-z0-9_-]{20,}"),
)
TEMPLATE_PATTERN = re.compile(r"^\$\{ENV:([A-Z][A-Z0-9_]*)\}$")
HEX_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
NETWORK_PROBE_KINDS = {"auth_flow", "http", "provider_http", "rate_limit"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def redact_value(value: Any, secret_values: set[str]) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            if SENSITIVE_KEY_PATTERN.search(str(key)):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = redact_value(child, secret_values)
        return redacted
    if isinstance(value, list):
        return [redact_value(child, secret_values) for child in value]
    if isinstance(value, str):
        result = value
        for secret in sorted(secret_values, key=len, reverse=True):
            if secret:
                result = result.replace(secret, "[REDACTED]")
        for pattern in SECRET_VALUE_PATTERNS:
            result = pattern.sub("[REDACTED]", result)
        return result
    return value


def template_value(value: Any, missing: list[str]) -> Any:
    if isinstance(value, dict):
        return {key: template_value(child, missing) for key, child in value.items()}
    if isinstance(value, list):
        return [template_value(child, missing) for child in value]
    if isinstance(value, str):
        match = TEMPLATE_PATTERN.fullmatch(value)
        if match:
            name = match.group(1)
            if name not in os.environ:
                missing.append(name)
                return None
            return os.environ[name]
    return value


def template_env_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            names.update(template_env_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(template_env_names(child))
    elif isinstance(value, str):
        match = TEMPLATE_PATTERN.fullmatch(value)
        if match:
            names.add(match.group(1))
    return names


def read_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("probe plan must be a JSON object")
    return payload


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def normalize_origin(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = 443 if scheme == "https" else 80
    netloc = host if port in (None, default_port) else f"{host}:{port}"
    return f"{scheme}://{netloc}"


def dot_path_value(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def generated_content(parsed: Any) -> str | None:
    if not isinstance(parsed, dict):
        return None
    choices = parsed.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    output_text = parsed.get("output_text")
    if isinstance(output_text, str):
        return output_text
    output = parsed.get("output")
    if isinstance(output, list):
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        if texts:
            return "".join(texts)
    return None


def usage_errors(usage: Any) -> list[str]:
    if usage is None:
        return []
    if not isinstance(usage, dict) or not usage:
        return ["provider_usage_schema_invalid"]
    numeric = [
        value
        for key, value in usage.items()
        if "token" in str(key).lower() and isinstance(value, (int, float))
    ]
    if "token" in json.dumps(usage, ensure_ascii=False).lower() and not numeric:
        return ["provider_usage_token_counts_missing"]
    return []


def provider_response_errors(
    call: Mapping[str, Any],
    parsed: Any,
    *,
    actual_model: Any,
    request_id: Any,
) -> list[str]:
    errors: list[str] = []
    text = generated_content(parsed)
    if not isinstance(text, str) or not text.strip():
        errors.append("provider_generated_content_empty")
    if not isinstance(request_id, str) or not request_id.strip():
        errors.append("provider_response_id_missing")
    if not isinstance(actual_model, str) or not actual_model.strip():
        errors.append("provider_actual_model_missing")
    if call.get("expected_response_format") == "json_object":
        content_object: Any = None
        if isinstance(parsed, dict) and isinstance(parsed.get("parsed_content"), dict):
            content_object = parsed["parsed_content"]
        elif isinstance(text, str):
            try:
                content_object = json.loads(text)
            except json.JSONDecodeError:
                content_object = None
        if not isinstance(content_object, dict):
            errors.append("provider_json_content_invalid")
        else:
            required_keys = {
                str(value) for value in call.get("required_content_keys", [])
            }
            missing = sorted(required_keys - set(content_object))
            if missing:
                errors.append(
                    "provider_json_content_missing_keys:" + ",".join(missing)
                )
    errors.extend(usage_errors(parsed.get("usage") if isinstance(parsed, dict) else None))
    return errors


def plan_validation_errors(plan: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in (
        "schema_version",
        "probe_version",
        "status",
        "reviewed_commit",
        "reviewed_tree",
        "execution_guards",
        "probes",
    ):
        if key not in plan:
            errors.append(f"missing_plan_field:{key}")
    if plan.get("status") not in {"UNCONFIGURED", "PILOT_READY", "READY"}:
        errors.append("invalid_plan_status")
    if not isinstance(plan.get("execution_authorized"), bool):
        errors.append("execution_authorized_must_be_boolean")
    if not isinstance(plan.get("review_status"), str):
        errors.append("review_status_missing")
    reviewed_commit = plan.get("reviewed_commit")
    if not isinstance(reviewed_commit, str) or not GIT_SHA_PATTERN.fullmatch(
        reviewed_commit
    ):
        errors.append("reviewed_commit_invalid")
    reviewed_tree = plan.get("reviewed_tree")
    if not isinstance(reviewed_tree, str) or not GIT_SHA_PATTERN.fullmatch(
        reviewed_tree
    ):
        errors.append("reviewed_tree_invalid")
    guards = plan.get("execution_guards")
    if not isinstance(guards, dict):
        errors.append("execution_guards_missing")
    else:
        if not isinstance(guards.get("required"), bool):
            errors.append("execution_guards_required_must_be_boolean")
        if not isinstance(guards.get("experiment_origin_allowlist"), list):
            errors.append("experiment_origin_allowlist_missing")
        if not isinstance(guards.get("stage0_report_sha256"), str):
            errors.append("stage0_report_sha256_missing")
        if not isinstance(guards.get("checkout_root_env"), str):
            errors.append("checkout_root_env_missing")
    probes = plan.get("probes")
    if not isinstance(probes, list) or not probes:
        errors.append("probes_must_be_nonempty_list")
        return errors
    ids: set[str] = set()
    for index, probe in enumerate(probes):
        if not isinstance(probe, dict):
            errors.append(f"probe_not_object:{index}")
            continue
        probe_id = str(probe.get("id") or "")
        if not probe_id:
            errors.append(f"probe_missing_id:{index}")
        elif probe_id in ids:
            errors.append(f"duplicate_probe_id:{probe_id}")
        ids.add(probe_id)
        kind = probe.get("kind")
        if kind not in PROBE_KINDS:
            errors.append(f"invalid_probe_kind:{probe_id}:{kind}")
            continue
        if kind in {"http", "provider_http", "rate_limit"}:
            if not probe.get("base_url_env"):
                errors.append(f"missing_base_url_env:{probe_id}")
            if not probe.get("path_candidates"):
                errors.append(f"missing_path_candidates:{probe_id}")
        if kind in {"http", "auth_flow", "rate_limit"} and probe.get(
            "base_url_env"
        ) != "CITRUS_EXPERIMENT_BASE_URL":
            errors.append(f"app_probe_base_url_env_not_experiment:{probe_id}")
        if kind == "provider_http":
            calls = probe.get("calls")
            if not isinstance(calls, list) or not calls:
                errors.append(f"provider_calls_missing:{probe_id}")
            elif any(not call.get("role") or not call.get("model_id") for call in calls):
                errors.append(f"provider_call_role_or_model_missing:{probe_id}")
            if not probe.get("api_key_env"):
                errors.append(f"provider_api_key_env_missing:{probe_id}")
            for call in calls if isinstance(calls, list) else []:
                if not isinstance(call, dict):
                    continue
                if (
                    call.get("expected_response_format")
                    not in {None, "text", "json_object"}
                ):
                    errors.append(
                        f"provider_expected_response_format_invalid:{probe_id}"
                    )
                if call.get("expected_response_format") == "json_object" and not isinstance(
                    call.get("required_content_keys"), list
                ):
                    errors.append(
                        f"provider_required_content_keys_missing:{probe_id}"
                    )
        if kind == "env_presence" and not isinstance(probe.get("variables"), list):
            errors.append(f"env_variables_missing:{probe_id}")
        if kind == "env_presence" and not probe.get("environment_file_env"):
            errors.append(f"environment_file_env_missing:{probe_id}")
        if kind == "http" and not isinstance(probe.get("required_json_paths", {}), dict):
            errors.append(f"required_json_paths_must_be_object:{probe_id}")
        if kind == "local_resources" and not isinstance(
            probe.get("required_python_distributions", []),
            list,
        ):
            errors.append(f"python_distributions_must_be_list:{probe_id}")
        if kind == "rate_limit":
            max_requests = probe.get("max_requests")
            if not isinstance(max_requests, int) or not 1 <= max_requests <= 20:
                errors.append(f"rate_limit_max_requests_invalid:{probe_id}")
            interval = probe.get("interval_seconds", 0)
            if not isinstance(interval, (int, float)) or not 0 <= interval <= 10:
                errors.append(f"rate_limit_interval_invalid:{probe_id}")
            if not isinstance(probe.get("expect_throttle"), bool):
                errors.append(f"rate_limit_expect_throttle_missing:{probe_id}")
            if len(probe.get("path_candidates", [])) != 1:
                errors.append(f"rate_limit_requires_one_path:{probe_id}")
        if kind == "model_identity":
            if not isinstance(probe.get("allowlists"), dict):
                errors.append(f"model_allowlists_missing:{probe_id}")
            if not isinstance(probe.get("source_probe_ids"), list):
                errors.append(f"model_source_probes_missing:{probe_id}")
        if kind == "auth_flow":
            for section in ("register", "login", "verify"):
                definition = probe.get(section)
                if not isinstance(definition, dict):
                    errors.append(f"auth_flow_{section}_missing:{probe_id}")
                    continue
                if not definition.get("path_candidates"):
                    errors.append(f"auth_flow_{section}_paths_missing:{probe_id}")

    env_probe = next(
        (
            probe
            for probe in probes
            if isinstance(probe, dict) and probe.get("kind") == "env_presence"
        ),
        None,
    )
    declared_env = {
        str(name)
        for name in (env_probe or {}).get("variables", [])
    }
    if env_probe and env_probe.get("environment_file_env"):
        declared_env.add(str(env_probe["environment_file_env"]))
    referenced_env: set[str] = set()
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        for field in ("base_url_env", "api_key_env"):
            if probe.get(field):
                referenced_env.add(str(probe[field]))
        if isinstance(probe.get("auth"), dict) and probe["auth"].get("token_env"):
            referenced_env.add(str(probe["auth"]["token_env"]))
        referenced_env.update(template_env_names(probe.get("body_template")))
        if probe.get("kind") == "auth_flow":
            for section in ("register", "login", "verify"):
                definition = probe.get(section)
                if isinstance(definition, dict):
                    referenced_env.update(
                        template_env_names(definition.get("body_template"))
                    )
        for call in probe.get("calls", []) or []:
            if not isinstance(call, dict):
                continue
            if call.get("api_key_env"):
                referenced_env.add(str(call["api_key_env"]))
            if isinstance(call.get("auth"), dict) and call["auth"].get("token_env"):
                referenced_env.add(str(call["auth"]["token_env"]))
            referenced_env.update(template_env_names(call.get("body_template")))
    missing_env_declarations = sorted(referenced_env - declared_env)
    if missing_env_declarations:
        errors.append(
            "env_probe_does_not_declare:"
            + ",".join(missing_env_declarations)
        )
    return errors


def execution_authorization_errors(plan: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if plan.get("status") != "READY":
        errors.append("status_not_READY")
    if plan.get("execution_authorized") is not True:
        errors.append("execution_authorized_not_true")
    if plan.get("review_status") not in {
        "APPROVED",
        "APPROVED_FOR_EXECUTION",
    }:
        errors.append("review_status_not_approved")
    guards = plan.get("execution_guards")
    if not isinstance(guards, dict) or guards.get("required") is not True:
        errors.append("execution_guards_not_required")
    else:
        allowlist = guards.get("experiment_origin_allowlist")
        normalized = {
            origin
            for value in allowlist or []
            if isinstance(value, str)
            for origin in [normalize_origin(value)]
            if origin
        }
        if not normalized or len(normalized) != len(allowlist or []):
            errors.append("experiment_origin_allowlist_invalid")
        if not HEX_SHA256_PATTERN.fullmatch(
            str(guards.get("stage0_report_sha256") or "")
        ):
            errors.append("stage0_report_sha256_not_frozen")
        if not str(guards.get("checkout_root_env") or ""):
            errors.append("checkout_root_env_missing")
    return errors


def stage0_guard_errors(
    plan: Mapping[str, Any],
    report_path: Path,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    guards = plan.get("execution_guards")
    if not isinstance(guards, dict):
        return None, ["execution_guards_missing"]
    if not report_path.is_file():
        return None, ["stage0_report_missing"]
    report_sha = sha256_file(report_path)
    expected_sha = str(guards.get("stage0_report_sha256") or "")
    if not HEX_SHA256_PATTERN.fullmatch(expected_sha):
        errors.append("stage0_report_sha_not_frozen")
    elif report_sha != expected_sha:
        errors.append("stage0_report_sha_mismatch")
    try:
        report = load_json_object(report_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, errors + ["stage0_report_invalid_json"]
    if report.get("status") != "PASS":
        errors.append("stage0_report_not_pass")
    if report.get("reviewed_commit") != plan.get("reviewed_commit"):
        errors.append("stage0_reviewed_commit_mismatch")
    if report.get("reviewed_tree") != plan.get("reviewed_tree"):
        errors.append("stage0_reviewed_tree_mismatch")

    experiment = report.get("experiment")
    isolation = report.get("isolation")
    production = report.get("production")
    corpora = report.get("corpora")
    index = report.get("index")
    tools = report.get("tools")
    logs = report.get("logs")
    runtime = report.get("runtime")
    if not isinstance(experiment, dict):
        errors.append("stage0_experiment_identity_missing")
        experiment = {}
    if not isinstance(isolation, dict):
        errors.append("stage0_isolation_evidence_missing")
        isolation = {}
    if not isinstance(production, dict):
        errors.append("stage0_production_boundary_missing")
        production = {}
    if not isinstance(corpora, dict):
        errors.append("stage0_corpora_evidence_missing")
        corpora = {}
    if not isinstance(index, dict):
        errors.append("stage0_index_evidence_missing")
        index = {}
    if not isinstance(tools, dict):
        errors.append("stage0_tool_registration_missing")
        tools = {}
    if not isinstance(logs, dict):
        errors.append("stage0_log_destinations_missing")
        logs = {}
    if not isinstance(runtime, dict):
        errors.append("stage0_runtime_snapshot_missing")
        runtime = {}

    experiment_root_text = str(isolation.get("experiment_root") or "").strip()
    production_root_text = str(isolation.get("production_root") or "").strip()
    experiment_root_path = (
        Path(experiment_root_text).expanduser().resolve()
        if experiment_root_text and Path(experiment_root_text).is_absolute()
        else None
    )
    production_root_path = (
        Path(production_root_text).expanduser().resolve()
        if production_root_text and Path(production_root_text).is_absolute()
        else None
    )

    def path_is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def verify_report_evidence(
        path_value: Any,
        sha_value: Any,
        label: str,
    ) -> bytes | None:
        path_text = str(path_value or "").strip()
        if not path_text:
            errors.append(f"stage0_evidence_path_missing:{label}")
            return None
        if not HEX_SHA256_PATTERN.fullmatch(str(sha_value or "")):
            errors.append(f"stage0_evidence_sha_invalid:{label}")
            return None
        candidate = Path(path_text).expanduser()
        evidence_path = (
            candidate
            if candidate.is_absolute()
            else report_path.parent / candidate
        ).resolve()
        if experiment_root_path is None:
            errors.append(f"stage0_evidence_root_not_established:{label}")
        elif not path_is_within(evidence_path, experiment_root_path):
            errors.append(f"stage0_evidence_path_outside_experiment_root:{label}")
        if not evidence_path.is_file():
            errors.append(f"stage0_evidence_file_missing:{label}")
            return None
        evidence_bytes = evidence_path.read_bytes()
        if not evidence_bytes:
            errors.append(f"stage0_evidence_file_empty:{label}")
        actual_sha = sha256_bytes(evidence_bytes)
        if actual_sha != str(sha_value):
            errors.append(f"stage0_evidence_sha_mismatch:{label}")
        return evidence_bytes

    resolved_report_path = report_path.expanduser().resolve()
    if experiment_root_path is None:
        errors.append("stage0_experiment_root_invalid")
    elif not path_is_within(resolved_report_path, experiment_root_path):
        errors.append("stage0_report_outside_experiment_root")
    if production_root_path is None:
        errors.append("stage0_production_root_invalid")
    if experiment_root_path is not None and production_root_path is not None:
        if (
            experiment_root_path == production_root_path
            or path_is_within(experiment_root_path, production_root_path)
            or path_is_within(production_root_path, experiment_root_path)
        ):
            errors.append("stage0_experiment_and_production_roots_not_separate")

    required_isolation_true = (
        "separate_service",
        "separate_database",
        "separate_sessions",
        "separate_uploads",
        "separate_artifacts",
        "production_write_denied",
        "production_credentials_unreadable",
    )
    for name in required_isolation_true:
        if isolation.get(name) is not True:
            errors.append(f"stage0_isolation_not_verified:{name}")
    for name in (
        "base_url_origin",
        "deployment_id",
        "database_identity",
        "service_identity",
        "deployed_file_manifest_path",
        "deployed_file_manifest_sha256",
        "deployed_file_root",
    ):
        if not str(experiment.get(name) or "").strip():
            errors.append(f"stage0_experiment_identity_missing:{name}")
    for name in (
        "deployment_id",
        "deployed_file_manifest_sha256",
    ):
        if experiment.get(name) and not HEX_SHA256_PATTERN.fullmatch(
            str(experiment.get(name))
        ):
            errors.append(f"stage0_experiment_sha256_invalid:{name}")
    deployment_manifest_bytes = verify_report_evidence(
        experiment.get("deployed_file_manifest_path"),
        experiment.get("deployed_file_manifest_sha256"),
        "experiment.deployed_file_manifest",
    )
    deployed_root_text = str(experiment.get("deployed_file_root") or "").strip()
    deployed_root_path = (
        Path(deployed_root_text).expanduser().resolve()
        if deployed_root_text and Path(deployed_root_text).is_absolute()
        else None
    )
    if deployed_root_path is None:
        errors.append("stage0_deployed_file_root_invalid")
    elif experiment_root_path is not None and not path_is_within(
        deployed_root_path, experiment_root_path
    ):
        errors.append("stage0_deployed_file_root_outside_experiment_root")
    elif not deployed_root_path.is_dir():
        errors.append("stage0_deployed_file_root_missing")
    if deployment_manifest_bytes is not None:
        try:
            deployment_manifest = json.loads(
                deployment_manifest_bytes.decode("utf-8-sig")
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            deployment_manifest = None
            errors.append("stage0_deployed_file_manifest_invalid_json")
        if not isinstance(deployment_manifest, dict):
            errors.append("stage0_deployed_file_manifest_invalid_object")
        else:
            manifest_commit = deployment_manifest.get(
                "git_commit",
                deployment_manifest.get("commit"),
            )
            manifest_tree = deployment_manifest.get(
                "git_tree",
                deployment_manifest.get("tree"),
            )
            manifest_files = deployment_manifest.get("files")
            if manifest_commit != experiment.get("deployment_git_sha"):
                errors.append("stage0_deployed_file_manifest_commit_mismatch")
            if manifest_tree != experiment.get("deployment_git_tree"):
                errors.append("stage0_deployed_file_manifest_tree_mismatch")
            manifest_deployment_id = str(
                deployment_manifest.get("deployment_id") or ""
            )
            if not HEX_SHA256_PATTERN.fullmatch(manifest_deployment_id):
                errors.append(
                    "stage0_deployed_file_manifest_deployment_id_missing_or_invalid"
                )
            elif manifest_deployment_id != experiment.get("deployment_id"):
                errors.append("stage0_deployed_file_manifest_deployment_id_mismatch")
            if not isinstance(manifest_files, list) or not manifest_files:
                errors.append("stage0_deployed_file_manifest_files_missing")
            else:
                manifest_paths: list[str] = []
                for file_index, entry in enumerate(manifest_files):
                    if not isinstance(entry, dict):
                        errors.append(
                            f"stage0_deployed_file_manifest_entry_invalid:{file_index}"
                        )
                        continue
                    relative_path = str(entry.get("path") or "").strip()
                    digest = entry.get("sha256")
                    if not relative_path:
                        errors.append(
                            f"stage0_deployed_file_manifest_path_missing:{file_index}"
                        )
                    else:
                        relative = Path(relative_path)
                        if (
                            relative.is_absolute()
                            or relative.drive
                            or not relative.parts
                            or any(part in {"", ".", ".."} for part in relative.parts)
                        ):
                            errors.append(
                                "stage0_deployed_file_manifest_path_unsafe:"
                                f"{file_index}"
                            )
                        else:
                            normalized_path = relative.as_posix()
                            manifest_paths.append(normalized_path)
                            if deployed_root_path is not None:
                                deployed_file = (
                                    deployed_root_path / relative
                                ).resolve()
                                if not path_is_within(
                                    deployed_file, deployed_root_path
                                ):
                                    errors.append(
                                        "stage0_deployed_file_outside_root:"
                                        f"{file_index}"
                                    )
                                elif not deployed_file.is_file():
                                    errors.append(
                                        "stage0_deployed_file_missing:"
                                        f"{file_index}"
                                    )
                                elif deployed_file.stat().st_size <= 0:
                                    errors.append(
                                        "stage0_deployed_file_empty:"
                                        f"{file_index}"
                                    )
                                elif (
                                    sha256_file(deployed_file)
                                    != str(digest or "")
                                ):
                                    errors.append(
                                        "stage0_deployed_file_sha_mismatch:"
                                        f"{file_index}"
                                    )
                    if not HEX_SHA256_PATTERN.fullmatch(str(digest or "")):
                        errors.append(
                            f"stage0_deployed_file_manifest_sha_invalid:{file_index}"
                        )
                if len(set(manifest_paths)) != len(manifest_paths):
                    errors.append("stage0_deployed_file_manifest_paths_not_unique")
    if experiment.get("deployment_git_sha") != plan.get("reviewed_commit"):
        errors.append("stage0_deployment_sha_differs_from_reviewed")
    if experiment.get("deployment_git_tree") != plan.get("reviewed_tree"):
        errors.append("stage0_deployment_tree_differs_from_reviewed")
    if experiment.get("health_status") != "ok":
        errors.append("stage0_health_not_ok")
    if not str(experiment.get("health_version") or "").strip():
        errors.append("stage0_health_version_missing")
    for name in (
        "identity_sha256",
        "write_denial_evidence_sha256",
        "credentials_unreadable_evidence_sha256",
    ):
        value = str(production.get(name) or "")
        if not value:
            errors.append(f"stage0_production_evidence_missing:{name}")
        elif not HEX_SHA256_PATTERN.fullmatch(value):
            errors.append(f"stage0_production_sha256_invalid:{name}")
    for name in (
        "identity",
        "write_denial_evidence",
        "credentials_unreadable_evidence",
    ):
        verify_report_evidence(
            production.get(f"{name}_path"),
            production.get(f"{name}_sha256"),
            f"production.{name}",
        )
    if tools.get("registered_count") != 17:
        errors.append("stage0_registered_tool_count_not_17")
    tool_names = tools.get("names")
    if not isinstance(tool_names, list) or len(tool_names) != 17:
        errors.append("stage0_registered_tool_names_incomplete")
    else:
        normalized_tools = [
            str(value).strip() if isinstance(value, str) else ""
            for value in tool_names
        ]
        if any(not value for value in normalized_tools):
            errors.append("stage0_registered_tool_name_empty")
        if len(set(normalized_tools)) != 17:
            errors.append("stage0_registered_tool_names_not_unique")
        if any(
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{1,127}", value)
            for value in normalized_tools
            if value
        ):
            errors.append("stage0_registered_tool_name_invalid")
    if not HEX_SHA256_PATTERN.fullmatch(
        str(tools.get("registration_evidence_sha256") or "")
    ):
        errors.append("stage0_tool_registration_evidence_sha_invalid")
    tool_registration_bytes = verify_report_evidence(
        tools.get("registration_evidence_path"),
        tools.get("registration_evidence_sha256"),
        "tools.registration_evidence",
    )
    normalized_tools: list[str] = []
    normalized_tool_registry: list[dict[str, str]] = []
    if isinstance(tool_names, list):
        normalized_tools = [
            str(value).strip() if isinstance(value, str) else ""
            for value in tool_names
        ]
    if tool_registration_bytes is not None:
        try:
            tool_registration = json.loads(
                tool_registration_bytes.decode("utf-8-sig")
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            tool_registration = None
            errors.append("stage0_tool_registration_evidence_invalid_json")
        registered_rows = (
            tool_registration.get("tools")
            if isinstance(tool_registration, dict)
            else None
        )
        if not isinstance(registered_rows, list):
            errors.append("stage0_tool_registration_evidence_tools_missing")
        else:
            registration_versions: set[str] = set()
            registered_paths: set[str] = set()
            registered_names = [
                (
                    str(row.get("name") or row.get("tool_name") or "").strip()
                    if isinstance(row, dict)
                    else str(row).strip()
                )
                for row in registered_rows
            ]
            if (
                len(registered_names) != len(normalized_tools)
                or set(registered_names) != set(normalized_tools)
            ):
                errors.append("stage0_tool_registration_names_mismatch")
            if len(registered_rows) != 17:
                errors.append("stage0_tool_registration_count_not_17")
            for row_index, row in enumerate(registered_rows):
                if not isinstance(row, dict):
                    errors.append(
                        f"stage0_tool_registration_entry_invalid:{row_index}"
                    )
                    continue
                name = str(row.get("name") or row.get("tool_name") or "").strip()
                group = str(row.get("tool_group") or row.get("group") or "").strip()
                category = str(row.get("tool_category") or row.get("category") or "").strip()
                version = str(row.get("registration_version") or "").strip()
                if not name:
                    errors.append(
                        f"stage0_tool_registration_name_missing:{row_index}"
                    )
                if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,31}", group):
                    errors.append(
                        f"stage0_tool_registration_group_invalid:{row_index}"
                    )
                if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", category):
                    errors.append(
                        f"stage0_tool_registration_category_invalid:{row_index}"
                    )
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", version):
                    errors.append(
                        f"stage0_tool_registration_version_invalid:{row_index}"
                    )
                if version:
                    registration_versions.add(version)
                normalized_tool_registry.append(
                    {
                        "tool_name": name,
                        "tool_group": group,
                        "tool_category": category,
                        "registration_version": version,
                    }
                )
            if len(registration_versions) != 1:
                errors.append(
                    "stage0_tool_registration_version_not_single_or_missing"
                )
            registry_names = [
                entry["tool_name"] for entry in normalized_tool_registry
            ]
            if len(set(registry_names)) != len(registry_names):
                errors.append("stage0_tool_registration_entries_not_unique")
    if corpora.get("all_five_categories_accessible") is not True:
        errors.append("stage0_five_corpora_not_accessible")
    registrations = corpora.get("registrations")
    if not isinstance(registrations, list) or len(registrations) != 5:
        errors.append("stage0_five_corpus_registrations_missing")
    else:
        corpus_ids: list[str] = []
        for registration_index, registration in enumerate(registrations):
            if not isinstance(registration, dict):
                errors.append(
                    f"stage0_corpus_registration_invalid:{registration_index}"
                )
                continue
            corpus_id = str(registration.get("corpus_id") or "").strip()
            if not corpus_id:
                errors.append(f"stage0_corpus_id_missing:{registration_index}")
            else:
                corpus_ids.append(corpus_id)
            source_count = registration.get("source_count")
            if not isinstance(source_count, int) or isinstance(source_count, bool):
                errors.append(
                    f"stage0_corpus_source_count_invalid:{registration_index}"
                )
            elif source_count <= 0:
                errors.append(
                    f"stage0_corpus_source_count_empty:{registration_index}"
                )
            if registration.get("accessible_from_locked_account") is not True:
                errors.append(
                    f"stage0_corpus_not_accessible:{registration_index}"
                )
            if not str(registration.get("generation") or "").strip():
                errors.append(
                    f"stage0_corpus_generation_missing:{registration_index}"
                )
            if not HEX_SHA256_PATTERN.fullmatch(
                str(registration.get("source_manifest_sha256") or "")
            ):
                errors.append(
                    f"stage0_corpus_manifest_sha_invalid:{registration_index}"
                )
            access_bytes = verify_report_evidence(
                registration.get("access_evidence_path"),
                registration.get("access_evidence_sha256"),
                f"corpora.registrations[{registration_index}].access_evidence",
            )
            if access_bytes is not None and corpus_id:
                access_text = access_bytes.decode("utf-8", errors="replace")
                if corpus_id not in access_text:
                    errors.append(
                        f"stage0_corpus_access_evidence_not_bound:{registration_index}"
                    )
            source_manifest_bytes = verify_report_evidence(
                registration.get("source_manifest_path"),
                registration.get("source_manifest_sha256"),
                f"corpora.registrations[{registration_index}].source_manifest",
            )
            if source_manifest_bytes is not None:
                try:
                    source_manifest = json.loads(
                        source_manifest_bytes.decode("utf-8-sig")
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    source_manifest = None
                    errors.append(
                        f"stage0_corpus_source_manifest_invalid_json:{registration_index}"
                    )
                sources = (
                    source_manifest.get("sources")
                    if isinstance(source_manifest, dict)
                    else None
                )
                if not isinstance(sources, list):
                    errors.append(
                        f"stage0_corpus_source_manifest_sources_missing:{registration_index}"
                    )
                elif isinstance(source_count, int) and len(sources) != source_count:
                    errors.append(
                        f"stage0_corpus_source_count_mismatch:{registration_index}"
                    )
        if len(set(corpus_ids)) != len(corpus_ids):
            errors.append("stage0_corpus_ids_not_unique")
    if index.get("source_versions_recorded") is not True:
        errors.append("stage0_index_source_versions_not_recorded")
    if not str(index.get("generation") or "").strip():
        errors.append("stage0_index_generation_missing")
    if not HEX_SHA256_PATTERN.fullmatch(
        str(index.get("manifest_sha256") or "")
    ):
        errors.append("stage0_index_manifest_sha_invalid")
    index_manifest_bytes = verify_report_evidence(
        index.get("manifest_path"),
        index.get("manifest_sha256"),
        "index.manifest",
    )
    if index_manifest_bytes is not None:
        try:
            index_manifest = json.loads(
                index_manifest_bytes.decode("utf-8-sig")
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            index_manifest = None
            errors.append("stage0_index_manifest_invalid_json")
        if not isinstance(index_manifest, dict):
            errors.append("stage0_index_manifest_invalid_object")
        else:
            if index_manifest.get("generation") != index.get("generation"):
                errors.append("stage0_index_manifest_generation_mismatch")
            source_versions = index_manifest.get("source_versions")
            if not isinstance(source_versions, (dict, list)) or not source_versions:
                errors.append("stage0_index_manifest_source_versions_missing")
    for name in ("api_log", "task_log", "retrieval_log", "tool_log"):
        log = logs.get(name)
        if not isinstance(log, dict):
            errors.append(f"stage0_log_evidence_invalid:{name}")
            continue
        path = str(log.get("path") or "").strip()
        if not path or not Path(path).is_absolute():
            errors.append(f"stage0_log_path_invalid:{name}")
        if not HEX_SHA256_PATTERN.fullmatch(str(log.get("sha256") or "")):
            errors.append(f"stage0_log_sha_invalid:{name}")
        verify_report_evidence(
            log.get("path"),
            log.get("sha256"),
            f"logs.{name}",
        )
    for name in ("python_version", "system_snapshot"):
        if not str(runtime.get(name) or "").strip():
            errors.append(f"stage0_runtime_field_missing:{name}")
    for name in ("dependency_lock_sha256", "service_config_sha256"):
        if not HEX_SHA256_PATTERN.fullmatch(str(runtime.get(name) or "")):
            errors.append(f"stage0_runtime_sha_invalid:{name}")
    verify_report_evidence(
        runtime.get("dependency_lock_path"),
        runtime.get("dependency_lock_sha256"),
        "runtime.dependency_lock",
    )
    verify_report_evidence(
        runtime.get("service_config_path"),
        runtime.get("service_config_sha256"),
        "runtime.service_config",
    )

    allowlist = {
        origin
        for value in guards.get("experiment_origin_allowlist", [])
        if isinstance(value, str)
        for origin in [normalize_origin(value)]
        if origin
    }
    report_origin = normalize_origin(str(experiment.get("base_url_origin") or ""))
    if report_origin not in allowlist:
        errors.append("stage0_experiment_origin_not_allowlisted")
    guard = {
        "status": "PASS" if not errors else "BLOCKED",
        "report_path_sha256": report_sha,
        "origin_allowlist": sorted(allowlist),
        "experiment_origin": report_origin,
        "deployment_id": experiment.get("deployment_id"),
        "database_identity": experiment.get("database_identity"),
        "service_identity": experiment.get("service_identity"),
        "experiment_root": str(experiment_root_path or ""),
        "production_root": str(production_root_path or ""),
        "deployed_file_manifest_sha256": experiment.get(
            "deployed_file_manifest_sha256"
        ),
        "tool_registration_evidence_sha256": tools.get(
            "registration_evidence_sha256"
        ),
        "tool_names": normalized_tools,
        "tool_registry": normalized_tool_registry,
        "errors": sorted(set(errors)),
    }
    return guard, sorted(set(errors))


def model_binding_errors(
    plan: Mapping[str, Any],
    model_config: Mapping[str, Any],
    judge_config: Mapping[str, Any] | None,
) -> list[str]:
    errors: list[str] = []
    agent_calls = [
        call
        for probe in plan.get("probes", [])
        if isinstance(probe, dict) and probe.get("kind") == "provider_http"
        for call in probe.get("calls", [])
        if isinstance(call, dict) and call.get("role") == "agent"
    ]
    candidate = str(model_config.get("candidate_model_id") or "")
    if model_config.get("status") not in {"PILOT_READY", "READY"}:
        errors.append("agent_model_config_status_not_probe_ready")
    if not candidate:
        errors.append("agent_candidate_model_id_missing")
    for call in agent_calls:
        if str(call.get("model_id") or "") != candidate:
            errors.append("agent_probe_model_differs_from_frozen_config")
        model_env = call.get("model_id_env")
        if model_env:
            actual_env = os.environ.get(str(model_env))
            if actual_env != candidate:
                errors.append("agent_model_environment_differs_from_frozen_config")
    if not agent_calls:
        errors.append("agent_provider_probe_missing")
    runtime_probe = next(
        (
            item
            for item in plan.get("probes", [])
            if isinstance(item, dict) and item.get("kind") == "http"
            and item.get("id") == "runtime_config_probe"
        ),
        None,
    )
    if not runtime_probe:
        errors.append("runtime_config_probe_missing")
    else:
        expected_paths = runtime_probe.get("required_json_paths", {})
        if not isinstance(expected_paths, dict):
            errors.append("runtime_config_required_paths_missing")
        else:
            if expected_paths.get("model.provider_id") != model_config.get(
                "provider_id"
            ):
                errors.append("runtime_config_provider_expectation_mismatch")
            if expected_paths.get("model.main") != candidate:
                errors.append("runtime_config_model_expectation_mismatch")

    if judge_config is not None:
        if judge_config.get("status") not in {"PILOT_READY", "READY"}:
            errors.append("judge_config_status_not_probe_ready")
        roles = [
            call.get("role")
            for probe in plan.get("probes", [])
            if isinstance(probe, dict) and probe.get("kind") == "provider_http"
            for call in probe.get("calls", [])
            if isinstance(call, dict) and str(call.get("role", "")).startswith("judge_")
        ]
        configured = judge_config.get("judges", {})
        identity_probe = next(
            (
                item
                for item in plan.get("probes", [])
                if isinstance(item, dict) and item.get("kind") == "model_identity"
            ),
            None,
        )
        identity_allowlists = (
            identity_probe.get("allowlists", {})
            if isinstance(identity_probe, dict)
            else {}
        )
        seen_candidates: set[str] = set()
        for role in ("judge_1", "judge_2", "judge_3"):
            if role not in roles:
                errors.append(f"judge_probe_missing:{role}")
            definition = configured.get(role) if isinstance(configured, dict) else None
            if not isinstance(definition, dict):
                errors.append(f"judge_config_missing:{role}")
                continue
            if str(definition.get("family") or "").lower() == "deepseek":
                errors.append(f"judge_family_forbidden:{role}")
            candidate = str(definition.get("candidate_model_id") or "")
            allowed = {
                str(value)
                for value in definition.get("allowed_model_ids", [])
                if str(value).strip()
            }
            if not candidate:
                errors.append(f"judge_candidate_model_missing:{role}")
            elif candidate in seen_candidates:
                errors.append(f"judge_candidate_model_duplicate:{role}")
            else:
                seen_candidates.add(candidate)
            if not allowed:
                errors.append(f"judge_allowed_models_missing:{role}")
            elif candidate and candidate not in allowed:
                errors.append(f"judge_candidate_not_in_allowed_models:{role}")
            actual_model = str(definition.get("actual_response_model") or "")
            if actual_model and actual_model not in allowed:
                errors.append(f"judge_actual_response_model_not_allowed:{role}")
            role_calls = [
                call
                for probe in plan.get("probes", [])
                if isinstance(probe, dict) and probe.get("kind") == "provider_http"
                for call in probe.get("calls", [])
                if isinstance(call, dict) and call.get("role") == role
            ]
            if not role_calls:
                errors.append(f"judge_probe_missing:{role}")
            for call in role_calls:
                if str(call.get("model_id") or "") != candidate:
                    errors.append(f"judge_probe_model_differs_from_config:{role}")
                probe_allowed = {
                    str(value)
                    for value in call.get("allowed_response_models", [])
                    if str(value).strip()
                }
                if probe_allowed != allowed:
                    errors.append(f"judge_probe_allowlist_differs_from_config:{role}")
            identity_allowed = {
                str(value)
                for value in (
                    identity_allowlists.get(role, [])
                    if isinstance(identity_allowlists, dict)
                    else []
                )
                if str(value).strip()
            }
            if identity_allowed != allowed:
                errors.append(f"judge_identity_allowlist_differs_from_config:{role}")
        if judge_config.get("forbidden_judge_family") != "DeepSeek":
            errors.append("judge_config_must_forbid_deepseek_family")
    return sorted(set(errors))


def write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def local_resources(probe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    usage = shutil.disk_usage(Path.cwd())
    package_names = sorted(
        {
            str(name)
            for name in (probe or {}).get("required_python_distributions", [])
        }
    )
    package_versions: dict[str, str | None] = {}
    for name in package_names:
        try:
            package_versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            package_versions[name] = None
    result: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version,
        "python_executable": sys.executable,
        "cpu_count": os.cpu_count(),
        "cwd": str(Path.cwd()),
        "disk_total_bytes": usage.total,
        "disk_free_bytes": usage.free,
        "required_python_distributions": package_names,
        "python_distribution_versions": package_versions,
        "missing_python_distributions": sorted(
            name for name, version in package_versions.items() if version is None
        ),
    }
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        wanted = {}
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith(("MemTotal:", "MemAvailable:")):
                key, value = line.split(":", 1)
                wanted[key] = value.strip()
        result["memory"] = wanted
    return result


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def env_presence(probe: Mapping[str, Any]) -> dict[str, Any]:
    variables = sorted({str(value) for value in probe.get("variables", [])})
    presence = {name: name in os.environ for name in variables}
    env_file_var = probe.get("environment_file_env")
    env_file_state: dict[str, Any] = {"declared": bool(env_file_var)}
    secure_permissions = True
    checkout_root_env = probe.get("checkout_root_env")
    checkout_root_value = (
        os.environ.get(str(checkout_root_env)) if checkout_root_env else None
    )
    checkout_root = (
        Path(checkout_root_value).expanduser().resolve()
        if checkout_root_value
        else None
    )
    env_outside_checkout = False
    if env_file_var:
        env_path = os.environ.get(str(env_file_var))
        env_file_state["exists"] = bool(env_path and Path(env_path).is_file())
        env_file_state["content_read"] = False
        if env_path and Path(env_path).is_file():
            resolved_env_path = Path(env_path).expanduser().resolve()
            metadata = resolved_env_path.stat()
            parent_metadata = resolved_env_path.parent.stat()
            file_mode = stat.S_IMODE(metadata.st_mode)
            parent_mode = stat.S_IMODE(parent_metadata.st_mode)
            current_uid = os.geteuid() if hasattr(os, "geteuid") else metadata.st_uid
            env_outside_checkout = bool(
                checkout_root is not None
                and not _is_relative_to(resolved_env_path, checkout_root)
            )
            env_file_state.update(
                {
                    "mode_octal": oct(file_mode),
                    "owner_uid": metadata.st_uid,
                    "owner_matches_process": metadata.st_uid == current_uid,
                    "directory_mode_octal": oct(parent_mode),
                    "file_mode_600": file_mode == 0o600,
                    "directory_mode_700": parent_mode == 0o700,
                    "path_sha256": sha256_bytes(str(resolved_env_path).encode("utf-8")),
                    "outside_git_checkout": env_outside_checkout,
                }
            )
            secure_permissions = (
                file_mode == 0o600
                and parent_mode == 0o700
                and metadata.st_uid == current_uid
                and checkout_root is not None
                and env_outside_checkout
            )
        else:
            secure_permissions = False
    env_file_state["checkout_root_env"] = checkout_root_env
    env_file_state["checkout_root_declared"] = checkout_root is not None
    env_file_state["outside_git_checkout"] = env_outside_checkout
    return {
        "variables_present": presence,
        "all_variables_present": all(presence.values()),
        "environment_file": env_file_state,
        "secure_permissions": secure_permissions,
        "all_required_present": all(presence.values()) and secure_permissions,
    }


def request_body_for(probe: Mapping[str, Any], call: Mapping[str, Any] | None = None) -> Any:
    if call is not None:
        return call.get("body_template", {})
    return probe.get("body_template")


def build_headers(
    probe: Mapping[str, Any],
    call: Mapping[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str], list[str], set[str]]:
    headers = {"User-Agent": "CitrusScholar-Audit/4.4.0"}
    safe_header_names: dict[str, str] = dict(headers)
    archive_headers: dict[str, str] = {
        name: value for name, value in headers.items()
    }
    missing: list[str] = []
    secret_values: set[str] = set()
    auth_config: dict[str, Any] = {}
    if isinstance(probe.get("auth"), dict):
        auth_config.update(probe["auth"])
    if call is not None and isinstance(call.get("auth"), dict):
        auth_config.update(call["auth"])
    token_env = auth_config.get("token_env")
    if token_env:
        name = str(token_env)
        token = os.environ.get(name)
        if token is None:
            missing.append(name)
        else:
            secret_values.add(token)
            scheme = str(auth_config.get("scheme") or "Bearer")
            headers["Authorization"] = f"{scheme} {token}"
            archive_headers["[AUTH_HEADER_NAME]"] = "[PRESENT]"
    api_key_env = (
        call.get("api_key_env") if call is not None else probe.get("api_key_env")
    ) or probe.get("api_key_env")
    if api_key_env:
        name = str(api_key_env)
        token = os.environ.get(name)
        if token is None:
            missing.append(name)
        else:
            secret_values.add(token)
            headers["Authorization"] = f"Bearer {token}"
            archive_headers["[AUTH_HEADER_NAME]"] = "[PRESENT]"
    return headers, archive_headers, missing, secret_values


def http_request(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: Any,
    timeout_seconds: float,
) -> tuple[int, dict[str, str], bytes, float]:
    encoded: bytes | None = None
    request_headers = dict(headers)
    if body is not None:
        encoded = canonical_json(body)
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=encoded,
        headers=request_headers,
        method=method,
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        ) as response:
            payload = response.read()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return response.status, dict(response.headers.items()), payload, elapsed_ms
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return exc.code, dict(exc.headers.items()), payload, elapsed_ms


def safe_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    allowed = {
        "content-type",
        "request-id",
        "x-request-id",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
        "retry-after",
    }
    return {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() in allowed
    }


def execute_http_probe(
    probe: Mapping[str, Any],
    *,
    timeout_seconds: float,
    attempt_root: Path,
) -> dict[str, Any]:
    base_url_env = str(probe["base_url_env"])
    base_url = os.environ.get(base_url_env)
    errors: list[str] = []
    if not base_url:
        errors.append(f"missing_env:{base_url_env}")
    headers, archive_headers, missing, secret_values = build_headers(probe, None)
    errors.extend(f"missing_env:{name}" for name in missing)
    body = request_body_for(probe)
    body_env_names = template_env_names(body)
    secret_values.update(
        os.environ[name]
        for name in body_env_names
        if name in os.environ
    )
    body = template_value(body, missing)
    errors.extend(f"missing_env:{name}" for name in missing if f"missing_env:{name}" not in errors)
    if errors:
        result = {
            "status": "BLOCKED",
            "errors": sorted(set(errors)),
            "auth_present": "Authorization" in headers,
        }
        write_new_json(attempt_root / f"{probe['id']}.json", result)
        return result

    last_result: dict[str, Any] | None = None
    for path in probe["path_candidates"]:
        url = str(base_url).rstrip("/") + "/" + str(path).lstrip("/")
        archived_body = redact_value(body, secret_values)
        request_archive = {
            "method": probe.get("method", "GET"),
            "url_path": str(path),
            "headers": archive_headers,
            "auth_present": "Authorization" in headers,
            "body": archived_body,
        }
        try:
            status, response_headers, response_bytes, elapsed_ms = http_request(
                method=str(probe.get("method", "GET")),
                url=url,
                headers=headers,
                body=body,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            last_result = {
                "status": "PROBE_TRANSPORT_ERROR",
                "path": path,
                "error_type": type(exc).__name__,
                "error": redact_value(str(exc), secret_values),
                "request": request_archive,
            }
            write_new_json(
                attempt_root / f"{probe['id']}_{str(path).strip('/').replace('/', '_')}.json",
                last_result,
            )
            continue
        try:
            parsed = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        safe_text = response_bytes.decode("utf-8", errors="replace")
        if parsed is not None:
            safe_text = json.dumps(
                redact_value(parsed, secret_values),
                ensure_ascii=False,
                sort_keys=True,
            )
        else:
            safe_text = redact_value(safe_text, secret_values)
        success_statuses = {
            int(value)
            for value in probe.get("success_statuses", [200, 201, 202, 204])
        }
        success = status in success_statuses
        expected_json = probe.get("required_json_equals", {})
        if success and isinstance(expected_json, dict):
            for key, expected in expected_json.items():
                if not isinstance(parsed, dict) or parsed.get(key) != expected:
                    success = False
        expected_paths = probe.get("required_json_paths", {})
        path_mismatches: list[str] = []
        if success and isinstance(expected_paths, dict):
            for path, expected in expected_paths.items():
                if dot_path_value(parsed, str(path)) != expected:
                    path_mismatches.append(str(path))
                    success = False
        result = {
            "status": (
                "PASS"
                if success
                else "HTTP_REJECTED"
                if status not in success_statuses
                else "RESPONSE_SCHEMA_MISMATCH"
            ),
            "path": path,
            "http_status": status,
            "elapsed_ms": elapsed_ms,
            "request": request_archive,
            "request_body_sha256": sha256_bytes(canonical_json(archived_body))
            if archived_body is not None else None,
            "response_headers": safe_response_headers(response_headers),
            "archived_response_sha256": sha256_bytes(safe_text.encode("utf-8")),
            "safe_response_body": safe_text,
            "parsed_json": redact_value(parsed, secret_values),
            "required_json_path_mismatches": sorted(path_mismatches),
        }
        write_new_json(
            attempt_root / f"{probe['id']}_{str(path).strip('/').replace('/', '_')}.json",
            result,
        )
        last_result = result
        if success:
            break
    return last_result or {"status": "BLOCKED", "errors": ["no_candidate_tested"]}


def _set_cookie_value(headers: Mapping[str, str]) -> str | None:
    for name, value in headers.items():
        if name.lower() != "set-cookie":
            continue
        first = str(value).split(";", 1)[0].strip()
        if "=" in first:
            cookie_name, cookie_value = first.split("=", 1)
            if cookie_name.strip() and cookie_value:
                return f"{cookie_name.strip()}={cookie_value}"
    return None


def _auth_step(
    *,
    step_name: str,
    base_url: str,
    definition: Mapping[str, Any],
    attempt_root: Path,
    timeout_seconds: float,
    cookie_header: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    body_missing: list[str] = []
    body = template_value(definition.get("body_template"), body_missing)
    body_env_names = template_env_names(definition.get("body_template"))
    secret_values = {
        os.environ[name] for name in body_env_names if name in os.environ
    }
    errors = [f"missing_env:{name}" for name in body_missing]
    if errors:
        result = {"status": "BLOCKED", "errors": sorted(set(errors))}
        write_new_json(attempt_root / f"{step_name}.json", result)
        return result, cookie_header

    method = str(definition.get("method") or ("GET" if step_name == "verify" else "POST"))
    success_statuses = {
        int(value)
        for value in definition.get(
            "success_statuses",
            [200, 201, 202] if step_name != "verify" else [200],
        )
    }
    last_result: dict[str, Any] | None = None
    for candidate_index, candidate in enumerate(definition["path_candidates"], start=1):
        headers = {"User-Agent": "CitrusScholar-Audit/4.4.0"}
        if cookie_header:
            headers["Cookie"] = cookie_header
        archived_body = redact_value(body, secret_values)
        archive_headers = {
            "User-Agent": headers["User-Agent"],
            "cookie_present": "Cookie" in headers,
        }
        try:
            status, response_headers, response_bytes, elapsed_ms = http_request(
                method=method,
                url=str(base_url).rstrip("/") + "/" + str(candidate).lstrip("/"),
                headers=headers,
                body=body,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            last_result = {
                "step": step_name,
                "candidate": candidate,
                "status": "PROBE_TRANSPORT_ERROR",
                "error_type": type(exc).__name__,
                "error": redact_value(str(exc), secret_values),
                "request": {
                    "method": method,
                    "url_path": candidate,
                    "headers": archive_headers,
                    "body": archived_body,
                },
            }
            write_new_json(
                attempt_root
                / f"{step_name}_{candidate_index:02d}.json",
                last_result,
            )
            continue
        try:
            parsed = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        safe_text = (
            json.dumps(
                redact_value(parsed, secret_values),
                ensure_ascii=False,
                sort_keys=True,
            )
            if parsed is not None
            else redact_value(
                response_bytes.decode("utf-8", errors="replace"),
                secret_values,
            )
        )
        next_cookie = _set_cookie_value(response_headers) or cookie_header
        success = status in success_statuses
        last_result = {
            "step": step_name,
            "candidate": candidate,
            "status": "PASS" if success else "HTTP_REJECTED",
            "http_status": status,
            "elapsed_ms": elapsed_ms,
            "request": {
                "method": method,
                "url_path": candidate,
                "headers": archive_headers,
                "body": archived_body,
            },
            "response_headers": safe_response_headers(response_headers),
            "set_cookie_present": _set_cookie_value(response_headers) is not None,
            "archived_response_sha256": sha256_bytes(safe_text.encode("utf-8")),
            "safe_response_body": safe_text,
            "parsed_json": redact_value(parsed, secret_values),
        }
        write_new_json(
            attempt_root / f"{step_name}_{candidate_index:02d}.json",
            last_result,
        )
        if success:
            return last_result, next_cookie
    return last_result or {"status": "BLOCKED", "errors": ["no_candidate_tested"]}, cookie_header


def execute_auth_flow_probe(
    probe: Mapping[str, Any],
    *,
    timeout_seconds: float,
    attempt_root: Path,
) -> dict[str, Any]:
    base_url_env = str(probe["base_url_env"])
    base_url = os.environ.get(base_url_env)
    if not base_url:
        result = {
            "status": "BLOCKED",
            "errors": [f"missing_env:{base_url_env}"],
            "credentials_persisted": False,
            "authorization_values_persisted": False,
            "cookie_values_persisted": False,
        }
        write_new_json(attempt_root / f"{probe['id']}_blocked.json", result)
        return result

    cookie_header: str | None = None
    register, cookie_header = _auth_step(
        step_name="register",
        base_url=base_url,
        definition=probe["register"],
        attempt_root=attempt_root,
        timeout_seconds=timeout_seconds,
        cookie_header=cookie_header,
    )
    login, cookie_header = _auth_step(
        step_name="login",
        base_url=base_url,
        definition=probe["login"],
        attempt_root=attempt_root,
        timeout_seconds=timeout_seconds,
        cookie_header=cookie_header,
    )
    verify, _ = _auth_step(
        step_name="verify",
        base_url=base_url,
        definition=probe["verify"],
        attempt_root=attempt_root,
        timeout_seconds=timeout_seconds,
        cookie_header=cookie_header,
    )
    status = (
        "PASS"
        if login.get("status") == "PASS"
        and verify.get("status") == "PASS"
        and cookie_header is not None
        else "BLOCKED"
    )
    return {
        "status": status,
        "register_status": register.get("status"),
        "login_status": login.get("status"),
        "verify_status": verify.get("status"),
        "session_cookie_observed": cookie_header is not None,
        "credentials_persisted": False,
        "authorization_values_persisted": False,
        "cookie_values_persisted": False,
    }


def execute_provider_probe(
    probe: Mapping[str, Any],
    *,
    timeout_seconds: float,
    attempt_root: Path,
) -> dict[str, Any]:
    base_url = os.environ.get(str(probe["base_url_env"]))
    model_results: list[dict[str, Any]] = []
    attempt_number = 0
    for call in probe["calls"]:
        model_id = str(call["model_id"])
        model_env = str(call.get("model_id_env") or "")
        body_missing: list[str] = []
        body = template_value(
            {
                **dict(call.get("body_template", {})),
                "model": model_id,
            },
            body_missing,
        )
        headers, archive_headers, missing, secret_values = build_headers(probe, call)
        body_env_names = template_env_names(call.get("body_template"))
        secret_values.update(
            os.environ[name]
            for name in body_env_names
            if name in os.environ
        )
        errors: list[str] = []
        if not base_url:
            errors.append(f"missing_env:{probe['base_url_env']}")
        if model_env and os.environ.get(model_env) != model_id:
            errors.append(f"model_env_mismatch:{model_env}")
        errors.extend(f"missing_env:{name}" for name in missing)
        errors.extend(
            f"missing_env:{name}"
            for name in body_missing
            if f"missing_env:{name}" not in errors
        )
        if errors:
            model_results.append(
                {
                    "role": call["role"],
                    "requested_model": model_id,
                    "status": "BLOCKED",
                    "errors": sorted(set(errors)),
                }
            )
            continue
        result: dict[str, Any] | None = None
        for path in probe["path_candidates"]:
            attempt_number += 1
            url = str(base_url).rstrip("/") + "/" + str(path).lstrip("/")
            try:
                status, response_headers, response_bytes, elapsed_ms = http_request(
                    method="POST",
                    url=url,
                    headers=headers,
                    body=body,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                result = {
                    "role": call["role"],
                    "requested_model": model_id,
                    "status": "PROBE_TRANSPORT_ERROR",
                    "error_type": type(exc).__name__,
                    "error": redact_value(str(exc), secret_values),
                    "path": path,
                    "attempt": attempt_number,
                    "request_sha256": sha256_bytes(
                        canonical_json(redact_value(body, secret_values))
                    ),
                }
                write_new_json(
                    attempt_root
                    / f"{probe['id']}_{call['role']}_attempt_{attempt_number:02d}.json",
                    result,
                )
                continue
            raw_text = response_bytes.decode("utf-8", errors="replace")
            try:
                parsed = json.loads(response_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = None
            safe_text = (
                json.dumps(
                    redact_value(parsed, secret_values),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if parsed is not None
                else redact_value(raw_text, secret_values)
            )
            actual_model = parsed.get("model") if isinstance(parsed, dict) else None
            usage = parsed.get("usage") if isinstance(parsed, dict) else None
            request_id = parsed.get("id") if isinstance(parsed, dict) else None
            allowed = {
                str(value)
                for value in call.get("allowed_response_models", [model_id])
            }
            identity = (
                "MATCH"
                if actual_model in allowed
                else "MISSING" if not actual_model else "MISMATCH"
            )
            response_errors = provider_response_errors(
                call,
                parsed,
                actual_model=actual_model,
                request_id=request_id,
            )
            success_statuses = {
                int(value)
                for value in probe.get("success_statuses", [200])
            }
            if status not in success_statuses:
                result_status = "HTTP_REJECTED"
            elif identity != "MATCH":
                result_status = "MODEL_IDENTITY_FAILURE"
            elif response_errors:
                result_status = "RESPONSE_CONTENT_OR_SCHEMA_FAILURE"
            else:
                result_status = "PASS"
            result = {
                "role": call["role"],
                "requested_model": model_id,
                "path": path,
                "attempt": attempt_number,
                "status": result_status,
                "http_status": status,
                "elapsed_ms": elapsed_ms,
                "auth_present": True,
                "headers": archive_headers,
                "request_sha256": sha256_bytes(
                    canonical_json(redact_value(body, secret_values))
                ),
                "request_archive": {
                    "body": redact_value(body, secret_values),
                },
                "response_headers": safe_response_headers(response_headers),
                "archived_response_sha256": sha256_bytes(
                    safe_text.encode("utf-8")
                ),
                "safe_response_body": safe_text,
                "actual_model": actual_model,
                "model_identity": identity,
                "usage": redact_value(usage, secret_values),
                "usage_present": usage is not None,
                "request_id": request_id,
                "generated_content": generated_content(parsed),
                "response_validation_errors": response_errors,
                "cost": nested_value(parsed, {"cost"}),
                "list_cost": nested_value(parsed, {"list_cost", "listCost"}),
                "allowance": nested_value(parsed, {"allowance"}),
            }
            write_new_json(
                attempt_root
                / f"{probe['id']}_{call['role']}_attempt_{attempt_number:02d}.json",
                result,
            )
            if result["status"] == "PASS":
                break
        if result is None:
            result = {
                "role": call["role"],
                "requested_model": model_id,
                "status": "BLOCKED",
                "errors": ["no_candidate_tested"],
            }
        model_results.append(result)
    status = (
        "PASS"
        if all(row["status"] == "PASS" for row in model_results)
        else "BLOCKED"
    )
    return {"status": status, "models": model_results}


def nested_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in keys and isinstance(child, (str, int, float, bool)):
                return child
        for child in value.values():
            found = nested_value(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = nested_value(child, keys)
            if found is not None:
                return found
    return None


def execute_rate_limit_probe(
    probe: Mapping[str, Any],
    *,
    timeout_seconds: float,
    attempt_root: Path,
) -> dict[str, Any]:
    base_url = os.environ.get(str(probe["base_url_env"]))
    headers, archive_headers, missing, secret_values = build_headers(probe, None)
    body_missing: list[str] = []
    body = template_value(request_body_for(probe), body_missing)
    body_env_names = template_env_names(request_body_for(probe))
    secret_values.update(
        os.environ[name]
        for name in body_env_names
        if name in os.environ
    )
    errors: list[str] = []
    if not base_url:
        errors.append(f"missing_env:{probe['base_url_env']}")
    errors.extend(f"missing_env:{name}" for name in missing)
    errors.extend(
        f"missing_env:{name}"
        for name in body_missing
        if f"missing_env:{name}" not in errors
    )
    if errors:
        result = {
            "status": "BLOCKED",
            "errors": sorted(set(errors)),
            "auth_present": "Authorization" in headers,
        }
        write_new_json(attempt_root / f"{probe['id']}_attempt_01.json", result)
        return result

    path = str(probe["path_candidates"][0])
    url = str(base_url).rstrip("/") + "/" + path.lstrip("/")
    success_statuses = {
        int(value) for value in probe.get("success_statuses", [200])
    }
    throttle_statuses = {
        int(value) for value in probe.get("throttle_statuses", [429, 503])
    }
    observations: list[dict[str, Any]] = []
    for attempt in range(1, int(probe["max_requests"]) + 1):
        try:
            status, response_headers, response_bytes, elapsed_ms = http_request(
                method=str(probe.get("method", "GET")),
                url=url,
                headers=headers,
                body=body,
                timeout_seconds=timeout_seconds,
            )
            safe_headers = safe_response_headers(response_headers)
            safe_text = redact_value(
                response_bytes.decode("utf-8", errors="replace"),
                secret_values,
            )
            observation = {
                "attempt": attempt,
                "status": "OBSERVED",
                "http_status": status,
                "elapsed_ms": elapsed_ms,
                "response_headers": safe_headers,
                "archived_response_sha256": sha256_bytes(safe_text.encode("utf-8")),
                "safe_response_body": safe_text,
                "throttled": status in throttle_statuses,
            }
        except Exception as exc:
            observation = {
                "attempt": attempt,
                "status": "PROBE_TRANSPORT_ERROR",
                "error_type": type(exc).__name__,
                "error": redact_value(str(exc), secret_values),
            }
        observations.append(observation)
        write_new_json(
            attempt_root / f"{probe['id']}_attempt_{attempt:02d}.json",
            {
                "path": path,
                "request_headers": archive_headers,
                "auth_present": "Authorization" in headers,
                **observation,
            },
        )
        if attempt < int(probe["max_requests"]):
            time.sleep(float(probe.get("interval_seconds", 0)))

    transport_errors = [
        row for row in observations if row["status"] == "PROBE_TRANSPORT_ERROR"
    ]
    statuses = [
        int(row["http_status"])
        for row in observations
        if "http_status" in row
    ]
    throttled = any(row.get("throttled") for row in observations)
    expect_throttle = bool(probe["expect_throttle"])
    if transport_errors:
        status = "PROBE_TRANSPORT_ERROR"
        errors.append("one_or_more_requests_failed_transport")
    elif expect_throttle and not throttled:
        status = "THROTTLE_NOT_OBSERVED"
        errors.append("expected_throttle_not_observed")
    elif not expect_throttle and any(value not in success_statuses for value in statuses):
        status = "UNEXPECTED_HTTP_STATUS"
        errors.append("unexpected_status_while_throttle_not_expected")
    else:
        status = "PASS"
    return {
        "status": status,
        "path": path,
        "request_count": len(observations),
        "observed_http_statuses": statuses,
        "throttle_observed": throttled,
        "expect_throttle": expect_throttle,
        "retry_after_headers": [
            row.get("response_headers", {}).get("retry-after")
            for row in observations
            if row.get("response_headers", {}).get("retry-after")
        ],
        "observations": observations,
        "errors": sorted(set(errors)),
    }


def execute_plan(
    plan: Mapping[str, Any],
    *,
    output_dir: Path,
    timeout_seconds: float,
    allow_rate_limit: bool,
    execution_guard: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    attempt_root = output_dir / "attempts"
    attempt_root.mkdir()
    results: dict[str, dict[str, Any]] = {}
    network_probes = [
        probe
        for probe in plan["probes"]
        if probe.get("kind") in NETWORK_PROBE_KINDS
    ]
    guard_errors: list[str] = []
    if network_probes and not execution_guard:
        guard_errors.append("execution_guard_missing")
    if execution_guard:
        if execution_guard.get("status") != "PASS":
            guard_errors.append("execution_guard_not_pass")
        allowlist = {
            str(value) for value in execution_guard.get("origin_allowlist", [])
        }
        app_base = os.environ.get("CITRUS_EXPERIMENT_BASE_URL", "")
        if normalize_origin(app_base) not in allowlist:
            guard_errors.append("experiment_base_url_not_allowlisted")
        if execution_guard.get("report_path_sha256") != (
            plan.get("execution_guards", {}).get("stage0_report_sha256")
        ):
            guard_errors.append("execution_guard_report_sha_mismatch")
    if guard_errors:
        manifest = {
            "schema_version": "1.0",
            "probe_version": plan.get("probe_version"),
            "artifact": "SERVER_API_PROBE_MANIFEST",
            "generated_utc": utc_now(),
            "status": "BLOCKED",
            "required_probe_failures": sorted(set(guard_errors)),
            "results": {},
            "execution_guard": dict(execution_guard or {}),
            "attempt_files": {},
            "credentials_persisted": False,
            "authorization_headers_persisted": False,
        }
        write_new_json(output_dir / "PROBE_MANIFEST.json", manifest)
        return manifest

    for probe in plan["probes"]:
        probe_id = str(probe["id"])
        kind = probe["kind"]
        if kind == "local_resources":
            resource_result = local_resources(probe)
            result = {
                "status": (
                    "PASS"
                    if not resource_result["missing_python_distributions"]
                    else "BLOCKED"
                ),
                "result": resource_result,
            }
            write_new_json(attempt_root / f"{probe_id}.json", result)
        elif kind == "env_presence":
            result = env_presence(probe)
            result["status"] = (
                "PASS" if result["all_required_present"] else "BLOCKED"
            )
            write_new_json(attempt_root / f"{probe_id}.json", result)
        elif kind == "http":
            result = execute_http_probe(
                probe,
                timeout_seconds=timeout_seconds,
                attempt_root=attempt_root,
            )
        elif kind == "auth_flow":
            result = execute_auth_flow_probe(
                probe,
                timeout_seconds=timeout_seconds,
                attempt_root=attempt_root,
            )
        elif kind == "provider_http":
            result = execute_provider_probe(
                probe,
                timeout_seconds=timeout_seconds,
                attempt_root=attempt_root,
            )
        elif kind == "rate_limit":
            if not allow_rate_limit or probe.get("enabled") is not True:
                result = {
                    "status": "SKIPPED_NOT_AUTHORIZED",
                    "reason": "rate-limit probe requires --allow-rate-limit and enabled=true",
                }
            else:
                result = execute_rate_limit_probe(
                    probe,
                    timeout_seconds=timeout_seconds,
                    attempt_root=attempt_root,
                )
            write_new_json(attempt_root / f"{probe_id}.json", result)
        elif kind == "model_identity":
            identity_rows: list[dict[str, Any]] = []
            for source_id in probe["source_probe_ids"]:
                source = results.get(str(source_id), {})
                for row in source.get("models", []):
                    allowed = {
                        str(value)
                        for value in probe["allowlists"].get(
                            str(row.get("role")),
                            [],
                        )
                    }
                    actual = row.get("actual_model")
                    identity_rows.append(
                        {
                            "role": row.get("role"),
                            "actual_model": actual,
                            "allowed": sorted(allowed),
                            "status": "PASS" if actual in allowed else "BLOCKED",
                        }
                    )
            result = {
                "status": (
                    "PASS"
                    if identity_rows
                    and all(row["status"] == "PASS" for row in identity_rows)
                    else "BLOCKED"
                ),
                "identities": identity_rows,
            }
            write_new_json(attempt_root / f"{probe_id}.json", result)
        else:
            result = {"status": "BLOCKED", "errors": ["unknown_probe_kind"]}
        results[probe_id] = result

    required_ids = {
        str(probe["id"])
        for probe in plan["probes"]
        if probe.get("required") is True
    }
    blockers = [
        probe_id
        for probe_id in sorted(required_ids)
        if results.get(probe_id, {}).get("status") != "PASS"
    ]
    attempt_files = {
        path.relative_to(output_dir).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": sha256_bytes(path.read_bytes()),
        }
        for path in sorted(attempt_root.glob("*.json"))
    }
    manifest = {
        "schema_version": "1.0",
        "probe_version": plan["probe_version"],
        "artifact": "SERVER_API_PROBE_MANIFEST",
        "generated_utc": utc_now(),
        "status": "PASS" if not blockers else "BLOCKED",
        "required_probe_failures": blockers,
        "results": results,
        "reviewed_commit": plan.get("reviewed_commit"),
        "reviewed_tree": plan.get("reviewed_tree"),
        "execution_guard": dict(execution_guard or {}),
        "runtime_app_config": results.get("runtime_config_probe", {}).get(
            "parsed_json"
        ),
        "provider_model_identity": {
            probe_id: [
                {
                    "role": row.get("role"),
                    "requested_model": row.get("requested_model"),
                    "actual_model": row.get("actual_model"),
                    "request_id": row.get("request_id"),
                    "usage_present": row.get("usage_present"),
                    "status": row.get("status"),
                }
                for row in results.get(probe_id, {}).get("models", [])
            ]
            for probe_id in ("agent_api_probe", "go_api_probe")
        },
        "deferred_probes": {
            "rate_limit_probe": {
                "status": "DEFERRED",
                "reported_as_completed": False,
                "reason": plan.get("execution_guards", {}).get(
                    "rate_limit_policy"
                ),
            }
        },
        "attempt_files": attempt_files,
        "credentials_persisted": False,
        "authorization_headers_persisted": False,
    }
    write_new_json(output_dir / "PROBE_MANIFEST.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--out")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-rate-limit", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--model-config")
    parser.add_argument("--judge-config")
    parser.add_argument("--stage0-report")
    args = parser.parse_args()

    plan_path = Path(args.plan).expanduser().resolve()
    plan = read_plan(plan_path)
    errors = plan_validation_errors(plan)
    if errors:
        print(json.dumps({"status": "INVALID_PLAN", "errors": errors}, indent=2))
        return 2
    if not args.execute:
        print(
            json.dumps(
                {
                    "status": "PLAN_VALIDATED_NOT_EXECUTED",
                    "plan_sha256": sha256_bytes(plan_path.read_bytes()),
                    "probe_count": len(plan["probes"]),
                    "execution_authorization_errors": execution_authorization_errors(plan),
                },
                indent=2,
            )
        )
        return 0
    authorization_errors = execution_authorization_errors(plan)
    if authorization_errors:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "errors": authorization_errors,
                },
                indent=2,
            )
        )
        return 2
    if not args.out:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "errors": ["out_required_for_execution"],
                },
                indent=2,
            )
        )
        return 2
    if not args.model_config or not args.judge_config or not args.stage0_report:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "errors": [
                        "model_config_required_for_execution",
                        "judge_config_required_for_execution",
                        "stage0_report_required_for_execution",
                    ],
                },
                indent=2,
            )
        )
        return 2
    model_config = load_json_object(
        Path(args.model_config).expanduser().resolve()
    )
    judge_config = load_json_object(
        Path(args.judge_config).expanduser().resolve()
    )
    binding_errors = model_binding_errors(plan, model_config, judge_config)
    if binding_errors:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "errors": binding_errors,
                },
                indent=2,
            )
        )
        return 2
    execution_guard, guard_errors = stage0_guard_errors(
        plan,
        Path(args.stage0_report).expanduser().resolve(),
    )
    if guard_errors:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "errors": guard_errors,
                    "execution_guard": execution_guard,
                },
                indent=2,
            )
        )
        return 2
    out = Path(args.out).expanduser().resolve()
    if out.exists():
        raise SystemExit(f"output path already exists: {out}")
    manifest = execute_plan(
        plan,
        output_dir=out,
        timeout_seconds=args.timeout_seconds,
        allow_rate_limit=args.allow_rate_limit,
        execution_guard=execution_guard,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

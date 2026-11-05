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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PROBE_KINDS = {
    "local_resources",
    "env_presence",
    "http",
    "provider_http",
    "rate_limit",
    "model_identity",
}
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(authorization|cookie|password|passwd|secret|token|api[_-]?key|credential)"
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-ws-[A-Za-z0-9._-]{20,}"),
    re.compile(r"oc_sk_[A-Za-z0-9_-]{20,}"),
)
TEMPLATE_PATTERN = re.compile(r"^\$\{ENV:([A-Z][A-Z0-9_]*)\}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def plan_validation_errors(plan: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("schema_version", "probe_version", "status", "probes"):
        if key not in plan:
            errors.append(f"missing_plan_field:{key}")
    if plan.get("status") not in {"UNCONFIGURED", "PILOT_READY", "READY"}:
        errors.append("invalid_plan_status")
    if not isinstance(plan.get("execution_authorized"), bool):
        errors.append("execution_authorized_must_be_boolean")
    if not isinstance(plan.get("review_status"), str):
        errors.append("review_status_missing")
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
        if kind == "provider_http":
            calls = probe.get("calls")
            if not isinstance(calls, list) or not calls:
                errors.append(f"provider_calls_missing:{probe_id}")
            elif any(not call.get("role") or not call.get("model_id") for call in calls):
                errors.append(f"provider_call_role_or_model_missing:{probe_id}")
            if not probe.get("api_key_env"):
                errors.append(f"provider_api_key_env_missing:{probe_id}")
        if kind == "env_presence" and not isinstance(probe.get("variables"), list):
            errors.append(f"env_variables_missing:{probe_id}")
        if kind == "env_presence" and not probe.get("environment_file_env"):
            errors.append(f"environment_file_env_missing:{probe_id}")
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
    return errors


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


def env_presence(probe: Mapping[str, Any]) -> dict[str, Any]:
    variables = sorted({str(value) for value in probe.get("variables", [])})
    presence = {name: name in os.environ for name in variables}
    env_file_var = probe.get("environment_file_env")
    env_file_state: dict[str, Any] = {"declared": bool(env_file_var)}
    secure_permissions = True
    if env_file_var:
        env_path = os.environ.get(str(env_file_var))
        env_file_state["exists"] = bool(env_path and Path(env_path).is_file())
        env_file_state["content_read"] = False
        if env_path and Path(env_path).is_file():
            metadata = Path(env_path).stat()
            parent_metadata = Path(env_path).parent.stat()
            file_mode = stat.S_IMODE(metadata.st_mode)
            parent_mode = stat.S_IMODE(parent_metadata.st_mode)
            current_uid = os.geteuid() if hasattr(os, "geteuid") else metadata.st_uid
            env_file_state.update(
                {
                    "mode_octal": oct(file_mode),
                    "owner_uid": metadata.st_uid,
                    "owner_matches_process": metadata.st_uid == current_uid,
                    "directory_mode_octal": oct(parent_mode),
                    "file_mode_600": file_mode == 0o600,
                    "directory_mode_700": parent_mode == 0o700,
                }
            )
            secure_permissions = (
                file_mode == 0o600
                and parent_mode == 0o700
                and metadata.st_uid == current_uid
            )
        else:
            secure_permissions = False
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
    headers = {"User-Agent": "CitrusScholar-Audit/4.3.3"}
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
        }
        write_new_json(
            attempt_root / f"{probe['id']}_{str(path).strip('/').replace('/', '_')}.json",
            result,
        )
        last_result = result
        if success:
            break
    return last_result or {"status": "BLOCKED", "errors": ["no_candidate_tested"]}


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
        body = template_value(
            {
                **dict(call.get("body_template", {})),
                "model": model_id,
            },
            [],
        )
        headers, archive_headers, missing, secret_values = build_headers(probe, call)
        errors: list[str] = []
        if not base_url:
            errors.append(f"missing_env:{probe['base_url_env']}")
        errors.extend(f"missing_env:{name}" for name in missing)
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
            allowed = {
                str(value)
                for value in call.get("allowed_response_models", [model_id])
            }
            identity = (
                "MATCH"
                if actual_model in allowed
                else "MISSING" if not actual_model else "MISMATCH"
            )
            success_statuses = {
                int(value)
                for value in probe.get("success_statuses", [200])
            }
            result = {
                "role": call["role"],
                "requested_model": model_id,
                "path": path,
                "attempt": attempt_number,
                "status": (
                    "PASS"
                    if status in success_statuses and identity == "MATCH"
                    else "MODEL_IDENTITY_FAILURE"
                    if status in success_statuses
                    else "HTTP_REJECTED"
                ),
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
                "request_id": (
                    parsed.get("id")
                    if isinstance(parsed, dict)
                    else None
                ),
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
    errors: list[str] = []
    if not base_url:
        errors.append(f"missing_env:{probe['base_url_env']}")
    errors.extend(f"missing_env:{name}" for name in missing)
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
                body=request_body_for(probe),
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
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    attempt_root = output_dir / "attempts"
    attempt_root.mkdir()
    results: dict[str, dict[str, Any]] = {}
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
    out = Path(args.out).expanduser().resolve()
    if out.exists():
        raise SystemExit(f"output path already exists: {out}")
    manifest = execute_plan(
        plan,
        output_dir=out,
        timeout_seconds=args.timeout_seconds,
        allow_rate_limit=args.allow_rate_limit,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

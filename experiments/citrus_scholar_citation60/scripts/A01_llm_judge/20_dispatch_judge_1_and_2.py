#!/usr/bin/env python3
"""Dispatch J1/J2 over blinded packets, retaining every attempt safely."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from aggregate_llm_judges import (  # noqa: E402
    model_identity_status,
    validate_judgment,
)
from common import redact_error  # noqa: E402
from execution_authorization import require_execution_authorized  # noqa: E402

CONFIG = ROOT / "05_baselines/judge_config.json"
PROMPT = ROOT / "05_baselines/prompts/qa_judge_prompt.md"
RETRYABLE_HTTP_STATUSES = {408, 429, 500, 502, 503, 504}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def protected_root() -> Path:
    value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not value:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    path = Path(value).expanduser().resolve()
    if path.is_relative_to(ROOT.resolve()):
        raise SystemExit("judge records must be stored outside the public Git tree")
    return path


def post_json(
    base_url: str,
    key: str,
    payload: dict,
    timeout: int,
    session_id: str,
) -> tuple[dict, dict[str, str]]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "CitrusScholar-Judge/4.3.2",
            "X-Session-Id": session_id,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")), {
            "request_sha256": sha256_bytes(body),
            "response_sha256": sha256_bytes(raw),
        }


def retryable_record(record: dict) -> bool:
    """Only transport failures without a model answer may be retried."""
    return (
        record.get("status") == "JUDGE_TRANSPORT_ERROR"
        or (
            record.get("status") == "JUDGE_HTTP_ERROR"
            and int(record.get("http_status", 0)) in RETRYABLE_HTTP_STATUSES
        )
    )


def parse_model_content(body: dict) -> dict:
    content = body["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("judge content is not a JSON object")
    return parsed


def allowed_model_ids(judge_config: dict) -> set[str]:
    values = {
        judge_config.get("actual_model_id"),
        *judge_config.get("allowed_model_ids", []),
    }
    return {str(value).strip() for value in values if str(value or "").strip()}


def status_for_identity(identity_status: str, schema_errors: list[str]) -> str:
    if identity_status == "MODEL_ID_MISSING":
        return "MODEL_ID_MISSING"
    if identity_status == "MODEL_ID_MISMATCH":
        return "MODEL_ID_MISMATCH"
    return "completed" if not schema_errors else "INVALID_SCHEMA"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-root", default="judge_packets")
    parser.add_argument("--out-root", default="judge_raw")
    parser.add_argument("--request-root", default="judge_requests")
    parser.add_argument("--max", type=int, default=0)
    parser.add_argument("--expected-packets", type=int, default=180)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not (args.dry_run or args.execute):
        parser.error("choose --dry-run or --execute")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    packet_dir = protected_root() / args.packet_root
    packets = sorted(packet_dir.glob("BLIND_*.json"))
    if len(packets) != args.expected_packets:
        raise SystemExit(
            f"expected {args.expected_packets} blind packets, found {len(packets)}"
        )
    if args.max:
        packets = packets[: args.max]
    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "packets": len(packets)}))
        return 0
    try:
        require_execution_authorized(config, component="judge_1_and_2")
    except Exception as exc:
        raise SystemExit(str(exc)) from exc

    key = os.environ.get(config["api_key_env"], "")
    base_url = os.environ.get(config["base_url_env"], "")
    if not key or not base_url:
        raise SystemExit("judge API credentials are not available in environment")

    l1_root = protected_root()
    prompt_text = PROMPT.read_text(encoding="utf-8")
    timeout = int(config.get("timeout_seconds") or 180)
    seed = int(config.get("seed", 20261104))
    for role in ("judge_1", "judge_2"):
        judge = config["judges"][role]
        model = judge.get("actual_model_id")
        if not model:
            raise SystemExit(f"{role} actual_model_id is not frozen")
        allowed_models = allowed_model_ids(judge)
        out_dir = l1_root / args.out_root / role
        request_dir = l1_root / args.request_root / role
        out_dir.mkdir(parents=True, exist_ok=True)
        request_dir.mkdir(parents=True, exist_ok=True)
        for packet_path in packets:
            blind_id = packet_path.stem
            target_dir = out_dir / blind_id
            if target_dir.exists() and list(target_dir.glob("attempt_*.json")):
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            request_target = request_dir / blind_id
            request_target.mkdir(parents=True, exist_ok=True)
            packet_text = packet_path.read_text(encoding="utf-8")
            packet_data = json.loads(packet_text)
            expected_answerability = packet_data.get("expected_answerability")
            packet_sha = sha256_bytes(packet_text.encode("utf-8"))
            session_id = f"{role}:{packet_sha[:24]}"
            payload = {
                "model": model,
                "temperature": float(config.get("temperature", 0)),
                "seed": seed,
                "max_tokens": config.get("max_output_tokens"),
                "messages": [
                    {"role": "system", "content": prompt_text},
                    {"role": "user", "content": packet_text},
                ],
                "response_format": {"type": "json_object"},
            }
            payload = {
                key: value for key, value in payload.items() if value is not None
            }
            for attempt in range(1, 4):
                started = time.monotonic()
                request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                request_record = {
                    "role": role,
                    "blind_id": blind_id,
                    "attempt": attempt,
                    "endpoint_host": urlparse(base_url).hostname,
                    "auth_present": True,
                    "request_body": payload,
                    "request_sha256": sha256_bytes(request_body),
                    "packet_sha256": packet_sha,
                    "session_id": session_id,
                    "prompt_sha256": sha256_bytes(prompt_text.encode("utf-8")),
                    "requested_model": model,
                }
                (request_target / f"attempt_{attempt:02d}.json").write_text(
                    json.dumps(
                        request_record, ensure_ascii=False, indent=2, sort_keys=True
                    )
                    + "\n",
                    encoding="utf-8",
                )
                try:
                    body, hashes = post_json(
                        base_url, key, payload, timeout, session_id
                    )
                    parsed = parse_model_content(body)
                    schema_errors = validate_judgment(
                        parsed, expected_answerability
                    )
                    identity_status = model_identity_status(
                        {"actual_model": body.get("model", "")},
                        allowed_models,
                    )
                    if identity_status != "MATCH":
                        schema_errors.append(identity_status)
                    record = {
                        **parsed,
                        "status": status_for_identity(
                            identity_status,
                            schema_errors,
                        ),
                        "role": role,
                        "blind_id": blind_id,
                        "attempt": attempt,
                        "parsed_judgment": parsed,
                        "schema_errors": schema_errors,
                        "raw_response_body": body,
                        "request_sha256": hashes["request_sha256"],
                        "response_sha256": hashes["response_sha256"],
                        "requested_model": model,
                        "actual_model": body.get("model", ""),
                        "model_identity_status": identity_status,
                        "actual_model_version_or_fingerprint": body.get(
                            "model_version"
                        )
                        or body.get("model_fingerprint")
                        or body.get("version")
                        or "",
                        "endpoint_host": urlparse(base_url).hostname,
                        "response_id": body.get("id", ""),
                        "http_status": 200,
                        "usage": body.get("usage", {}),
                        "tokens_in": body.get("usage", {}).get("prompt_tokens", ""),
                        "tokens_out": body.get(
                            "usage", {}
                        ).get("completion_tokens", ""),
                        "cost": body.get("usage", {}).get(
                            "cost", body.get("cost", "")
                        ),
                        "list_cost": body.get("list_cost", ""),
                        "allowance": body.get("allowance", ""),
                        "elapsed_ms": round(
                            (time.monotonic() - started) * 1000, 2
                        ),
                    }
                    write_attempt = True
                except urllib.error.HTTPError as exc:
                    record = {
                        "status": "JUDGE_HTTP_ERROR",
                        "role": role,
                        "blind_id": blind_id,
                        "attempt": attempt,
                        "http_status": exc.code,
                        "error": redact_error(exc.reason, (key,)),
                        "endpoint_host": urlparse(base_url).hostname,
                        "request_sha256": sha256_bytes(request_body),
                        "elapsed_ms": round(
                            (time.monotonic() - started) * 1000, 2
                        ),
                    }
                    write_attempt = True
                except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                    record = {
                        "status": "JUDGE_TRANSPORT_ERROR",
                        "role": role,
                        "blind_id": blind_id,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": redact_error(exc, (key,)),
                        "endpoint_host": urlparse(base_url).hostname,
                        "request_sha256": sha256_bytes(request_body),
                        "elapsed_ms": round(
                            (time.monotonic() - started) * 1000, 2
                        ),
                    }
                    write_attempt = True
                except json.JSONDecodeError as exc:
                    record = {
                        "status": "JUDGE_INVALID_JSON",
                        "role": role,
                        "blind_id": blind_id,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": redact_error(exc, (key,)),
                        "endpoint_host": urlparse(base_url).hostname,
                        "request_sha256": sha256_bytes(request_body),
                        "elapsed_ms": round(
                            (time.monotonic() - started) * 1000, 2
                        ),
                    }
                    write_attempt = True
                except Exception as exc:
                    record = {
                        "status": "JUDGE_ERROR",
                        "role": role,
                        "blind_id": blind_id,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "error": redact_error(exc, (key,)),
                        "endpoint_host": urlparse(base_url).hostname,
                        "request_sha256": sha256_bytes(request_body),
                        "elapsed_ms": round(
                            (time.monotonic() - started) * 1000, 2
                        ),
                    }
                    write_attempt = True
                if write_attempt:
                    (target_dir / f"attempt_{attempt:02d}.json").write_text(
                        json.dumps(
                            record,
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                if not retryable_record(record):
                    break
    print(json.dumps({"status": "DISPATCHED", "packets": len(packets)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

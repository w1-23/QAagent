#!/usr/bin/env python3
"""Dispatch J3 only on four-field J1/J2 disagreements."""
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
    JUDGMENT_FIELDS,
    disputed_fields,
    validate_adjudication,
)
from common import redact_error  # noqa: E402

CONFIG = ROOT / "05_baselines/judge_config.json"
PROMPT = ROOT / "05_baselines/prompts/qa_adjudicator_prompt.md"


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


def latest_attempt(directory: Path) -> dict | None:
    files = sorted(directory.glob("attempt_*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding="utf-8"))


def judgments_disagree(first: dict, second: dict) -> bool:
    return bool(disputed_fields(first, second))


def validate(payload: dict, fields: set[str], expected: str) -> list[str]:
    return validate_adjudication(payload, fields, expected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-root", default="judge_packets")
    parser.add_argument("--judge-root", default="judge_raw")
    parser.add_argument("--request-root", default="judge_requests")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not (args.dry_run or args.execute):
        parser.error("choose --dry-run or --execute")

    l1_root = protected_root()
    packet_dir = l1_root / args.packet_root
    judge_root = l1_root / args.judge_root
    selected: list[tuple[Path, dict, dict]] = []
    for packet_path in sorted(packet_dir.glob("BLIND_*.json")):
        blind_id = packet_path.stem
        first = latest_attempt(judge_root / "judge_1" / blind_id)
        second = latest_attempt(judge_root / "judge_2" / blind_id)
        if (
            first
            and second
            and first.get("status") == "completed"
            and second.get("status") == "completed"
            and judgments_disagree(first, second)
        ):
            selected.append((packet_path, first, second))
    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "adjudications": len(selected)}))
        return 0

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config.get("status") != "READY":
        raise SystemExit("judge_config status != READY")
    key = os.environ.get(config["api_key_env"], "")
    base_url = os.environ.get(config["base_url_env"], "")
    if not key or not base_url:
        raise SystemExit("judge API credentials are not available in environment")

    judge = config["judges"]["judge_3"]
    model = judge.get("actual_model_id")
    if not model:
        raise SystemExit("judge_3 actual_model_id is not frozen")
    timeout = int(config.get("timeout_seconds") or 180)
    seed = int(config.get("seed", 20261104))
    prompt_text = PROMPT.read_text(encoding="utf-8")
    out_dir = judge_root / "judge_3"
    request_dir = l1_root / args.request_root / "judge_3"
    out_dir.mkdir(parents=True, exist_ok=True)
    request_dir.mkdir(parents=True, exist_ok=True)

    for packet_path, first, second in selected:
        blind_id = packet_path.stem
        target_dir = out_dir / blind_id
        if target_dir.exists() and list(target_dir.glob("attempt_*.json")):
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        attempt_request_dir = request_dir / blind_id
        attempt_request_dir.mkdir(parents=True, exist_ok=True)
        packet_text = packet_path.read_text(encoding="utf-8")
        packet = json.loads(packet_text)
        fields = disputed_fields(first, second)
        anonymized = {
            "judge_1": {
                "disputed_values": {
                    field: first.get(field) for field in sorted(fields)
                },
                "reason_codes_by_field": {
                    field: first.get("reason_codes_by_field", {}).get(field, [])
                    for field in sorted(fields)
                },
                "short_reason": first.get("short_reason", ""),
                "answer_span_quote": first.get("answer_span_quote", ""),
                "evidence_span_locators": first.get(
                    "evidence_span_locators", []
                ),
            },
            "judge_2": {
                "disputed_values": {
                    field: second.get(field) for field in sorted(fields)
                },
                "reason_codes_by_field": {
                    field: second.get("reason_codes_by_field", {}).get(field, [])
                    for field in sorted(fields)
                },
                "short_reason": second.get("short_reason", ""),
                "answer_span_quote": second.get("answer_span_quote", ""),
                "evidence_span_locators": second.get(
                    "evidence_span_locators", []
                ),
            },
        }
        user_content = json.dumps(
            {"packet": packet, "judge_judgments": anonymized},
            ensure_ascii=False,
            sort_keys=True,
        )
        payload = {
            "model": model,
            "temperature": float(config.get("temperature", 0)),
            "seed": seed,
            "messages": [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
        }
        if config.get("max_output_tokens") is not None:
            payload["max_tokens"] = config["max_output_tokens"]
        session_id = f"judge_3:{sha256_bytes(packet_text.encode('utf-8'))[:24]}"
        for attempt in range(1, 4):
            started = time.monotonic()
            request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            (attempt_request_dir / f"attempt_{attempt:02d}.json").write_text(
                json.dumps(
                    {
                        "role": "judge_3",
                        "blind_id": blind_id,
                        "attempt": attempt,
                        "disputed_fields": sorted(fields),
                        "endpoint_host": urlparse(base_url).hostname,
                        "auth_present": True,
                        "request_body": payload,
                        "request_sha256": sha256_bytes(request_body),
                        "packet_sha256": sha256_bytes(
                            packet_text.encode("utf-8")
                        ),
                        "prompt_sha256": sha256_bytes(
                            prompt_text.encode("utf-8")
                        ),
                        "session_id": session_id,
                        "requested_model": model,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            try:
                request = urllib.request.Request(
                    base_url.rstrip("/") + "/chat/completions",
                    data=request_body,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                        "User-Agent": "CitrusScholar-Judge/4.3.1",
                        "X-Session-Id": session_id,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                body = json.loads(raw.decode("utf-8"))
                parsed = json.loads(body["choices"][0]["message"]["content"])
                schema_errors = validate(
                    parsed, fields, packet["expected_answerability"]
                )
                record = {
                    **parsed,
                    "status": "completed"
                    if not schema_errors
                    else "INVALID_SCHEMA",
                    "role": "judge_3",
                    "blind_id": blind_id,
                    "attempt": attempt,
                    "schema_errors": schema_errors,
                    "raw_response_body": body,
                    "request_sha256": sha256_bytes(request_body),
                    "response_sha256": sha256_bytes(raw),
                    "requested_model": model,
                    "actual_model": body.get("model", ""),
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
                    "tokens_in": body.get("usage", {}).get(
                        "prompt_tokens", ""
                    ),
                    "tokens_out": body.get("usage", {}).get(
                        "completion_tokens", ""
                    ),
                    "cost": body.get("usage", {}).get(
                        "cost", body.get("cost", "")
                    ),
                    "list_cost": body.get("list_cost", ""),
                    "allowance": body.get("allowance", ""),
                    "elapsed_ms": round(
                        (time.monotonic() - started) * 1000, 2
                    ),
                }
            except urllib.error.HTTPError as exc:
                record = {
                    "status": "JUDGE_HTTP_ERROR",
                    "role": "judge_3",
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
            except Exception as exc:
                record = {
                    "status": "JUDGE_ERROR",
                    "role": "judge_3",
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
            (target_dir / f"attempt_{attempt:02d}.json").write_text(
                json.dumps(
                    record, ensure_ascii=False, indent=2, sort_keys=True
                )
                + "\n",
                encoding="utf-8",
            )
            retryable = (
                record["status"] == "JUDGE_ERROR"
                or (
                    record["status"] == "JUDGE_HTTP_ERROR"
                    and int(record.get("http_status", 0)) in {408, 429, 500, 502, 503, 504}
                )
            )
            if not retryable:
                break
    print(json.dumps({"status": "DISPATCHED", "adjudications": len(selected)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Dispatch frozen judge 1 and judge 2 over blinded packets.

Keys are read only from environment variables. No key is written or printed.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT = ROOT / "05_baselines/prompts/qa_judge_prompt.md"
CONFIG = ROOT / "05_baselines/judge_config.json"


def validate(payload: dict) -> list[str]:
    errors = []
    if payload.get("decision") not in {"PASS", "FAIL"}:
        errors.append("decision_not_PASS_FAIL")
    for field in ("answerability_correct", "answer_correct", "fatal_error"):
        if not isinstance(payload.get(field), bool):
            errors.append(f"{field}_not_boolean")
    if not (
        isinstance(payload.get("evidence_used"), bool)
        or payload.get("evidence_used") == "not_applicable"
    ):
        errors.append("evidence_used_invalid")
    if not str(payload.get("reason_code") or "").strip():
        errors.append("reason_code_blank")
    if not isinstance(payload.get("evidence_span_locators"), list):
        errors.append("evidence_span_locators_not_list")
    return errors


def call(base_url: str, key: str, model: str, content: str, seed: int) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "temperature": 0,
                "seed": seed,
                "messages": [
                    {"role": "system", "content": PROMPT.read_text(encoding="utf-8")},
                    {"role": "user", "content": content},
                ],
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-dir", default="06_runs/judge_packets")
    parser.add_argument("--out-root", default="06_runs/judge_raw")
    parser.add_argument("--max", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not (args.dry_run or args.execute):
        parser.error("choose --dry-run or --execute")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config.get("status") != "READY":
        raise SystemExit("judge_config status != READY")
    packet_dir = ROOT / args.packet_dir
    packets = sorted(packet_dir.glob("*.json"))
    packets = [path for path in packets if path.name != "manifest.json"]
    if args.max:
        packets = packets[: args.max]
    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "packets": len(packets)}))
        return 0

    key = os.environ.get(config["api_key_env"], "")
    base_url = os.environ.get(config["base_url_env"], "")
    if not key or not base_url:
        raise SystemExit("judge API credentials are not available in environment")
    for judge_index in (1, 2):
        model = config[f"judge_{judge_index}_model_id"]
        out_dir = ROOT / args.out_root / f"judge_{judge_index}"
        out_dir.mkdir(parents=True, exist_ok=True)
        for packet_path in packets:
            target = out_dir / packet_path.name
            if target.exists():
                continue
            started = time.monotonic()
            try:
                response = call(
                    base_url,
                    key,
                    model,
                    packet_path.read_text(encoding="utf-8"),
                    int(config.get("seed", 20261104)),
                )
                content = response["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                errors = validate(parsed)
                record = {
                    "status": "completed" if not errors else "INVALID_SCHEMA",
                    **parsed,
                    "schema_errors": errors,
                    "model_id": model,
                    "model_version": config[f"judge_{judge_index}_model_version"],
                    "prompt_sha256": config[f"judge_{judge_index}_prompt_sha256"],
                    "packet_sha256": __import__("hashlib").sha256(
                        packet_path.read_bytes()
                    ).hexdigest(),
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                }
            except urllib.error.HTTPError as exc:
                record = {
                    "status": "JUDGE_HTTP_ERROR",
                    "http_status": exc.code,
                    "error": exc.reason,
                }
            except Exception as exc:
                record = {
                    "status": "JUDGE_ERROR",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            target.write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    print(json.dumps({"status": "DISPATCHED", "packets": len(packets)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

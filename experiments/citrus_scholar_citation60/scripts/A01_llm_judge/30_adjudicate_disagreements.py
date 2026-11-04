#!/usr/bin/env python3
"""Dispatch the frozen judge 3 only where judge 1 and judge 2 disagree."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "05_baselines/judge_config.json"
PROMPT = ROOT / "05_baselines/prompts/qa_adjudicator_prompt.md"
JUDGMENT_FIELDS = (
    "answerability_correct",
    "answer_correct",
    "evidence_used",
    "fatal_error",
    "decision",
)


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


def judgments_disagree(first: dict, second: dict) -> bool:
    return any(
        first.get(field) != second.get(field)
        for field in JUDGMENT_FIELDS
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet-dir", default="06_runs/judge_packets")
    parser.add_argument("--judge-root", default="06_runs/judge_raw")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not (args.dry_run or args.execute):
        parser.error("choose --dry-run or --execute")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config.get("status") != "READY":
        raise SystemExit("judge_config status != READY")
    packet_dir = ROOT / args.packet_dir
    judge_root = ROOT / args.judge_root
    selected = []
    for packet_path in sorted(packet_dir.glob("*.json")):
        if packet_path.name == "manifest.json":
            continue
        first_path = judge_root / "judge_1" / packet_path.name
        second_path = judge_root / "judge_2" / packet_path.name
        if not first_path.is_file() or not second_path.is_file():
            continue
        first = json.loads(first_path.read_text(encoding="utf-8"))
        second = json.loads(second_path.read_text(encoding="utf-8"))
        if (
            first.get("status") == "completed"
            and second.get("status") == "completed"
            and judgments_disagree(first, second)
        ):
            selected.append((packet_path, first, second))
    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "adjudications": len(selected)}))
        return 0

    key = os.environ.get(config["api_key_env"], "")
    base_url = os.environ.get(config["base_url_env"], "")
    if not key or not base_url:
        raise SystemExit("judge API credentials are not available in environment")
    out_dir = judge_root / "judge_3"
    out_dir.mkdir(parents=True, exist_ok=True)
    for packet_path, first, second in selected:
        target = out_dir / packet_path.name
        if target.exists():
            continue
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        content = json.dumps(
            {
                "packet": packet,
                "judge_1": first,
                "judge_2": second,
            },
            ensure_ascii=False,
        )
        request = urllib.request.Request(
            base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(
                {
                    "model": config["adjudicator_model_id"],
                    "temperature": 0,
                    "seed": config["seed"],
                    "messages": [
                        {
                            "role": "system",
                            "content": PROMPT.read_text(encoding="utf-8"),
                        },
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
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.loads(response.read().decode("utf-8"))
            parsed = json.loads(body["choices"][0]["message"]["content"])
            errors = validate(parsed)
            record = {
                "status": "completed" if not errors else "INVALID_SCHEMA",
                **parsed,
                "schema_errors": errors,
                "model_id": config["adjudicator_model_id"],
                "model_version": config["adjudicator_model_version"],
                "prompt_sha256": config["adjudicator_prompt_sha256"],
                "packet_sha256": __import__("hashlib").sha256(
                    packet_path.read_bytes()
                ).hexdigest(),
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
    print(json.dumps({"status": "DISPATCHED", "adjudications": len(selected)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

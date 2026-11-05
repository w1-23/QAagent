#!/usr/bin/env python3
"""Validate and optionally freeze the machine-judge configuration."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "05_baselines/judge_config.json"
PROMPTS = {
    "judge_1_prompt_sha256": ROOT / "05_baselines/prompts/qa_judge_prompt.md",
    "judge_2_prompt_sha256": ROOT / "05_baselines/prompts/qa_judge_prompt.md",
    "adjudicator_prompt_sha256": ROOT
    / "05_baselines/prompts/qa_adjudicator_prompt.md",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--lock", action="store_true")
    args = parser.parse_args()
    if not (args.check or args.lock):
        parser.error("choose --check or --lock")

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    blockers = []
    if config.get("status") != "READY":
        blockers.append("judge config status != READY")
    for field, prompt in PROMPTS.items():
        if config.get(field) != sha256(prompt):
            blockers.append(f"{field} does not match frozen prompt")
    judges = config.get("judges", {})
    for role in ("judge_1", "judge_2", "judge_3"):
        judge = judges.get(role, {})
        for field in ("actual_model_id", "version_or_fingerprint"):
            value = str(judge.get(field) or "")
            if not value or "FILL" in value:
                blockers.append(f"judges.{role}.{field} is not frozen")
        if judge.get("family") == "DeepSeek":
            blockers.append(f"{role} uses forbidden DeepSeek family")
    for field in ("api_key_env", "base_url_env"):
        value = str(config.get(field) or "")
        if not value or "FILL" in value:
            blockers.append(f"{field} is not frozen")
    if (
        judges.get("judge_1", {}).get("actual_model_id")
        == judges.get("judge_2", {}).get("actual_model_id")
    ):
        blockers.append("judge 1 and judge 2 deployments are not independent")
    if config.get("temperature") != 0:
        blockers.append("temperature must be 0")
    if config.get("blind_to_condition_model_run_question") is not True:
        blockers.append("blind_to_condition_model_run_question must be true")
    if config.get("semantic_support_reported") is not False:
        blockers.append("semantic support must remain disabled")

    target = ROOT / "05_baselines/judge_config.lock.json"
    if args.lock and blockers:
        status = "BLOCKED"
    elif args.lock:
        if target.exists():
            raise SystemExit("refuse overwrite of judge configuration lock")
        target.write_text(
            json.dumps(
                {
                    "status": "LOCKED",
                    "judge_config_sha256": sha256(CONFIG),
                    "prompt_sha256": {
                        field: sha256(path) for field, path in PROMPTS.items()
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        status = "LOCKED"
    else:
        status = "PASS" if not blockers else "BLOCKED"
    print(
        json.dumps(
            {
                "status": status,
                "blockers": blockers,
                "prompt_sha256": {
                    field: sha256(path) for field, path in PROMPTS.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if status in {"PASS", "LOCKED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

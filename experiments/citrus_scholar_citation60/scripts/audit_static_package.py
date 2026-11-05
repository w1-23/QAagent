#!/usr/bin/env python3
"""Run deterministic public-tree, release, schema, and secret checks."""
from __future__ import annotations

import csv
import fnmatch
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".pytest_cache", "__pycache__"}
CREDENTIAL_NAME_PATTERNS = (
    ".env",
    ".env.*",
    "*.env",
    "*.env.*",
    "*.key",
    "*.pem",
    "credentials.json",
    "secrets.json",
    "secrets.*",
)
SECRET_PATTERNS = {
    "bailian_key": re.compile(r"sk-ws-[A-Za-z0-9._-]{20,}"),
    "opencode_key": re.compile(r"oc_sk_[A-Za-z0-9_-]{20,}"),
    "authorization_header": re.compile(
        r"Authorization\s*:\s*Bearer\s+(?=(?!\[REDACTED\]|<redacted>|<token>))"
        r"[A-Za-z0-9._-]{20,}",
        re.IGNORECASE,
    ),
    "pem_private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    ),
    "inline_api_key": re.compile(
        r"CITRUS_(?:AGENT|JUDGE)_API_KEY\s*=\s*"
        r"(?!(?:<redacted>|\[REDACTED\]|<token>|\{|\s|$))[^\s]{20,}"
    ),
}
TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".json",
    ".jsonl",
    ".csv",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".ps1",
}


def files() -> list[Path]:
    return [
        path
        for path in sorted(ROOT.rglob("*"))
        if path.is_file()
        and not SKIP_PARTS.intersection(path.parts)
        and path.suffix.lower() != ".pyc"
    ]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def text_for_secret_scan(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="ignore")


def audit_json(path: Path, errors: list[str]) -> None:
    try:
        json.loads(read_text(path))
    except Exception as exc:
        errors.append(f"JSON_PARSE:{path.relative_to(ROOT)}:{type(exc).__name__}")


def audit_jsonl(path: Path, errors: list[str]) -> None:
    line_number = 0
    try:
        for line_number, line in enumerate(read_text(path).splitlines(), start=1):
            if line.strip():
                json.loads(line)
    except Exception as exc:
        errors.append(
            f"JSONL_PARSE:{path.relative_to(ROOT)}:{line_number}:"
            f"{type(exc).__name__}"
        )


def audit_csv(path: Path, errors: list[str]) -> None:
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = csv.reader(handle)
            header = next(rows, None)
            if not header:
                errors.append(f"CSV_EMPTY:{path.relative_to(ROOT)}")
                return
            width = len(header)
            for line_number, row in enumerate(rows, start=2):
                if row and len(row) != width:
                    errors.append(
                        f"CSV_WIDTH:{path.relative_to(ROOT)}:{line_number}:"
                        f"{len(row)}!={width}"
                    )
    except Exception as exc:
        errors.append(f"CSV_PARSE:{path.relative_to(ROOT)}:{type(exc).__name__}")


def audit_markdown(path: Path, errors: list[str]) -> None:
    fence_count = sum(
        1
        for line in read_text(path).splitlines()
        if line.lstrip().startswith("```")
    )
    if fence_count % 2:
        errors.append(f"MARKDOWN_FENCE:{path.relative_to(ROOT)}:{fence_count}")


def credential_filename(name: str) -> bool:
    lowered = name.lower()
    return any(
        fnmatch.fnmatch(lowered, pattern.lower())
        for pattern in CREDENTIAL_NAME_PATTERNS
    )


def audit_secrets(path: Path, errors: list[str]) -> None:
    if credential_filename(path.name):
        errors.append(f"CREDENTIAL_FILE:{path.relative_to(ROOT)}")
    text = text_for_secret_scan(path)
    for name, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            errors.append(f"SECRET_PATTERN:{name}:{path.relative_to(ROOT)}")


def audit_release_boundary(errors: list[str]) -> None:
    manifest_path = ROOT / "12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json"
    try:
        manifest = json.loads(read_text(manifest_path))
    except Exception as exc:
        errors.append(f"L1_MANIFEST_INVALID:{type(exc).__name__}")
        return
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        errors.append("L1_MANIFEST_ASSETS_INVALID")
        return
    ids: set[str] = set()
    for row in assets:
        asset_id = str(row.get("asset_id") or "")
        if not asset_id or asset_id in ids:
            errors.append(f"L1_MANIFEST_ASSET_ID:{asset_id or 'EMPTY'}")
        ids.add(asset_id)
        relative = str(row.get("former_public_path") or "")
        if row.get("data_class") != "L1":
            errors.append(f"L1_MANIFEST_CLASS:{relative}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))):
            errors.append(f"L1_MANIFEST_SHA:{relative}")
        if (
            row.get("remove_from_public_tree") is True
            and (ROOT / relative).exists()
        ):
            errors.append(f"L1_PRESENT_IN_PUBLIC_TREE:{relative}")


def audit_asset_register(errors: list[str]) -> None:
    path = ROOT / "12_reproducibility/ASSET_REGISTER_CURRENT.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            rows = list(reader)
    except Exception as exc:
        errors.append(f"ASSET_REGISTER_INVALID:{type(exc).__name__}")
        return
    if not {"data_class", "release_status", "relative_path"}.issubset(fields):
        errors.append("ASSET_REGISTER_CLASSIFICATION_FIELDS")
        return
    if any(row["data_class"] not in {"L0", "L1", "L2"} for row in rows):
        errors.append("ASSET_REGISTER_DATA_CLASS")
    if any(row["data_class"] == "L0" for row in rows):
        errors.append("ASSET_REGISTER_CONTAINS_L0")
    for row in rows:
        paths = [
            part.strip()
            for part in row["relative_path"].split("+")
            if part.strip()
        ]
        if row["release_status"] == "PENDING_RELEASE_REVIEW":
            for relative in paths:
                if (ROOT / relative).exists():
                    errors.append(
                        f"PENDING_ASSET_PRESENT_IN_PUBLIC_TREE:{relative}"
                    )
        if row["data_class"] == "L1":
            for relative in paths:
                if (ROOT / relative).exists():
                    errors.append(f"ASSET_REGISTER_PUBLIC_L1:{relative}")


def audit_gates(errors: list[str]) -> None:
    path = ROOT / "01_preflight/gates.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        statuses = {row["gate_id"]: row["status"] for row in rows}
        expected = {
            "G01": "BLOCKED",
            **{f"G{number:02d}": "PENDING" for number in range(2, 16)},
        }
        if statuses != expected:
            errors.append("GATE_STATE_CHANGED")
    except Exception as exc:
        errors.append(f"GATE_LEDGER_INVALID:{type(exc).__name__}")


def audit_execution_contract(errors: list[str]) -> None:
    index = read_text(ROOT / "AUDIT_INDEX.md")
    required_index_lines = (
        "Protocol version: `4.4.0`",
        "Review state: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4_2`",
        "Execution authorization in this review unit: `NONE`",
        "Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`",
    )
    for line in required_index_lines:
        if line not in index:
            errors.append(f"AUDIT_INDEX_CONTRACT_MISSING:{line}")

    config_paths = (
        "05_baselines/model_config.json",
        "05_baselines/judge_config.json",
        "01_preflight/SERVER_API_PROBE_PLAN.json",
    )
    for relative in config_paths:
        try:
            config = json.loads(read_text(ROOT / relative))
        except Exception as exc:
            errors.append(f"EXECUTION_CONFIG_INVALID:{relative}:{type(exc).__name__}")
            continue
        actual = {
            "status": config.get("status"),
            "execution_authorized": config.get("execution_authorized"),
            "review_status": config.get("review_status"),
        }
        expected = {
            "status": "UNCONFIGURED",
            "execution_authorized": False,
            "review_status": "AWAITING_EXTERNAL_GPT_REVIEW_V4_4_2",
        }
        if actual != expected:
            errors.append(f"EXECUTION_CONFIG_NOT_FAIL_CLOSED:{relative}:{actual}")

    superseded = read_text(
        ROOT / "scripts/A00_execution_readiness/20_audit_agent_input_boundary.py"
    )
    if "SUPERSEDED" not in superseded:
        errors.append("SUPERSEDED_INPUT_BOUNDARY_NOT_MARKED")


def audit_simulated_schema(errors: list[str]) -> None:
    runs_dir = ROOT / "06_runs/simulated_dry_run"
    for filename in ("core_qa_runs.jsonl", "judge_responses.jsonl"):
        path = runs_dir / filename
        try:
            rows = [
                json.loads(line)
                for line in read_text(path).splitlines()
                if line.strip()
            ]
        except Exception as exc:
            errors.append(
                f"SIMULATED_SCHEMA_PARSE:{filename}:{type(exc).__name__}"
            )
            continue
        for row in rows:
            if "evidence_used" in row:
                errors.append(f"SIMULATED_LEGACY_FIELD:{filename}:evidence_used")
            if filename == "core_qa_runs.jsonl":
                metrics = row.get("synthetic_metrics", {})
                if "evidence_used" in metrics:
                    errors.append(
                        "SIMULATED_LEGACY_FIELD:core_qa_runs:synthetic_metrics"
                    )
                if "evidence_traceable" not in metrics:
                    errors.append(
                        "SIMULATED_SCHEMA_FIELD:core_qa_runs:evidence_traceable"
                    )
            else:
                if "decision" in row:
                    errors.append(f"SIMULATED_MODEL_DECISION:{filename}")
                if "reason_codes_by_field" not in row:
                    errors.append(
                        f"SIMULATED_SCHEMA_FIELD:{filename}:reason_codes_by_field"
                    )
                if "answer_span_quote" not in row:
                    errors.append(
                        f"SIMULATED_SCHEMA_FIELD:{filename}:answer_span_quote"
                    )
                if row.get("judge_id") == "judge_3":
                    if "disputed_values" not in row:
                        errors.append(
                            "SIMULATED_SCHEMA_FIELD:judge_3:disputed_values"
                        )
                    if "answer_correct" in row:
                        errors.append("SIMULATED_JUDGE_3_FULL_REPLACEMENT")


def main() -> int:
    errors: list[str] = []
    checked = {
        "json": 0,
        "jsonl": 0,
        "csv": 0,
        "markdown": 0,
        "all_secret_scanned": 0,
        "text": 0,
    }
    for path in files():
        suffix = path.suffix.lower()
        if suffix == ".json":
            audit_json(path, errors)
            checked["json"] += 1
        elif suffix == ".jsonl":
            audit_jsonl(path, errors)
            checked["jsonl"] += 1
        elif suffix == ".csv":
            audit_csv(path, errors)
            checked["csv"] += 1
        audit_secrets(path, errors)
        checked["all_secret_scanned"] += 1
        if suffix in TEXT_SUFFIXES or path.name.lower() in {".env", ".env.local"}:
            checked["text"] += 1
            if suffix == ".md":
                audit_markdown(path, errors)
                checked["markdown"] += 1
    audit_release_boundary(errors)
    audit_asset_register(errors)
    audit_gates(errors)
    audit_execution_contract(errors)
    audit_simulated_schema(errors)
    result = {
        "status": "PASS" if not errors else "FAIL",
        "checked": checked,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run deterministic static checks for the public experiment package."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", ".pytest_cache", "__pycache__"}
CREDENTIAL_NAMES = {
    ".env",
    "credentials.json",
    "secrets.json",
}
SECRET_PATTERNS = {
    "bailian_key": re.compile(r"sk-ws-[A-Za-z0-9._-]{16,}"),
    "opencode_key": re.compile(r"oc_sk_[A-Za-z0-9_-]{16,}"),
    "authorization_header": re.compile(
        r"Authorization\s*:\s*Bearer\s+(?!(?:<redacted>|\[REDACTED\]|<token>|\{))"
        r"[A-Za-z0-9._-]{16,}",
        re.IGNORECASE,
    ),
    "inline_api_key": re.compile(
        r"CITRUS_(?:AGENT|JUDGE)_API_KEY\s*=\s*[^\s<{][^\s]*"
    ),
}


def files() -> list[Path]:
    return [
        path
        for path in sorted(ROOT.rglob("*"))
        if path.is_file()
        and not SKIP_PARTS.intersection(path.parts)
        and path.suffix != ".pyc"
    ]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def audit_json(path: Path, errors: list[str]) -> None:
    try:
        json.loads(read_text(path))
    except Exception as exc:
        errors.append(f"JSON_PARSE:{path.relative_to(ROOT)}:{type(exc).__name__}")


def audit_jsonl(path: Path, errors: list[str]) -> None:
    line_number = 0
    try:
        text = read_text(path)
        for line_number, line in enumerate(text.splitlines(), start=1):
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


def audit_secrets(path: Path, text: str, errors: list[str]) -> None:
    if (
        path.name.lower() == ".env"
        or path.name.lower().endswith(".env")
        or path.name.lower().endswith(".key")
        or path.name.lower() in CREDENTIAL_NAMES
    ):
        errors.append(f"CREDENTIAL_FILE:{path.relative_to(ROOT)}")
    for name, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            errors.append(f"SECRET_PATTERN:{name}:{path.relative_to(ROOT)}")


def audit_release_boundary(errors: list[str]) -> None:
    for relative in (
        "03_benchmarks/citation60/chunks_FROZEN.jsonl",
        "03_benchmarks/citation60/question_authoring_packet.jsonl",
    ):
        if (ROOT / relative).exists():
            errors.append(f"L1_PRESENT_IN_PUBLIC_TREE:{relative}")

    manifest_path = ROOT / "12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json"
    try:
        manifest = json.loads(read_text(manifest_path))
        assets = {row["former_public_path"]: row for row in manifest["assets"]}
        expected = {
            "03_benchmarks/citation60/chunks_FROZEN.jsonl",
            "03_benchmarks/citation60/question_authoring_packet.jsonl",
        }
        if set(assets) != expected:
            errors.append("L1_MANIFEST_ASSET_SET")
        for row in assets.values():
            if row.get("data_class") != "L1":
                errors.append(f"L1_MANIFEST_CLASS:{row.get('former_public_path')}")
            if not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", ""))):
                errors.append(f"L1_MANIFEST_SHA:{row.get('former_public_path')}")
    except Exception as exc:
        errors.append(f"L1_MANIFEST_INVALID:{type(exc).__name__}")


def audit_asset_register(errors: list[str]) -> None:
    path = ROOT / "12_reproducibility/ASSET_REGISTER_CURRENT.csv"
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            rows = list(reader)
        if not {"data_class", "release_status"}.issubset(fields):
            errors.append("ASSET_REGISTER_CLASSIFICATION_FIELDS")
        paths = {row["relative_path"] for row in rows}
        for relative in (
            "03_benchmarks/citation60/chunks_FROZEN.jsonl",
            "03_benchmarks/citation60/question_authoring_packet.jsonl",
        ):
            if relative in paths:
                errors.append(f"ASSET_REGISTER_PUBLIC_L1:{relative}")
        if any(row["data_class"] not in {"L0", "L1", "L2"} for row in rows):
            errors.append("ASSET_REGISTER_DATA_CLASS")
        if any(row["data_class"] == "L0" for row in rows):
            errors.append("ASSET_REGISTER_CONTAINS_L0")
        for row in rows:
            if (
                row["relative_path"].startswith(
                    "03_benchmarks/citation60/"
                )
                or row["relative_path"]
                in {
                    "06_runs/manifests/citation_run_plan.csv",
                    "06_runs/manifests/citation_run_plan_meta.json",
                }
            ) and row["release_status"] == "PUBLIC_RELEASED":
                errors.append(
                    f"ASSET_REGISTER_PREMATURE_CITATION_RELEASE:"
                    f"{row['relative_path']}"
                )
    except Exception as exc:
        errors.append(f"ASSET_REGISTER_INVALID:{type(exc).__name__}")


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
                    errors.append(
                        f"SIMULATED_MODEL_DECISION:{filename}"
                    )
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
                        errors.append(
                            "SIMULATED_JUDGE_3_FULL_REPLACEMENT"
                        )


def main() -> int:
    errors: list[str] = []
    checked = {"json": 0, "jsonl": 0, "csv": 0, "markdown": 0, "text": 0}
    for path in files():
        relative = path.relative_to(ROOT)
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
        if suffix in {".md", ".txt", ".py", ".json", ".jsonl", ".csv", ".yaml", ".yml"}:
            text = read_text(path)
            audit_secrets(path, text, errors)
            checked["text"] += 1
            if suffix == ".md":
                audit_markdown(path, errors)
                checked["markdown"] += 1
    audit_release_boundary(errors)
    audit_asset_register(errors)
    audit_gates(errors)
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

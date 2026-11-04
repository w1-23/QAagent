#!/usr/bin/env python3
"""Refresh the current per-file SHA register while preserving curated row IDs."""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "12_reproducibility" / "ASSET_REGISTER_CURRENT.csv"
FILE_MANIFEST = ROOT / "12_reproducibility" / "FILE_SHA256_MANIFEST.csv"
DIRS = (
    "00_protocol",
    "01_preflight",
    "02_sources",
    "03_benchmarks",
    "04_fixtures",
    "05_baselines",
    "06_runs",
    "07_scoring",
    "08_workflows",
    "09_analysis",
    "10_figures",
    "11_manuscript",
    "12_reproducibility",
    "scripts",
)
EXCLUDE_NAMES = {".DS_Store", "Thumbs.db"}
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache"}


def is_private_runtime(rel: str) -> bool:
    """Exclude credentials only; every experimental input and output is retained."""
    filename = Path(rel).name.lower()
    return (
        filename == ".env"
        or filename.endswith(".env")
        or filename.endswith(".key")
        or filename in {"credentials.json", "secrets.json"}
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify(rel: str) -> tuple[str, str, str]:
    if rel.startswith("03_benchmarks/citation60/"):
        frozen = Path(rel).name in {
            "chunks_FROZEN.jsonl",
            "questions_FROZEN.jsonl",
            "questions_FROZEN.manifest.json",
        }
        state = "PRESENT_FROZEN" if frozen else "PRESENT"
        return "public_frozen_input", "E01_CHUNK_CITATION60", state
    if rel in {
        "06_runs/manifests/citation_run_plan.csv",
        "06_runs/manifests/citation_run_plan_meta.json",
    }:
        return "controlled_benchmark", "E01_CHUNK_CITATION60", "PRESENT_FROZEN_NOT_RUN"
    if rel.startswith("scripts/E01_chunk_citation/"):
        return "controlled_code", "E01_CHUNK_CITATION60", "PRESENT"
    if rel.startswith("scripts/"):
        return "controlled_code", "ALL", "PRESENT"
    if rel.startswith("00_protocol/"):
        return "controlled_protocol", "ALL", "PRESENT"
    if rel.startswith("01_preflight/"):
        return "controlled_evidence", "ALL", "PRESENT"
    if rel.startswith("02_sources/"):
        return "source_manifest_or_snapshot", "ALL", "PRESENT"
    if rel.startswith("03_benchmarks/"):
        return "controlled_benchmark", "ALL", "PRESENT"
    if rel.startswith("04_fixtures/"):
        return "controlled_fixture", "ALL", "PRESENT"
    if rel.startswith("05_baselines/"):
        return "controlled_config_or_code", "ALL", "PRESENT"
    if rel.startswith("06_runs/"):
        return "raw_append_only", "ALL", "PRESENT"
    if rel.startswith("07_scoring/validity/"):
        return "controlled_human_validity", "ALL", "PRESENT"
    if rel.startswith("07_scoring/judge/"):
        return "controlled_machine_judgment", "ALL", "PRESENT"
    if rel.startswith("07_scoring/"):
        return "controlled_scoring", "ALL", "PRESENT"
    if rel.startswith("08_workflows/"):
        return "controlled_protocol", "ALL", "PRESENT"
    if rel.startswith("09_analysis/"):
        return "derived_result", "ALL", "PRESENT"
    if rel.startswith("10_figures/"):
        return "derived_figure", "ALL", "PRESENT"
    if rel.startswith("11_manuscript/"):
        return "controlled_manuscript", "ALL", "PRESENT"
    if rel.startswith("12_reproducibility/"):
        return "controlled_reproducibility", "ALL", "PRESENT"
    return "package_metadata", "ALL", "PRESENT"


def iter_files(include_register: bool = False):
    for dirname in DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path == FILE_MANIFEST:
                continue
            if path == REGISTER and not include_register:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if is_private_runtime(rel):
                continue
            if path.name.startswith(".") or path.name in EXCLUDE_NAMES:
                continue
            if any(part in EXCLUDE_PARTS for part in path.parts):
                continue
            if path.suffix in {".tmp", ".pyc"} or path.name.endswith("~"):
                continue
            yield path
    for path in sorted(ROOT.iterdir()):
        if path.is_file() and not path.name.startswith("."):
            yield path


def main() -> int:
    existing: dict[str, dict[str, str]] = {}
    if REGISTER.is_file():
        with REGISTER.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("relative_path"):
                    existing[row["relative_path"].replace("\\", "/")] = row

    numeric_ids = [
        int(row["asset_id"][1:])
        for row in existing.values()
        if row.get("asset_id", "").startswith("C") and row["asset_id"][1:].isdigit()
    ]
    next_id = max(numeric_ids, default=0) + 1
    fields = [
        "asset_id",
        "relative_path",
        "subdir",
        "bytes",
        "sha256",
        "state",
        "retention_class",
        "experiment_ids",
        "description",
    ]
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in iter_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        old = existing.get(rel, {})
        retention, experiments, default_state = classify(rel)
        rows.append({
            "asset_id": old.get("asset_id") or f"C{next_id:04d}",
            "relative_path": rel,
            "subdir": rel.split("/", 1)[0] if "/" in rel else "ROOT",
            "bytes": str(path.stat().st_size),
            "sha256": sha256(path),
            "state": old.get("state") or default_state,
            "retention_class": old.get("retention_class") or retention,
            "experiment_ids": old.get("experiment_ids") or experiments,
            "description": old.get("description") or (
                "Current file registered by refresh_asset_register.py"
            ),
        })
        if not old.get("asset_id"):
            next_id += 1
    rows.append({
        "asset_id": "C9999",
        "relative_path": "12_reproducibility/ASSET_REGISTER_CURRENT.csv",
        "subdir": "12_reproducibility",
        "bytes": "SELF_EXCLUDED",
        "sha256": "",
        "state": "SELF_REFERENCE_SHA_EXCLUDED",
        "retention_class": "controlled_reproducibility",
        "experiment_ids": "ALL",
        "description": "Current asset-register snapshot; own SHA excluded to avoid self-reference",
    })
    rows.sort(key=lambda row: (row["subdir"], row["relative_path"]))
    tmp = REGISTER.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(REGISTER)

    manifest_rows = []
    for path in iter_files(include_register=True):
        rel = path.relative_to(ROOT).as_posix()
        manifest_rows.append(
            {
                "relative_path": rel,
                "bytes": str(path.stat().st_size),
                "sha256": sha256(path),
            }
        )
    manifest_tmp = FILE_MANIFEST.with_suffix(".csv.tmp")
    with manifest_tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["relative_path", "bytes", "sha256"],
        )
        writer.writeheader()
        writer.writerows(manifest_rows)
    manifest_tmp.replace(FILE_MANIFEST)
    print(
        f"registered={len(rows)} manifest_files={len(manifest_rows)} snapshot_utc="
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

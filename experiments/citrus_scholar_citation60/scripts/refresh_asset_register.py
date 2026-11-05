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


def classify(rel: str) -> tuple[str, str, str, str, str]:
    """Return legacy retention label, data class, release status, experiments, state."""
    protected_names = {
        "chunks_FROZEN.jsonl",
        "question_authoring_packet.jsonl",
    }
    if rel.startswith("03_benchmarks/citation60/"):
        name = Path(rel).name
        if name in protected_names:
            return (
                "protected_l1_source_text",
                "L1",
                "PROTECTED_SERVER_ONLY",
                "E01_SUPP_CHUNK_CITATION60",
                "BLOCKED_PUBLIC_L1",
            )
        state = "PRESENT_FROZEN" if name in {
            "questions_FROZEN.jsonl",
            "questions_FROZEN.manifest.json",
        } else "PRESENT"
        # G12 is still pending. Presence in the latest tree is not evidence
        # that the source licence or redistribution boundary has been cleared.
        release = "PENDING_RELEASE_REVIEW"
        return (
            "public_metadata_or_derived_question",
            "L2",
            release,
            "E01_SUPP_CHUNK_CITATION60",
            state,
        )
    if rel in {
        "06_runs/manifests/citation_run_plan.csv",
        "06_runs/manifests/citation_run_plan_meta.json",
    }:
        return (
            "controlled_benchmark",
            "L2",
            "PENDING_RELEASE_REVIEW",
            "E01_SUPP_CHUNK_CITATION60",
            "PRESENT_FROZEN_NOT_RUN",
        )
    if rel.startswith("06_runs/judge_") or rel.startswith(
        "06_runs/protected/"
    ):
        return (
            "protected_l1_evidence",
            "L1",
            "PROTECTED_SERVER_ONLY",
            "ALL",
            "PROTECTED_SERVER_ONLY",
        )
    if rel.startswith("07_scoring/judge/blind_packets/"):
        return (
            "protected_l1_blind_packet",
            "L1",
            "PROTECTED_SERVER_ONLY",
            "E01_QA60_EGTS",
            "PROTECTED_SERVER_ONLY",
        )
    if rel.startswith("scripts/E01_chunk_citation/"):
        return (
            "controlled_code",
            "L2",
            "PUBLIC_RELEASED",
            "E01_SUPP_CHUNK_CITATION60",
            "PRESENT",
        )
    if rel.startswith("scripts/"):
        return "controlled_code", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"
    if rel.startswith("00_protocol/"):
        return "controlled_protocol", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"
    if rel.startswith("01_preflight/"):
        return "controlled_evidence", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"
    if rel.startswith("02_sources/"):
        name = Path(rel).name
        if name.lower().endswith((".pdf", ".xlsx", ".xls", ".docx", ".doc")) or (
            "/supplements/" in rel or "/ucr_snapshots/" in rel
        ):
            return (
                "protected_l1_source",
                "L1",
                "PROTECTED_SERVER_ONLY",
                "ALL",
                "PRESENT",
            )
        return (
            "source_manifest",
            "L2",
            "PUBLIC_RELEASED",
            "ALL",
            "PRESENT",
        )
    if rel.startswith("03_benchmarks/"):
        return "controlled_benchmark", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"
    if rel.startswith("04_fixtures/"):
        return (
            "public_safe_synthetic_fixture",
            "L2",
            "PUBLIC_RELEASED",
            "ALL",
            "PRESENT",
        )
    if rel.startswith("05_baselines/"):
        return (
            "controlled_config_or_code",
            "L2",
            "PUBLIC_RELEASED",
            "ALL",
            "PRESENT",
        )
    if rel.startswith("06_runs/"):
        if rel.startswith("06_runs/simulated_dry_run/"):
            return (
                "labelled_simulation",
                "L2",
                "PUBLIC_RELEASED",
                "S00_SIMULATED_DRY_RUN",
                "SIMULATED_LABELLED",
            )
        if rel.startswith("06_runs/manifests/"):
            return "run_plan_metadata", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"
        return (
            "protected_l1_raw_evidence",
            "L1",
            "PROTECTED_SERVER_ONLY",
            "ALL",
            "CONTROLLED_REVIEW_REQUIRED",
        )
    if rel.startswith("07_scoring/validity/"):
        return (
            "controlled_human_validity",
            "L2",
            "PUBLIC_RELEASED",
            "ALL",
            "PRESENT",
        )
    if rel.startswith("07_scoring/judge/"):
        return (
            "controlled_machine_judgment",
            "L2",
            "PUBLIC_RELEASED",
            "ALL",
            "PRESENT",
        )
    if rel.startswith("07_scoring/"):
        return "controlled_scoring", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"
    if rel.startswith("08_workflows/"):
        return "controlled_protocol", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"
    if rel.startswith("09_analysis/"):
        return "derived_result", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"
    if rel.startswith("10_figures/"):
        return "derived_figure", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"
    if rel.startswith("11_manuscript/"):
        return (
            "controlled_manuscript",
            "L2",
            "PUBLIC_RELEASED",
            "ALL",
            "PRESENT",
        )
    if rel.startswith("12_reproducibility/"):
        return (
            "controlled_reproducibility",
            "L2",
            "PUBLIC_RELEASED",
            "ALL",
            "PRESENT",
        )
    return "package_metadata", "L2", "PUBLIC_RELEASED", "ALL", "PRESENT"


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
        "data_class",
        "release_status",
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
        retention, data_class, release_status, experiments, default_state = classify(rel)
        rows.append({
            "asset_id": old.get("asset_id") or f"C{next_id:04d}",
            "relative_path": rel,
            "subdir": rel.split("/", 1)[0] if "/" in rel else "ROOT",
            "bytes": str(path.stat().st_size),
            "sha256": sha256(path),
            "state": old.get("state") or default_state,
            "retention_class": retention,
            "data_class": data_class,
            "release_status": release_status,
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
        "data_class": "L2",
        "release_status": "PUBLIC_RELEASED",
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

#!/usr/bin/env python3
"""Validate an immutable expert score submission without editing it."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from A02_human_scoring.scoring_common import (  # noqa: E402
    SCORE_FIELDS,
    read_csv,
    sha256_file,
    validate_score_row,
)


def protected_output(path: Path | None, l1_root: Path, relative: str) -> Path:
    target = (path or (l1_root / relative)).expanduser().resolve()
    if not target.is_relative_to(l1_root) or target.is_relative_to(ROOT.resolve()):
        raise SystemExit("validation outputs must remain in protected L1 storage")
    return target


def reviewer_slot(materials: dict, reviewer_id: str) -> str | None:
    for slot, value in materials.get("reviewers", {}).items():
        if value.get("reviewer_id") == reviewer_id:
            return slot
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", required=True)
    parser.add_argument("--packet-root", required=True)
    parser.add_argument("--materials-manifest", required=True)
    parser.add_argument("--attestation", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    l1_root_value = os.environ.get("CITRUS_EXPERIMENT_L1_ROOT", "").strip()
    if not l1_root_value:
        raise SystemExit("CITRUS_EXPERIMENT_L1_ROOT is required")
    l1_root = Path(l1_root_value).expanduser().resolve()
    scores_path = Path(args.scores).expanduser().resolve()
    packet_root = Path(args.packet_root).expanduser().resolve()
    materials_path = Path(args.materials_manifest).expanduser().resolve()
    if not scores_path.is_relative_to(l1_root):
        raise SystemExit("expert score file must be in protected L1 storage")
    if not packet_root.is_relative_to(l1_root):
        raise SystemExit("expert packet root must be in protected L1 storage")
    if not materials_path.is_relative_to(l1_root):
        raise SystemExit("expert materials manifest must be in protected L1 storage")

    header, rows = read_csv(scores_path)
    if header != SCORE_FIELDS:
        raise SystemExit("expert score header does not match the frozen schema")
    materials = json.loads(materials_path.read_text(encoding="utf-8"))
    if materials.get("manifest_version") != "4.3.2":
        raise SystemExit("expert materials manifest version is not 4.3.2")
    packet_files = sorted(packet_root.glob("BLIND_*.json"))
    packets = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in packet_files
    }
    expected_count = int(materials.get("packet_count", 0))
    if len(packets) != expected_count:
        raise SystemExit("expert materials manifest packet count mismatch")

    errors: list[dict] = []
    reviewer_ids = {row.get("reviewer_id", "") for row in rows}
    reviewer_id = next(iter(reviewer_ids), "")
    out_path = protected_output(
        Path(args.out) if args.out else None,
        l1_root,
        (
            f"expert_validation/{reviewer_id or 'UNKNOWN'}_"
            f"{sha256_file(scores_path)[:12]}.json"
        ),
    )
    if out_path.exists():
        raise SystemExit(f"refuse overwrite: {out_path}")
    if not reviewer_id or len(reviewer_ids) != 1:
        errors.append({"row": 0, "blind_id": "", "errors": ["reviewer_id_inconsistent"]})
    elif reviewer_slot(materials, reviewer_id) is None:
        errors.append(
            {
                "row": 0,
                "blind_id": "",
                "errors": ["reviewer_id_not_in_materials_manifest"],
            }
        )
    if len(rows) != expected_count:
        errors.append(
            {
                "row": 0,
                "blind_id": "",
                "errors": [f"row_count:{len(rows)}!={expected_count}"],
            }
        )
    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for line_number, row in enumerate(rows, start=2):
        blind_id = row.get("blind_id", "")
        if blind_id in seen:
            duplicate_ids.add(blind_id)
        seen.add(blind_id)
        packet = packets.get(blind_id)
        if packet is None:
            errors.append(
                {
                    "row": line_number,
                    "blind_id": blind_id,
                    "errors": ["blind_id_not_in_packet_set"],
                }
            )
            continue
        if row.get("batch_id") != packet.get("batch_id"):
            errors.append(
                {
                    "row": line_number,
                    "blind_id": blind_id,
                    "errors": ["batch_id_mismatch"],
                }
            )
        if row.get("reviewer_id") != reviewer_id:
            errors.append(
                {
                    "row": line_number,
                    "blind_id": blind_id,
                    "errors": ["reviewer_id_inconsistent"],
                }
            )
        if str(row.get("score_version") or "") != materials.get("score_version"):
            errors.append(
                {
                    "row": line_number,
                    "blind_id": blind_id,
                    "errors": ["score_version_mismatch"],
                }
            )
        reviewed_utc = row.get("reviewed_utc", "").strip()
        try:
            datetime.fromisoformat(reviewed_utc.replace("Z", "+00:00"))
        except Exception:
            errors.append(
                {
                    "row": line_number,
                    "blind_id": blind_id,
                    "errors": ["reviewed_utc_invalid"],
                }
            )
        row_errors, _ = validate_score_row(
            row,
            packet.get("expected_answerability", ""),
        )
        if row_errors:
            errors.append(
                {
                    "row": line_number,
                    "blind_id": blind_id,
                    "errors": row_errors,
                }
            )
    if duplicate_ids:
        errors.append(
            {
                "row": 0,
                "blind_id": "",
                "errors": [f"duplicate_blind_ids:{','.join(sorted(duplicate_ids))}"],
            }
        )
    if seen != set(packets):
        missing = sorted(set(packets) - seen)
        extra = sorted(seen - set(packets))
        errors.append(
            {
                "row": 0,
                "blind_id": "",
                "errors": [
                    f"missing_blind_ids:{','.join(missing)}",
                    f"extra_blind_ids:{','.join(extra)}",
                ],
            }
        )

    attestation_status = "NOT_PROVIDED"
    attestation_errors: list[str] = []
    if args.attestation:
        attestation_path = Path(args.attestation).expanduser().resolve()
        if not attestation_path.is_relative_to(l1_root):
            raise SystemExit("reviewer attestation must be in protected L1 storage")
        attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
        attestation_status = "PRESENT"
        if attestation.get("reviewer_id") != reviewer_id:
            attestation_errors.append("reviewer_id_mismatch")
        if attestation.get("protocol_version") != materials.get("score_version"):
            attestation_errors.append("protocol_version_mismatch")
        if attestation.get("packet_manifest_sha256") != sha256_file(materials_path):
            attestation_errors.append("materials_manifest_sha256_mismatch")
        for field in (
            "independent_scoring_confirmed",
            "no_external_search_confirmed",
            "no_consultation_confirmed",
            "rubric_training_completed",
        ):
            if attestation.get(field) is not True:
                attestation_errors.append(f"{field}_false")
        if not str(attestation.get("reviewer_signature") or "").strip():
            attestation_errors.append("reviewer_signature_missing")
        try:
            datetime.fromisoformat(
                str(attestation.get("completed_utc", "")).replace("Z", "+00:00")
            )
        except Exception:
            attestation_errors.append("completed_utc_invalid")
    elif args.strict:
        attestation_errors.append("attestation_required")

    blockers = errors + [
        {"row": 0, "blind_id": "", "errors": [f"attestation:{item}"]}
        for item in attestation_errors
    ]
    report = {
        "status": "PASS" if not blockers else "BLOCKED",
        "scores_path": str(scores_path),
        "scores_sha256": sha256_file(scores_path),
        "packet_count": len(packets),
        "rows": len(rows),
        "row_errors": len(errors),
        "attestation_status": attestation_status,
        "attestation_errors": attestation_errors,
        "blockers": blockers,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 2 if args.strict and blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())

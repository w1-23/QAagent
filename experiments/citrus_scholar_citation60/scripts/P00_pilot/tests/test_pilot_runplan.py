from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "10_make_pilot_runplan.py"
STRATA = ("L", "V", "W", "X", "P", "N")
CONDITIONS = ("B0", "B1", "B2")


def test_pilot_runplan_freezes_exactly_36_runs(tmp_path: Path) -> None:
    source = tmp_path / "pilot12.csv"
    fieldnames = [
        "pilot_id",
        "prompt",
        "source_scope",
        "allowed_corpus_family_ids",
        "stratum",
        "attachment",
        "reference_evidence_json",
        "negative_evidence_json",
        "materials_preflight",
        "availability_status",
    ]
    rows = []
    for stratum in STRATA:
        for index in range(1, 3):
            pilot_id = f"P{stratum}{index:02d}"
            rows.append(
                {
                    "pilot_id": pilot_id,
                    "prompt": f"Pilot prompt {pilot_id}",
                    "source_scope": "自动",
                    "allowed_corpus_family_ids": '["literature"]',
                    "stratum": stratum,
                    "attachment": "",
                    "reference_evidence_json": "[]",
                    "negative_evidence_json": "{}",
                    "materials_preflight": "TEST_ONLY",
                    "availability_status": "NOT_VERIFIED",
                }
            )
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    output = tmp_path / "pilot_runplan.csv"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source",
            str(source),
            "--out",
            str(output),
            "--seed",
            "20261104",
            "--reviewed-head",
            "a" * 40,
            "--reviewed-tree",
            "b" * 40,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout

    with output.open(encoding="utf-8-sig", newline="") as handle:
        plan = list(csv.DictReader(handle))
    assert len(plan) == 36
    assert [int(row["run_order"]) for row in plan] == list(range(1, 37))
    assert Counter(row["condition"] for row in plan) == {
        condition: 12 for condition in CONDITIONS
    }
    assert Counter(row["stratum"] for row in plan) == {
        stratum: 6 for stratum in STRATA
    }
    for pilot_id in {row["pilot_id"] for row in plan}:
        assert {
            row["condition"] for row in plan if row["pilot_id"] == pilot_id
        } == set(CONDITIONS)

    manifest = json.loads(
        output.with_suffix(".manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "PILOT_PLAN_FROZEN"
    assert manifest["questions"] == 12
    assert manifest["runs"] == 36
    assert manifest["not_formal_denominator"] is True
    assert manifest["seed"] == 20261104
    assert manifest["order_algorithm"] == "python-random-shuffle-v1"
    assert manifest["ordered_run_ids"] == [
        row["run_id"] for row in sorted(plan, key=lambda item: int(item["run_order"]))
    ]
    assert manifest["plan_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert [row["condition"] for row in plan] != (
        ["B0"] * 12 + ["B1"] * 12 + ["B2"] * 12
    )

    execute_result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT.with_name("20_run_pilot.py")),
            "--plan",
            str(output),
            "--stage2-report",
            str(tmp_path / "stage2-missing.json"),
            "--g10-report",
            str(tmp_path / "g10-missing.json"),
            "--stage2-report-sha256",
            "c" * 64,
            "--g10-report-sha256",
            "d" * 64,
            "--stage1-manifest",
            str(tmp_path / "stage1-missing.json"),
            "--stage1-manifest-sha256",
            "e" * 64,
            "--g01-report",
            str(tmp_path / "g01-missing.json"),
            "--g01-report-sha256",
            "f" * 64,
            "--execute",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert execute_result.returncode != 0
    assert "availability_status_not_verified" in (
        execute_result.stderr + execute_result.stdout
    )


def test_pilot_runplan_is_reproducible_and_shuffled(tmp_path: Path) -> None:
    source = tmp_path / "pilot12.csv"
    fieldnames = [
        "pilot_id",
        "prompt",
        "source_scope",
        "allowed_corpus_family_ids",
        "stratum",
        "attachment",
        "reference_evidence_json",
        "negative_evidence_json",
        "materials_preflight",
        "availability_status",
    ]
    rows = []
    for stratum in STRATA:
        for index in range(1, 3):
            pilot_id = f"P{stratum}{index:02d}"
            rows.append(
                {
                    "pilot_id": pilot_id,
                    "prompt": f"Pilot prompt {pilot_id}",
                    "source_scope": "自动",
                    "allowed_corpus_family_ids": '["literature"]',
                    "stratum": stratum,
                    "attachment": "",
                    "reference_evidence_json": "[]",
                    "negative_evidence_json": "{}",
                    "materials_preflight": "TEST_ONLY",
                    "availability_status": "VERIFIED",
                }
            )
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    plans = []
    for index in range(2):
        output = tmp_path / f"pilot_runplan_{index}.csv"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source",
                str(source),
                "--out",
                str(output),
                "--seed",
                "20261104",
                "--reviewed-head",
                "a" * 40,
                "--reviewed-tree",
                "b" * 40,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        with output.open(encoding="utf-8-sig", newline="") as handle:
            plans.append(list(csv.DictReader(handle)))
    assert [row["run_id"] for row in plans[0]] == [
        row["run_id"] for row in plans[1]
    ]
    assert [row["condition"] for row in plans[0]] != (
        ["B0"] * 12 + ["B1"] * 12 + ["B2"] * 12
    )

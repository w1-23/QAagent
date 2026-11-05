from __future__ import annotations

import csv
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
        "stratum",
        "attachment",
        "reference_evidence_json",
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
                    "source_scope": "test scope",
                    "stratum": stratum,
                    "attachment": "",
                    "reference_evidence_json": "[]",
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

"""Saved-run repair handoff with real temporary files and an offline runner.

The stub never executes code or starts containers. It captures the exact job
passed across the existing sandbox boundary, including verified input bytes.
"""
import json
from pathlib import Path

import pytest

from src.research.prepared_dataset_assets_test import temporary_stores, OWNER, SID  # noqa: F401
from src.core import account_leases, tracing
from src.figures import sandbox
from src.research import script_assets, figure_tools
from src.session import files


@pytest.fixture
def run_setup(monkeypatch):
    monkeypatch.setattr(account_leases, "_states", {})
    for name, value in (("get_user_id", OWNER), ("get_session_id", SID),
                        ("get_job_id", ""), ("get_client_request_id", "synthetic-repair")):
        monkeypatch.setattr(tracing, name, lambda value=value: value)
    jobs = []

    class Runner:
        def run(self, job):
            jobs.append((job, {name: Path(path).read_bytes() for name, path in job.sources.items()}))
            return sandbox.SandboxResult(ok=False, message="Synthetic recoverable script failure")

    monkeypatch.setattr(sandbox, "select_backend", lambda **kwargs: Runner())
    entry = files.register_file(session_id=SID, owner_user_id=OWNER,
                                filename="fixture.csv", content=b"A\n7\n")
    _, first = figure_tools.run_research_figure_script.func(script="pass # original",
        inputs_json={"data": {"file_id": entry["file_id"]}},
        parameters_json={"seed": 17}, title="Synthetic repair",
        qa_profile_json={"min_linewidth_pt": 0.7})
    assert first["_tool_outcome"]["status"] == "failed"
    assert len(jobs) == 1
    return entry, first, jobs


def repair(first, **kwargs):
    return figure_tools.run_research_figure_script.func(
        script="pass # modified", repair_from_run_id=first["run_id"], **kwargs)


def test_repair_reuses_exact_snapshots_and_options_without_internal_id_rebinding(run_setup):
    entry, first, jobs = run_setup
    original = files.get_account_file(entry["file_id"], OWNER)
    Path(original["path"]).write_bytes(b"A\n999\n")
    _, second = repair(first)
    assert len(jobs) == 2
    assert jobs[1][1] == jobs[0][1] == {"data": b"A\n7\n"}
    assert jobs[1][0].qa_profile == jobs[0][0].qa_profile
    assert jobs[1][0].qa_profile["min_linewidth_pt"] == 0.7
    assert jobs[1][0].render_mode == "initial"
    assert jobs[1][0].script.endswith("pass # modified")
    assert second["parameters"] == {"seed": 17}
    assert second["input_snapshots"] == first["input_snapshots"]
    assert second["repair_from_run_id"] == first["run_id"]
    assert second["run_id"] != first["run_id"]
    old = script_assets.get_run(OWNER, SID, first["run_id"])
    new = script_assets.get_run(OWNER, SID, second["run_id"])
    assert old["logical_task_id"] == new["logical_task_id"]
    assert old["output_contract"] == new["output_contract"]
    assert old["result"]["replay"] == new["result"]["replay"]


@pytest.mark.parametrize("change", ["owner", "session", "snapshot", "running", "succeeded"])
def test_repair_rejects_unowned_corrupt_and_uncertain_runs(run_setup, monkeypatch, change):
    _, first, jobs = run_setup
    if change == "owner":
        monkeypatch.setattr(tracing, "get_user_id", lambda: OWNER + 1)
    elif change == "session":
        monkeypatch.setattr(tracing, "get_session_id", lambda: "another-repair-session")
    elif change == "snapshot":
        snap = files.get_account_file(first["input_snapshots"][0]["file_id"], OWNER, allow_internal=True)
        Path(snap["path"]).write_bytes(b"A\n8\n")
    else:
        with script_assets._connect() as conn:
            conn.execute("UPDATE runs SET status=? WHERE run_id=?", (change, first["run_id"]))
    _, result = repair(first)
    assert result["_tool_outcome"]["status"] == "failed"
    assert "run_id" not in result
    assert len(jobs) == 1


@pytest.mark.parametrize("override", [
    {"inputs_json": {"data": {"file_id": "a" * 32}}},
    {"parameters_json": {"seed": 999}},
    {"qa_profile_json": {"min_linewidth_pt": 0.1}},
    {"outputs_json": {"version": 2, "figures": []}},
])
def test_repair_cannot_change_saved_input_or_render_contract(run_setup, override):
    _, first, jobs = run_setup
    _, result = repair(first, **override)
    assert result["_tool_outcome"]["status"] == "not_run"
    assert result["_tool_outcome"]["reason_code"] == "script_execution_rejected"
    assert "run_id" not in result and "script_ref" not in result
    assert "inherits" in result["error"]
    assert len(jobs) == 1


def test_large_run_log_retains_exact_replay_context(run_setup):
    _, first, _ = run_setup
    encoded = script_assets._result_json({**first, "script_report": "x" * 100000})
    stored = json.loads(encoded)
    assert stored["run_result_compacted"] is True
    assert stored["replay"] == first["replay"]


def test_old_run_without_replay_context_is_rejected_without_execution(run_setup):
    _, first, jobs = run_setup
    with script_assets._connect() as conn:
        payload = dict(first)
        payload.pop("replay")
        conn.execute("UPDATE runs SET result=? WHERE run_id=?", (json.dumps(payload), first["run_id"]))
    _, result = repair(first)
    assert result["_tool_outcome"]["status"] == "failed"
    assert "no complete retained repair inputs" in result["error"]
    assert len(jobs) == 1

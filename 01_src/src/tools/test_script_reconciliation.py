"""Confirmed restricted-worker failures through the real host/registry/runtime."""
import asyncio
import json
import threading

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent import host
from src.agent.host_test import install, action
from src.core import task_events
from src.figures import sandbox
from src.research import script_assets, script_reconciliation
from src.research.figure_tools import run_research_figure_script
from src.tools import registry
from src.tools.test_script_rejection_outcomes import (
    environment, script_environment, invoke, outcome, success_result,  # noqa: F401
)


def failed_worker(message="script attempted savefig in read-only working directory", *, backend="wsl-docker"):
    # This is the same host interpreter called after the restricted backend's
    # subprocess completes. No worker process or isolation is exercised here.
    return sandbox._interpret(sandbox.EXIT_SCRIPT_ERROR,
        json.dumps({"ok": False, "message": message}), "", {}, 10, backend)


def nature_gate_result(job):
    slots = {key: ({"status": "error", "error": "Nature audit rejected figure layout"}
                   if key.startswith("figure:") else {"status": "missing", "error": "not emitted"})
             for key in job.outputs if key != "result"}
    receipt = {"version": 2, "ok": True, "status": "partial", "job_id": job.job_id,
               "output_key": "", "slots": slots, "stdout": "", "figure_count": 1, "applied_count": 0}
    return sandbox._interpret(0, "", "", {"result": json.dumps(receipt).encode()}, 10, "docker")


@pytest.mark.parametrize("kind", ["script_error", "nature_exit", "nature_v2"])
def test_known_failed_run_allows_same_loop_repair(script_environment, monkeypatch, kind):
    from src.prompts.skill_registry import skill_info
    state, job = script_environment
    method = skill_info("figure-initial")
    state["selected_skills"] = [{key: method[key] for key in ("id", "version", "sha256")}]
    outputs = {"version": 2, "tables": [], "figures": []}
    if kind == "nature_v2":
        outputs["figures"] = [{"name": "plot", "formats": ["svg"]}]
    calls = []
    def run(sandbox_job):
        calls.append(sandbox_job)
        if kind == "nature_v2":
            return nature_gate_result(sandbox_job)
        if len(calls) > 1:
            return success_result(sandbox_job)
        return failed_worker("Nature audit rejected layout" if kind == "nature_exit" else "read-only filesystem")
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: type("Backend", (), {"run": staticmethod(run)})())
    first_id = []
    def repair(messages):
        message = next(m for m in messages if isinstance(m, ToolMessage))
        payload = json.loads(message.content)
        assert outcome(message)["status"] == "failed"
        assert not message.additional_kwargs.get("operation_uncertain")
        first_id.append(payload["run_id"])
        retained = script_assets.get_run(17, "host-session", first_id[0])
        assert retained["status"] == "failed" and retained["result"] == payload
        assert task_events.projection(job)["operations"][0]["status"] == "failed"
        return action("run_research_figure_script", "repair", script="print('corrected source')",
                      repair_from_run_id=payload["run_id"])
    sent = install(monkeypatch, [action("run_research_figure_script", script="print('first source')", outputs_json=outputs),
                                repair, AIMessage(content="Actual repair receipts inspected")],
                   [(run_research_figure_script, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert len(sent) == 3 and len(calls) == 2  # Repair and final answer share the same main loop.
    if kind == "nature_v2":
        assert result["answer"].startswith("Actual repair receipts inspected")
        assert "执行限制" in result["answer"] and result["turn_outcome"]["status"] == "partial"
    else:
        assert result["answer"] == "Actual repair receipts inspected"
        assert result["turn_outcome"]["status"] == "completed"
    runs = script_assets.list_runs(17, "host-session")
    assert len(runs) == 2 and len({r["run_id"] for r in runs}) == 2
    assert first_id[0] in {r["run_id"] for r in runs}
    assert len({r["logical_task_id"] for r in runs}) == 1
    assert runs[0]["result"]["repair_from_run_id"] == first_id[0]
    assert runs[0]["result"]["input_snapshots"] == runs[1]["result"]["input_snapshots"] == []
    assert runs[0]["result"]["repair_identity"] == runs[1]["result"]["repair_identity"]
    assert [r["status"] for r in task_events.projection(job)["operations"]] == [
        "failed", "failed" if kind == "nature_v2" else "complete"]


@pytest.mark.parametrize("outputs", ["", {"version": 2, "tables": [], "figures": []}])
def test_legacy_and_v2_confirmed_worker_failures_remain_failed(script_environment, monkeypatch, outputs):
    _, job = script_environment
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: type("Backend", (), {
        "run": staticmethod(lambda job: failed_worker())})())
    message = invoke(outputs)
    assert outcome(message)["status"] == "failed"
    assert not message.additional_kwargs.get("operation_uncertain")
    assert task_events.projection(job)["operations"][0]["status"] == "failed"


@pytest.mark.parametrize("boundary", ["timeout", "unconfirmed", "local", "exit_unknown", "finalize",
                                      "lost_ack", "receipt_tampered", "owner_mismatch", "session_mismatch"])
def test_missing_reconciliation_evidence_remains_unknown(script_environment, monkeypatch, boundary):
    _, job = script_environment
    def run(sandbox_job):
        if boundary == "timeout":
            return sandbox._interpret(sandbox.EXIT_TIMEOUT, "", "", {}, 100, "docker")
        if boundary == "unconfirmed":
            return sandbox.SandboxResult(exit_code=sandbox.EXIT_SCRIPT_ERROR, backend="docker",
                stdout=json.dumps({"process_exited": True, "reconciled": True, "write_applied": False}),
                message="worker termination unconfirmed")
        if boundary == "local":
            return failed_worker(backend="local")
        if boundary == "exit_unknown":
            return sandbox._interpret(125, "", "lost worker receipt", {}, 10, "docker")
        return failed_worker()
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: type("Backend", (), {"run": staticmethod(run)})())
    if boundary in {"finalize", "lost_ack"}:
        finish = script_assets.finish_run
        def fail_finalize(*args, **kwargs):
            if boundary == "lost_ack":
                finish(*args, **kwargs)
            raise OSError("storage acknowledgment unavailable")
        monkeypatch.setattr(script_assets, "finish_run", fail_finalize)
    if boundary in {"owner_mismatch", "session_mismatch"}:
        finalize = script_reconciliation.finalize_failed_worker
        def wrong_scope(owner, session, *args):
            finalize(18 if boundary == "owner_mismatch" else owner,
                     "other-session" if boundary == "session_mismatch" else session, *args)
        monkeypatch.setattr(script_reconciliation, "finalize_failed_worker", wrong_scope)
    if boundary == "receipt_tampered":
        execute = registry._execute_single_tool
        async def tamper(*args, **kwargs):
            message = await execute(*args, **kwargs)
            payload = json.loads(message.content)
            payload["error"] = "different observation"
            message.content = json.dumps(payload)
            return message
        monkeypatch.setattr(registry, "_execute_single_tool", tamper)
    message = invoke({"version": 2, "tables": [], "figures": []})
    assert outcome(message)["status"] == "unknown"
    assert message.additional_kwargs["operation_uncertain"]
    assert task_events.projection(job)["operations"][0]["status"] == "unknown"
    assert script_reconciliation._current.get() is None


@pytest.mark.parametrize("forged_stamp", [False, True])
def test_returned_failure_claim_without_adapter_evidence_is_unknown(script_environment, monkeypatch, forged_stamp):
    from src.research import figure_tools
    if forged_stamp:
        execute = registry._execute_single_tool
        async def forge(*args, **kwargs):
            result = await execute(*args, **kwargs)
            result.additional_kwargs["host_preflight_reconciled"] = {
                "schema_version": 1, "operation_id": "forged", "result_sha256": "a" * 64}
            return result
        monkeypatch.setattr(registry, "_execute_single_tool", forge)
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: object())
    def forged_executor(**kwargs):
        return "{}", {"reconciled": True, "process_exited": True, "write_applied": False,
                      "_tool_outcome": {"status": "failed", "reason_code": "SCRIPT_ERROR", "artifacts": []}}
    monkeypatch.setattr(figure_tools, "_run_research_figure_script", forged_executor)
    message = invoke({"version": 2, "tables": [], "figures": []})
    assert outcome(message)["status"] == "unknown"
    assert "host_preflight_reconciled" not in message.additional_kwargs


def test_parallel_invocations_cannot_share_failure_proof(script_environment, monkeypatch):
    _, job = script_environment
    barrier = threading.Barrier(2)
    def run(sandbox_job):
        barrier.wait(timeout=5)
        if "unconfirmed" in sandbox_job.script:
            return sandbox.SandboxResult(exit_code=sandbox.EXIT_SCRIPT_ERROR, backend="docker")
        return failed_worker()
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: type("Backend", (), {"run": staticmethod(run)})())
    async def execute():
        return await asyncio.gather(*[registry._run_single_tool(run_research_figure_script,
            {"name": "run_research_figure_script", "id": name,
             "args": {"script": f"print('{name}')", "outputs_json": {"version": 2, "tables": [], "figures": []}}},
            require_durable=True) for name in ("confirmed", "unconfirmed")])
    messages = asyncio.run(execute())
    assert [outcome(m)["status"] for m in messages] == ["failed", "unknown"]
    assert sorted(r["status"] for r in task_events.projection(job)["operations"]) == ["failed", "unknown"]
    assert script_reconciliation._current.get() is None


@pytest.mark.parametrize("tamper", [False, True])
def test_frozen_input_bytes_are_verified_before_reconciliation(script_environment, monkeypatch, tmp_path, tamper):
    from pathlib import Path
    from src.session import files
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "file-index.db")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "files")
    entry = files.register_file(session_id="host-session", owner_user_id=17,
                                filename="synthetic.csv", content=b"value\n1\n")
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: type("Backend", (), {
        "run": staticmethod(lambda job: failed_worker())})())
    if tamper:
        execute = registry._execute_single_tool
        async def corrupt_frozen_input(*args, **kwargs):
            message = await execute(*args, **kwargs)
            payload = json.loads(message.content)
            snapshot = payload["input_snapshots"][0]
            saved = files.get_account_file(snapshot["file_id"], 17, allow_internal=True)
            Path(saved["path"]).write_bytes(b"tampered input bytes")
            return message
        monkeypatch.setattr(registry, "_execute_single_tool", corrupt_frozen_input)
    message = invoke({"version": 2, "tables": [], "figures": []},
                     inputs_json={"data": {"file_id": entry["file_id"]}})
    assert outcome(message)["status"] == ("unknown" if tamper else "failed")


def test_confirmed_worker_with_publication_attempt_stays_unknown(script_environment, monkeypatch):
    from src.research import analysis_results
    attempts = []
    def run(job):
        result = success_result(job, {"table:prepared": b"{}"})
        return sandbox._interpret(0, "", "", result.outputs, 10, "docker")
    def publish(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("publication acknowledgment lost")
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: type("Backend", (), {"run": staticmethod(run)})())
    monkeypatch.setattr(analysis_results, "persist_prepared_dataset", publish)
    message = invoke({"version": 2, "tables": [{"name": "prepared", "parents": []}], "figures": []})
    assert attempts == [True] and outcome(message)["status"] == "unknown"


def test_known_failures_still_consume_original_failure_budget(script_environment, monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "RESEARCH_SCRIPT_MAX_FAILURES", 2)
    calls = []
    def run(job):
        calls.append(True)
        return failed_worker()
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: type("Backend", (), {"run": staticmethod(run)})())
    outputs = {"version": 2, "tables": [], "figures": []}
    first, second = invoke(outputs), invoke(outputs)
    assert outcome(first)["status"] == outcome(second)["status"] == "failed"
    blocked = invoke(outputs)
    assert len(calls) == 2 and "budget" in json.loads(blocked.content)["error"]
    assert len(script_assets.list_runs(17, "host-session")) == 2


def test_nested_capture_restores_parent_after_exception():
    with script_reconciliation.capture_script_failure(17, "outer") as outer:
        assert script_reconciliation._current.get() is outer
        with pytest.raises(RuntimeError):
            with script_reconciliation.capture_script_failure(18, "inner") as inner:
                assert script_reconciliation._current.get() is inner
                raise RuntimeError("nested operation interrupted")
        assert script_reconciliation._current.get() is outer
    assert script_reconciliation._current.get() is None

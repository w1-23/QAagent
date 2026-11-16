"""Pre-execution script validation through the production guarded tool runner."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent import host
from src.agent.host_test import environment, install, action  # noqa: F401
from src.core import task_events
from src.figures.script_outputs import normalize_output_contract, output_slots
from src.research import script_assets
from src.research.figure_tools import run_research_figure_script
from src.tools import registry
from src.tools.outcome import safe_tool_step


@pytest.fixture
def script_environment(environment, monkeypatch, tmp_path):
    from src import auth, config
    from src.core import account_leases
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"id": owner, "active": True})
    monkeypatch.setattr(account_leases, "_states", {})
    registry.register_tool(run_research_figure_script, is_readonly=False, write_scope="session_artifact")
    token = account_leases.set_current_account_lease(None)
    try:
        yield environment
    finally:
        account_leases.reset_current_account_lease(token)


def contract(renderer=""):
    return {"version": 2, "tables": [{"name": "result", "parents": []}],
            "figures": [{"name": "noop", "parents": ["result"], "formats": ["svg"],
                         "render_script": renderer}]}


def invoke(outputs, **kwargs):
    args = {"script": "print('synthetic')", "outputs_json": outputs, **kwargs}
    return asyncio.run(registry._run_single_tool(run_research_figure_script,
        {"name": "run_research_figure_script", "id": "script-call", "args": args}, require_durable=True))


def outcome(message):
    return safe_tool_step(message.name, message.content, message.artifact)


def success_result(job, outputs=None):
    outputs = dict(outputs or {})
    slots = {}
    for name in output_slots(job.output_contract):
        if name == "result":
            continue
        data = outputs.get(name)
        slots[name] = ({"status": "ok", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                       if data is not None else {"status": "missing", "error": "not emitted"})
    receipt = {"version": 2, "ok": True, "status": "ok", "job_id": job.job_id,
               "output_key": "", "slots": slots, "stdout": "", "figure_count": 0, "applied_count": 0}
    outputs["result"] = json.dumps(receipt).encode()
    return SimpleNamespace(ok=True, outputs=outputs, backend="offline-test", duration_ms=1)


def test_unavailable_worker_is_confirmed_not_run_before_script_or_run_writes(script_environment, monkeypatch):
    from src.figures import sandbox
    _, job = script_environment
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: None)
    monkeypatch.setattr(script_assets, "save_script", lambda *a, **kw: pytest.fail("saved source before availability"))
    message = invoke({"version": 2, "tables": [], "figures": []})
    assert outcome(message)["status"] == "not_run"
    assert not message.additional_kwargs.get("operation_uncertain")
    assert "worker" in json.loads(message.content)["error"]
    assert script_assets.list_runs(17, "host-session") == []
    assert task_events.projection(job)["operations"][0]["status"] == "not_run"


def test_rejected_contract_corrects_in_same_host_loop(script_environment, monkeypatch):
    from src.figures import sandbox
    from src.prompts.skill_registry import skill_info
    state, job = script_environment
    method = skill_info("figure-initial")
    state["selected_skills"] = [{key: method[key] for key in ("id", "version", "sha256")}]
    worker_calls = []
    def run(job):
        worker_calls.append(job)
        return success_result(job)
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: SimpleNamespace(run=run))
    source = "emit.report({'synthetic': True})"
    reference = {}
    def correct(messages):
        message = next(m for m in messages if isinstance(m, ToolMessage))
        payload = json.loads(message.content)
        assert outcome(message)["status"] == "not_run"
        assert "figure 'noop'" in payload["error"]
        assert "run_id" not in payload and not worker_calls
        assert script_assets.list_runs(17, "host-session") == []
        assert "script_ref" not in payload
        assert script_assets.list_scripts(17, "host-session") == []
        return action("run_research_figure_script", "corrected", script=source,
                      outputs_json={"version": 2, "tables": [], "figures": []})
    sent = install(monkeypatch, [action("run_research_figure_script", script=source, outputs_json=contract()),
                                correct, AIMessage(content="Corrected contract executed")],
                   [(run_research_figure_script, True)])
    result = asyncio.run(host.general_agent_node(state))
    assert result["answer"] == "Corrected contract executed" and len(sent) == 3
    assert result["turn_outcome"]["status"] == "completed"
    assert result["_trace"]["task_result"]["acceptance_status"] == "accepted"
    assert len(worker_calls) == 1
    assert [item["status"] for item in task_events.projection(job)["operations"]] == ["not_run", "complete"]
    runs = script_assets.list_runs(17, "host-session")
    assert len(runs) == 1 and runs[0]["status"] == "succeeded"
    assert script_assets.get_script(17, "host-session", runs[0]["script_ref"])["source"] == source


@pytest.mark.parametrize("outputs,diagnostic", [
    (contract(""), "nonempty bounded Python source"),
    (contract({"code": "pass"}), "nonempty bounded Python source"),
    (contract("if :\n    pass"), "line 1"),
    ("{not-json", "outputs_json 需要 JSON"),
])
def test_pure_contract_rejection_writes_nothing_without_unknown(script_environment, outputs, diagnostic):
    _, job = script_environment
    message = invoke(outputs)
    payload = json.loads(message.content)
    assert message.additional_kwargs["execution_dispatched"]
    assert not message.additional_kwargs.get("operation_uncertain")
    assert outcome(message)["status"] == "not_run"
    assert outcome(message)["reason_code"] == "script_execution_rejected"
    proof = message.additional_kwargs["host_preflight_reconciled"]
    assert proof == {"schema_version": 1, "operation_id": message.additional_kwargs["operation_id"],
                     "result_sha256": hashlib.sha256(message.content.encode("utf-8")).hexdigest()}
    assert diagnostic in payload["error"]
    assert "script_ref" not in payload and "run_id" not in payload
    assert script_assets.list_scripts(17, "host-session") == []
    assert script_assets.list_runs(17, "host-session") == []
    assert task_events.projection(job)["operations"][0]["status"] == "not_run"


def test_null_renderer_is_omitted_but_nonnull_source_remains_strict():
    explicit_null = contract(None)
    omitted = contract(None)
    del omitted["figures"][0]["render_script"]
    assert normalize_output_contract(explicit_null) == normalize_output_contract(omitted)
    assert "render_script" not in normalize_output_contract(explicit_null)["figures"][0]
    valid = contract("fig = None\n")
    assert normalize_output_contract(valid)["figures"][0]["render_script"] == "fig = None\n"
    for renderer in ("", " ", {}, [], False, 3):
        with pytest.raises(ValueError, match="figure 'noop'.*nonempty"):
            normalize_output_contract(contract(renderer))


def test_null_renderer_reaches_worker_as_absent(script_environment, monkeypatch):
    from src.figures import sandbox
    observed = []
    def run(job):
        observed.append(job.output_contract)
        raise RuntimeError("worker observation lost")
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: SimpleNamespace(run=run))
    message = invoke(contract(None))
    assert len(observed) == 1 and "render_script" not in observed[0]["figures"][0]
    assert outcome(message)["status"] == "unknown"
    assert message.additional_kwargs["operation_uncertain"]


@pytest.mark.parametrize("stage", ["source_save", "run_reservation", "worker", "publication"])
def test_uncertain_writes_are_not_reclassified_as_validation(script_environment, monkeypatch, stage):
    from src.figures import sandbox
    from src.research import analysis_results
    _, job = script_environment
    effects = []
    outputs = {"version": 2, "tables": [], "figures": []}
    def run(sandbox_job):
        if stage == "worker":
            effects.append("worker entered")
            raise ValueError("worker receipt lost")
        return success_result(sandbox_job, {"table:prepared": b"{}"} if stage == "publication" else {})
    monkeypatch.setattr(sandbox, "select_backend", lambda **_: SimpleNamespace(run=run))
    if stage in {"source_save", "run_reservation"}:
        name = "save_script" if stage == "source_save" else "begin_budgeted_run"
        original = getattr(script_assets, name)
        def lost_ack(*args, **kwargs):
            original(*args, **kwargs)
            effects.append(stage)
            raise ValueError("lost write acknowledgment")
        monkeypatch.setattr(script_assets, name, lost_ack)
    elif stage == "publication":
        outputs["tables"] = [{"name": "prepared", "parents": []}]
        def failed_publication(*args, **kwargs):
            effects.append("publication entered")
            raise ValueError("publication acknowledgment lost")
        monkeypatch.setattr(analysis_results, "persist_prepared_dataset", failed_publication)
    message = invoke(outputs)
    assert len(effects) == 1
    assert outcome(message)["status"] == "unknown"
    assert outcome(message)["reason_code"] == "write_requires_reconciliation"
    assert message.additional_kwargs["operation_uncertain"]
    assert task_events.projection(job)["operations"][0]["status"] == "unknown"

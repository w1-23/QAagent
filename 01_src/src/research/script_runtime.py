"""Persist-and-run adapter over the existing sandbox, never an interpreter."""
from __future__ import annotations

import contextvars
import hashlib
import json
import math
import re

_task_scope = contextvars.ContextVar("research_script_task_scope", default="")


def bind_task_scope(scope: str):
    return _task_scope.set(str(scope or ""))


def reset_task_scope(token):
    _task_scope.reset(token)


def _object(value, name):
    if value is None or value == "":
        return {}
    result = json.loads(value) if isinstance(value, str) else value
    if not isinstance(result, dict):
        raise ValueError(name + " must be an object")
    text = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if len(text) > 64000:
        raise ValueError(name + " exceeds its size limit")
    return json.loads(text)


def _repair_output_contract(contract):
    """Output slots/lineage are requirements; renderer source is repairable code."""
    return {**contract, "figures": [
        {key: value for key, value in figure.items() if key != "render_script"}
        for figure in contract.get("figures", [])]} if contract else {}


def _repair_execution_options(options):
    # Caption prose may be corrected alongside source. Keep the QA profile and
    # every numeric check (dimensions, fonts, line widths), formats and outputs.
    # Request-key idempotency still uses the FULL original options separately.
    result = dict(options)
    if options.get("qa_profile_json"):
        profile = _object(options["qa_profile_json"], "qa_profile_json")
        result["qa_profile_json"] = {key: value for key, value in profile.items() if key != "caption"}
    return result


def _receipt_json(payload):
    """Keep accepted refs even if ancillary executor metadata is not JSON."""
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False), payload
    except (TypeError, ValueError, RecursionError):
        def clean(value, depth=0):
            if depth > 32:
                return '[metadata nesting omitted]'
            if value is None or isinstance(value, (str, bool, int)):
                return value
            if isinstance(value, float):
                return value if math.isfinite(value) else '[non-finite metadata omitted]'
            if isinstance(value, dict):
                return {str(key): clean(item, depth + 1) for key, item in value.items()}
            if isinstance(value, (tuple, list)):
                return [clean(item, depth + 1) for item in value]
            return '[non-JSON metadata omitted]'
        payload = clean(payload)
        payload['receipt_metadata_normalized'] = True
        return json.dumps(payload, ensure_ascii=False, allow_nan=False), payload


def _cancelled(exc):
    from src.core.cancel import ToolCancelled
    return (isinstance(exc, (ToolCancelled, KeyboardInterrupt, SystemExit))
            or "cancel" in type(exc).__name__.lower())


def _finalize(assets, owner, session, run, status, payload):
    """Do not turn a completed publication into an empty rejection on log I/O.

    An identical retry handles transient storage errors (including a lost commit
    acknowledgment). A persistent database outage remains explicitly visible;
    it cannot truthfully be reported as a successfully persisted terminal run.
    """
    first_error = None
    for _ in range(2):
        try:
            assets.finish_run(owner, session, run['run_id'], status=status,
                result=payload, error=str(payload.get('error', '')))
            if first_error is not None:
                return {'status': 'recovered', 'message': 'Run record persisted after a storage retry'}
            return None
        except Exception as exc:
            first_error = first_error or exc
    return {'status': 'not_persisted', 'run_id': run['run_id'],
            'message': 'Execution receipt retained; terminal run record could not be persisted',
            'error': assets._error(str(first_error))}


def execute_script_request(executor, *, script="", script_ref_json="", parameters_json="",
                           repair_from_run_id="", **kwargs):
    from src.research import script_assets as assets
    from src.research.script_presets import get_preset
    from src.core.tracing import get_user_id, get_session_id, get_job_id, get_client_request_id
    from src.core.account_leases import account_lease
    from src.config import settings

    owner, session = get_user_id(), get_session_id()
    saved, run, context = None, None, {}
    created = False
    # Only a real host request identity authorizes idempotent reuse. Neither
    # identical source nor equal parameters means two research runs are one.
    # This optional host-only kwarg is never forwarded into the sandbox executor.
    request_key = kwargs.pop('request_key', '')

    def receipt(content, payload):
        payload = dict(payload or {})
        if saved:
            payload["script_ref"] = saved["script_ref"]
        if run:
            payload["run_id"] = run["run_id"]
        if "input_snapshots" in context:
            payload["input_snapshots"] = context["input_snapshots"]
            payload["replay"] = {"input_snapshots": context["input_snapshots"],
                                 "execution_options": options}
        if repair_from_run_id:
            payload["repair_from_run_id"] = repair_from_run_id
        if context.get("parameters") is not None:
            payload["parameters"] = context["parameters"]
        if (run and run.get("logical_task_id")
                and isinstance(context.get("input_snapshots"), list)):
            # An authenticated empty input snapshot is complete evidence too.
            # Absence of the snapshot remains ineligible for repair reduction.
            # Host-bound output contract, input bytes, parameters and environment
            # identify a repair. Source-code changes alone do not create a new
            # required operation; model-supplied labels cannot grant this identity.
            sources = [{k: item.get(k) for k in ("variable", "sha256", "source_file_id", "dataset_ref")}
                       for item in context["input_snapshots"]]
            repair_environment = {**environment, "execution_options_sha256": hashlib.sha256(
                json.dumps(_repair_execution_options(options), sort_keys=True,
                           ensure_ascii=False, allow_nan=False).encode()).hexdigest()}
            identity = [run["logical_task_id"], sources, parameters, repair_environment]
            payload["repair_identity"] = hashlib.sha256(json.dumps(identity,
                ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if not isinstance(content, str) or not content.startswith("{"):
            payload["message"] = str(content)[:4000]
        payload = assets.public_provenance(payload)
        return _receipt_json(payload)

    def complete(content, payload, status):
        content, payload = receipt(content, payload)
        warning = _finalize(assets, owner, session, run, status, payload)
        if warning:
            payload['run_record'] = warning
            content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        from src.research.script_reconciliation import finalize_failed_worker
        finalize_failed_worker(owner, session, run, payload, warning)
        return content, payload

    try:
        ref = _object(script_ref_json, "script_ref_json")
        parameters = _object(parameters_json, "parameters_json")
        bindings = _object(kwargs.get("inputs_json"), "inputs_json")
        if repair_from_run_id and (parameters or bindings or any(value for value in kwargs.values())):
            payload = {"error": "Repair inherits inputs, parameters, outputs and render options; supply only new source/ref and repair_from_run_id. A changed output contract requires a new run with explicit authorized bindings.",
                       "_tool_outcome": {"status": "not_run", "reason_code": "script_execution_rejected", "artifacts": []}}
            from src.research.script_reconciliation import observe_preflight_rejection
            observe_preflight_rejection(payload)
            return _receipt_json(payload)
        if "parameters" in bindings or "_citrus_parameters_json" in bindings:
            raise ValueError("parameters is reserved for host-bound script parameters")
        if bool(script.strip()) == bool(ref):
            raise ValueError("Supply source script or an exact script_ref_json, not both")
        with account_lease(owner):
            previous = None
            replay = None
            if repair_from_run_id:
                # This ref selects retained host facts, never caller-supplied
                # internal file IDs. Running/unknown writes cannot be replayed.
                previous = assets.get_run(owner, session, repair_from_run_id)
                if previous["status"] not in {"failed", "partial"}:
                    raise ValueError("Repair requires a retained failed or partial run; inspect other run states first")
                replay = (previous.get("result") or {}).get("replay")
                if not isinstance(replay, dict) or set(replay) != {"input_snapshots", "execution_options"}:
                    raise ValueError("This run has no complete retained repair inputs; read its receipt and bind authorized sources explicitly")
                from src.figures.projects import script_replay_inputs
                script_replay_inputs(replay, owner)
                parameters = previous["parameters"]
                bindings = previous["inputs"]
                kwargs.update(replay["execution_options"])
                kwargs["inputs_json"] = bindings
                kwargs["outputs_json"] = previous["output_contract"]
            preset = None
            if ref.get("kind") == "builtin_script":
                preset = get_preset(ref)
                saved = assets.save_script(owner, session, name=preset["name"], source=preset["source"], origin_ref=ref)
            elif ref:
                saved = assets.get_script(owner, session, ref, touch=True)
                if (saved.get("origin_ref") or {}).get("kind") == "builtin_script":
                    try:
                        preset = get_preset(saved["origin_ref"])
                    except ValueError:
                        pass
            else:
                name = re.sub(r"[^A-Za-z0-9_-]", "_", str(kwargs.get("output_name") or "analysis"))[:60] + ".py"
                if not re.match(r'[A-Za-z_]', name):
                    name = 'analysis_' + name
                saved = assets.save_script(owner, session, name=name, source=script)
            from src.figures.script_outputs import normalize_output_contract
            try:
                contract = _object(kwargs.get("outputs_json"), "outputs_json")
                if not contract and preset:
                    contract = preset["outputs"]
                contract = normalize_output_contract(contract)
            except ValueError as exc:
                # Source persistence above has an acknowledged exact ref. This
                # pure validation cannot have reserved a run, executed code or
                # published outputs; report that boundary, retaining the ref so
                # the model can correct the contract without resaving the source.
                return receipt(str(exc), {"error": assets._error(str(exc)), "_tool_outcome": {
                    "status": "not_run", "reason_code": "script_execution_rejected", "artifacts": []}})
            context = {"script_ref": saved["script_ref"], "parameters": parameters}
            if replay is not None:
                context["replay_input_snapshots"] = replay["input_snapshots"]
            scope = _task_scope.get() or get_job_id() or get_client_request_id() or session
            operation = hashlib.sha256(json.dumps([bindings, _repair_output_contract(contract)],
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
            logical = (previous["logical_task_id"] if previous else
                       hashlib.sha256(str(scope).encode()).hexdigest() + ":" + operation)
            environment = {key: str(getattr(settings, key, "") or "") for key in (
                "FIGURES_SANDBOX_BACKEND", "FIGURES_SANDBOX_IMAGE", "FIGURES_SANDBOX_IMAGE_SHA256", "FIGURES_SANDBOX_RUNTIME")}
            if contract.get("figures") or not kwargs.get("outputs_json"):
                from src.figures.nature_receipts import COMMIT as nature_commit
                environment["nature_skill_commit"] = nature_commit
            # QA, analysis refs, titles and legacy formats affect the execution
            # too; a reused request key with any changed option must conflict.
            options = {key: value for key, value in kwargs.items() if key not in {'inputs_json', 'outputs_json'}}
            environment['execution_options_sha256'] = hashlib.sha256(json.dumps(
                options, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
            failure_limit = int(getattr(settings, "RESEARCH_SCRIPT_MAX_FAILURES", 0) or
                                getattr(settings, "AGENT_MAX_MODEL_CALLS", 64) or 64)
            if previous:
                inherited = (previous.get("environment") or {}).get("script_failure_limit")
                if type(inherited) is int and inherited > 0:
                    failure_limit = min(failure_limit, inherited)
            environment["script_failure_limit"] = failure_limit
            run, created = assets.begin_budgeted_run(owner, session, saved["script_ref"], inputs=bindings,
                parameters=parameters, output_contract=contract, environment=environment,
                logical_task_id=logical, request_key=request_key, max_failures=failure_limit)
            context["run_id"] = run["run_id"]
            if not created:
                if run['status'] == 'running':
                    return receipt('', {'run_status': 'running', '_tool_outcome': {
                        'status': 'not_run', 'reason_code': 'script_request_in_progress', 'artifacts': []}})
                payload = dict(run.get('result') or {})
                payload.setdefault('_tool_outcome', {'status': {'succeeded': 'complete'}.get(run['status'], run['status']),
                    'reason_code': 'script_request_replayed', 'artifacts': []})
                if run.get('error'):
                    payload.setdefault('error', run['error'])
                return receipt('{}', payload)
            try:
                prefix = "import json as _citrus_parameters_json\nparameters = _citrus_parameters_json.loads(" + repr(
                    json.dumps(parameters, ensure_ascii=False, allow_nan=False)) + ")\n"
                call = dict(kwargs, script=prefix + saved["source"], inputs_json=bindings, outputs_json=contract,
                            script_record=context)
                content, payload = executor(**call)
            except BaseException as exc:
                state = "cancelled" if _cancelled(exc) else "failed"
                payload = {"error": assets._error(str(exc)), "_tool_outcome": {
                    "status": state, "reason_code": "script_execution_cancelled" if state == 'cancelled' else "script_execution_failed",
                    "artifacts": []}}
                result = complete(str(exc), payload, state)
                if not isinstance(exc, Exception):
                    raise
                return result
            outcome = (payload or {}).get("_tool_outcome", {})
            outcome = outcome if isinstance(outcome, dict) else {}
            status = {"complete": "succeeded", "partial": "partial", "cancelled": "cancelled"}.get(outcome.get("status"), "failed")
            # Publication is already complete here. Its receipt survives logging
            # compaction or a finalization failure and is never replaced by [] .
            return complete(content, payload, status)
    except Exception as exc:
        state = 'cancelled' if _cancelled(exc) else 'failed'
        payload = {"error": assets._error(str(exc)), "_tool_outcome": {
            "status": state, "reason_code": "script_execution_cancelled" if state == 'cancelled' else "script_execution_rejected",
            "artifacts": []}}
        if run and created:
            return complete(str(exc), payload, state)
        return receipt(str(exc), payload)

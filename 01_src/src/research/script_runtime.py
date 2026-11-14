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


def execute_script_request(executor, *, script="", script_ref_json="", parameters_json="", **kwargs):
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
        if context.get("input_snapshots"):
            payload["input_snapshots"] = context["input_snapshots"]
        if context.get("parameters") is not None:
            payload["parameters"] = context["parameters"]
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
        return content, payload

    try:
        ref = _object(script_ref_json, "script_ref_json")
        parameters = _object(parameters_json, "parameters_json")
        bindings = _object(kwargs.get("inputs_json"), "inputs_json")
        if "parameters" in bindings or "_citrus_parameters_json" in bindings:
            raise ValueError("parameters is reserved for host-bound script parameters")
        if bool(script.strip()) == bool(ref):
            raise ValueError("Supply source script or an exact script_ref_json, not both")
        with account_lease(owner):
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
            contract = _object(kwargs.get("outputs_json"), "outputs_json")
            if not contract and preset:
                contract = preset["outputs"]
            from src.figures.script_outputs import normalize_output_contract
            contract = normalize_output_contract(contract)
            context = {"script_ref": saved["script_ref"], "parameters": parameters}
            scope = _task_scope.get() or get_job_id() or get_client_request_id() or session
            operation = hashlib.sha256(json.dumps([bindings, contract],
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
            logical = hashlib.sha256(str(scope).encode()).hexdigest() + ":" + operation
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
            run, created = assets.begin_budgeted_run(owner, session, saved["script_ref"], inputs=bindings,
                parameters=parameters, output_contract=contract, environment=environment,
                logical_task_id=logical, request_key=request_key)
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

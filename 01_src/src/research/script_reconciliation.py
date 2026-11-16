"""Private, invocation-scoped evidence for a failed script with no publication.

Only production adapters can record this evidence. Nothing in tool arguments,
worker JSON, or a model-visible outcome grants reconciliation authority.
"""
from __future__ import annotations

from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
import hashlib
import json


_current = contextvars.ContextVar("script_failure_reconciliation", default=None)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


@dataclass
class _FailureEvidence:
    owner: int
    session: str
    source: dict | None = None
    result_digest: str = ""
    preflight_digest: str = ""

    def matches(self, message) -> bool:
        """Recheck private invocation evidence before relaxing unknown."""
        if self.preflight_digest:
            try:
                payload = json.loads(message.content)
                return (payload.get("_tool_outcome") == {
                            "status": "not_run", "reason_code": "script_execution_rejected", "artifacts": []}
                        and _digest(payload) == self.preflight_digest
                        and {key: value for key, value in (message.artifact or {}).items()
                             if key != "op_receipt"} == payload)
            except (ValueError, TypeError, AttributeError):
                return False
        if not self.source or not self.result_digest:
            return False
        try:
            from src.research import script_assets
            from src.figures.projects import script_replay_inputs
            payload = json.loads(message.content)
            if (_digest(payload) != self.result_digest
                    or payload.get("_tool_outcome", {}).get("status") != "failed"
                    or (message.artifact or {}).get("_tool_outcome") != payload["_tool_outcome"]):
                return False
            run = script_assets.get_run(self.owner, self.session, self.source["run_id"])
            if (run["status"] != "failed" or not run["finished_at"]
                    or run["script_ref"] != self.source["script_ref"]
                    or _digest(run["result"]) != self.result_digest):
                return False
            # get_run verifies the owned source version; verify frozen input
            # bytes too, rather than allowing a repair against missing evidence.
            script_replay_inputs(payload["replay"], self.owner)
            return True
        except Exception:
            return False  # Lost/compacted/corrupt receipts remain unknown writes.


@contextmanager
def capture_script_failure(owner: int, session: str):
    evidence = _FailureEvidence(owner, session)
    token = _current.set(evidence)
    try:
        yield evidence
    finally:
        _current.reset(token)


def observe_preflight_rejection(payload):
    """Seal a pure validation rejection before any script/run/input writes.

    Only the first-party adapter calls this branch. Public payload fields do
    not grant authority, and evidence never survives the invocation context.
    """
    from src.core.tracing import get_user_id, get_session_id
    evidence = _current.get()
    if (evidence is not None and evidence.source is None and not evidence.result_digest
            and (get_user_id(), get_session_id()) == (evidence.owner, evidence.session)
            and payload.get("_tool_outcome") == {
                "status": "not_run", "reason_code": "script_execution_rejected", "artifacts": []}):
        evidence.preflight_digest = _digest(payload)


def observe_failed_worker(result, source: dict, *, validated_output_failure=False):
    """Called only at adapter branches that have made no publication attempt.

    A successful worker with a validated v2 failure receipt can also be observed;
    the publisher must have rejected all outputs without attempting persistence.
    """
    from src.figures.sandbox import (SandboxResult, EXIT_SCRIPT_ERROR, EXIT_NO_FIGURE,
                                    EXIT_MULTIPLE_FIGURES, EXIT_FORBIDDEN, EXIT_OK)
    evidence = _current.get()
    if (evidence is None or not isinstance(result, SandboxResult)
            or result.process_exited is not True or result.timed_out
            or result.backend not in {"docker", "wsl-docker", "apptainer"}):
        return
    known_failure = (not result.ok and not result.outputs
                     and result.exit_code in {EXIT_SCRIPT_ERROR, EXIT_NO_FIGURE,
                                              EXIT_MULTIPLE_FIGURES, EXIT_FORBIDDEN})
    if not known_failure and not (validated_output_failure and result.ok and result.exit_code == EXIT_OK):
        return
    if not source.get("run_id") or not source.get("script_ref") or "input_snapshots" not in source:
        return
    evidence.source = json.loads(json.dumps({key: source[key] for key in
        ("run_id", "script_ref", "input_snapshots")}))


def finalize_failed_worker(owner, session, run, payload, warning):
    """Seal only an acknowledged durable failed run, before returning its receipt."""
    evidence = _current.get()
    if (evidence is None or evidence.source is None or warning is not None
            or (owner, session) != (evidence.owner, evidence.session)
            or run["run_id"] != evidence.source["run_id"]
            or payload.get("run_id") != evidence.source["run_id"]
            or payload.get("script_ref") != evidence.source["script_ref"]
            or payload.get("_tool_outcome", {}).get("status") != "failed"
            or payload.get("replay", {}).get("input_snapshots") != evidence.source["input_snapshots"]):
        return
    evidence.result_digest = _digest(payload)

"""Invocation-private proof of a rejected workspace CAS, never a payload claim."""
from contextlib import contextmanager
import contextvars
from dataclasses import dataclass
import hashlib
import json

_current = contextvars.ContextVar("workspace_cas_reconciliation", default=None)
_OUTCOME = {"status": "not_run", "reason_code": "workspace_version_conflict", "artifacts": []}


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode()).hexdigest()


@dataclass
class _ConflictEvidence:
    owner: int
    session: str
    preflight_digest: str = ""

    def matches(self, message):
        try:
            payload = json.loads(message.content)
            return bool(self.preflight_digest and payload.get("_tool_outcome") == _OUTCOME
                and payload.get("write_applied") is False
                and _digest(payload) == self.preflight_digest
                and {key: value for key, value in (message.artifact or {}).items()
                     if key != "op_receipt"} == payload)
        except (ValueError, TypeError, AttributeError):
            return False


@contextmanager
def capture_write_conflict(owner, session):
    evidence = _ConflictEvidence(owner, session)
    token = _current.set(evidence)
    try:
        yield evidence
    finally:
        _current.reset(token)


def observe_write_conflict(storage, payload):
    """Called only after WorkspaceConflict unwinds the storage transaction.

    The first CAS check precedes persistence; the second rolls back the version
    transaction and deletes its own staged file before reaching this adapter.
    A cleanup failure never reaches this branch and cannot acquire this proof.
    """
    from src.core import tracing
    evidence = _current.get()
    if (evidence is not None and (storage.owner, storage.session) == (evidence.owner, evidence.session)
            and (tracing.get_user_id(), tracing.get_session_id()) == (evidence.owner, evidence.session)
            and payload.get("_tool_outcome") == _OUTCOME and payload.get("write_applied") is False):
        evidence.preflight_digest = _digest(payload)

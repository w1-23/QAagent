"""Host callbacks for the protected Harness checkpoint/wake boundary.

No model tool, scheduler, run takeover, budget reset or Code resubmission lives
here. Caller must own the run fence and original request accounting. In
particular, do not enable product Code tools before Host/API parking is wired.
"""
from __future__ import annotations

import json

from src.code_execution.waits import ExecutionWaits, WaitConflict
from .runtime import validate_execution_waits


class ExecutionWaitBoundary:
    def __init__(self, executions):
        self.executions = executions
        self.identity = executions.identity
        self.waits = ExecutionWaits(executions)

    def _authorize(self, identity, wait_ids):
        if identity != self.identity:
            raise PermissionError("execution_wait_identity_conflict")
        validate_execution_waits(wait_ids)
        if not wait_ids:
            raise ValueError("execution_wait_references_required")

    async def resolve(self, identity, wait_ids):
        """Read real, reauthorized terminal observations without a wake claim."""
        self._authorize(identity, wait_ids)
        observations = []
        for wait_id in wait_ids:
            current = self.waits.status(wait_id)
            if current["state"] in {"waiting", "revoked"}:
                return None
            if current["state"] != "ready":
                raise WaitConflict("execution_wake_already_consumed")
            execution = self.executions.status(current["execution_id"])
            if (execution["status"] != current["completion"]["execution_status"]
                    or execution["fence"] != current["completion"]["execution_fence"]):
                raise WaitConflict("execution_wake_observation_conflict")
            observations.append({"wait_id":wait_id, "execution_id":current["execution_id"],
                                 **current["completion"]})
        note = "执行队列的持久完成观察：\n" + json.dumps(observations, ensure_ascii=False, separators=(",", ":"))
        if len(note) > 65536:
            raise ValueError("execution_completion_observation_limit")
        return (note,)

    async def acknowledge(self, identity, wait_ids, checkpoint_ref):
        """Only after Harness commits the consumed receipt to its protected saver.

        Claim after commit, so a slow checkpoint cannot exhaust a wake lease.
        If a process dies after commit but before acknowledgment, the encrypted
        graph retains the IDs and actual checkpoint config. A fresh process can
        acquire a new notification generation and ack the SAME receipt, before
        another model request. Acknowledged prefixes are idempotent.
        """
        self._authorize(identity, wait_ids)
        for wait_id in wait_ids:
            current = self.waits.status(wait_id)
            if current["state"] == "acknowledged":
                if current["checkpoint_ref"] != checkpoint_ref:
                    raise WaitConflict("execution_wake_checkpoint_conflict")
                continue
            claim = self.waits.claim(wait_id)
            if claim is None:
                raise WaitConflict("execution_wake_claim_unavailable")
            self.waits.acknowledge(wait_id, generation=claim["generation"],
                claim_token=claim["claim_token"], checkpoint_ref=checkpoint_ref)

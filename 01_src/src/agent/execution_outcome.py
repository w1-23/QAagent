"""Private Host/graph suspension contract; never accept this from a client.

Validation checks shape and identity, not checkpoint existence or authorization.
The Host alone issues the receipt after reading its protected committed saver.
"""
from __future__ import annotations

from dataclasses import asdict

from .identity import ExecutionIdentity
from .runtime import validate_execution_waits


def validate_execution_suspension(value, identity: ExecutionIdentity) -> dict:
    from src.code_execution.protocol import token

    if (type(identity) is not ExecutionIdentity or type(value) is not dict
            or set(value) != {"identity", "checkpoint_ref", "wait_ids"}):
        raise ValueError("invalid_execution_suspension")
    binding = value["identity"]
    if (type(binding) is not dict or set(binding) != set(asdict(identity))
            or ExecutionIdentity(**binding) != identity):
        raise ValueError("execution_suspension_identity_conflict")
    if type(value["checkpoint_ref"]) is not str:
        raise ValueError("invalid_execution_suspension_checkpoint")
    token(value["checkpoint_ref"])
    if type(value["wait_ids"]) is not list:
        raise ValueError("invalid_execution_suspension_waits")
    waits = validate_execution_waits(tuple(value["wait_ids"]))
    if not waits:
        raise ValueError("execution_suspension_waits_required")
    return {"identity": asdict(identity), "checkpoint_ref": value["checkpoint_ref"],
            "wait_ids": list(waits)}

"""Private persisted backend dispatch and physical-runtime identity validation."""
from __future__ import annotations

import json
import re

from .oci import OCIError, OCIHandle, OCIStatus


def parse_handle(payload):
    if not isinstance(payload, dict):
        raise OCIError("invalid_persisted_execution_handle")
    if payload.get("backend") == "apptainer-v1":
        from .apptainer import ApptainerHandle
        return ApptainerHandle.parse(payload)
    if payload.get("backend", "oci-v1") == "oci-v1":
        return OCIHandle.parse(payload)
    raise OCIError("unknown_execution_backend")


def supported_handle(handle):
    from .apptainer import ApptainerHandle
    return type(handle) in (OCIHandle, ApptainerHandle)


def stopped_identity(handle, observed):
    """Only exact typed, physically stopped observations authorize release/GC."""
    if type(handle) is OCIHandle:
        if (type(observed) is not OCIStatus or observed.state != "stopped" or observed.stopped is not True
                or not re.fullmatch(r"[a-f0-9]{64}", observed.container_id)):
            raise OCIError("execution_physical_stop_required")
        return observed.container_id
    from .apptainer import ApptainerHandle, ApptainerStatus
    if (type(handle) is not ApptainerHandle or type(observed) is not ApptainerStatus
            or observed.state != "stopped" or observed.stopped is not True):
        raise OCIError("execution_physical_stop_required")
    observed.validate_identity(handle)
    return json.dumps(observed.identity, sort_keys=True, separators=(",", ":"))

#!/usr/bin/env python3
"""Fail-closed authorization checks for model and judge execution."""
from __future__ import annotations

from typing import Any, Mapping


APPROVED_REVIEW_STATUSES = {
    "APPROVED",
    "APPROVED_FOR_EXECUTION",
}


class ExecutionAuthorizationError(RuntimeError):
    """Raised when execution lacks every required authorization signal."""


def authorization_errors(config: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if config.get("status") != "READY":
        errors.append("status_not_READY")
    if config.get("execution_authorized") is not True:
        errors.append("execution_authorized_not_true")
    if config.get("review_status") not in APPROVED_REVIEW_STATUSES:
        errors.append("review_status_not_approved")
    return errors


def require_execution_authorized(
    config: Mapping[str, Any],
    *,
    component: str,
) -> None:
    errors = authorization_errors(config)
    if errors:
        joined = ",".join(errors)
        raise ExecutionAuthorizationError(
            f"{component} execution is not authorized: {joined}"
        )


def require_pilot_authorized(
    config: Mapping[str, Any],
    *,
    component: str,
) -> None:
    """Pilot execution may start at PILOT_READY; formal execution may not."""
    errors: list[str] = []
    if config.get("status") not in {"PILOT_READY", "READY"}:
        errors.append("status_not_PILOT_READY_or_READY")
    if config.get("execution_authorized") is not True:
        errors.append("execution_authorized_not_true")
    if config.get("review_status") not in APPROVED_REVIEW_STATUSES:
        errors.append("review_status_not_approved")
    if errors:
        raise ExecutionAuthorizationError(
            f"{component} pilot execution is not authorized: {','.join(errors)}"
        )

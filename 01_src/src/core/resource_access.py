"""Shared authorization decisions for server-owned resource classes.

Routes remain responsible for mapping a decision to their existing HTTP
contract. This module centralizes the policy so corpus reads, session files,
deliverables, and admin operations do not grow independent ownership rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PUBLIC_CORPUS = "public_corpus"
PRIVATE_FILE = "private_file"
PROJECT_RECORD = "project_record"
GENERATED_ARTIFACT = "generated_artifact"
ADMIN_OPERATION = "admin_operation"


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str


def authorize_resource(
    resource_class: str,
    user: dict[str, Any] | None,
    *,
    access_level: str = "owner",
    owner_user_id: int | None = None,
) -> AccessDecision:
    """Return the authorization decision without leaking resource existence.

    Private resources remain owner-scoped even for administrators. Public
    corpora require an authenticated account; admin-only resources require an
    admin role. Unauthenticated responses are handled by the API auth layer.
    """
    if not isinstance(user, dict) or not user.get("user_id"):
        return AccessDecision(False, "unauthenticated")
    resource_class = str(resource_class or "").strip()
    access_level = str(access_level or "owner").strip().casefold()
    if resource_class == ADMIN_OPERATION:
        return AccessDecision(str(user.get("role") or "") == "admin",
                              "allowed" if user.get("role") == "admin" else "admin_required")
    if resource_class == PUBLIC_CORPUS:
        allowed = access_level == "public" or str(user.get("role") or "") == "admin"
        return AccessDecision(allowed, "allowed" if allowed else "forbidden")
    if resource_class in {PRIVATE_FILE, PROJECT_RECORD, GENERATED_ARTIFACT}:
        allowed = owner_user_id is not None and int(user["user_id"]) == int(owner_user_id)
        return AccessDecision(allowed, "allowed" if allowed else "owner_mismatch")
    return AccessDecision(False, "unknown_resource_class")

"""Invite, application-review, and security-audit repository (A1).

Every function accepts an existing ``sqlite3.Connection`` whose caller owns the
write transaction; nothing here commits or rolls back.  Rows are read by column
name so the caller may use any row factory, and every value is bound through a
SQL parameter.

Only the standard library is imported: ``src.auth`` imports this module.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

FIXED_INVITE_CODE_RE = re.compile(r"[A-Z]{6}")
INVITE_CODE_RE = re.compile(r"(?:[A-Za-z0-9_-]{32}|[A-Z]{6})")

_APPLICATION_FIELDS = (
    "application_id", "user_id", "username", "avatar_preset", "status",
    "submitted_at", "reviewed_by", "reviewed_at", "rejection_reason",
)
_APPLICATION_STATUSES = ("pending", "approved", "rejected")
_DECISIONS = ("approved", "rejected")
_AUDIT_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,40}")
_AUDIT_SENSITIVE_TOKENS = frozenset({
    "password", "passwd", "secret", "token", "code", "ip", "body", "raw",
    "hash", "salt", "credential", "credentials", "recovery",
})
_AUDIT_MAX_VALUE = 200
_MAX_REJECTION_REASON = 300


class AccountInviteError(Exception):
    """Repository account-admission error carrying a stable ``code``."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = str(code)
        super().__init__(message or self.code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _fetch_all(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    cursor = conn.execute(sql, params)
    names = [column[0] for column in cursor.description] if cursor.description else []
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _fetch_one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    cursor = conn.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        return None
    names = [column[0] for column in cursor.description] if cursor.description else []
    return dict(zip(names, row))


def _page(limit: Any, offset: Any) -> tuple[int, int]:
    try:
        limit_value = int(limit)
        offset_value = int(offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and offset must be integers") from exc
    if not 1 <= limit_value <= 100:
        raise ValueError("limit must be between 1 and 100")
    if offset_value < 0:
        raise ValueError("offset must be non-negative")
    return limit_value, offset_value


def _invite_status(stored: Any, expires_at: Any, now: datetime) -> str:
    status = str(stored or "")
    if status == "issued":
        expiry = _parse(expires_at)
        if expiry is None or now >= expiry:
            return "expired"
    return status


def _public_invite(row: Mapping[str, Any], now: datetime | None = None) -> dict:
    now = now or _now()
    return {
        "invite_id": row["invite_id"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "status": _invite_status(row["status"], row["expires_at"], now),
        "claimed_by": row["claimed_by"],
        "claimed_at": row["claimed_at"],
        "revoked_at": row["revoked_at"],
    }


def _application_receipt(conn: sqlite3.Connection, application_id: str) -> dict:
    row = _fetch_one(
        conn,
        "SELECT a.application_id, a.user_id, u.username, u.avatar_preset, "
        "a.status, a.submitted_at, a.reviewed_by, a.reviewed_at, a.rejection_reason "
        "FROM account_applications a JOIN users u ON u.user_id=a.user_id "
        "WHERE a.application_id=?",
        (application_id,),
    )
    if row is None:
        raise AccountInviteError("application_not_found")
    return {field: row[field] for field in _APPLICATION_FIELDS}


def _safe_metadata(metadata: Any) -> dict | None:
    """Keep a small allowlist of structured, non-identifying audit fields."""
    if metadata is None:
        return None
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            continue
        normalized = key.strip().lower()
        if not _AUDIT_KEY_RE.fullmatch(normalized):
            continue
        if set(normalized.split("_")) & _AUDIT_SENSITIVE_TOKENS:
            continue
        if isinstance(value, bool):
            safe[normalized] = value
        elif isinstance(value, int):
            safe[normalized] = value
        elif isinstance(value, str):
            text = value.strip()
            if text and len(text) <= _AUDIT_MAX_VALUE and "\n" not in text and "\r" not in text:
                safe[normalized] = text
        # Floats, bytes, containers and ``None`` are intentionally dropped.
    return safe or None


def record_security_event(conn: sqlite3.Connection, *, event_type: str,
                          actor_user_id: int | None = None,
                          target_user_id: int | None = None,
                          source_hash: str | None = None,
                          metadata: Mapping[str, Any] | None = None) -> None:
    """Append one minimal structured event inside the caller's transaction."""
    event_type = str(event_type or "").strip()
    if not event_type:
        raise ValueError("event_type is required")
    if len(event_type) > 80:
        raise ValueError("event_type is too long")
    payload = _safe_metadata(metadata)
    conn.execute(
        "INSERT INTO account_security_events("
        "actor_user_id,target_user_id,event_type,occurred_at,source_hash,metadata_safe_json) "
        "VALUES(?,?,?,?,?,?)",
        (None if actor_user_id is None else int(actor_user_id),
         None if target_user_id is None else int(target_user_id),
         event_type, _iso(_now()),
         None if source_hash is None else str(source_hash),
         json.dumps(payload, sort_keys=True, separators=(",", ":")) if payload else None),
    )


def issue_invite(conn: sqlite3.Connection, *, actor_user_id: int | None,
                 expires_in_days: int = 7, source_hash: str | None = None,
                 fixed_code: str | None = None) -> dict:
    """Create a one-use invitation, optionally reusing a configured short code."""
    if isinstance(expires_in_days, bool) or not isinstance(expires_in_days, int):
        raise ValueError("expires_in_days must be an integer from 1 to 30")
    if not 1 <= expires_in_days <= 30:
        raise ValueError("expires_in_days must be an integer from 1 to 30")
    now = _now()
    created_at = _iso(now)
    expires_at = _iso(now + timedelta(days=expires_in_days))
    if fixed_code is not None and not FIXED_INVITE_CODE_RE.fullmatch(fixed_code):
        raise ValueError("The configured fixed invitation must contain six uppercase letters")
    code = secrets.token_urlsafe(24) if fixed_code is None else fixed_code
    digest = _hash_code(code)
    if fixed_code is not None:
        previous = _fetch_one(conn,
            "SELECT invite_id, created_at, expires_at, status FROM account_invites WHERE code_hash=?",
            (digest,))
        if previous and _invite_status(previous["status"], previous["expires_at"], now) == "issued":
            return {**previous, "code": code, "fixed": True, "reused": True}
        if previous:
            # Preserve the expired or spent invitation and its links. Only the
            # new invitation can be resolved by the supplied code's digest.
            retired_digest = _hash_code(f"retired-invite:{previous['invite_id']}:{digest}")
            conn.execute("UPDATE account_invites SET code_hash=? WHERE invite_id=?",
                         (retired_digest, previous["invite_id"]))
    invite_id = secrets.token_urlsafe(18)
    conn.execute(
        "INSERT INTO account_invites("
        "invite_id,code_hash,created_by,created_at,expires_at,status) "
        "VALUES(?,?,?,?,?,'issued')",
        (invite_id, digest,
         None if actor_user_id is None else int(actor_user_id),
         created_at, expires_at),
    )
    record_security_event(
        conn, event_type="invite_issued",
        actor_user_id=actor_user_id, source_hash=source_hash,
        metadata={"invite_id": invite_id, "expires_at": expires_at},
    )
    receipt = {"invite_id": invite_id, "code": code, "status": "issued",
               "created_at": created_at, "expires_at": expires_at}
    if fixed_code is not None:
        receipt["fixed"] = True
    return receipt


def list_invites(conn: sqlite3.Connection, *, limit: int = 50, offset: int = 0) -> list[dict]:
    """List public invite rows without the digest or the raw code."""
    limit, offset = _page(limit, offset)
    rows = _fetch_all(
        conn,
        "SELECT invite_id,created_by,created_at,expires_at,status,"
        "claimed_by,claimed_at,revoked_at FROM account_invites "
        "ORDER BY created_at DESC, invite_id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    now = _now()
    return [_public_invite(row, now) for row in rows]


def revoke_invite(conn: sqlite3.Connection, invite_id: str, *,
                  actor_user_id: int | None, source_hash: str | None = None) -> dict:
    """Revoke an issued invite; repeated revoke returns the same public row."""
    row = _fetch_one(
        conn,
        "SELECT invite_id,created_by,created_at,expires_at,status,"
        "claimed_by,claimed_at,revoked_at FROM account_invites WHERE invite_id=?",
        (str(invite_id or ""),),
    )
    if row is None:
        raise AccountInviteError("invite_not_found")
    now = _now()
    status = _invite_status(row["status"], row["expires_at"], now)
    if status == "revoked":
        return _public_invite(row, now)
    if status != "issued" or row["claimed_by"] is not None:
        raise AccountInviteError("invite_conflict")
    revoked_at = _iso(now)
    cursor = conn.execute(
        "UPDATE account_invites SET status='revoked',revoked_at=? "
        "WHERE invite_id=? AND status='issued' AND claimed_by IS NULL",
        (revoked_at, row["invite_id"]),
    )
    if cursor.rowcount != 1:
        current = _fetch_one(
            conn,
            "SELECT invite_id,created_by,created_at,expires_at,status,"
            "claimed_by,claimed_at,revoked_at FROM account_invites WHERE invite_id=?",
            (row["invite_id"],),
        )
        if current is not None and current["status"] == "revoked":
            return _public_invite(current, now)
        raise AccountInviteError("invite_conflict")
    row["status"] = "revoked"
    row["revoked_at"] = revoked_at
    record_security_event(
        conn, event_type="invite_revoked", actor_user_id=actor_user_id,
        source_hash=source_hash, metadata={"invite_id": row["invite_id"]},
    )
    return _public_invite(row, now)


def submit_application(conn: sqlite3.Connection, *, username: str, salt: bytes,
                       password_hash: bytes, avatar_preset: str, invite_code: str,
                       source_hash: str | None = None) -> dict:
    """Atomically consume one invite and create an inactive pending account."""
    if not isinstance(invite_code, str) or not INVITE_CODE_RE.fullmatch(invite_code):
        raise AccountInviteError("invalid_invite")
    if not isinstance(username, str) or not username.strip():
        raise ValueError("username is required")
    username = username.strip()
    if not isinstance(salt, (bytes, bytearray, memoryview)):
        raise ValueError("salt must be bytes")
    if not isinstance(password_hash, (bytes, bytearray, memoryview)):
        raise ValueError("password_hash must be bytes")
    avatar_preset = str(avatar_preset or "01").strip() or "01"

    now = _now()
    stamp = _iso(now)
    invite = _fetch_one(
        conn,
        "SELECT invite_id,status,expires_at,claimed_by,revoked_at "
        "FROM account_invites WHERE code_hash=?",
        (_hash_code(invite_code),),
    )
    expiry = _parse(invite["expires_at"]) if invite else None
    if (invite is None or invite["status"] != "issued"
            or invite["claimed_by"] is not None or invite["revoked_at"] is not None
            or expiry is None or now >= expiry):
        raise AccountInviteError("invalid_invite")

    if _fetch_one(conn, "SELECT 1 AS present FROM users WHERE username=?", (username,)):
        raise AccountInviteError("username_unavailable")

    try:
        cursor = conn.execute(
            "INSERT INTO users("
            "username,salt,password_hash,role,active,daily_limit,created_at,"
            "approval_status,avatar_preset) "
            "VALUES(?,?,?,'user',0,30,?,'pending',?)",
            (username, bytes(salt), bytes(password_hash), stamp, avatar_preset),
        )
    except sqlite3.IntegrityError as exc:
        if getattr(exc, "sqlite_errorname", "") == "SQLITE_CONSTRAINT_UNIQUE":
            raise AccountInviteError("username_unavailable") from exc
        raise
    user_id = int(cursor.lastrowid)

    claimed = conn.execute(
        "UPDATE account_invites SET status='claimed',claimed_by=?,claimed_at=? "
        "WHERE invite_id=? AND status='issued' AND claimed_by IS NULL",
        (user_id, stamp, invite["invite_id"]),
    )
    if claimed.rowcount != 1:
        raise AccountInviteError("invalid_invite")

    application_id = secrets.token_urlsafe(18)
    try:
        conn.execute(
            "INSERT INTO account_applications("
            "application_id,user_id,invite_id,status,submitted_at) "
            "VALUES(?,?,?,'pending',?)",
            (application_id, user_id, invite["invite_id"], stamp),
        )
    except sqlite3.IntegrityError as exc:
        if getattr(exc, "sqlite_errorname", "") == "SQLITE_CONSTRAINT_UNIQUE":
            raise AccountInviteError("invalid_invite") from exc
        raise

    record_security_event(
        conn, event_type="application_submitted", target_user_id=user_id,
        source_hash=source_hash,
        metadata={"application_id": application_id, "invite_id": invite["invite_id"]},
    )
    return {"status": "pending", "application_id": application_id}


def list_applications(conn: sqlite3.Connection, *, status: str,
                      limit: int = 50, offset: int = 0) -> list[dict]:
    """List review receipts for one status without password material."""
    if status not in _APPLICATION_STATUSES:
        raise ValueError("status must be pending, approved or rejected")
    limit, offset = _page(limit, offset)
    rows = _fetch_all(
        conn,
        "SELECT a.application_id,a.user_id,u.username,u.avatar_preset,a.status,"
        "a.submitted_at,a.reviewed_by,a.reviewed_at,a.rejection_reason "
        "FROM account_applications a JOIN users u ON u.user_id=a.user_id "
        "WHERE a.status=? "
        "ORDER BY a.submitted_at DESC, a.application_id DESC LIMIT ? OFFSET ?",
        (status, limit, offset),
    )
    return [{field: row[field] for field in _APPLICATION_FIELDS} for row in rows]


def review_application(conn: sqlite3.Connection, application_id: str, *,
                       actor_user_id: int | None, decision: str,
                       rejection_reason: str | None = None,
                       source_hash: str | None = None) -> dict:
    """Approve or reject a pending application exactly once."""
    if decision not in _DECISIONS:
        raise ValueError("decision must be approved or rejected")
    if rejection_reason is not None:
        if not isinstance(rejection_reason, str):
            raise ValueError("rejection_reason must be text")
        rejection_reason = rejection_reason.strip() or None
        if rejection_reason is not None and len(rejection_reason) > _MAX_REJECTION_REASON:
            raise ValueError("rejection_reason must be at most 300 characters")
    if decision == "approved":
        rejection_reason = None

    application_id = str(application_id or "")
    application = _fetch_one(
        conn,
        "SELECT application_id,user_id,status FROM account_applications "
        "WHERE application_id=?",
        (application_id,),
    )
    if application is None:
        raise AccountInviteError("application_not_found")
    if application["status"] != "pending":
        if application["status"] == decision:
            return _application_receipt(conn, application_id)
        raise AccountInviteError("approval_conflict")

    now = _now()
    stamp = _iso(now)
    actor = None if actor_user_id is None else int(actor_user_id)
    user_id = int(application["user_id"])

    if decision == "approved":
        updated_user = conn.execute(
            "UPDATE users SET approval_status='approved',active=1,"
            "approved_at=?,approved_by=? "
            "WHERE user_id=? AND approval_status='pending'",
            (stamp, actor, user_id),
        )
        if updated_user.rowcount != 1:
            raise AccountInviteError("approval_conflict")
    else:
        conn.execute(
            "UPDATE users SET approval_status='rejected',active=0 WHERE user_id=?",
            (user_id,),
        )
        # A rejected application must not retain any browser session.
        conn.execute("DELETE FROM browser_sessions WHERE user_id=?", (user_id,))

    updated_application = conn.execute(
        "UPDATE account_applications SET status=?,reviewed_by=?,reviewed_at=?,"
        "rejection_reason=? WHERE application_id=? AND status='pending'",
        (decision, actor, stamp, rejection_reason, application_id),
    )
    if updated_application.rowcount != 1:
        current = _fetch_one(
            conn,
            "SELECT status FROM account_applications WHERE application_id=?",
            (application_id,),
        )
        if current is not None and current["status"] == decision:
            return _application_receipt(conn, application_id)
        raise AccountInviteError("approval_conflict")

    record_security_event(
        conn,
        event_type="application_approved" if decision == "approved" else "application_rejected",
        actor_user_id=actor_user_id, target_user_id=user_id,
        source_hash=source_hash,
        metadata={"application_id": application_id, "decision": decision},
    )
    return _application_receipt(conn, application_id)

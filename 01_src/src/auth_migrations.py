"""Forward-only account schema migration (A1).

The migration is deliberately additive: it never rebuilds ``users`` and never
changes existing identities, password hashes, sessions, or active flags.  One
marker row records that legacy rows were backfilled as approved, so a later
application-created ``pending`` row is never promoted by a re-run.

The caller supplies a connection outside an explicit transaction.  ``migrate``
opens ``BEGIN IMMEDIATE`` itself, uses only single-statement ``execute`` calls
(never ``executescript``), and rolls the whole unit back on any failure.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

MIGRATION_VERSION = 1

_TABLE_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS auth_schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS account_invites (
        invite_id TEXT PRIMARY KEY,
        code_hash TEXT NOT NULL UNIQUE,
        created_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'issued'
            CHECK (status IN ('issued','claimed','revoked')),
        claimed_by INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
        claimed_at TEXT,
        revoked_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_account_invites_created "
    "ON account_invites(created_at, invite_id)",
    "CREATE INDEX IF NOT EXISTS idx_account_invites_status "
    "ON account_invites(status, expires_at)",
    """CREATE TABLE IF NOT EXISTS account_applications (
        application_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
        invite_id TEXT UNIQUE REFERENCES account_invites(invite_id) ON DELETE SET NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','approved','rejected')),
        submitted_at TEXT NOT NULL,
        reviewed_by INTEGER,
        reviewed_at TEXT,
        rejection_reason TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_account_applications_status "
    "ON account_applications(status, submitted_at, application_id)",
    """CREATE TABLE IF NOT EXISTS account_security_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_user_id INTEGER,
        target_user_id INTEGER,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        source_hash TEXT,
        metadata_safe_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_account_security_events_target "
    "ON account_security_events(target_user_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_account_security_events_type "
    "ON account_security_events(event_type, occurred_at)",
    """CREATE TABLE IF NOT EXISTS account_recovery_codes (
        code_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        used_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_account_recovery_codes_user "
    "ON account_recovery_codes(user_id)",
)

_USER_COLUMN_STATEMENTS = (
    ("approval_status",
     "ALTER TABLE users ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'pending' "
     "CHECK (approval_status IN ('pending','approved','rejected'))"),
    ("approved_at", "ALTER TABLE users ADD COLUMN approved_at TEXT"),
    ("approved_by", "ALTER TABLE users ADD COLUMN approved_by INTEGER"),
    ("password_changed_at", "ALTER TABLE users ADD COLUMN password_changed_at TEXT"),
)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(%s)" % table)}


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def migrate(conn: sqlite3.Connection) -> None:
    """Apply the frozen A1 account migration transactionally and idempotently."""
    if conn.in_transaction:
        raise RuntimeError("migrate() requires a connection outside an explicit transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        columns = _column_names(conn, "users")
        approval_was_missing = "approval_status" not in columns

        conn.execute(_TABLE_STATEMENTS[0])
        applied = conn.execute(
            "SELECT 1 FROM auth_schema_migrations WHERE version=?",
            (MIGRATION_VERSION,),
        ).fetchone() is not None

        for statement in _TABLE_STATEMENTS[1:]:
            conn.execute(statement)
        for name, statement in _USER_COLUMN_STATEMENTS:
            if name not in columns:
                conn.execute(statement)
                columns.add(name)

        if not applied:
            # Only a clean legacy table (no approval_status column at all) is
            # backfilled to approved.  If the column already exists without the
            # marker, that state was produced outside this transactional
            # migration: preserve its explicit statuses and never promote a
            # pending row merely because it is active.
            if approval_was_missing:
                conn.execute(
                    "UPDATE users SET approval_status='approved', "
                    "approved_at=COALESCE(approved_at, created_at) "
                    "WHERE approval_status='pending'"
                )
            conn.execute(
                "INSERT INTO auth_schema_migrations(version, applied_at) VALUES(?,?)",
                (MIGRATION_VERSION, _stamp()),
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise

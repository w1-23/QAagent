"""A1 repository regression for invites, review, audit, and migration.

Covers the repository-level acceptance points AUTH-01/02/03/04/05/06/07/12/13/
14/24/25.  HTTP Set-Cookie flags (AUTH-03) and task-route middleware (AUTH-12)
live in ``api/main.py`` and ``core/jobs.py``; this file asserts the data and
session boundaries those layers depend on, plus the existing owner-scoped job
query used by the task routes.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest

from src import auth_invites, auth_migrations
from src.core import account_admission
from src.core.account_admission import AccountUnavailable, AdmissionStore

_APPLICATION_FIELDS = {
    "application_id", "user_id", "username", "avatar_preset", "status",
    "submitted_at", "reviewed_by", "reviewed_at", "rejection_reason",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _future(days: int = 3650) -> str:
    return _stamp(_now() + timedelta(days=days))


def _past(days: int = 1) -> str:
    return _stamp(_now() - timedelta(days=days))


def _connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _create_store_schema(conn: sqlite3.Connection, *, profile: bool = True) -> None:
    """Mirror the base tables auth.init_store creates before migrate()."""
    conn.execute(
        "CREATE TABLE users ("
        "user_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "username TEXT NOT NULL COLLATE NOCASE UNIQUE,"
        "salt BLOB NOT NULL, password_hash BLOB NOT NULL,"
        "role TEXT NOT NULL DEFAULT 'user', active INTEGER NOT NULL DEFAULT 1,"
        "daily_limit INTEGER NOT NULL DEFAULT 30, created_at TEXT NOT NULL)")
    if profile:
        for statement in (
            "ALTER TABLE users ADD COLUMN avatar_preset TEXT NOT NULL DEFAULT '01'",
            "ALTER TABLE users ADD COLUMN avatar_data BLOB",
            "ALTER TABLE users ADD COLUMN avatar_updated_at TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE users ADD COLUMN onboarding_completed INTEGER NOT NULL DEFAULT 0",
        ):
            conn.execute(statement)
    conn.execute(
        "CREATE TABLE browser_sessions ("
        "token_hash TEXT PRIMARY KEY,"
        "user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,"
        "created_at TEXT NOT NULL, expires_at TEXT NOT NULL)")
    conn.execute("CREATE INDEX idx_browser_sessions_user ON browser_sessions(user_id)")
    conn.execute(
        "CREATE TABLE account_deletion_retries ("
        "token_hash TEXT PRIMARY KEY,"
        "user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,"
        "expires_at TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE auth_events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "action TEXT NOT NULL, source_hash TEXT NOT NULL, occurred_at REAL NOT NULL)")
    conn.execute(
        "CREATE TABLE daily_usage (user_id INTEGER NOT NULL REFERENCES users(user_id) "
        "ON DELETE CASCADE, day TEXT NOT NULL, request_count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY(user_id, day))")
    conn.execute(
        "CREATE TABLE workspace_files (user_id INTEGER NOT NULL REFERENCES users(user_id) "
        "ON DELETE CASCADE, session_id TEXT NOT NULL, relative_path TEXT NOT NULL, "
        "created_at TEXT NOT NULL, PRIMARY KEY(session_id, relative_path))")
    conn.execute("CREATE INDEX idx_workspace_files_owner ON workspace_files(user_id, session_id)")


class _Store:
    def __init__(self, conn: sqlite3.Connection, path):
        self.conn = conn
        self.path = str(path)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "accounts.sqlite3"
    conn = _connect(path)
    _create_store_schema(conn)
    account_admission.init_schema(conn)
    auth_migrations.migrate(conn)
    yield _Store(conn, path)
    conn.close()


@contextmanager
def _tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def _run(conn: sqlite3.Connection, func, *args, **kwargs):
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = func(conn, *args, **kwargs)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return result


def _user_columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(users)")]


def _user(conn: sqlite3.Connection, user_id: int) -> dict:
    columns = _user_columns(conn)
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (int(user_id),)).fetchone()
    return dict(zip(columns, row)) if row else {}


def _count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()) -> int:
    sql = "SELECT COUNT(*) FROM " + table + (" WHERE " + where if where else "")
    return int(conn.execute(sql, params).fetchone()[0])


def _audit(conn: sqlite3.Connection, event_type: str | None = None) -> list[tuple]:
    sql = ("SELECT actor_user_id,target_user_id,event_type,occurred_at,source_hash,"
           "metadata_safe_json FROM account_security_events")
    params: tuple = ()
    if event_type is not None:
        sql += " WHERE event_type=?"
        params = (event_type,)
    return conn.execute(sql + " ORDER BY id", params).fetchall()


def _insert_user(conn, username, *, role="user", active=1, approval="approved", preset="01") -> int:
    stamp = _stamp(_now())
    cursor = conn.execute(
        "INSERT INTO users(username,salt,password_hash,role,active,daily_limit,created_at,"
        "approval_status,approved_at,avatar_preset) VALUES(?,?,?,?,?,30,?,?,?,?)",
        (username, b"salt-0123456789", b"hash-" + b"x" * 27, role, active, stamp,
         approval, stamp if approval == "approved" else None, preset))
    return int(cursor.lastrowid)


def _issue(conn, actor=1, days=7) -> dict:
    return _run(conn, auth_invites.issue_invite, actor_user_id=actor, expires_in_days=days)


def _submit(conn, invite_code, username="alice", preset="01") -> dict:
    return _run(
        conn, auth_invites.submit_application, username=username,
        salt=b"0123456789abcdef", password_hash=b"p" * 32,
        avatar_preset=preset, invite_code=invite_code)


def _review(conn, application_id, decision, *, actor=1, reason=None) -> dict:
    return _run(
        conn, auth_invites.review_application, application_id,
        actor_user_id=actor, decision=decision, rejection_reason=reason)


def _application(conn, application_id) -> dict:
    columns = ["application_id", "user_id", "invite_id", "status", "submitted_at",
               "reviewed_by", "reviewed_at", "rejection_reason"]
    row = conn.execute(
        "SELECT application_id,user_id,invite_id,status,submitted_at,reviewed_by,"
        "reviewed_at,rejection_reason FROM account_applications WHERE application_id=?",
        (application_id,)).fetchone()
    return dict(zip(columns, row)) if row else {}


def _invite(conn, invite_id) -> dict:
    columns = ["invite_id", "code_hash", "created_by", "created_at", "expires_at",
               "status", "claimed_by", "claimed_at", "revoked_at"]
    row = conn.execute("SELECT * FROM account_invites WHERE invite_id=?", (invite_id,)).fetchone()
    return dict(zip(columns, row)) if row else {}


# ---------------------------------------------------------------------------
# AUTH-25 / AUTH-05 / AUTH-12: forward migration preserves legacy state.
# ---------------------------------------------------------------------------

def test_migrate_preserves_legacy_identity_hash_active_and_sessions(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    conn = _connect(path)
    try:
        _create_store_schema(conn, profile=False)
        salt = b"0123456789abcdef"
        digest = bytes(range(32))
        created = "2025-01-02T03:04:05+00:00"
        conn.execute(
            "INSERT INTO users(user_id,username,salt,password_hash,role,active,daily_limit,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (7, "Legacy.User", salt, digest, "user", 0, 30, created))
        conn.execute(
            "INSERT INTO users(user_id,username,salt,password_hash,role,active,daily_limit,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)", (1, "Root", salt, digest, "admin", 1, 30, created))
        token_hash = hashlib.sha256(b"legacy-token").hexdigest()
        conn.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                     (token_hash, 7, created, "2999-01-01T00:00:00+00:00"))
        conn.execute(
            "INSERT INTO workspace_files VALUES(?,?,?,?)", (7, "session-7", "out/result.txt", created))
        conn.execute("INSERT INTO daily_usage VALUES(?,?,?)", (7, "2025-01-02", 3))
        conn.execute("INSERT INTO account_deletion_retries VALUES(?,?,?)",
                     (hashlib.sha256(b"retry").hexdigest(), 7, "2999-01-01T00:00:00+00:00"))
        conn.execute("INSERT INTO auth_events(action,source_hash,occurred_at) VALUES(?,?,?)",
                     ("login", "source-hash", 1.0))

        auth_migrations.migrate(conn)

        user = _user(conn, 7)
        assert user["username"] == "Legacy.User"
        assert bytes(user["salt"]) == salt
        assert bytes(user["password_hash"]) == digest
        assert user["role"] == "user"
        assert int(user["active"]) == 0
        assert int(user["daily_limit"]) == 30
        assert user["created_at"] == created
        # Legacy rows are approved regardless of active state; the hash and ID
        # never change and the unexpired session survives.
        assert user["approval_status"] == "approved"
        assert user["approved_at"] == created
        assert user["approved_by"] is None
        assert user["password_changed_at"] is None
        assert _count(conn, "browser_sessions") == 1
        assert _count(conn, "workspace_files", "user_id=?", (7,)) == 1
        assert _count(conn, "daily_usage", "user_id=?", (7,)) == 1
        assert _count(conn, "account_deletion_retries") == 1
        assert _count(conn, "auth_events") == 1
        assert conn.execute("SELECT version FROM auth_schema_migrations").fetchall() == [(1,)]
        applied_at = conn.execute("SELECT applied_at FROM auth_schema_migrations").fetchone()[0]
        assert datetime.fromisoformat(applied_at).tzinfo is not None

        # New rows created after the marker default to pending and are never
        # re-approved by an idempotent second run, even if active.
        conn.execute(
            "INSERT INTO users(username,salt,password_hash,created_at) VALUES(?,?,?,?)",
            ("new.legacy", salt, digest, created))
        new_id = int(conn.execute("SELECT user_id FROM users WHERE username='new.legacy'").fetchone()[0])
        auth_migrations.migrate(conn)
        assert _user(conn, new_id)["approval_status"] == "pending"
        assert _user(conn, new_id)["approved_at"] is None
        assert _user(conn, 7)["approved_at"] == created
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_migrate_preserves_explicit_status_when_marker_is_absent(tmp_path):
    conn = _connect(tmp_path / "partial.sqlite3")
    try:
        _create_store_schema(conn, profile=False)
        # Simulate an externally added column without this migration's marker.
        conn.execute(
            "ALTER TABLE users ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'pending' "
            "CHECK (approval_status IN ('pending','approved','rejected'))")
        stamp = _stamp(_now())
        conn.execute(
            "INSERT INTO users(username,salt,password_hash,role,active,daily_limit,created_at,approval_status) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("explicit.pending", b"s" * 16, b"p" * 32, "user", 1, 30, stamp, "pending"))
        conn.execute(
            "INSERT INTO users(username,salt,password_hash,role,active,daily_limit,created_at,approval_status) "
            "VALUES(?,?,?,?,?,?,?,?)",
            ("explicit.approved", b"s" * 16, b"p" * 32, "admin", 1, 30, stamp, "approved"))

        auth_migrations.migrate(conn)

        assert _user(conn, 1)["approval_status"] == "pending"
        assert _user(conn, 1)["approved_at"] is None
        assert _user(conn, 2)["approval_status"] == "approved"
        assert conn.execute("SELECT version FROM auth_schema_migrations").fetchall() == [(1,)]
    finally:
        conn.close()


def test_migrate_rolls_back_ddl_and_marker_atomically(tmp_path):
    conn = _connect(tmp_path / "rollback.sqlite3")
    try:
        _create_store_schema(conn, profile=False)
        # A conflicting marker table makes the version-1 insert fail after the
        # feature tables and columns were created.
        conn.execute(
            "CREATE TABLE auth_schema_migrations("
            "version INTEGER PRIMARY KEY CHECK(version=999), applied_at TEXT)")
        with pytest.raises(sqlite3.IntegrityError):
            auth_migrations.migrate(conn)
        assert "approval_status" not in _user_columns(conn)
        assert _count(conn, "sqlite_master", "type='table' AND name='account_invites'") == 0
        assert _count(conn, "sqlite_master", "type='table' AND name='account_applications'") == 0
        assert conn.in_transaction is False
    finally:
        conn.close()


def test_migrate_rejects_a_caller_owned_transaction(tmp_path):
    conn = _connect(tmp_path / "busy.sqlite3")
    try:
        _create_store_schema(conn, profile=False)
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError):
            auth_migrations.migrate(conn)
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Invite issuance, listing, expiry, and revocation.
# ---------------------------------------------------------------------------

def test_issue_invite_returns_high_entropy_code_and_stores_only_digest(db):
    _insert_user(db.conn, "admin", role="admin")
    issued = _issue(db.conn, actor=1)

    assert auth_invites.INVITE_CODE_RE.fullmatch(issued["code"])
    assert len(issued["code"]) == 32
    assert issued["status"] == "issued"
    assert issued["invite_id"]
    created = datetime.fromisoformat(issued["created_at"])
    expires = datetime.fromisoformat(issued["expires_at"])
    assert created.tzinfo is not None and expires.tzinfo is not None
    assert timedelta(days=6, hours=23) < expires - created < timedelta(days=7, minutes=1)

    stored = _invite(db.conn, issued["invite_id"])
    assert stored["code_hash"] == hashlib.sha256(issued["code"].encode()).hexdigest()
    assert "code" not in stored
    listed = auth_invites.list_invites(db.conn, limit=10)
    assert len(listed) == 1
    assert set(listed[0]) == {
        "invite_id", "created_by", "created_at", "expires_at", "status",
        "claimed_by", "claimed_at", "revoked_at"}
    events = _audit(db.conn, "invite_issued")
    assert len(events) == 1
    metadata = json.loads(events[0][5])
    assert metadata["invite_id"] == issued["invite_id"]
    assert issued["code"] not in json.dumps(metadata)


def test_locally_seeded_six_letter_invite_is_single_use(db):
    _insert_user(db.conn, "admin", role="admin")
    issued = _issue(db.conn)
    short_code = "ABCDEF"
    db.conn.execute("UPDATE account_invites SET code_hash=? WHERE invite_id=?",
                    (hashlib.sha256(short_code.encode()).hexdigest(), issued["invite_id"]))

    assert _submit(db.conn, short_code)["status"] == "pending"
    with pytest.raises(auth_invites.AccountInviteError) as second:
        _submit(db.conn, short_code, username="second-user")
    assert second.value.code == "invalid_invite"


@pytest.mark.parametrize("days", [1, 30])
def test_issue_invite_accepts_whole_day_bounds(db, days):
    _insert_user(db.conn, "admin", role="admin")
    issued = _issue(db.conn, days=days)
    delta = datetime.fromisoformat(issued["expires_at"]) - datetime.fromisoformat(issued["created_at"])
    assert timedelta(days=days, minutes=-1) < delta < timedelta(days=days, minutes=1)


@pytest.mark.parametrize("days", [0, 31, -1, 7.5, "7", True, None])
def test_issue_invite_rejects_out_of_range_or_non_integer(db, days):
    _insert_user(db.conn, "admin", role="admin")
    with pytest.raises(ValueError):
        _issue(db.conn, days=days)
    assert _count(db.conn, "account_invites") == 0


def test_invite_lifecycle_derives_expiry_and_revoke_is_idempotent(db):
    _insert_user(db.conn, "admin", role="admin")
    active = _issue(db.conn)
    expired = _issue(db.conn)
    db.conn.execute("UPDATE account_invites SET expires_at=? WHERE invite_id=?",
                    (_past(), expired["invite_id"]))

    listed = {row["invite_id"]: row for row in auth_invites.list_invites(db.conn, limit=10)}
    assert listed[active["invite_id"]]["status"] == "issued"
    assert listed[expired["invite_id"]]["status"] == "expired"

    with pytest.raises(auth_invites.AccountInviteError) as expired_error:
        _run(db.conn, auth_invites.revoke_invite, expired["invite_id"], actor_user_id=1)
    assert expired_error.value.code == "invite_conflict"

    revoked = _run(db.conn, auth_invites.revoke_invite, active["invite_id"], actor_user_id=1)
    assert revoked["status"] == "revoked" and revoked["revoked_at"]
    again = _run(db.conn, auth_invites.revoke_invite, active["invite_id"], actor_user_id=1)
    assert again == revoked
    assert len(_audit(db.conn, "invite_revoked")) == 1

    with pytest.raises(auth_invites.AccountInviteError) as missing:
        _run(db.conn, auth_invites.revoke_invite, "missing", actor_user_id=1)
    assert missing.value.code == "invite_not_found"

    used = _issue(db.conn)
    _submit(db.conn, used["code"], username="used-user")
    with pytest.raises(auth_invites.AccountInviteError) as claimed:
        _run(db.conn, auth_invites.revoke_invite, used["invite_id"], actor_user_id=1)
    assert claimed.value.code == "invite_conflict"

    with pytest.raises(auth_invites.AccountInviteError) as consume_expired:
        _submit(db.conn, expired["code"], username="late-user")
    assert consume_expired.value.code == "invalid_invite"


def test_list_invites_paginates_without_secrets(db):
    _insert_user(db.conn, "admin", role="admin")
    first = _issue(db.conn)
    second = _issue(db.conn)
    page_one = auth_invites.list_invites(db.conn, limit=1, offset=0)
    page_two = auth_invites.list_invites(db.conn, limit=1, offset=1)
    assert len(page_one) == len(page_two) == 1
    assert page_one[0]["invite_id"] != page_two[0]["invite_id"]
    assert {page_one[0]["invite_id"], page_two[0]["invite_id"]} == {
        first["invite_id"], second["invite_id"]}
    for row in page_one + page_two:
        assert "code" not in row and "code_hash" not in row
    for bad in (0, 101):
        with pytest.raises(ValueError):
            auth_invites.list_invites(db.conn, limit=bad)
    with pytest.raises(ValueError):
        auth_invites.list_invites(db.conn, offset=-1)


# ---------------------------------------------------------------------------
# Atomic pending application creation and one-time invite consumption.
# ---------------------------------------------------------------------------

def test_submit_application_is_atomic_pending_without_session_or_login(db):
    _insert_user(db.conn, "admin", role="admin")
    issued = _issue(db.conn)

    result = _submit(db.conn, issued["code"], username="alice", preset="03")
    assert result["status"] == "pending" and result["application_id"]

    user = _user(db.conn, int(_application(db.conn, result["application_id"])["user_id"]))
    assert user["username"] == "alice"
    assert user["role"] == "user"
    assert int(user["active"]) == 0
    assert user["approval_status"] == "pending"
    assert user["approved_at"] is None
    assert user["avatar_preset"] == "03"
    assert bytes(user["salt"]) == b"0123456789abcdef"
    assert bytes(user["password_hash"]) == b"p" * 32
    # A1 never issues a browser session for a pending application.
    assert _count(db.conn, "browser_sessions", "user_id=?", (user["user_id"],)) == 0

    invite_row = _invite(db.conn, issued["invite_id"])
    assert invite_row["status"] == "claimed"
    assert int(invite_row["claimed_by"]) == int(user["user_id"])
    assert invite_row["claimed_at"]
    application = _application(db.conn, result["application_id"])
    assert application["status"] == "pending"
    assert int(application["user_id"]) == int(user["user_id"])
    assert application["invite_id"] == issued["invite_id"]

    events = _audit(db.conn, "application_submitted")
    assert len(events) == 1
    assert events[0][1] == int(user["user_id"])
    assert json.loads(events[0][5]) == {
        "application_id": result["application_id"], "invite_id": issued["invite_id"]}

    # The same invite cannot create a second pending account.
    with pytest.raises(auth_invites.AccountInviteError) as reused:
        _submit(db.conn, issued["code"], username="mallory")
    assert reused.value.code == "invalid_invite"
    assert _count(db.conn, "users", "username='mallory'") == 0
    assert _count(db.conn, "account_applications") == 1


def test_submit_application_duplicate_username_rolls_back_invite(db):
    _insert_user(db.conn, "admin", role="admin")
    _insert_user(db.conn, "bob")
    issued = _issue(db.conn)

    with pytest.raises(auth_invites.AccountInviteError) as error:
        _submit(db.conn, issued["code"], username="BOB")
    assert error.value.code == "username_unavailable"
    # The failed attempt must not consume the invite or create anything.
    assert _invite(db.conn, issued["invite_id"])["status"] == "issued"
    assert _invite(db.conn, issued["invite_id"])["claimed_by"] is None
    assert _count(db.conn, "account_applications") == 0
    assert _count(db.conn, "account_security_events", "event_type='application_submitted'") == 0


def test_submit_application_invalid_invites_share_one_error(db):
    _insert_user(db.conn, "admin", role="admin")
    # A syntactically valid code that was never issued.
    unknown = secrets.token_urlsafe(24)
    revoked = _issue(db.conn)
    _run(db.conn, auth_invites.revoke_invite, revoked["invite_id"], actor_user_id=1)
    expired = _issue(db.conn)
    db.conn.execute("UPDATE account_invites SET expires_at=? WHERE invite_id=?",
                    (_past(), expired["invite_id"]))
    used = _issue(db.conn)
    _submit(db.conn, used["code"], username="first-claim")

    cases = [
        ("", "u-empty"), (None, "u-none"), ("too-short", "u-short"),
        (unknown, "u-unknown"), (revoked["code"], "u-revoked"),
        (expired["code"], "u-expired"), (used["code"], "u-used"),
    ]
    for index, (code, username) in enumerate(cases):
        with pytest.raises(auth_invites.AccountInviteError) as error:
            _submit(db.conn, code, username=username)
        assert error.value.code == "invalid_invite", index
    # Only the single successful claim produced an account.
    assert _count(db.conn, "users", "username LIKE 'u-%'") == 0
    assert _count(db.conn, "account_applications") == 1


# ---------------------------------------------------------------------------
# Idempotent review with one audit row per real transition.
# ---------------------------------------------------------------------------

def test_review_approve_is_idempotent_and_enables_admission(db):
    _insert_user(db.conn, "admin", role="admin")
    issued = _issue(db.conn)
    result = _submit(db.conn, issued["code"], username="alice")
    application_id = result["application_id"]
    user_id = int(_application(db.conn, application_id)["user_id"])

    store = AdmissionStore(db.path)
    with pytest.raises(AccountUnavailable):
        store.acquire(user_id)

    receipt = _review(db.conn, application_id, "approved", actor=1)
    assert set(receipt) == _APPLICATION_FIELDS
    assert receipt["status"] == "approved"
    assert int(receipt["reviewed_by"]) == 1 and receipt["reviewed_at"]
    assert receipt["rejection_reason"] is None

    user = _user(db.conn, user_id)
    assert user["approval_status"] == "approved"
    assert int(user["active"]) == 1
    assert int(user["approved_by"]) == 1
    assert user["approved_at"]

    events = _audit(db.conn, "application_approved")
    assert len(events) == 1 and events[0][0] == 1 and events[0][1] == user_id
    assert json.loads(events[0][5]) == {
        "application_id": application_id, "decision": "approved"}

    # Same decision retries return the same receipt without a second audit.
    assert _review(db.conn, application_id, "approved", actor=1) == receipt
    assert len(_audit(db.conn, "application_approved")) == 1
    with pytest.raises(auth_invites.AccountInviteError) as conflict:
        _review(db.conn, application_id, "rejected", actor=1)
    assert conflict.value.code == "approval_conflict"

    ticket = store.acquire(user_id)
    assert ticket.epoch >= 1
    store.release(ticket)


def test_review_reject_is_idempotent_and_keeps_account_inactive(db):
    _insert_user(db.conn, "admin", role="admin")
    issued = _issue(db.conn)
    result = _submit(db.conn, issued["code"], username="rejected-user")
    application_id = result["application_id"]
    user_id = int(_application(db.conn, application_id)["user_id"])
    # Give the pending account a session row to prove rejection revokes it.
    db.conn.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                    (hashlib.sha256(b"pending").hexdigest(), user_id, _stamp(_now()), _future()))

    reason = "x'); DROP TABLE users;--"
    receipt = _review(db.conn, application_id, "rejected", actor=2, reason=reason)
    assert receipt["status"] == "rejected"
    assert receipt["rejection_reason"] == reason
    assert int(receipt["reviewed_by"]) == 2
    user = _user(db.conn, user_id)
    assert user["approval_status"] == "rejected"
    assert int(user["active"]) == 0
    assert _count(db.conn, "browser_sessions", "user_id=?", (user_id,)) == 0
    assert _count(db.conn, "users") == 2  # injection text did not execute
    assert len(_audit(db.conn, "application_rejected")) == 1

    assert _review(db.conn, application_id, "rejected", actor=2, reason=reason) == receipt
    assert len(_audit(db.conn, "application_rejected")) == 1
    with pytest.raises(auth_invites.AccountInviteError) as conflict:
        _review(db.conn, application_id, "approved", actor=1)
    assert conflict.value.code == "approval_conflict"

    rejected_rows = auth_invites.list_applications(db.conn, status="rejected")
    assert [row["application_id"] for row in rejected_rows] == [application_id]
    assert set(rejected_rows[0]) == _APPLICATION_FIELDS

    with pytest.raises(ValueError):
        _review(db.conn, application_id, "maybe", actor=1)
    with pytest.raises(ValueError):
        _review(db.conn, application_id, "rejected", actor=1, reason="x" * 301)


def test_review_unknown_application_raises_not_found(db):
    _insert_user(db.conn, "admin", role="admin")
    with pytest.raises(auth_invites.AccountInviteError) as error:
        _review(db.conn, "missing", "approved", actor=1)
    assert error.value.code == "application_not_found"


def test_list_applications_filters_and_paginates(db):
    _insert_user(db.conn, "admin", role="admin")
    application_ids = []
    for index, name in enumerate(("alpha", "bravo", "charlie")):
        issued = _issue(db.conn)
        application_ids.append(_submit(db.conn, issued["code"], username=name)["application_id"])
    _review(db.conn, application_ids[0], "approved", actor=1)
    _review(db.conn, application_ids[1], "rejected", actor=1, reason="no")

    pending = auth_invites.list_applications(db.conn, status="pending")
    assert [row["application_id"] for row in pending] == [application_ids[2]]
    assert set(pending[0]) == _APPLICATION_FIELDS
    for row in pending:
        assert not ({"salt", "password_hash", "code_hash", "code"} & set(row))

    page_one = auth_invites.list_applications(db.conn, status="approved", limit=1, offset=0)
    page_two = auth_invites.list_applications(db.conn, status="approved", limit=1, offset=1)
    assert len(page_one) == 1 and page_two == []

    with pytest.raises(ValueError):
        auth_invites.list_applications(db.conn, status="unknown")
    with pytest.raises(ValueError):
        auth_invites.list_applications(db.conn, status="pending", limit=0)


# ---------------------------------------------------------------------------
# Minimal audit records.
# ---------------------------------------------------------------------------

def test_record_security_event_is_minimal_sanitized_and_transactional(db):
    with _tx(db.conn):
        auth_invites.record_security_event(
            db.conn, event_type="invite_issued", actor_user_id=1, target_user_id=2,
            source_hash="abc123",
            metadata={"invite_id": "iv", "decision": "approved", "description": "ok",
                      "expires_at": "2030-01-01T00:00:00+00:00",
                      "password": "hunter2", "raw_ip": "1.2.3.4",
                      "request_body": "{...}", "recovery_code": "r_x"})
    rows = _audit(db.conn)
    assert len(rows) == 1
    actor, target, event_type, occurred_at, source_hash, metadata_json = rows[0]
    assert (actor, target, event_type, source_hash) == (1, 2, "invite_issued", "abc123")
    assert datetime.fromisoformat(occurred_at).tzinfo is not None
    metadata = json.loads(metadata_json)
    assert metadata == {"invite_id": "iv", "decision": "approved", "description": "ok",
                        "expires_at": "2030-01-01T00:00:00+00:00"}
    assert "hunter2" not in metadata_json and "1.2.3.4" not in metadata_json

    # Rollback removes the event with the rest of the transaction.
    with pytest.raises(RuntimeError):
        with _tx(db.conn):
            auth_invites.record_security_event(db.conn, event_type="invite_revoked")
            raise RuntimeError("boom")
    assert len(_audit(db.conn)) == 1

    with _tx(db.conn):
        auth_invites.record_security_event(db.conn, event_type="clean")
    assert _audit(db.conn)[-1][5] is None
    with pytest.raises(ValueError):
        with _tx(db.conn):
            auth_invites.record_security_event(db.conn, event_type="  ")


# ---------------------------------------------------------------------------
# AUTH-24: account deletion cascades application data but keeps invite facts.
# ---------------------------------------------------------------------------

def test_account_delete_cascades_application_and_codes_and_nulls_claims(db):
    _insert_user(db.conn, "admin", role="admin")
    issued = _issue(db.conn)
    result = _submit(db.conn, issued["code"], username="alice")
    application_id = result["application_id"]
    user_id = int(_application(db.conn, application_id)["user_id"])
    _review(db.conn, application_id, "approved", actor=1)
    db.conn.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                    (hashlib.sha256(b"t").hexdigest(), user_id, _stamp(_now()), _future()))
    db.conn.executemany(
        "INSERT INTO account_recovery_codes(code_hash,user_id,created_at) VALUES(?,?,?)",
        [(hashlib.sha256(code.encode()).hexdigest(), user_id, _stamp(_now()))
         for code in ("r_one", "r_two")])

    db.conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))

    assert _count(db.conn, "account_applications", "user_id=?", (user_id,)) == 0
    assert _count(db.conn, "account_recovery_codes", "user_id=?", (user_id,)) == 0
    assert _count(db.conn, "browser_sessions", "user_id=?", (user_id,)) == 0
    invite_row = _invite(db.conn, issued["invite_id"])
    # The consumed fact survives; the associated account reference is cleared.
    assert invite_row["status"] == "claimed"
    assert invite_row["claimed_by"] is None
    assert _count(db.conn, "account_security_events", "target_user_id=?", (user_id,)) >= 1


# ---------------------------------------------------------------------------
# AUTH-01/02/03/04/05/06/07/13/14/24 facade boundaries.
# ---------------------------------------------------------------------------

@pytest.fixture
def auth(tmp_path, monkeypatch):
    import src.auth as auth_module
    monkeypatch.setattr(auth_module, "DB_PATH", tmp_path / "accounts.sqlite3")
    auth_module.init_store()
    return auth_module


def _admin(auth_module):
    user, token, error = auth_module.create_first_admin("AdminUser", "longpassword1")
    assert error is None and token
    return user, token


def _db_rows(db_path, sql, params=()):
    conn = _connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_auth_01_02_03_register_creates_pending_without_session_or_login(auth):
    admin_user, _ = _admin(auth)
    invite = auth.issue_invite(actor_user_id=admin_user["user_id"])
    application = auth.register("alice", "longpassword1", "03", invite_code=invite["code"])

    # AUTH-01/02: registration no longer creates a session or auto-login.
    assert set(application) == {"status", "application_id"}
    assert application["status"] == "pending"
    assert auth.login("alice", "longpassword1") == (None, None)
    # AUTH-03: no token/cookie material leaves the repository layer here.
    assert not ({"token", "cookie", "set-cookie"} & set(application))

    user_row = _db_rows(
        auth.DB_PATH,
        "SELECT user_id,approval_status,active,role,avatar_preset "
        "FROM users WHERE username='alice'")[0]
    assert tuple(user_row[1:]) == ("pending", 0, "user", "03")
    assert _db_rows(auth.DB_PATH, "SELECT COUNT(*) FROM browser_sessions WHERE user_id=?",
                    (user_row[0],))[0][0] == 0
    pending = auth.list_applications(status="pending")
    assert [row["application_id"] for row in pending] == [application["application_id"]]


def test_auth_04_05_06_07_login_token_logout_and_admin_gate(auth):
    admin_user, admin_token = _admin(auth)
    invite = auth.issue_invite(actor_user_id=admin_user["user_id"])
    application = auth.register("alice", "longpassword1", invite_code=invite["code"])
    receipt = auth.review_application(
        application["application_id"], actor_user_id=admin_user["user_id"],
        actor_token=admin_token, decision="approved")
    assert receipt["status"] == "approved"

    # AUTH-04: wrong and unknown passwords fail uniformly; correct one logs in.
    assert auth.login("alice", "wrongpassword") == (None, None)
    assert auth.login("nobody", "longpassword1") == (None, None)
    public, token = auth.login("alice", "longpassword1")
    assert public["user_id"] == receipt["user_id"] and token
    assert auth.user_for_token(token)["user_id"] == public["user_id"]

    # AUTH-07: only approved active administrators pass require_admin.
    assert auth.require_admin(public) is False
    assert auth.require_admin(admin_user) is True
    assert auth.require_admin({**admin_user, "active": False}) is False

    # AUTH-06: logout removes the token.
    auth.logout(token)
    assert auth.user_for_token(token) is None

    # AUTH-05: expired sessions and inactive accounts fail token lookup.
    _, expiring = auth.login("alice", "longpassword1")
    digest = hashlib.sha256(expiring.encode()).hexdigest()
    conn = _connect(auth.DB_PATH)
    try:
        conn.execute("UPDATE browser_sessions SET expires_at=? WHERE token_hash=?",
                     (_past(), digest))
    finally:
        conn.close()
    assert auth.user_for_token(expiring) is None

    _, live = auth.login("alice", "longpassword1")
    assert auth.set_user_status(
        public["user_id"], False, actor_user_id=admin_user["user_id"],
        actor_token=admin_token) is True
    assert auth.user_for_token(live) is None
    assert _db_rows(auth.DB_PATH, "SELECT COUNT(*) FROM browser_sessions WHERE user_id=?",
                    (public["user_id"],))[0][0] == 0
    # A disabled account cannot log in; re-enabling an approved account works.
    assert auth.login("alice", "longpassword1") == (None, None)
    assert auth.set_user_status(
        public["user_id"], True, actor_user_id=admin_user["user_id"],
        actor_token=admin_token) is True
    _, restored = auth.login("alice", "longpassword1")
    assert auth.user_for_token(restored)["user_id"] == public["user_id"]


def test_auth_13_pending_account_is_denied_admission_until_approved(auth):
    admin_user, admin_token = _admin(auth)
    invite = auth.issue_invite(actor_user_id=admin_user["user_id"])
    application = auth.register("alice", "longpassword1", invite_code=invite["code"])
    user_id = _db_rows(auth.DB_PATH, "SELECT user_id FROM users WHERE username='alice'")[0][0]
    store = AdmissionStore(auth.DB_PATH)
    with pytest.raises(AccountUnavailable):
        store.acquire(user_id)
    auth.review_application(application["application_id"], actor_user_id=admin_user["user_id"],
                            actor_token=admin_token, decision="approved")
    ticket = store.acquire(user_id)
    store.release(ticket)


def test_auth_14_register_rate_limit_and_auth_24_deletion_retry_token(auth):
    # AUTH-14: the ninth attempt inside the window is rejected.
    for _ in range(8):
        assert auth.check_rate_limit("register", "203.0.113.9", 8, 3600) is True
    assert auth.check_rate_limit("register", "203.0.113.9", 8, 3600) is False

    admin_user, admin_token = _admin(auth)
    invite = auth.issue_invite(actor_user_id=admin_user["user_id"])
    application = auth.register("alice", "longpassword1", invite_code=invite["code"])
    auth.review_application(application["application_id"], actor_user_id=admin_user["user_id"],
                            actor_token=admin_token, decision="approved")
    public, token = auth.login("alice", "longpassword1")

    # AUTH-24: the retry token is scoped to deletion only and survives closing.
    auth.retain_deletion_retry(token, public["user_id"])
    assert auth.user_for_deletion_retry(token) is None  # still active
    conn = _connect(auth.DB_PATH)
    try:
        conn.execute("UPDATE users SET active=0 WHERE user_id=?", (public["user_id"],))
        conn.execute("DELETE FROM browser_sessions WHERE user_id=?", (public["user_id"],))
    finally:
        conn.close()
    assert auth.user_for_token(token) is None
    retry_user = auth.user_for_deletion_retry(token)
    assert retry_user is not None and retry_user["user_id"] == public["user_id"]


def test_auth_12_task_listing_is_owner_scoped(tmp_path, monkeypatch):
    from src.core import jobs
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "jobs.sqlite3")
    own = jobs.claim_job("session-one", "request-one", owner_user_id=1,
                         client_request_id="client-one", payload={})
    assert jobs.update_job(own.job_id, status="completed")
    foreign = jobs.claim_job("session-two", "request-two", owner_user_id=2,
                             client_request_id="client-two", payload={})
    assert jobs.update_job(foreign.job_id, status="completed")
    assert [job["job_id"] for job in jobs.list_for_session(
        "session-one", owner_user_id=1)] == [own.job_id]
    assert jobs.list_for_session("session-one", owner_user_id=2) == []


def test_concurrent_invite_claim_has_exactly_one_winner(auth):
    admin, _ = _admin(auth)
    invite = auth.issue_invite(actor_user_id=admin["user_id"])
    start = Barrier(2, timeout=5)

    def register(username):
        start.wait()
        try:
            return auth.register(username, "longpassword1", invite_code=invite["code"])["status"]
        except auth_invites.AccountInviteError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(register, ("first-member", "second-member")))
    assert outcomes == ["invalid_invite", "pending"]
    applications = _db_rows(auth.DB_PATH, "SELECT user_id FROM account_applications")
    claims = _db_rows(auth.DB_PATH, "SELECT claimed_by FROM account_invites WHERE status='claimed'")
    assert len(applications) == 1 and applications == claims
    pending = _db_rows(auth.DB_PATH, "SELECT user_id FROM users WHERE approval_status='pending'")
    assert pending == applications


def test_concurrent_opposite_reviews_commit_one_decision_and_event(auth):
    admin, token = _admin(auth)
    actor = admin["user_id"]
    invite = auth.issue_invite(actor_user_id=actor)
    application = auth.register("applicant", "longpassword1", invite_code=invite["code"])
    start = Barrier(2, timeout=5)

    def review(decision):
        start.wait()
        try:
            return auth.review_application(application["application_id"], actor_user_id=actor,
                                           actor_token=token, decision=decision)["status"]
        except auth_invites.AccountInviteError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(review, ("approved", "rejected")))
    assert outcomes.count("approval_conflict") == 1
    winner = next(status for status in outcomes if status != "approval_conflict")
    assert winner in {"approved", "rejected"}
    user_id = _db_rows(
        auth.DB_PATH, "SELECT user_id FROM account_applications WHERE application_id=?",
        (application["application_id"],),
    )[0][0]
    assert [tuple(row) for row in _db_rows(
        auth.DB_PATH, "SELECT approval_status,active FROM users WHERE user_id=?", (user_id,)
    )] == [(winner, int(winner == "approved"))]
    assert [row[0] for row in _db_rows(
        auth.DB_PATH, "SELECT event_type FROM account_security_events WHERE target_user_id=? "
        "AND event_type IN ('application_approved','application_rejected')", (user_id,)
    )] == [f"application_{winner}"]


def test_legacy_passwords_and_admin_session_are_live_after_upgrade(tmp_path, monkeypatch):
    from src import auth as auth_module

    path = tmp_path / "accounts.sqlite3"
    monkeypatch.setattr(auth_module, "DB_PATH", path)
    monkeypatch.setattr(auth_module, "PROJECT_ROOT", tmp_path)
    stamp = "2025-01-02T03:04:05+00:00"
    password = "legacy-fixture-password"
    salt = b"legacy-salt-1234"
    digest = auth_module._password_hash(password, salt)
    admin_token = "legacy-fixture-session"
    conn = _connect(path)
    try:
        _create_store_schema(conn, profile=False)
        conn.executemany(
            "INSERT INTO users(user_id,username,salt,password_hash,role,active,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            [(1, "old-admin", salt, digest, "admin", 1, stamp),
             (9, "old-member", salt, digest, "user", 1, stamp),
             (10, "old-inactive", salt, digest, "user", 0, stamp)],
        )
        conn.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)", (
            hashlib.sha256(admin_token.encode()).hexdigest(), 1, stamp, _future()))
    finally:
        conn.close()
    auth_module.init_store()
    auth_module.init_store()
    administrator = auth_module.user_for_token(admin_token)
    assert administrator["user_id"] == 1 and auth_module.require_admin(administrator)
    member, token = auth_module.login("old-member", password)
    assert member["user_id"] == 9 and member["role"] == "user" and token
    assert auth_module.login("old-inactive", password) == (None, None)
    assert [tuple(row) for row in _db_rows(
        path, "SELECT user_id,role,active,salt,password_hash FROM users ORDER BY user_id"
    )] == [(1, "admin", 1, salt, digest), (9, "user", 1, salt, digest), (10, "user", 0, salt, digest)]

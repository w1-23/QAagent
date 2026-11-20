"""Small account/auth store for the public CitrusQA web service.

Passwords are PBKDF2-HMAC-SHA256 hashed and browser sessions are opaque random
tokens stored only as SHA-256 digests. Public registration always creates a
pending application; the first administrator is created through a private CLI.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src import auth_invites
from src.auth_invites import AccountInviteError
from src.auth_migrations import migrate
from src.config import PROJECT_ROOT
from src.core.account_leases import AccountDeletionPending

DB_PATH = PROJECT_ROOT / "state" / "accounts.sqlite3"
COOKIE_NAME = "citrus_auth"
COOKIE_TTL_SECONDS = 7 * 24 * 60 * 60
PBKDF2_ROUNDS = 310_000
_lock = threading.RLock()
logger = logging.getLogger(__name__)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{2,31}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


class _ClosingConnection(sqlite3.Connection):
    """Auth's existing connection context commits/rolls back *and* closes."""

    def __exit__(self, *exc):
        try:
            return super().__exit__(*exc)
        finally:
            self.close()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if os.name != "nt":
        try:
            DB_PATH.parent.chmod(0o700)
        except OSError:
            pass
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None, factory=_ClosingConnection)
    if os.name != "nt":
        try:
            DB_PATH.chmod(0o600)
        except OSError:
            pass
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except BaseException:
        conn.close()
        raise


def init_store() -> None:
    with _lock, _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            active INTEGER NOT NULL DEFAULT 1,
            daily_limit INTEGER NOT NULL DEFAULT 30,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS browser_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_browser_sessions_user ON browser_sessions(user_id);
        CREATE TABLE IF NOT EXISTS account_deletion_retries (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            occurred_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_auth_events_lookup ON auth_events(action, source_hash, occurred_at);
        CREATE TABLE IF NOT EXISTS daily_usage (
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, day)
        );
        CREATE TABLE IF NOT EXISTS workspace_files (
            user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            session_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(session_id, relative_path)
        );
        CREATE INDEX IF NOT EXISTS idx_workspace_files_owner ON workspace_files(user_id, session_id);
        """)
        from src.core.account_admission import init_schema
        conn.execute("BEGIN IMMEDIATE")
        _ensure_user_profile_columns(conn)
        init_schema(conn)
        conn.commit()
        migrate(conn)
        conn.execute("DELETE FROM browser_sessions WHERE expires_at < ?", (_stamp(),))
        _ensure_session_owner_column()


def _ensure_user_profile_columns(conn: sqlite3.Connection) -> None:
    """Add profile fields in place so existing users and sessions stay intact."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    migrations = (
        ("avatar_preset", "ALTER TABLE users ADD COLUMN avatar_preset TEXT NOT NULL DEFAULT '01'"),
        ("avatar_data", "ALTER TABLE users ADD COLUMN avatar_data BLOB"),
        ("avatar_updated_at", "ALTER TABLE users ADD COLUMN avatar_updated_at TEXT NOT NULL DEFAULT ''"),
        ("onboarding_completed", "ALTER TABLE users ADD COLUMN onboarding_completed INTEGER NOT NULL DEFAULT 0"),
    )
    for name, statement in migrations:
        if name not in columns:
            conn.execute(statement)


def _ensure_session_owner_column() -> None:
    sessions_db = PROJECT_ROOT / "state" / "sessions.db"
    if not sessions_db.exists():
        return
    conn = sqlite3.connect(str(sessions_db), timeout=30)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "owner_user_id" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN owner_user_id INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_user_id, updated_at)")
        conn.commit()
    finally:
        conn.close()


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)


def _public_user(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    preset = str(row["avatar_preset"] or "01") if "avatar_preset" in keys else "01"
    if not re.fullmatch(r"(?:0[1-9]|1[0-9]|20)", preset):
        preset = "01"
    onboarding_completed = bool(row["onboarding_completed"]) if "onboarding_completed" in keys else False
    return {
        "user_id": int(row["user_id"]),
        "username": str(row["username"]),
        "role": str(row["role"]),
        "active": bool(row["active"]),
        "approval_status": str(row["approval_status"]),
        "daily_limit": int(row["daily_limit"]),
        "created_at": str(row["created_at"]),
        "avatar_preset": preset,
        "avatar_custom": bool(row["avatar_data"]) if "avatar_data" in keys else False,
        "avatar_updated_at": str(row["avatar_updated_at"] or "") if "avatar_updated_at" in keys else "",
        "onboarding_completed": onboarding_completed,
        "onboarding_required": not onboarding_completed,
    }


def _valid_password_encoding(password: str) -> bool:
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def validate_credentials(username: str, password: str) -> str | None:
    username = str(username or "").strip()
    if not _USERNAME_RE.fullmatch(username):
        return "用户名需为 3–32 位字母、数字、点、下划线或短横线。"
    if not isinstance(password, str) or not 10 <= len(password) <= 256 or not _valid_password_encoding(password):
        return "密码长度需为 10–256 个字符。"
    return None


def create_first_admin(username: str, password: str
                       ) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Create the first administrator from a trusted local CLI."""
    username = str(username or "").strip()
    error = validate_credentials(username, password)
    if error:
        return None, None, error
    salt = secrets.token_bytes(16)
    digest = _password_hash(password, salt)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            return None, None, "管理员账号已存在；拒绝再次初始化。"
        stamp = _stamp()
        try:
            cur = conn.execute(
                "INSERT INTO users(username,salt,password_hash,role,active,created_at,"
                "approval_status,approved_at) VALUES(?,?,?,'admin',1,?,'approved',?)",
                (username, salt, digest, stamp, stamp))
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorname != "SQLITE_CONSTRAINT_UNIQUE":
                raise
            return None, None, "该用户名已被使用。"
        user_id = int(cur.lastrowid)
        token = _insert_session(conn, user_id)
        auth_invites.record_security_event(conn, event_type="account_created_local", target_user_id=user_id)
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    assign_legacy_sessions_to_admin(user_id)
    return _public_user(row), token, None


def register(username: str, password: str, avatar_preset: str = "01", *,
             invite_code: str, source_hash: str | None = None) -> dict[str, Any]:
    """Consume an invite and create a pending application without a session."""
    username = str(username or "").strip()
    if validate_credentials(username, password):
        raise AccountInviteError("invalid_credentials")
    avatar_preset = str(avatar_preset or "01").strip()
    if not re.fullmatch(r"(?:0[1-9]|1[0-9]|20)", avatar_preset):
        raise AccountInviteError("invalid_avatar")
    if not isinstance(invite_code, str) or not auth_invites.INVITE_CODE_RE.fullmatch(invite_code):
        raise AccountInviteError("invalid_invite")
    salt = secrets.token_bytes(16)
    digest = _password_hash(password, salt)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        return auth_invites.submit_application(
            conn, username=username, salt=salt, password_hash=digest, avatar_preset=avatar_preset,
            invite_code=invite_code, source_hash=source_hash)


def create_local_account(username: str, password: str, *, role: str = "user",
                         daily_limit: int = 30) -> int:
    """Create an approved account through the trusted maintenance CLI, without a session."""
    username = str(username or "").strip()
    if validate_credentials(username, password):
        raise AccountInviteError("invalid_credentials")
    if role not in {"user", "admin"} or type(daily_limit) is not int or daily_limit < 0:
        raise AccountInviteError("invalid_account_options")
    salt, digest = _new_password_material(password)
    stamp = _stamp()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "INSERT INTO users(username,salt,password_hash,role,active,daily_limit,created_at,"
                "approval_status,approved_at) VALUES(?,?,?,?,1,?,?,'approved',?)",
                (username, salt, digest, role, daily_limit, stamp, stamp))
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorname != "SQLITE_CONSTRAINT_UNIQUE":
                raise
            raise AccountInviteError("username_unavailable") from None
        user_id = int(row.lastrowid)
        auth_invites.record_security_event(conn, event_type="account_created_local", target_user_id=user_id)
    if role == "admin":
        assign_legacy_sessions_to_admin(user_id)
    return user_id


def promote_existing_first_admin(username: str, password: str
                                 ) -> tuple[dict[str, Any] | None, str | None]:
    """Promote an existing account from a private CLI if no administrator exists.

    This recovers installations where public registration happened before the
    private administrator bootstrap. The account's password must be verified;
    the HTTP registration path never calls this function.
    """
    username = str(username or "").strip()
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            conn.rollback()
            return None, "管理员账号已存在；拒绝再次初始化。"
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            conn.rollback()
            return None, "没有找到该账号。"
        if not _eligible(row):
            conn.rollback()
            return None, "该账号已停用，不能升级。"
        candidate = _password_hash(str(password or ""), bytes(row["salt"]))
        if not hmac.compare_digest(candidate, bytes(row["password_hash"])):
            conn.rollback()
            return None, "当前密码不正确。"
        conn.execute("UPDATE users SET role='admin' WHERE user_id=?", (int(row["user_id"]),))
        auth_invites.record_security_event(conn, event_type="account_promoted_local",
                                           target_user_id=int(row["user_id"]))
        promoted = conn.execute("SELECT * FROM users WHERE user_id=?", (int(row["user_id"]),)).fetchone()
        conn.commit()
    assign_legacy_sessions_to_admin(int(promoted["user_id"]))
    return _public_user(promoted), None


def set_avatar_preset(user_id: int, preset: str) -> bool:
    preset = str(preset or "").strip()
    if not re.fullmatch(r"(?:0[1-9]|1[0-9]|20)", preset):
        return False
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET avatar_preset=?,avatar_data=NULL,avatar_updated_at=? WHERE user_id=? AND active=1",
            (preset, _stamp(), int(user_id)),
        )
    return cur.rowcount > 0


def set_custom_avatar(user_id: int, image_data: bytes) -> bool:
    if not image_data:
        return False
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET avatar_data=?,avatar_updated_at=? WHERE user_id=? AND active=1",
            (sqlite3.Binary(image_data), _stamp(), int(user_id)),
        )
    return cur.rowcount > 0


def get_custom_avatar(user_id: int) -> bytes | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT avatar_data FROM users WHERE user_id=? AND active=1", (int(user_id),)
        ).fetchone()
    return bytes(row["avatar_data"]) if row and row["avatar_data"] else None


def complete_onboarding(user_id: int) -> bool:
    with _lock, _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET onboarding_completed=1 WHERE user_id=? AND active=1", (int(user_id),)
        )
    return cur.rowcount > 0


def _eligible(row: sqlite3.Row | None) -> bool:
    return bool(row and row["active"] and row["approval_status"] == "approved")


def _matches_password(row: sqlite3.Row | None, password: str) -> bool:
    # Use comparable PBKDF2 work for unknown and ineligible accounts.
    if not _valid_password_encoding(password):
        return False
    salt = bytes(row["salt"]) if row else bytes(16)
    digest = bytes(row["password_hash"]) if row else bytes(32)
    return hmac.compare_digest(_password_hash(password, salt), digest) and row is not None


def _insert_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(36)
    now = _now()
    conn.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                 (hashlib.sha256(token.encode()).hexdigest(), user_id,
                  _stamp(now), _stamp(now + timedelta(seconds=COOKIE_TTL_SECONDS))))
    return token


def login(username: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(password, str) or not 0 < len(password) <= 256:
        return None, None
    username = str(username or "").strip()
    if not _USERNAME_RE.fullmatch(username):
        return None, None
    query = ("SELECT u.*,a.epoch FROM users u JOIN account_admission a ON a.owner=u.user_id "
             "WHERE u.username=? AND a.deleting=0")
    with _connect() as conn:
        original = conn.execute(query, (username,)).fetchone()
    if not _matches_password(original, password) or not _eligible(original):
        return None, None
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(query, (username,)).fetchone()
        fence = ("user_id", "epoch", "salt", "password_hash")
        if not _eligible(row) or any(row[key] != original[key] for key in fence):
            return None, None
        token = _insert_session(conn, row["user_id"])
    return _public_user(row), token


def verify_password(user_id: int, password: str, *, allow_disabled: bool = False) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (int(user_id),)).fetchone()
    return bool(row and row["approval_status"] == "approved" and (row["active"] or allow_disabled)
                and _matches_password(row, str(password or "")))


def logout(token: str) -> None:
    if not token:
        return
    with _connect() as conn:
        digest = hashlib.sha256(token.encode()).hexdigest()
        conn.execute("DELETE FROM browser_sessions WHERE token_hash=?", (digest,))
        conn.execute("DELETE FROM account_deletion_retries WHERE token_hash=?", (digest,))


def _session_user(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    return conn.execute(
        "SELECT u.* FROM users u JOIN browser_sessions s ON s.user_id=u.user_id "
        "JOIN account_admission a ON a.owner=u.user_id "
        "WHERE s.token_hash=? AND s.expires_at>? AND u.active=1 "
        "AND u.approval_status='approved' AND a.deleting=0", (digest, _stamp())).fetchone()


def user_for_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    with _connect() as conn:
        row = _session_user(conn, token)
    return _public_user(row) if row else None


def retain_deletion_retry(token: str, user_id: int) -> None:
    """Keep this verified browser token valid only for retrying account deletion."""
    if not token:
        return
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO account_deletion_retries(token_hash,user_id,expires_at) "
            "SELECT token_hash,user_id,expires_at FROM browser_sessions "
            "WHERE token_hash=? AND user_id=? AND expires_at>?",
            (digest, int(user_id), _stamp()))


def user_for_deletion_retry(token: str) -> dict[str, Any] | None:
    """Never use this lookup for general authentication or resource access."""
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    with _connect() as conn:
        row = conn.execute(
            "SELECT u.* FROM users u JOIN account_deletion_retries d ON d.user_id=u.user_id "
            "WHERE d.token_hash=? AND d.expires_at>? AND u.active=0 AND u.role!='admin'",
            (digest, _stamp())).fetchone()
    return _public_user(row) if row else None


def user_by_id(user_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (int(user_id),)).fetchone()
    return _public_user(row) if row else None


def require_admin(user: dict[str, Any] | None) -> bool:
    return bool(user and user.get("role") == "admin" and user.get("active")
                and user.get("approval_status", "approved") == "approved")


def check_rate_limit(action: str, source: str, limit: int, window_seconds: int) -> bool:
    source_hash = hashlib.sha256(str(source or "unknown").encode()).hexdigest()
    cutoff = time.time() - int(window_seconds)
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM auth_events WHERE occurred_at < ?", (time.time() - 86400,))
        count = conn.execute("SELECT COUNT(*) FROM auth_events WHERE action=? AND source_hash=? AND occurred_at>=?",
                             (action, source_hash, cutoff)).fetchone()[0]
        if int(count) >= int(limit):
            return False
        conn.execute("INSERT INTO auth_events(action,source_hash,occurred_at) VALUES(?,?,?)",
                     (action, source_hash, time.time()))
    return True


def reserve_chat_request(user: dict[str, Any]) -> tuple[bool, int]:
    if user.get("role") == "admin":
        return True, 0
    day = _now().date().isoformat()
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT daily_limit FROM users WHERE user_id=? AND active=1",
                           (user["user_id"],)).fetchone()
        if not row:
            conn.rollback()
            return False, 0
        limit = int(row["daily_limit"])
        conn.execute("INSERT OR IGNORE INTO daily_usage(user_id,day,request_count) VALUES(?,?,0)",
                     (user["user_id"], day))
        current = int(conn.execute("SELECT request_count FROM daily_usage WHERE user_id=? AND day=?",
                                   (user["user_id"], day)).fetchone()[0])
        if current >= limit:
            conn.rollback()
            return False, limit
        conn.execute("UPDATE daily_usage SET request_count=request_count+1 WHERE user_id=? AND day=?",
                     (user["user_id"], day))
        conn.commit()
    return True, limit


def release_chat_request(user: dict[str, Any]) -> None:
    """Undo a reserved request only when its attachment payload is rejected."""
    if user.get("role") == "admin":
        return
    day = _now().date().isoformat()
    with _lock, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE daily_usage SET request_count=MAX(0,request_count-1) "
            "WHERE user_id=? AND day=?",
            (int(user["user_id"]), day),
        )
        conn.commit()


def list_users() -> list[dict[str, Any]]:
    day = _now().date().isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT u.*,COALESCE(d.request_count,0) AS requests_today FROM users u "
            "LEFT JOIN daily_usage d ON d.user_id=u.user_id AND d.day=? ORDER BY u.user_id", (day,)).fetchall()
    return [{**_public_user(row), "requests_today": int(row["requests_today"])} for row in rows]


def _require_current_session(conn: sqlite3.Connection, user_id: int, token: str) -> sqlite3.Row:
    row = _session_user(conn, token)
    if row is None or row["user_id"] != int(user_id):
        raise AccountInviteError("reauthentication_required")
    return row


def _require_admin_actor(conn: sqlite3.Connection, actor_user_id: int, actor_token: str) -> sqlite3.Row:
    row = _require_current_session(conn, actor_user_id, actor_token)
    if row["role"] != "admin":
        raise AccountInviteError("admin_required")
    return row


def issue_invite(*, actor_user_id: int, expires_in_days: int = 7, actor_token: str | None = None,
                 source_hash: str | None = None) -> dict[str, Any]:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if actor_token is None:
            actor = conn.execute("SELECT * FROM users WHERE user_id=?", (actor_user_id,)).fetchone()
            if not _eligible(actor) or actor["role"] != "admin":
                raise AccountInviteError("admin_required")
        else:
            _require_admin_actor(conn, actor_user_id, actor_token)
        return auth_invites.issue_invite(conn, actor_user_id=actor_user_id,
                                        expires_in_days=expires_in_days, source_hash=source_hash)


def list_invites(*, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    with _connect() as conn:
        return auth_invites.list_invites(conn, limit=limit, offset=offset)


def list_applications(*, status: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    with _connect() as conn:
        return auth_invites.list_applications(conn, status=status, limit=limit, offset=offset)


def revoke_invite(invite_id: str, *, actor_user_id: int, actor_token: str,
                  source_hash: str | None = None) -> dict[str, Any]:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_admin_actor(conn, actor_user_id, actor_token)
        return auth_invites.revoke_invite(conn, invite_id, actor_user_id=actor_user_id, source_hash=source_hash)


def review_application(application_id: str, *, actor_user_id: int, actor_token: str, decision: str,
                       rejection_reason: str | None = None, source_hash: str | None = None) -> dict[str, Any]:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _require_admin_actor(conn, actor_user_id, actor_token)
        return auth_invites.review_application(
            conn, application_id, actor_user_id=actor_user_id, decision=decision,
            rejection_reason=rejection_reason, source_hash=source_hash)


def set_user_status(user_id: int, active: bool, *, actor_user_id: int | None = None,
                    actor_token: str | None = None, source_hash: str | None = None) -> bool:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if actor_user_id is not None:
            _require_admin_actor(conn, actor_user_id, actor_token or "")
            if user_id == actor_user_id and not active:
                raise AccountInviteError("cannot_disable_self")
        row = conn.execute(
            "SELECT u.active,u.approval_status,a.deleting FROM users u "
            "LEFT JOIN account_admission a ON a.owner=u.user_id WHERE u.user_id=?", (int(user_id),)).fetchone()
        if row is None:
            return False
        if active and row["deleting"]:
            if actor_user_id is None:
                return False
            raise AccountInviteError("account_deletion_pending")
        if active and row["approval_status"] != "approved":
            raise AccountInviteError("account_not_approved")
        if bool(row["active"]) != active:
            conn.execute("UPDATE users SET active=? WHERE user_id=?", (int(active), int(user_id)))
            auth_invites.record_security_event(
                conn, event_type="account_enabled" if active else "account_disabled",
                actor_user_id=actor_user_id, target_user_id=user_id, source_hash=source_hash)
        if not active:
            conn.execute("DELETE FROM browser_sessions WHERE user_id=?", (int(user_id),))
    return True


def set_user_daily_limit(user_id: int, limit: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET daily_limit=? WHERE user_id=?", (int(limit), int(user_id)))
    return cur.rowcount > 0


def register_workspace_file(user_id: int, session_id: str, relative_path: str) -> None:
    """Record an output file as belonging to one user's session."""
    relative_path = str(relative_path or "").replace("\\", "/").lstrip("/")
    if not session_id or not relative_path or ".." in Path(relative_path).parts:
        return
    with _connect() as conn:
        conn.execute("INSERT INTO workspace_files(user_id,session_id,relative_path,created_at) "
                     "VALUES(?,?,?,?) ON CONFLICT(session_id,relative_path) DO UPDATE SET "
                     "user_id=excluded.user_id,created_at=excluded.created_at",
                     (int(user_id), session_id, relative_path, _stamp()))


def _new_password_material(password: str) -> tuple[bytes, bytes]:
    if not isinstance(password, str) or not 10 <= len(password) <= 256 or not _valid_password_encoding(password):
        raise AccountInviteError("invalid_password")
    salt = secrets.token_bytes(16)
    return salt, _password_hash(password, salt)


def _verify_reauthentication(row: sqlite3.Row, password: str | None) -> None:
    if not isinstance(password, str) or not 0 < len(password) <= 256 or not _matches_password(row, password):
        raise AccountInviteError("reauthentication_required")


def _write_password(conn: sqlite3.Connection, user_id: int, salt: bytes, digest: bytes,
                    *, event_type: str, actor_user_id: int | None, source_hash: str | None) -> None:
    conn.execute("UPDATE users SET salt=?,password_hash=?,password_changed_at=? WHERE user_id=?",
                 (salt, digest, _stamp(), int(user_id)))
    conn.execute("DELETE FROM browser_sessions WHERE user_id=?", (int(user_id),))
    conn.execute("DELETE FROM account_deletion_retries WHERE user_id=?", (int(user_id),))
    conn.execute("DELETE FROM account_recovery_codes WHERE user_id=?", (int(user_id),))
    auth_invites.record_security_event(conn, event_type=event_type, actor_user_id=actor_user_id,
                                       target_user_id=user_id, source_hash=source_hash)


def reset_password(user_id: int, new_password: str | None = None, *, actor_user_id: int | None = None,
                   actor_token: str | None = None, current_password: str | None = None,
                   source_hash: str | None = None) -> tuple[str | None, bool]:
    """Reset through a reauthenticated administrator or the trusted local CLI."""
    temporary = secrets.token_urlsafe(24) if new_password is None else None
    salt, digest = _new_password_material(temporary if temporary is not None else new_password)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if actor_user_id is not None:
            actor = _require_admin_actor(conn, actor_user_id, actor_token or "")
            _verify_reauthentication(actor, current_password)
        target = conn.execute(
            "SELECT u.role,a.deleting FROM users u JOIN account_admission a ON a.owner=u.user_id "
            "WHERE u.user_id=?", (int(user_id),)).fetchone()
        if target is None:
            return None, False
        if target["deleting"]:
            raise AccountInviteError("account_deletion_pending")
        if actor_user_id is not None and target["role"] == "admin":
            raise AccountInviteError("admin_target_forbidden")
        _write_password(conn, user_id, salt, digest, event_type="password_reset_admin" if actor_user_id
                        is not None else "password_reset_local", actor_user_id=actor_user_id,
                        source_hash=source_hash)
    return temporary, True


def change_password(user_id: int, current_password: str, new_password: str, *, token: str,
                    source_hash: str | None = None) -> None:
    salt, digest = _new_password_material(new_password)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = _require_current_session(conn, user_id, token)
        _verify_reauthentication(user, current_password)
        _write_password(conn, user_id, salt, digest, event_type="password_changed", actor_user_id=user_id,
                        source_hash=source_hash)


def replace_recovery_codes(user_id: int, current_password: str, *, token: str,
                           source_hash: str | None = None) -> list[str]:
    codes = ["r_" + secrets.token_urlsafe(24) for _ in range(8)]
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = _require_current_session(conn, user_id, token)
        _verify_reauthentication(user, current_password)
        conn.execute("DELETE FROM account_recovery_codes WHERE user_id=?", (int(user_id),))
        stamp = _stamp()
        conn.executemany("INSERT INTO account_recovery_codes(code_hash,user_id,created_at) VALUES(?,?,?)",
                         [(hashlib.sha256(code.encode()).hexdigest(), int(user_id), stamp) for code in codes])
        auth_invites.record_security_event(conn, event_type="recovery_codes_replaced", actor_user_id=user_id,
                                           target_user_id=user_id, source_hash=source_hash)
    return codes


def recover_password(username: str, recovery_code: str, new_password: str, *,
                     source_hash: str | None = None) -> None:
    if not isinstance(recovery_code, str) or not re.fullmatch(r"r_[A-Za-z0-9_-]{32}", recovery_code):
        raise AccountInviteError("invalid_recovery")
    username = str(username or "").strip()
    if not _USERNAME_RE.fullmatch(username):
        raise AccountInviteError("invalid_recovery")
    salt, digest = _new_password_material(new_password)
    code_hash = hashlib.sha256(recovery_code.encode()).hexdigest()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT u.user_id FROM account_recovery_codes c JOIN users u ON c.user_id=u.user_id "
            "JOIN account_admission a ON u.user_id=a.owner "
            "WHERE u.username=? AND c.code_hash=? AND c.used_at IS NULL AND u.active=1 "
            "AND u.approval_status='approved' AND a.deleting=0", (username, code_hash)).fetchone()
        if row is None:
            raise AccountInviteError("invalid_recovery")
        _write_password(conn, row["user_id"], salt, digest, event_type="password_recovered",
                        actor_user_id=row["user_id"], source_hash=source_hash)


def assign_legacy_sessions_to_admin(user_id: int) -> None:
    sessions_db = PROJECT_ROOT / "state" / "sessions.db"
    if not sessions_db.exists():
        return
    conn = sqlite3.connect(str(sessions_db), timeout=30)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "owner_user_id" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN owner_user_id INTEGER")
        conn.execute("UPDATE sessions SET owner_user_id=? WHERE owner_user_id IS NULL", (int(user_id),))
        conn.commit()
    finally:
        conn.close()


def _assert_no_active_account_jobs(conn: sqlite3.Connection, user_id: int) -> None:
    """Refuse destructive cleanup while any recorded job is not terminal.

    Missing legacy job tables mean no recorded jobs. An unreadable schema or an
    unknown status is not evidence of quiescence and must fail closed instead.
    This is not a drain barrier for requests that have not created a job yet.
    """
    if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_jobs'").fetchone():
        return
    from src.core.jobs import TERMINAL_STATUS
    terminal = sorted(TERMINAL_STATUS)
    placeholders = ",".join("?" for _ in terminal)
    has_sessions = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(task_jobs)")}
    if has_sessions:
        owner_clause = "s.owner_user_id=?"
        owners = (int(user_id),)
        if "owner_user_id" in columns:
            # A job can be admitted before its session row is created.
            owner_clause = "(j.owner_user_id=? OR s.owner_user_id=?)"
            owners = (int(user_id), int(user_id))
        source = "task_jobs j LEFT JOIN sessions s ON s.session_id=j.session_id"
    else:
        # Empty/new accounts can have a jobs-only store. Unbound legacy work
        # remains conservative: without session ownership it cannot be ignored.
        owner_clause = "(j.owner_user_id=? OR j.owner_user_id IS NULL)" if "owner_user_id" in columns else "1=1"
        owners = (int(user_id),) if "owner_user_id" in columns else ()
        source = "task_jobs j"
    active = conn.execute(
        "SELECT 1 FROM " + source + " WHERE " + owner_clause +
        " AND COALESCE(j.status,'') NOT IN (" + placeholders + ") LIMIT 1",
        (*owners, *terminal),
    ).fetchone()
    if active:
        raise AccountDeletionPending("account still has unfinished jobs; retry deletion after they settle")


def delete_user_account(user_id: int, *, raise_if_busy: bool = False) -> bool:
    """Claim one cleanup owner across processes; preserve the existing bool API."""
    from src.core.account_leases import deletion_lock
    try:
        with deletion_lock(user_id) as claim:
            if claim is None:
                return False
            return _delete_user_account(user_id, raise_if_busy=raise_if_busy, claim=claim)
    except AccountDeletionPending:
        if raise_if_busy:
            raise
        return False


def _delete_user_account(user_id: int, *, raise_if_busy: bool, claim) -> bool:
    """Disable an account, purge its scoped data, then remove its identity.

    The identity is deleted last. Shared admission references cover accepted
    requests and retained worker threads; job rows remain an additional legacy
    guard. The persistent claim spans purge without a long SQLite transaction.
    Writers outside these admission/publication boundaries need their own lease.
    """
    from src.core.account_leases import assert_idle
    sessions_db = PROJECT_ROOT / "state" / "sessions.db"
    session_ids: list[str] = []
    try:
        # Claim acquisition atomically closed admission and disabled identity in
        # the auth database. Validate the fence before touching any scoped data.
        with claim.store.transaction() as conn:
            claim.validate(conn)

        # Revoke durable queued executions after closing account admission. The
        # queue hook also defers deletion for running/unknown execution states.
        from src.code_execution.repository import cancel_owner_executions
        cancel_owner_executions(int(user_id))

        # Includes requests admitted before reserve/create_job and retained
        # threads; a terminal job row alone is never proof that they exited.
        assert_idle(user_id)
        # The final admitted submitter may have queued work after the first
        # revoke and released its lease just before idle was observed. With
        # admission closed and all holders drained, this second sweep is final.
        cancel_owner_executions(int(user_id))
        from src.code_execution.logs import purge_owner as purge_execution_logs
        purge_execution_logs(int(user_id), claim=claim)
        if sessions_db.exists():
            with closing(sqlite3.connect(str(sessions_db), timeout=30)) as conn, conn:
                conn.execute("BEGIN")
                cols = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
                if "owner_user_id" not in cols:
                    raise RuntimeError("session ownership unavailable during account deletion")
                _assert_no_active_account_jobs(conn, user_id)
                session_ids = [str(row[0]) for row in conn.execute(
                    "SELECT session_id FROM sessions WHERE owner_user_id=?", (int(user_id),))]

        # Task workspaces own file pins too. Their metadata/pins must drain
        # under this fenced cleanup claim before session files remove bytes.
        from src.workspaces import purge_owner as purge_workspaces
        purge_workspaces(int(user_id), claim=claim)

        # Figure projects own pins on account files. Purge them first so file
        # cleanup can remove those bytes; any failure keeps the identity disabled.
        from src.figures.store import purge_account as purge_figure_projects
        purge_figure_projects(int(user_id))
        from src.research.analysis_results import purge_owner as purge_analysis_results
        from src.research.integrity_review import purge_account as purge_integrity_reviews
        purge_analysis_results(int(user_id))
        from src.research.script_assets import purge_owner as purge_script_assets
        purge_script_assets(int(user_id))
        purge_integrity_reviews(int(user_id))
        from src.research.reference_audit import purge_owner as purge_reference_audits
        purge_reference_audits(int(user_id))
        from src.core.jobs import purge_protocol_for_owner
        purge_protocol_for_owner(int(user_id))
        # Release every retained Word version's source pins before file purge.
        from src.core.word_documents import purge_account_drafts
        purge_account_drafts(PROJECT_ROOT / "state" / "word_drafts.sqlite3", int(user_id))

        from src.session import files as session_files
        session_files.init_store()
        for sid in session_ids:
            if not session_files.valid_session(sid):
                raise ValueError("account contains an invalid session identifier")
            session_files.purge_session(sid, strict=True,
                                        owner_user_id=int(user_id))

        # Generated artifacts and workspace exports are session/user scoped.
        # Resolve and constrain every recursive deletion to its managed root.
        import shutil
        artifact_base = (PROJECT_ROOT / "state" / "artifacts").resolve()
        for sid in session_ids:
            root = artifact_base / sid
            resolved = root.resolve()
            if not resolved.is_relative_to(artifact_base):
                raise ValueError("artifact path escaped the managed root")
            if root.is_symlink():
                raise ValueError("refusing to recursively remove an artifact symlink")
            if root.exists():
                shutil.rmtree(root)

        artifact_db = PROJECT_ROOT / "state" / "research_artifacts.sqlite3"
        if artifact_db.exists():
            with closing(sqlite3.connect(str(artifact_db), timeout=30)) as conn, conn:
                for sid in session_ids:
                    for table in ("publication_outbox", "artifacts", "research_notes"):
                        try:
                            conn.execute(f"DELETE FROM {table} WHERE session_id=?", (sid,))
                        except sqlite3.OperationalError as exc:
                            if "no such table" not in str(exc).lower():
                                raise

        output_base = (PROJECT_ROOT / "workspace" / "output" / "users").resolve()
        output_root = output_base / str(int(user_id))
        if output_root.exists() or output_root.is_symlink():
            if not output_root.resolve().is_relative_to(output_base):
                raise ValueError("user output path escaped the managed root")
            if output_root.is_symlink():
                raise ValueError("refusing to recursively remove an output symlink")
            shutil.rmtree(output_root)

        from src.guardrails.memory import memory_store
        memory_store.clear_user_memory(user_id)
        from src.guardrails.account_preferences import reset_preferences
        try:
            db_path = str(memory_store.db_path)
        except Exception:
            db_path = str(PROJECT_ROOT / "state" / "sessions.db")
        reset_preferences(user_id, db_path)
        for sid in session_ids:
            memory_store.clear_session(sid)

        if sessions_db.exists():
            with closing(sqlite3.connect(str(sessions_db), timeout=30)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                # Recheck before removing the evidence used to identify jobs.
                # A pre-existing request may have registered during file cleanup.
                _assert_no_active_account_jobs(conn, user_id)
                has_sessions = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
                latest_ids = ({str(row[0]) for row in conn.execute(
                    "SELECT session_id FROM sessions WHERE owner_user_id=?", (int(user_id),))}
                    if has_sessions else set())
                if latest_ids != set(session_ids):
                    raise RuntimeError("account sessions changed during deletion; retry cleanup")
                for sid in session_ids:
                    for table in ("messages", "session_evidence", "permission_grants",
                                  "pipeline_tasks", "task_jobs", "feedback"):
                        try:
                            conn.execute(f"DELETE FROM {table} WHERE session_id=?", (sid,))
                        except sqlite3.OperationalError as exc:
                            if "no such table" not in str(exc).lower():
                                raise
                    conn.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
                conn.commit()

        with claim.store.transaction() as conn:
            claim.validate(conn)
            if conn.execute("SELECT 1 FROM account_work_leases WHERE owner=? LIMIT 1",
                            (int(user_id),)).fetchone():
                raise AccountDeletionPending("account work changed during deletion")
            cur = conn.execute(
                "DELETE FROM users WHERE user_id=? AND role!='admin'", (int(user_id),))
            if cur.rowcount != 1:
                raise RuntimeError("account changed during deletion")
        return True
    except AccountDeletionPending:
        logger.info("Account deletion waiting for work to finish: user_id=%s", int(user_id))
        if raise_if_busy:
            raise
        return False
    except Exception:
        logger.exception("Account data purge failed for user_id=%s", int(user_id))
        # Fail closed. Some session/file stores may already have been purged;
        # reactivating the account here would create an ambiguous partial state.
        return False

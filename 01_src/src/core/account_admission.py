"""Persistent account admission in the *same SQLite database* as auth.users.

No expiration or PID probe proves work stopped. Leases and deletion claims left
by a crashed process deliberately survive until explicit, evidence-based
reconciliation. All connections are short lived; a claim spans purge file I/O.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path


class AccountUnavailable(RuntimeError):
    """The account no longer accepts new work or publications."""


class AccountDeletionPending(RuntimeError):
    """Deletion is waiting for work or another cleanup owner."""


def init_schema(conn: sqlite3.Connection) -> None:
    # executescript commits implicitly: use individual statements so callers'
    # BEGIN IMMEDIATE transactions remain intact.
    statements = (
        """CREATE TABLE IF NOT EXISTS account_admission (
            owner INTEGER PRIMARY KEY, epoch INTEGER NOT NULL DEFAULT 1,
            deleting INTEGER NOT NULL DEFAULT 0,
            claim_token TEXT, claim_epoch INTEGER, claim_pid INTEGER,
            claim_process TEXT, claim_started REAL)""",
        """CREATE TABLE IF NOT EXISTS account_work_leases (
            lease_id TEXT PRIMARY KEY, owner INTEGER NOT NULL,
            epoch INTEGER NOT NULL, refs INTEGER NOT NULL CHECK(refs > 0),
            process_token TEXT NOT NULL, pid INTEGER NOT NULL, created_at REAL NOT NULL,
            FOREIGN KEY(owner) REFERENCES account_admission(owner))""",
        "CREATE INDEX IF NOT EXISTS idx_account_work_owner ON account_work_leases(owner)",
        """CREATE TRIGGER IF NOT EXISTS account_admission_user_insert
            AFTER INSERT ON users BEGIN
            INSERT INTO account_admission(owner) VALUES(NEW.user_id);
            END""",
        """CREATE TRIGGER IF NOT EXISTS account_admission_status_epoch
            AFTER UPDATE OF active ON users WHEN NEW.active != OLD.active BEGIN
            UPDATE account_admission SET epoch=epoch+1 WHERE owner=NEW.user_id;
            END""",
        """CREATE TRIGGER IF NOT EXISTS account_admission_no_revive
            BEFORE UPDATE OF active ON users WHEN NEW.active=1 AND EXISTS (
                SELECT 1 FROM account_admission WHERE owner=NEW.user_id AND deleting=1)
            BEGIN SELECT RAISE(ABORT, 'account deletion is irreversible'); END""",
        """CREATE TRIGGER IF NOT EXISTS account_admission_guard_delete
            BEFORE DELETE ON users WHEN EXISTS (
                SELECT 1 FROM account_work_leases WHERE owner=OLD.user_id)
            BEGIN SELECT RAISE(ABORT, 'account has admitted work'); END""",
        "INSERT OR IGNORE INTO account_admission(owner) SELECT user_id FROM users",
    )
    for statement in statements:
        conn.execute(statement)


# Recomputed after fork as well as spawn. Diagnostic only, never authorization.
_process_identity: tuple[int, str] | None = None


def _process_token() -> str:
    global _process_identity
    pid = os.getpid()
    if _process_identity is None or _process_identity[0] != pid:
        _process_identity = (pid, secrets.token_hex(24))
    return _process_identity[1]


@dataclass(frozen=True)
class LeaseTicket:
    lease_id: str
    owner: int
    epoch: int
    process_token: str


@dataclass(frozen=True)
class DeletionClaim:
    store: AdmissionStore
    owner: int
    token: str
    epoch: int

    def validate(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT 1 FROM account_admission WHERE owner=? AND deleting=1 "
            "AND claim_token=? AND claim_epoch=? AND epoch=?",
            (self.owner, self.token, self.epoch, self.epoch)).fetchone()
        if not row:
            raise AccountDeletionPending("deletion claim changed; reconciliation required")


class AdmissionStore:
    """A runtime-bound handle, never cached by account ID or global root.

    Obtain with current_store(). Tickets must use the handle that acquired them,
    including release after runtime configuration changes. retain/release count
    actual holders of a group; each successful retain requires one release.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(str(self.path), timeout=30, isolation_level=None)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def acquire(self, owner: int, *, expected_epoch: int | None = None) -> LeaseTicket:
        owner = int(owner)
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT a.epoch FROM users u JOIN account_admission a ON a.owner=u.user_id "
                "WHERE u.user_id=? AND u.active=1 AND a.deleting=0 AND a.claim_token IS NULL",
                (owner,)).fetchone()
            if not row or owner <= 0 or (expected_epoch is not None and row[0] != expected_epoch):
                raise AccountUnavailable("account is disabled, closing, deleted or epoch changed")
            ticket = LeaseTicket(secrets.token_hex(24), owner, int(row[0]), _process_token())
            conn.execute("INSERT INTO account_work_leases VALUES(?,?,?,?,?,?,?)",
                         (ticket.lease_id, owner, ticket.epoch, 1, ticket.process_token,
                          os.getpid(), time.time()))
        return ticket

    def retain(self, ticket: LeaseTicket) -> None:
        # Already admitted child work may finish after close/disable. Its group
        # must still exist; a stale ticket can never recreate it.
        with self.transaction() as conn:
            cur = conn.execute(
                "UPDATE account_work_leases SET refs=refs+1 WHERE lease_id=? "
                "AND owner=? AND epoch=? AND process_token=?",
                (ticket.lease_id, ticket.owner, ticket.epoch, ticket.process_token))
            if cur.rowcount != 1:
                raise AccountUnavailable("lease has already finished or was reconciled")

    def release(self, ticket: LeaseTicket) -> None:
        with self.transaction() as conn:
            args = (ticket.lease_id, ticket.owner, ticket.epoch, ticket.process_token)
            row = conn.execute(
                "SELECT refs FROM account_work_leases WHERE lease_id=? AND owner=? "
                "AND epoch=? AND process_token=?", args).fetchone()
            if not row:
                return
            if row[0] == 1:
                conn.execute("DELETE FROM account_work_leases WHERE lease_id=?", (ticket.lease_id,))
            else:
                conn.execute("UPDATE account_work_leases SET refs=refs-1 WHERE lease_id=?", (ticket.lease_id,))

    def check_epoch(self, owner: int, epoch: int) -> bool:
        """Snapshot only; acquire(expected_epoch=...) is the atomic authorization."""
        with self.transaction() as conn:
            return bool(conn.execute(
                "SELECT 1 FROM users u JOIN account_admission a ON a.owner=u.user_id "
                "WHERE u.user_id=? AND u.active=1 AND a.deleting=0 AND a.epoch=? "
                "AND a.claim_token IS NULL", (int(owner), int(epoch))).fetchone())

    def status(self, owner: int) -> dict | None:
        with self.transaction() as conn:
            row = conn.execute("SELECT a.*, u.active FROM account_admission a "
                               "LEFT JOIN users u ON u.user_id=a.owner WHERE a.owner=?",
                               (int(owner),)).fetchone()
            return dict(row) if row else None

    def active_count(self, owner: int) -> int:
        with self.transaction() as conn:
            return int(conn.execute("SELECT COALESCE(SUM(refs),0) FROM account_work_leases WHERE owner=?",
                                    (int(owner),)).fetchone()[0])

    @staticmethod
    def _close(conn, owner: int) -> bool:
        row = conn.execute("SELECT 1 FROM users WHERE user_id=?", (owner,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE account_admission SET deleting=1, epoch=epoch+1 "
                     "WHERE owner=? AND deleting=0", (owner,))
        conn.execute("UPDATE users SET active=0 WHERE user_id=?", (owner,))
        conn.execute("DELETE FROM browser_sessions WHERE user_id=?", (owner,))
        return True

    def close_admission(self, owner: int) -> bool:
        """Irreversible deletion close, distinct from an administrator disable."""
        with self.transaction() as conn:
            return self._close(conn, int(owner))

    def claim_deletion(self, owner: int) -> DeletionClaim | None:
        owner = int(owner)
        with self.transaction() as conn:
            user = conn.execute("SELECT role FROM users WHERE user_id=?", (owner,)).fetchone()
            if not user or user[0] == "admin":
                return None
            row = conn.execute("SELECT claim_token FROM account_admission WHERE owner=?", (owner,)).fetchone()
            if row[0] is not None:
                raise AccountDeletionPending("another deletion claim is pending; never expires automatically")
            self._close(conn, owner)
            epoch = int(conn.execute("SELECT epoch FROM account_admission WHERE owner=?", (owner,)).fetchone()[0])
            token = secrets.token_hex(24)
            conn.execute("UPDATE account_admission SET claim_token=?,claim_epoch=?,claim_pid=?,"
                         "claim_process=?,claim_started=? WHERE owner=?",
                         (token, epoch, os.getpid(), _process_token(), time.time(), owner))
            return DeletionClaim(self, owner, token, epoch)

    def release_claim(self, claim: DeletionClaim) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE account_admission SET claim_token=NULL,claim_epoch=NULL,claim_pid=NULL,"
                         "claim_process=NULL,claim_started=NULL WHERE owner=? AND claim_token=? AND claim_epoch=?",
                         (claim.owner, claim.token, claim.epoch))

    def reconciliation_snapshot(self, owner: int) -> dict:
        """Evidence for operators; timestamps/PIDs do not authorize dropping work."""
        with self.transaction() as conn:
            state = conn.execute("SELECT * FROM account_admission WHERE owner=?", (int(owner),)).fetchone()
            leases = conn.execute("SELECT * FROM account_work_leases WHERE owner=?", (int(owner),)).fetchall()
            return {"admission": dict(state) if state else None, "leases": [dict(row) for row in leases]}

    def reconcile(self, owner: int, *, stopped_process_token: str, lease_ids: tuple[str, ...] = (),
                  abandoned_claim_token: str | None = None, confirmed_stopped: bool = False) -> None:
        """Explicit operator-only repair after proving the entire writer stopped.

        Caller supplies the *exact* observed process/lease/claim tokens. This does
        not reopen admission, kill workers, infer death from age/PID, or reconcile
        jobs/other stores. Never expose as an untrusted request operation.
        """
        if not confirmed_stopped or not stopped_process_token:
            raise ValueError("external confirmation that the writer stopped is required")
        with self.transaction() as conn:
            for lease_id in lease_ids:
                conn.execute("DELETE FROM account_work_leases WHERE owner=? AND lease_id=? AND process_token=?",
                             (int(owner), lease_id, stopped_process_token))
            if abandoned_claim_token is not None:
                conn.execute("UPDATE account_admission SET claim_token=NULL,claim_epoch=NULL,claim_pid=NULL,"
                             "claim_process=NULL,claim_started=NULL WHERE owner=? AND claim_token=? AND claim_process=?",
                             (int(owner), abandoned_claim_token, stopped_process_token))


def current_store() -> AdmissionStore:
    # Resolve the actual connector, including runtime/test overrides, every time.
    # Existing handles remain pinned to their original database for completion.
    from src import auth
    with closing(auth._connect()) as conn:
        path = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    if not path:
        raise RuntimeError("shared account admission requires a file-backed auth database")
    return AdmissionStore(path)

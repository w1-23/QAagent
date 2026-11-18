"""Private bounded raw execution logs in the existing queue database.

Internal controller storage, not a model/API authorization boundary. The service
must recheck account + session + task access on every page. Byte offsets and
base64 preserve arbitrary output exactly; text previews are explicitly lossy.
Quotas bound live payload/reservations, not SQLite physical file size or the
queue's other history. Expiration never reclaims an active/unknown attempt.
"""
from __future__ import annotations

import base64
import hashlib

from .protocol import ExecutionRequest, TERMINAL, bounded_integer, identifier


_SCHEMA = """
CREATE TABLE IF NOT EXISTS code_execution_logs (
 execution_id TEXT NOT NULL REFERENCES code_executions(id), fence INTEGER NOT NULL,
 owner INTEGER NOT NULL, session TEXT NOT NULL, workspace TEXT NOT NULL,
 fingerprint TEXT NOT NULL, state TEXT NOT NULL, allocation INTEGER NOT NULL,
 stdout BLOB, stderr BLOB, stdout_sha256 TEXT, stderr_sha256 TEXT,
 capture_truncated INTEGER, created REAL NOT NULL, expires REAL NOT NULL,
 PRIMARY KEY(execution_id,fence));
CREATE INDEX IF NOT EXISTS code_logs_owner ON code_execution_logs(owner,state);
"""


class ExecutionLogs:
    def __init__(self, repository, *, owner_bytes=64 * 1024**2,
                 total_bytes=256 * 1024**2, ttl_sec=72 * 3600,
                 owner_records=1000, total_records=4096):
        self.repository = repository
        self.owner_bytes = bounded_integer(owner_bytes, 1024, 2**40, "owner_log_bytes")
        self.total_bytes = bounded_integer(total_bytes, 1024, 2**40, "total_log_bytes")
        self.ttl = bounded_integer(ttl_sec, 1, 30 * 86400, "log_ttl")
        self.owner_records = bounded_integer(owner_records, 1, 100000, "owner_log_records")
        self.total_records = bounded_integer(total_records, 1, 1000000, "total_log_records")
        with repository._connection() as conn:
            conn.executescript(_SCHEMA)

    @staticmethod
    def _expire(conn, now):
        terminal = sorted(TERMINAL)
        marks = ",".join("?" for _ in terminal)
        return conn.execute(
            "UPDATE code_execution_logs SET state='expired',allocation=0,stdout=NULL,stderr=NULL "
            "WHERE state!='expired' AND expires<=? AND execution_id IN "
            "(SELECT id FROM code_executions WHERE status IN (" + marks + "))",
            (now, *terminal)).rowcount

    def expire(self):
        """Logical payload GC only; never VACUUM or infer a stopped process."""
        with self.repository._connection(write=True) as conn:
            return self._expire(conn, self.repository._now())

    def reserve(self, conn, row):
        """Same transaction as worker intent, before any create/start side effect."""
        from .repository import ExecutionConflict
        request = ExecutionRequest.parse(row["request"])
        now = self.repository._now()
        current = self.repository._owned(conn, row["execution_id"], row["worker"], row["fence"], now)
        if current["fingerprint"] != request.fingerprint:
            raise ExecutionConflict("execution_log_scope_conflict")
        self._expire(conn, now)
        previous = conn.execute("SELECT 1 FROM code_execution_logs WHERE execution_id=? AND fence=?",
                                (row["execution_id"], row["fence"])).fetchone()
        if previous is not None:
            raise ExecutionConflict("execution_log_reservation_exists")
        total = conn.execute("SELECT COALESCE(SUM(allocation),0),COUNT(*) FROM code_execution_logs WHERE state!='expired'").fetchone()
        own = conn.execute("SELECT COALESCE(SUM(allocation),0),COUNT(*) FROM code_execution_logs WHERE state!='expired' AND owner=?",
                           (row["owner"],)).fetchone()
        budget = request.limits.log_bytes
        if (total[0] + budget > self.total_bytes or own[0] + budget > self.owner_bytes
                or total[1] >= self.total_records or own[1] >= self.owner_records):
            raise ExecutionConflict("execution_log_quota_exceeded")
        conn.execute("INSERT INTO code_execution_logs "
            "(execution_id,fence,owner,session,workspace,fingerprint,state,allocation,created,expires) "
            "VALUES(?,?,?,?,?,?,'reserved',?,?,?)",
            (row["execution_id"], row["fence"], row["owner"], row["session"], row["workspace"],
             request.fingerprint, budget, now, now + self.ttl))

    @staticmethod
    def _reference(row, stream):
        data = row[stream]
        return {"execution_id": row["execution_id"], "fence": row["fence"], "stream": stream,
                "size_bytes": len(data), "sha256": row[stream + "_sha256"],
                "capture_truncated": bool(row["capture_truncated"]),
                "expires_at": row["expires"], "offset_basis": "captured_bytes"}

    def store(self, row, *, stdout, stderr, capture_truncated):
        """All captured bytes or failure; retry cannot replace or extend retention."""
        from .repository import ExecutionConflict
        if type(stdout) is not bytes or type(stderr) is not bytes or type(capture_truncated) is not bool:
            raise ValueError("execution_raw_log_bytes_required")
        request = ExecutionRequest.parse(row["request"])
        if len(stdout) + len(stderr) > request.limits.log_bytes:
            raise ValueError("execution_log_capture_over_budget")
        with self.repository._connection(write=True) as conn:
            now = self.repository._now()
            self.repository._owned(conn, row["execution_id"], row["worker"], row["fence"], now)
            stored = conn.execute("SELECT * FROM code_execution_logs WHERE execution_id=? AND fence=?",
                                  (row["execution_id"], row["fence"])).fetchone()
            if (stored is None or stored["state"] == "expired"
                    or any(stored[key] != row[key] for key in ("owner", "session", "workspace", "fingerprint"))):
                raise ExecutionConflict("execution_log_reservation_required")
            if stored["state"] == "ready":
                if (stored["stdout"] != stdout or stored["stderr"] != stderr
                        or stored["capture_truncated"] != int(capture_truncated)):
                    raise ExecutionConflict("execution_log_content_conflict")
            else:
                conn.execute("UPDATE code_execution_logs SET state='ready',allocation=?,stdout=?,stderr=?,"
                    "stdout_sha256=?,stderr_sha256=?,capture_truncated=?,expires=? WHERE execution_id=? AND fence=?",
                    (len(stdout) + len(stderr), stdout, stderr, hashlib.sha256(stdout).hexdigest(),
                     hashlib.sha256(stderr).hexdigest(), int(capture_truncated), now + self.ttl,
                     row["execution_id"], row["fence"]))
                stored = conn.execute("SELECT * FROM code_execution_logs WHERE execution_id=? AND fence=?",
                                      (row["execution_id"], row["fence"])).fetchone()
            return {stream: self._reference(stored, stream) for stream in ("stdout", "stderr")}

    def read(self, execution_id, *, owner, session, fence, stream, offset=0, limit=16384):
        """Internal scoped page; caller additionally holds admission/task access."""
        identifier(execution_id)
        bounded_integer(fence, 1, 2**63 - 1, "log_fence")
        bounded_integer(offset, 0, 4 * 1024**2, "log_offset")
        bounded_integer(limit, 1, 65536, "log_page_bytes")
        if stream not in ("stdout", "stderr"):
            raise ValueError("execution_log_stream_invalid")
        with self.repository._connection(write=True) as conn:
            parent = self.repository._scope(conn, execution_id, owner, session)
            self._expire(conn, self.repository._now())
            row = conn.execute("SELECT * FROM code_execution_logs WHERE execution_id=? AND fence=? AND owner=? AND session=?",
                               (execution_id, fence, owner, session)).fetchone()
            if row is None or row["fingerprint"] != parent["fingerprint"] or row["workspace"] != parent["workspace"]:
                raise LookupError("execution_log_unavailable")
            # Expiration commits even when a read is refused; raising inside the
            # transaction would roll back the logical GC.
            state = row["state"]
            result = None
            if state == "ready":
                data = row[stream]
                if type(data) is not bytes or hashlib.sha256(data).hexdigest() != row[stream + "_sha256"]:
                    raise ValueError("execution_log_integrity_mismatch")
                if offset > len(data):
                    raise ValueError("execution_log_offset_out_of_range")
                chunk = data[offset:offset + limit]
                end = offset + len(chunk)
                result = {**self._reference(row, stream), "offset": offset,
                          "next_offset": end if end < len(data) else None,
                          "eof": end == len(data), "returned_bytes": len(chunk),
                          "data_base64": base64.b64encode(chunk).decode("ascii"),
                          "text_preview": chunk.decode("utf-8", errors="replace"),
                          "text_preview_encoding": "utf-8-replace"}
        if result is None:
            raise LookupError("execution_log_expired" if state == "expired" else "execution_log_not_captured")
        return result


def purge_owner(owner, *, claim):
    """Fenced account-deletion hook; never initialize an absent runtime store."""
    from src import config
    from src.core.account_leases import AccountDeletionPending
    from .repository import ExecutionRepository
    path = config.PROJECT_ROOT / "state" / "code_executions.sqlite3"
    with claim.store.transaction() as admission:
        claim.validate(admission)
        if claim.owner != owner:
            raise PermissionError("execution_log_deletion_owner_mismatch")
        if admission.execute("SELECT 1 FROM account_work_leases WHERE owner=? AND refs>0 LIMIT 1", (owner,)).fetchone():
            raise AccountDeletionPending("account has admitted work still running")
        if not path.exists():
            return
        repository = ExecutionRepository(path)
        with repository._connection(write=True) as conn:
            terminal = sorted(TERMINAL)
            marks = ",".join("?" for _ in terminal)
            if conn.execute("SELECT 1 FROM code_executions WHERE owner=? AND status NOT IN (" + marks + ") LIMIT 1", (owner, *terminal)).fetchone():
                raise AccountDeletionPending("account still has unconfirmed code executions")
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_execution_logs'").fetchone()
            if exists:
                conn.execute("DELETE FROM code_execution_logs WHERE owner=?", (owner,))
            # Queue receipts and outbox also contain private log excerpts. Drop
            # them under the same closed-admission/idle guard on account delete.
            conn.execute("UPDATE code_executions SET receipt=NULL WHERE owner=?", (owner,))
            conn.execute("UPDATE code_events SET payload='{}' WHERE execution_id IN (SELECT id FROM code_executions WHERE owner=?)", (owner,))

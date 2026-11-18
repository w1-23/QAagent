"""Journaled cleanup of exact, terminal offline attempts, never an age reaper.

Only a live controller may authorize its own stopped attempt. Maintenance resumes
that authorization; a terminal row or a missing container alone grants none.
Private staging is controller-owned and never mounted writable into user code.
This is not race-safe against a malicious host administrator or another trusted
controller violating the protocol. Ambiguous container removal remains pending.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat

from .handles import parse_handle, supported_handle, stopped_identity
from .oci import OCIError
from .protocol import TERMINAL, bounded_integer
from .repository import ExecutionConflict
from .results import _no_links


def _remove_staging(path, device, inode):
    """Shared exact private-tree removal, after independent durable authorization."""
    path = Path(path)
    _no_links(path)
    if not path.exists():
        return
    root_info = path.lstat()
    if (not stat.S_ISDIR(root_info.st_mode) or path.is_mount()
            or (str(root_info.st_dev), str(root_info.st_ino)) != (device, inode)):
        raise ExecutionConflict("execution_cleanup_staging_replaced")
    visited = 0
    for parent, directories, files in os.walk(path, followlinks=False):
        for name in (*directories, *files):
            visited += 1
            if visited > 1024:
                raise ExecutionConflict("execution_cleanup_tree_limit")
            item = Path(parent) / name
            info = item.lstat()
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                    or info.st_dev != root_info.st_dev or item.is_mount()
                    or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                raise ExecutionConflict("execution_cleanup_unsafe_tree")
    shutil.rmtree(path)


class ExecutionCleanup:
    def __init__(self, repository, backend, *, staging_root):
        self.repository, self.backend = repository, backend
        self.root = Path(staging_root)
        if not self.root.is_absolute():
            raise ValueError("execution_cleanup_private_root_required")
        _no_links(self.root)
        with repository._connection(write=True) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS code_cleanup_journal (
                execution_id TEXT NOT NULL, fence INTEGER NOT NULL, owner INTEGER NOT NULL,
                fingerprint TEXT NOT NULL, handle TEXT NOT NULL, container_id TEXT NOT NULL,
                staging_device TEXT NOT NULL, staging_inode TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                queue_fence INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(execution_id,fence))""")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(code_cleanup_journal)")}
            if "queue_fence" not in columns:
                conn.execute("ALTER TABLE code_cleanup_journal ADD COLUMN queue_fence INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE code_cleanup_journal SET queue_fence=fence WHERE queue_fence=0")

    def _directory(self, handle):
        # Derive from the configured root; do not delete arbitrary handle paths.
        expected = self.root / handle.execution_id
        _no_links(expected)
        if Path(handle.input_root) != expected:
            raise ExecutionConflict("execution_cleanup_staging_scope_mismatch")
        return expected

    def _scope(self, conn, execution_id, fence):
        journal = conn.execute("SELECT * FROM code_cleanup_journal WHERE execution_id=? AND fence=?",
                               (execution_id, fence)).fetchone()
        if journal is None:
            raise LookupError("execution_cleanup_authorization_missing")
        row = conn.execute("SELECT * FROM code_executions WHERE id=?", (execution_id,)).fetchone()
        if (row is None or row["status"] not in TERMINAL or row["fence"] != journal["queue_fence"]
                or row["owner"] != journal["owner"] or row["fingerprint"] != journal["fingerprint"]):
            raise ExecutionConflict("execution_cleanup_fence_conflict")
        handle = parse_handle(json.loads(journal["handle"]))
        if (handle.execution_id != execution_id or handle.fence != fence
                or handle.request.serialize() != row["request"]):
            raise ExecutionConflict("execution_cleanup_handle_conflict")
        return dict(journal), handle

    def terminal_callback(self, row, handle, lease):
        """Observe stop before the queue lock; return a same-transaction writer.

        Journal and terminal/outbox must either commit together or all roll back.
        The callback performs no daemon or filesystem I/O. The original live
        controller remains the only possible writer/start issuer throughout.
        """
        if not supported_handle(handle):
            raise ValueError("execution_cleanup_supported_handle_required")
        path = self._directory(handle)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or path.is_mount():
            raise ExecutionConflict("execution_cleanup_staging_invalid")
        observed = self.backend.inspect(handle, read_summary=False)
        try:
            identity = stopped_identity(handle, observed)
        except RuntimeError as exc:
            raise ExecutionConflict("execution_cleanup_stop_unconfirmed") from exc
        encoded = json.dumps(handle.as_dict(), sort_keys=True, separators=(",", ":"))
        def record(conn, terminal):
            if terminal["execution_id"] != row["execution_id"] or terminal["fence"] != row["fence"]:
                raise ExecutionConflict("execution_cleanup_terminal_identity_conflict")
            current = conn.execute("SELECT * FROM code_executions WHERE id=?", (row["execution_id"],)).fetchone()
            attempt = conn.execute("SELECT * FROM code_worker_attempts WHERE execution_id=? AND fence=?",
                                   (row["execution_id"], row["fence"])).fetchone()
            if (current is None or current["status"] not in TERMINAL or current["fence"] != row["fence"]
                    or current["worker"] != row["worker"] or current["fingerprint"] != row["fingerprint"]
                    or attempt is None or attempt["worker"] != row["worker"]
                    or attempt["lease_id"] != lease.lease_id or attempt["process_token"] != lease.process_token
                    or parse_handle(json.loads(attempt["handle"])) != handle
                    or handle.execution_id != row["execution_id"] or handle.fence != row["fence"]
                    or handle.request.serialize() != current["request"]):
                raise ExecutionConflict("execution_cleanup_live_attempt_mismatch")
            old = conn.execute("SELECT * FROM code_cleanup_journal WHERE execution_id=? AND fence=?",
                               (row["execution_id"], row["fence"])).fetchone()
            if old is not None:
                if old["handle"] != encoded or old["container_id"] != identity:
                    raise ExecutionConflict("execution_cleanup_journal_conflict")
                return
            now = self.repository._now()
            conn.execute("INSERT INTO code_cleanup_journal VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["execution_id"], row["fence"], row["owner"], row["fingerprint"], encoded,
                 identity, str(info.st_dev), str(info.st_ino), "container_pending", now, now, row["fence"]))
        return record

    def recovery_callback(self, row, handle, observed):
        """Prepare opt-in GC to commit WITH exact stopped-worker recovery.

        The recovery transaction must first insert its independently validated
        external-writer-stop journal. No terminal row alone grants this path.
        """
        try:
            identity = stopped_identity(handle, observed)
        except RuntimeError as exc:
            raise ExecutionConflict("execution_cleanup_recovery_stop_required") from exc
        path = self._directory(handle)
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or path.is_mount():
            raise ExecutionConflict("execution_cleanup_staging_invalid")
        encoded = json.dumps(handle.as_dict(), sort_keys=True, separators=(",", ":"))
        def record(conn, terminal):
            proof = conn.execute("SELECT * FROM code_recovery_journal WHERE execution_id=? AND fence=?",
                                 (row["execution_id"], row["fence"])).fetchone()
            attempt = conn.execute("SELECT * FROM code_worker_attempts WHERE execution_id=? AND fence=?",
                                   (row["execution_id"], row["fence"])).fetchone()
            if (proof is None or attempt is None or terminal["execution_id"] != row["execution_id"]
                    or terminal["status"] not in TERMINAL or terminal["fence"] != row["fence"] + 1
                    or proof["container_id"] != identity or proof["owner"] != row["owner"]
                    or proof["fingerprint"] != row["fingerprint"] or proof["worker"] != row["worker"]
                    or proof["lease_id"] != attempt["lease_id"] or not proof["process_token"]
                    or parse_handle(json.loads(attempt["handle"])) != handle
                    or handle.execution_id != row["execution_id"] or handle.fence != row["fence"]
                    or handle.request.serialize() != row["request"]):
                raise ExecutionConflict("execution_cleanup_recovery_journal_required")
            now = self.repository._now()
            conn.execute("INSERT INTO code_cleanup_journal VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["execution_id"], row["fence"], row["owner"], row["fingerprint"], encoded,
                 identity, str(info.st_dev), str(info.st_ino), "container_pending", now, now, terminal["fence"]))
        return record

    def authorize(self, row, handle, lease):
        """Compatibility for a live controller's previously committed attempt."""
        record = self.terminal_callback(row, handle, lease)
        with self.repository._connection(write=True) as conn:
            record(conn, row)

    def _mark(self, execution_id, fence, state):
        with self.repository._connection(write=True) as conn:
            self._scope(conn, execution_id, fence)
            conn.execute("UPDATE code_cleanup_journal SET state=?,updated=? WHERE execution_id=? AND fence=?",
                         (state, self.repository._now(), execution_id, fence))

    def resume(self, execution_id, fence):
        with self.repository._connection() as conn:
            journal, handle = self._scope(conn, execution_id, fence)
        if journal["state"] == "staging_removed":
            return True
        path = self._directory(handle)
        if journal["state"] == "container_pending":
            observed = self.backend.inspect(handle, read_summary=False)
            try:
                matching_stop = stopped_identity(handle, observed) == journal["container_id"]
            except RuntimeError:
                matching_stop = False
            removed = self.backend.remove(handle) is True if matching_stop else False
            if not removed:
                # The durable journal already records exact identity + confirmed
                # physical stop + removal intent. Fresh absence of both the old
                # full ID and name may close an interrupted rm; missing inspect
                # alone, errors or a replacement name never suffice.
                confirm = getattr(self.backend, "confirm_removed", None)
                if confirm is None or confirm(handle, container_id=journal["container_id"]) is not True:
                    return False
            self._mark(execution_id, fence, "container_removed")
        elif journal["state"] != "container_removed":
            raise ExecutionConflict("execution_cleanup_state_invalid")
        _remove_staging(path, journal["staging_device"], journal["staging_inode"])
        self._mark(execution_id, fence, "staging_removed")
        return True

    def maintain(self, *, limit=4):
        """Bounded pass over already-authorized work; never discover age orphans."""
        bounded_integer(limit, 1, 32, "cleanup_limit")
        with self.repository._connection() as conn:
            pending = list(conn.execute("SELECT execution_id,fence FROM code_cleanup_journal "
                                        "WHERE state!='staging_removed' ORDER BY updated LIMIT ?", (limit,)))
        completed = 0
        for row in pending:
            try:
                completed += self.resume(row["execution_id"], row["fence"]) is True
            except (OSError, ValueError, LookupError, OCIError):
                # Retain authorization and artifacts for later maintenance. No
                # uncertain cleanup failure may rewrite a committed user result.
                pass
        return completed

"""Task manifests in the file-library DB; session_files remains byte authority.

No models, tools, API registration, byte copies or physical GC. Public repository
operations admit an account lease and enforce the session manager's ownership.
A manifest revision covers bindings, retention changes and explicit touch; opening
and listing are observations. File versions are immutable, including their pins.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid

MAX_FILE_BYTES = 64 * 1024**2
MAX_WORKSPACE_BYTES = 256 * 1024**2  # Sum of every retained binding version.
MAX_PATHS = 128
MAX_VERSIONS = 256
IDLE_SECONDS = 7 * 86400
RESOURCE_KIND = "task_workspace_file"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,128}")
_HASH = re.compile(r"[a-f0-9]{64}")
_FILE_ID = re.compile(r"[a-f0-9]{32}")


class WorkspaceConflict(ValueError):
    def __init__(self, latest_revision):
        super().__init__("workspace_revision_conflict")
        self.latest_revision = latest_revision


def _token(value):
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise ValueError("workspace_invalid_identity")
    return value


def virtual_path(path):
    """Logical paths only; binary suffixes are unrestricted, input is read only."""
    if not isinstance(path, str) or len(path) > 240:
        raise ValueError("workspace_invalid_path")
    parts = path.split("/")
    if (len(parts) < 3 or parts[0] or parts[1] not in {"input", "workspace", "outputs"}
            or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. -]{0,119}", p)
                   or p.endswith((".", " ")) or ".." in p for p in parts[2:])):
        raise ValueError("workspace_invalid_path")
    return path


def _no_links(path):
    # Same lexical-ancestor rule as agent.workspace_storage, without importing
    # the text/model backend. Windows junctions and hard-linked files are denied.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise PermissionError("workspace_link_denied")


def _json_object(value):
    """Strict bounded JSON object, rejecting coercions, NaN and deep structures."""
    count = 0

    def visit(item, depth):
        nonlocal count
        count += 1
        if depth > 8 or count > 256:
            raise ValueError("workspace_metadata_limit")
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("workspace_invalid_json")
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        elif item is None or type(item) in (str, bool, int):
            pass
        elif type(item) is float and math.isfinite(item):
            pass
        else:
            raise ValueError("workspace_invalid_json")
    if type(value) is not dict:
        raise ValueError("workspace_json_object_required")
    visit(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False)
        if len(encoded.encode("utf-8")) > 16384:
            raise ValueError("workspace_metadata_limit")
    except (UnicodeError, OverflowError) as exc:
        raise ValueError("workspace_invalid_json") from exc
    return encoded


def _page(limit, offset):
    if (type(limit) is not int or not 1 <= limit <= 100
            or type(offset) is not int or not 0 <= offset <= 10000):
        raise ValueError("workspace_invalid_page")


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS task_workspaces (
        workspace_id TEXT PRIMARY KEY, owner INTEGER NOT NULL, session TEXT NOT NULL,
        task_id TEXT NOT NULL, revision INTEGER NOT NULL, saved INTEGER NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
        touched_at REAL NOT NULL, metadata TEXT NOT NULL,
        UNIQUE(owner,session,task_id))""",
    """CREATE TABLE IF NOT EXISTS task_workspace_requests (
        owner INTEGER NOT NULL, session TEXT NOT NULL, request_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, workspace_id TEXT NOT NULL,
        PRIMARY KEY(owner,session,request_id))""",
    """CREATE TABLE IF NOT EXISTS task_workspace_current (
        owner INTEGER NOT NULL, session TEXT NOT NULL, workspace_id TEXT NOT NULL,
        PRIMARY KEY(owner,session))""",
    """CREATE TABLE IF NOT EXISTS task_workspace_files (
        workspace_id TEXT NOT NULL, path TEXT NOT NULL, version INTEGER NOT NULL,
        file_id TEXT NOT NULL, sha256 TEXT NOT NULL, mime_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL, source TEXT NOT NULL, parents TEXT NOT NULL,
        created_at REAL NOT NULL, PRIMARY KEY(workspace_id,path,version))""",
    """CREATE TABLE IF NOT EXISTS task_workspace_inflight (
        workspace_id TEXT NOT NULL, operation_id TEXT NOT NULL,
        PRIMARY KEY(workspace_id,operation_id))""",
    "CREATE INDEX IF NOT EXISTS idx_task_workspaces_owner ON task_workspaces(owner,session,touched_at)",
)


def purge_owner(owner, *, claim):
    """Internal account-deletion hook, called before session_files byte purge.

    Caller holds the existing account deletion_lock and passes its live fenced
    claim, after admission is closed/identity disabled. This hook validates that
    claim against the current auth store, requires no admitted work, and removes
    only this module's metadata and pins in one files transaction. It never
    acquires a new account lease, opens admission, or removes file bytes.
    """
    from src.core.account_leases import assert_idle, current_store
    from src.session import files
    if type(owner) is not int or owner <= 0:
        raise ValueError("workspace_invalid_owner")
    if (claim is None or claim.owner != owner
            or claim.store.path != current_store().path):
        raise PermissionError("workspace_deletion_claim_required")
    with claim.store.transaction() as conn:
        claim.validate(conn)
    assert_idle(owner)
    for suffix in ("", "-wal", "-shm", "-journal"):
        _no_links(Path(str(files.DB_PATH) + suffix))
    if not files.DB_PATH.exists():
        return 0
    with files._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_workspaces'").fetchone():
            return 0
        conn.execute("DELETE FROM session_file_resource_refs WHERE owner_user_id=? AND resource_kind=?",
                     (owner, RESOURCE_KIND))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_execution_acceptance'").fetchone():
            conn.execute("DELETE FROM task_execution_acceptance WHERE owner=?", (owner,))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_environment_revisions'").fetchone():
            conn.execute("DELETE FROM session_file_resource_refs WHERE owner_user_id=? AND resource_kind='task_environment'", (owner,))
            conn.execute("DELETE FROM task_environment_revisions WHERE workspace IN "
                         "(SELECT workspace_id FROM task_workspaces WHERE owner=?)", (owner,))
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_environment_preparations'").fetchone():
            conn.execute("DELETE FROM task_environment_preparations WHERE workspace IN "
                         "(SELECT workspace_id FROM task_workspaces WHERE owner=?)", (owner,))
        for table in ("task_workspace_inflight", "task_workspace_files"):
            conn.execute(f"DELETE FROM {table} WHERE workspace_id IN (SELECT workspace_id FROM task_workspaces WHERE owner=?)",
                         (owner,))
        for table in ("task_workspace_requests", "task_workspace_current"):
            conn.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))
        return conn.execute("DELETE FROM task_workspaces WHERE owner=?", (owner,)).rowcount


class Repository:
    """Authenticated manifest repository. session_manager is a trusted dependency.

    Constructing this object does no I/O. Omit session_manager to use the existing
    SessionManager lazily. All operations use the existing files._connect store.
    """
    def __init__(self, session_manager=None):
        self._manager = session_manager

    @contextmanager
    def _transaction(self, owner, session):
        from src.core.account_leases import account_lease
        from src.session import files
        if type(owner) is not int or owner <= 0 or not isinstance(session, str) or not files.valid_session(session):
            raise PermissionError("workspace_scope_denied")
        with account_lease(owner):
            manager = self._manager
            if manager is None:
                from src.session.manager import SessionManager
                manager = SessionManager()
            if not manager.session_belongs_to(session, owner):
                raise PermissionError("workspace_session_denied")
            for suffix in ("", "-wal", "-shm", "-journal"):
                _no_links(Path(str(files.DB_PATH) + suffix))
            _no_links(files.FILES_ROOT)
            files.init_store()
            with files._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for statement in _SCHEMA:
                    conn.execute(statement)
                yield conn, manager

    @staticmethod
    def _workspace(conn, owner, session, workspace_id):
        _token(workspace_id)
        row = conn.execute("SELECT * FROM task_workspaces WHERE workspace_id=? AND owner=? AND session=?",
                           (workspace_id, owner, session)).fetchone()
        if row is None:
            raise LookupError("workspace_unavailable")
        return row

    @staticmethod
    def _cas(row, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError("workspace_expected_revision_required")
        if row["revision"] != revision:
            raise WorkspaceConflict(row["revision"])

    @staticmethod
    def _manifest(row):
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        result["saved"], result["pinned"] = bool(result["saved"]), bool(result["pinned"])
        return result

    def create(self, owner, session, *, task_id, request_id, saved=False, metadata=None):
        """Create a NEW task and select it as current; exact request retry is inert.

        Reusing a task_id under a different request is a conflict. Use open or
        touch to resume. A retry never changes the current task selected later.
        """
        _token(task_id)
        _token(request_id)
        if type(saved) is not bool:
            raise ValueError("workspace_invalid_saved")
        metadata = _json_object({} if metadata is None else metadata)
        fingerprint = hashlib.sha256(json.dumps([task_id, saved, metadata]).encode()).hexdigest()
        with self._transaction(owner, session) as (conn, _):
            prior = conn.execute("SELECT * FROM task_workspace_requests WHERE owner=? AND session=? AND request_id=?",
                                 (owner, session, request_id)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise ValueError("workspace_create_request_conflict")
                return self._manifest(self._workspace(conn, owner, session, prior["workspace_id"]))
            if conn.execute("SELECT 1 FROM task_workspaces WHERE owner=? AND session=? AND task_id=?",
                            (owner, session, task_id)).fetchone():
                raise ValueError("workspace_task_exists")
            wid, now = uuid.uuid4().hex, time.time()
            conn.execute("INSERT INTO task_workspaces VALUES(?,?,?,?,?,?,0,?,?,?)",
                         (wid, owner, session, task_id, 1, int(saved), now, now, metadata))
            conn.execute("INSERT INTO task_workspace_requests VALUES(?,?,?,?,?)",
                         (owner, session, request_id, fingerprint, wid))
            self._select_current(conn, owner, session, wid)
            return self._manifest(self._workspace(conn, owner, session, wid))

    @staticmethod
    def _select_current(conn, owner, session, wid):
        conn.execute("INSERT INTO task_workspace_current VALUES(?,?,?) ON CONFLICT(owner,session) "
                     "DO UPDATE SET workspace_id=excluded.workspace_id", (owner, session, wid))

    def open(self, owner, session, workspace_id=None):
        """Observe explicit or current task metadata; does not select/touch it."""
        with self._transaction(owner, session) as (conn, _):
            if workspace_id is None:
                current = conn.execute("SELECT workspace_id FROM task_workspace_current WHERE owner=? AND session=?",
                                       (owner, session)).fetchone()
                if current is None:
                    raise LookupError("workspace_current_unavailable")
                workspace_id = current[0]
            return self._manifest(self._workspace(conn, owner, session, workspace_id))

    def list(self, owner, session, *, limit=50, offset=0):
        _page(limit, offset)
        with self._transaction(owner, session) as (conn, _):
            rows = conn.execute("SELECT * FROM task_workspaces WHERE owner=? AND session=? "
                                "ORDER BY created_at,workspace_id LIMIT ? OFFSET ?",
                                (owner, session, limit + 1, offset)).fetchall()
            return {"items": [self._manifest(r) for r in rows[:limit]],
                    "next_offset": offset + limit if len(rows) > limit else None}

    def touch(self, owner, session, workspace_id, *, expected_revision, make_current=True):
        if type(make_current) is not bool:
            raise ValueError("workspace_invalid_current")
        with self._transaction(owner, session) as (conn, _):
            row = self._workspace(conn, owner, session, workspace_id)
            self._cas(row, expected_revision)
            conn.execute("UPDATE task_workspaces SET touched_at=?,revision=revision+1 WHERE workspace_id=?",
                         (time.time(), workspace_id))
            if make_current:
                self._select_current(conn, owner, session, workspace_id)
            return self._manifest(self._workspace(conn, owner, session, workspace_id))

    def set_retention(self, owner, session, workspace_id, *, expected_revision, saved, pinned):
        if type(saved) is not bool or type(pinned) is not bool:
            raise ValueError("workspace_invalid_retention")
        with self._transaction(owner, session) as (conn, _):
            row = self._workspace(conn, owner, session, workspace_id)
            self._cas(row, expected_revision)
            conn.execute("UPDATE task_workspaces SET saved=?,pinned=?,touched_at=?,revision=revision+1 WHERE workspace_id=?",
                         (int(saved), int(pinned), time.time(), workspace_id))
            return self._manifest(self._workspace(conn, owner, session, workspace_id))

    @staticmethod
    def _file_ref(row):
        result = dict(row)
        result["source"], result["parents"] = json.loads(result["source"]), json.loads(result["parents"])
        result["readonly"] = result["path"].startswith("/input/")
        return result

    @staticmethod
    def _bytes(row, owner):
        from src.session import files
        path = Path(row["storage_path"])
        _no_links(path)
        # The file library authorizes by account, including files whose original
        # session was deleted. Only the destination task's session must be live.
        if (not path.is_absolute() or row["owner_user_id"] != owner
                or not files._allowed_file_path(row["session_id"], path, row["origin"], owner)):
            raise PermissionError("workspace_file_scope_denied")
        if (type(row["size_bytes"]) is not int or not 0 <= row["size_bytes"] <= MAX_FILE_BYTES
                or not isinstance(row["sha256"], str) or not _HASH.fullmatch(row["sha256"])):
            raise ValueError("workspace_integrity_or_quota")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_NONBLOCK", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != row["size_bytes"]:
                raise ValueError("workspace_file_type_or_size")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            _no_links(path)
            current = path.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise PermissionError("workspace_file_changed")
        finally:
            os.close(fd)
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("workspace_integrity_mismatch")
        return data

    def bind(self, owner, session, workspace_id, *, path, file_id, expected_revision,
             expected_sha256, source=None, parents=None):
        """Bind an explicitly authorized library file, pin + version in one commit.

        Parents are exact {path,version,file_id,sha256} references to this task.
        Every historical version remains pinned; /input paths cannot be replaced.
        """
        virtual_path(path)
        if not isinstance(file_id, str) or not _FILE_ID.fullmatch(file_id):
            raise ValueError("workspace_invalid_file_id")
        if not isinstance(expected_sha256, str) or not _HASH.fullmatch(expected_sha256):
            raise ValueError("workspace_expected_hash_required")
        source_json = _json_object({} if source is None else source)
        parents = [] if parents is None else parents
        if type(parents) is not list or len(parents) > 32:
            raise ValueError("workspace_invalid_parents")
        # Canonicalize and detach caller-owned mutable objects before I/O.
        parents = json.loads(_json_object({"refs": parents}))["refs"]
        from src.session import files
        with self._transaction(owner, session) as (conn, _):
            workspace = self._workspace(conn, owner, session, workspace_id)
            self._cas(workspace, expected_revision)
            latest = conn.execute("SELECT version FROM task_workspace_files WHERE workspace_id=? AND path=? "
                                  "ORDER BY version DESC LIMIT 1", (workspace_id, path)).fetchone()
            if latest and path.startswith("/input/"):
                raise PermissionError("workspace_mount_readonly")
            count, paths, used = conn.execute("SELECT COUNT(*),COUNT(DISTINCT path),COALESCE(SUM(size_bytes),0) "
                                             "FROM task_workspace_files WHERE workspace_id=?", (workspace_id,)).fetchone()
            row = conn.execute("SELECT * FROM session_files WHERE file_id=? AND owner_user_id=?",
                               (file_id, owner)).fetchone()
            if row is None:
                raise LookupError("workspace_file_unavailable")
            if row["sha256"] != expected_sha256:
                raise ValueError("workspace_reference_hash_changed")
            if type(row["size_bytes"]) is not int or not 0 <= row["size_bytes"] <= MAX_FILE_BYTES:
                raise ValueError("workspace_integrity_or_quota")
            if count >= MAX_VERSIONS or (not latest and paths >= MAX_PATHS) or used + row["size_bytes"] > MAX_WORKSPACE_BYTES:
                raise ValueError("workspace_quota_exceeded")
            self._bytes(row, owner)
            for ref in parents:
                if type(ref) is not dict or set(ref) != {"path", "version", "file_id", "sha256"}:
                    raise ValueError("workspace_invalid_parent")
                virtual_path(ref["path"])
                if (type(ref["version"]) is not int or ref["version"] < 1
                        or not isinstance(ref["file_id"], str) or not _FILE_ID.fullmatch(ref["file_id"])
                        or not isinstance(ref["sha256"], str) or not _HASH.fullmatch(ref["sha256"])):
                    raise ValueError("workspace_invalid_parent")
                parent = conn.execute("SELECT 1 FROM task_workspace_files WHERE workspace_id=? AND path=? "
                                      "AND version=? AND file_id=? AND sha256=?",
                                      (workspace_id, ref["path"], ref["version"], ref["file_id"], ref["sha256"])).fetchone()
                if parent is None:
                    raise ValueError("workspace_parent_unavailable")
            version, now = (latest[0] + 1 if latest else 1), time.time()
            provenance = _json_object({"session_id": row["session_id"], "origin": row["origin"],
                                      "producer_tool": row["producer_tool"], "metadata": json.loads(source_json)})
            pin_id = self._pin_id(workspace_id, path, version)
            files._replace_resource_references_locked(conn, owner, RESOURCE_KIND, pin_id, [file_id])
            conn.execute("INSERT INTO task_workspace_files VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (workspace_id, path, version, file_id, row["sha256"], row["mime_type"],
                          row["size_bytes"], provenance, json.dumps(parents), now))
            conn.execute("UPDATE task_workspaces SET revision=revision+1,touched_at=? WHERE workspace_id=?",
                         (now, workspace_id))
            conn.execute("UPDATE session_files SET last_accessed_at=? WHERE file_id=?", (now, file_id))
            return {"revision": expected_revision + 1, "ref": self._file_ref(conn.execute(
                "SELECT * FROM task_workspace_files WHERE workspace_id=? AND path=? AND version=?",
                (workspace_id, path, version)).fetchone())}

    @staticmethod
    def _pin_id(workspace_id, path, version):
        return workspace_id + ":" + hashlib.sha256(path.encode()).hexdigest() + ":" + str(version)

    def list_files(self, owner, session, workspace_id, *, limit=50, offset=0, history=False):
        _page(limit, offset)
        if type(history) is not bool:
            raise ValueError("workspace_invalid_history")
        with self._transaction(owner, session) as (conn, _):
            self._workspace(conn, owner, session, workspace_id)
            clause = "" if history else " AND version=(SELECT MAX(x.version) FROM task_workspace_files x WHERE x.workspace_id=f.workspace_id AND x.path=f.path)"
            rows = conn.execute("SELECT * FROM task_workspace_files f WHERE workspace_id=?" + clause +
                                " ORDER BY path,version LIMIT ? OFFSET ?", (workspace_id, limit + 1, offset)).fetchall()
            return {"items": [self._file_ref(r) for r in rows[:limit]],
                    "next_offset": offset + limit if len(rows) > limit else None}

    def snapshot(self, owner, session, workspace_id, *, task_id, expected_revision):
        """Resolve one authorized task revision to a complete bounded JSON list.

        Returns {workspace: manifest, files: [{path,file_id,version,sha256,size_bytes}]}.
        The transaction checks the current revision, library metadata and each
        immutable version's pin. No bytes are read here: the execution boundary
        must verify the recorded hash when materializing bytes, e.g. read_bytes
        with the returned path/version/expected_sha256. Historical pins persist
        after later bindings, so the captured file references remain stable.
        """
        _token(task_id)
        with self._transaction(owner, session) as (conn, _):
            workspace = self._workspace(conn, owner, session, workspace_id)
            if workspace["task_id"] != task_id:
                raise PermissionError("workspace_task_denied")
            self._cas(workspace, expected_revision)
            rows = conn.execute("""SELECT f.* FROM task_workspace_files f WHERE workspace_id=?
                AND version=(SELECT MAX(x.version) FROM task_workspace_files x
                    WHERE x.workspace_id=f.workspace_id AND x.path=f.path)
                ORDER BY path LIMIT ?""", (workspace_id, MAX_PATHS + 1)).fetchall()
            if len(rows) > MAX_PATHS or sum(row["size_bytes"] for row in rows) > MAX_WORKSPACE_BYTES:
                raise ValueError("workspace_quota_exceeded")
            refs = []
            for row in rows:
                file = conn.execute("SELECT * FROM session_files WHERE file_id=? AND owner_user_id=?",
                                    (row["file_id"], owner)).fetchone()
                if file is None or any(file[k] != row[k] for k in ("sha256", "size_bytes", "mime_type")):
                    raise ValueError("workspace_index_integrity")
                pin = conn.execute("SELECT sha256 FROM session_file_resource_refs WHERE owner_user_id=? "
                                   "AND resource_kind=? AND resource_id=? AND file_id=?",
                                   (owner, RESOURCE_KIND, self._pin_id(workspace_id, row["path"], row["version"]),
                                    row["file_id"])).fetchone()
                if pin is None or pin[0] != row["sha256"]:
                    raise ValueError("workspace_pin_integrity")
                refs.append({key: row[key] for key in ("path", "file_id", "version", "sha256", "size_bytes")})
            return {"workspace": self._manifest(workspace), "files": refs}

    def read_bytes(self, owner, session, workspace_id, *, path, version=None, expected_sha256=None):
        virtual_path(path)
        if version is not None and (type(version) is not int or version < 1):
            raise ValueError("workspace_invalid_version")
        if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not _HASH.fullmatch(expected_sha256)):
            raise ValueError("workspace_invalid_hash")
        with self._transaction(owner, session) as (conn, _):
            self._workspace(conn, owner, session, workspace_id)
            row = conn.execute("SELECT * FROM task_workspace_files WHERE workspace_id=? AND path=?" +
                               (" AND version=?" if version is not None else "") + " ORDER BY version DESC LIMIT 1",
                               (workspace_id, path, version) if version is not None else (workspace_id, path)).fetchone()
            if row is None:
                raise LookupError("workspace_file_unavailable")
            if expected_sha256 is not None and expected_sha256 != row["sha256"]:
                raise ValueError("workspace_reference_hash_changed")
            file = conn.execute("SELECT * FROM session_files WHERE file_id=? AND owner_user_id=?",
                                (row["file_id"], owner)).fetchone()
            if file is None:
                raise LookupError("workspace_file_unavailable")
            if any(file[k] != row[k] for k in ("sha256", "size_bytes", "mime_type")):
                raise ValueError("workspace_index_integrity")
            data = self._bytes(file, owner)
            conn.execute("UPDATE session_files SET last_accessed_at=? WHERE file_id=?", (time.time(), row["file_id"]))
            return data

    def retain_execution(self, owner, session, workspace_id, *, operation_id, task_id, expected_revision):
        """Persist an explicit execution hold before submitting to another DB.

        Scope, task and revision checks share the insert transaction. Returns True
        for a new hold, False for the existing exact key. Does not touch revision.
        Submission exceptions do not prove absence of a queue commit: callers
        release only after a terminal outcome or confirmed reconciliation.
        """
        _token(operation_id)
        _token(task_id)
        with self._transaction(owner, session) as (conn, _):
            workspace = self._workspace(conn, owner, session, workspace_id)
            if workspace["task_id"] != task_id:
                raise PermissionError("workspace_task_denied")
            self._cas(workspace, expected_revision)
            return conn.execute("INSERT OR IGNORE INTO task_workspace_inflight(workspace_id,operation_id) VALUES(?,?)",
                                (workspace_id, operation_id)).rowcount == 1

    def release_execution(self, owner, session, workspace_id, *, operation_id):
        """Idempotently release the exact key under normal lease/scope checks.

        Returns True if a hold was removed, False if already absent. No expiry or
        exception-driven cleanup is performed by this repository.
        """
        _token(operation_id)
        with self._transaction(owner, session) as (conn, _):
            self._workspace(conn, owner, session, workspace_id)
            return conn.execute("DELETE FROM task_workspace_inflight WHERE workspace_id=? AND operation_id=?",
                                (workspace_id, operation_id)).rowcount == 1

    @contextmanager
    def inflight(self, owner, session, workspace_id):
        """Hold admission for actual work and persist an expiry barrier until exit.

        A crashed process leaves a conservative barrier for explicit reconciliation;
        selection never silently ages out a possibly running operation.
        """
        from src.core.account_leases import account_lease
        with account_lease(owner):
            operation_id = uuid.uuid4().hex
            with self._transaction(owner, session) as (conn, _):
                self._workspace(conn, owner, session, workspace_id)
                conn.execute("INSERT INTO task_workspace_inflight VALUES(?,?)", (workspace_id, operation_id))
            try:
                yield operation_id
            finally:
                # Cleanup under the already admitted outer lease, even if the
                # account was disabled during the operation. No new publication.
                from src.session import files
                with files._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.execute("DELETE FROM task_workspace_inflight WHERE workspace_id=? AND operation_id=?",
                                 (workspace_id, operation_id))

    def select_expired(self, owner, session, *, limit=50, offset=0):
        """Observe candidates only; preserve ALL metadata, pins and bytes.

        Saved projects, explicit workspace pins, inflight operations and external
        file-resource dependencies protect a workspace. Its own immutable version
        pins stay in place and do not disqualify it from metadata expiry selection.
        """
        _page(limit, offset)
        with self._transaction(owner, session) as (conn, _):
            rows = conn.execute("""SELECT w.* FROM task_workspaces w
                WHERE w.owner=? AND w.session=? AND w.saved=0 AND w.pinned=0 AND w.touched_at<=?
                AND NOT EXISTS(SELECT 1 FROM task_workspace_inflight i WHERE i.workspace_id=w.workspace_id)
                AND NOT EXISTS(SELECT 1 FROM task_workspace_files f JOIN session_file_resource_refs r
                    ON r.file_id=f.file_id AND r.owner_user_id=w.owner
                    WHERE f.workspace_id=w.workspace_id AND (r.resource_kind<>? OR r.resource_id NOT LIKE w.workspace_id||':%'))
                ORDER BY w.touched_at,w.workspace_id LIMIT ? OFFSET ?""",
                (owner, session, time.time() - IDLE_SECONDS, RESOURCE_KIND, limit + 1, offset)).fetchall()
            return {"items": [self._manifest(r) for r in rows[:limit]],
                    "next_offset": offset + limit if len(rows) > limit else None,
                    "selection_only": True}

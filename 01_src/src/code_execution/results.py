"""Host byte validation and transactional task/library acceptance.

Worker paths, MIME types and hashes are observations, never authority. This
module only accepts explicitly requested logical outputs from a trusted private
staging directory, after the controller has stopped all possible writers. It
never extracts archives, imports output, runs code or publishes a download link.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

from src.core.account_leases import acquire_account_lease
from src.workspaces import repository as workspace_module

from .protocol import ExecutionRequest, identifier, bounded_integer


@dataclass(frozen=True)
class OutputBlob:
    path: str
    data: bytes = field(repr=False)

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()


def _no_links(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise PermissionError("execution_output_link_denied")


def collect_outputs(staging: Path, request: ExecutionRequest) -> tuple[OutputBlob, ...]:
    """Read bounded bytes from a controller-owned, quiescent staging directory.

    The controller must first ensure the transfer and sandbox writers stopped.
    A changed inode/stat, link, device, pipe, socket or extra file is rejected;
    no filename supplied by an archive is extracted by this function.
    """
    root = Path(staging)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("execution_private_staging_required")
    _no_links(root)
    expected = {path.lstrip("/") for path in request.outputs}
    seen, pending, visited = set(), [root], 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > request.limits.output_files * 16 + 32:
                    raise ValueError("execution_output_tree_limit")
                path = Path(entry.path)
                _no_links(path)
                # Windows DirEntry's cached stat can omit the link count.
                # Query the file itself before trusting regular-file metadata.
                info = path.stat(follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    relative = path.relative_to(root).as_posix()
                    if relative not in expected:
                        raise ValueError("execution_undeclared_output")
                    seen.add(relative)
                else:
                    raise PermissionError("execution_output_type_denied")
    if seen != expected:
        raise ValueError("execution_declared_output_missing")
    result, used = [], 0
    for logical in request.outputs:
        path = root / logical.lstrip("/")
        _no_links(path)
        before = path.stat(follow_symlinks=False)
        remaining = min(request.limits.output_bytes - used, workspace_module.MAX_FILE_BYTES)
        if before.st_size > remaining:
            raise ValueError("execution_output_byte_limit")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        try:
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
                raise PermissionError("execution_output_changed")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(remaining + 1)
            after_fd = os.fstat(fd)
            after = path.stat(follow_symlinks=False)
            def stable(info):
                return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink)
            # Python/Windows stat(path) and fstat(fd) expose different ctime
            # semantics; compare ctime only within the same API. Access time may
            # legitimately change because of our own read.
            if (stable(before) != stable(opened) or stable(opened) != stable(after_fd)
                    or stable(after_fd) != stable(after)
                    or before.st_ctime_ns != after.st_ctime_ns
                    or opened.st_ctime_ns != after_fd.st_ctime_ns):
                raise PermissionError("execution_output_changed")
        finally:
            os.close(fd)
        _no_links(path)
        if len(data) > remaining or len(data) != opened.st_size:
            raise ValueError("execution_output_byte_limit")
        used += len(data)
        result.append(OutputBlob(logical, data))
    return tuple(result)


def accept_outputs(request: ExecutionRequest, *, execution_id: str, fence: int,
                   blobs: tuple[OutputBlob, ...], workspace_repository) -> dict:
    """Internal controller call under the queue's collecting/fence transaction.

    Admission is reacquired at the ORIGINAL authorization epoch. File index,
    task versions, all resource pins and the idempotent acceptance receipt commit
    together in the existing files database. Bytes have one library location;
    rollback removes only newly created, unregistered objects. A process crash
    can leave private orphan bytes, for GC, never partially indexed outputs.
    All files start internal; explicit product delivery remains a separate step.
    """
    identifier(execution_id)
    bounded_integer(fence, 1, 2**63 - 1, "fence")
    if (type(blobs) is not tuple or len(blobs) > request.limits.output_files
            or any(type(blob) is not OutputBlob or type(blob.data) is not bytes for blob in blobs)
            or tuple(blob.path for blob in blobs) != request.outputs
            or sum(len(blob.data) for blob in blobs) > request.limits.output_bytes
            or any(len(blob.data) > workspace_module.MAX_FILE_BYTES for blob in blobs)):
        raise ValueError("execution_output_snapshot_invalid")
    owner, session = request.identity.owner_user_id, request.identity.session_id
    lease = acquire_account_lease(owner, expected_epoch=request.account_epoch)
    created = []
    from src.session import files
    try:
        with workspace_repository._transaction(owner, session) as (conn, _):
            conn.execute("""CREATE TABLE IF NOT EXISTS task_execution_acceptance (
                execution_id TEXT NOT NULL, fence INTEGER NOT NULL,
                owner INTEGER NOT NULL, workspace_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, content_hash TEXT NOT NULL, receipt TEXT NOT NULL,
                PRIMARY KEY(execution_id,fence))""")
            content_hash = hashlib.sha256(json.dumps(
                [(b.path, b.sha256, len(b.data)) for b in blobs], separators=(",", ":")).encode()).hexdigest()
            prior = conn.execute("SELECT * FROM task_execution_acceptance WHERE execution_id=? AND fence=?",
                                 (execution_id, fence)).fetchone()
            if prior is not None:
                if (prior["owner"] != owner or prior["workspace_id"] != request.workspace_id
                        or prior["fingerprint"] != request.fingerprint or prior["content_hash"] != content_hash):
                    raise ValueError("execution_acceptance_conflict")
                return json.loads(prior["receipt"])
            work = workspace_repository._workspace(conn, owner, session, request.workspace_id)
            if work["task_id"] != request.task_id:
                raise PermissionError("execution_task_scope_mismatch")
            workspace_repository._cas(work, request.workspace_revision)
            count, paths, used = conn.execute("SELECT COUNT(*),COUNT(DISTINCT path),COALESCE(SUM(size_bytes),0) "
                "FROM task_workspace_files WHERE workspace_id=?", (request.workspace_id,)).fetchone()
            current = {row[0]: row[1] for row in conn.execute("SELECT path,MAX(version) FROM task_workspace_files "
                "WHERE workspace_id=? GROUP BY path", (request.workspace_id,))}
            total = sum(len(blob.data) for blob in blobs)
            if (count + len(blobs) > workspace_module.MAX_VERSIONS
                    or paths + sum(blob.path not in current for blob in blobs) > workspace_module.MAX_PATHS
                    or used + total > workspace_module.MAX_WORKSPACE_BYTES):
                raise ValueError("workspace_quota_exceeded")
            # This write transaction reserves the whole batch against other file
            # registrations BEFORE any persistent bytes are written.
            used_bytes, used_files = conn.execute("SELECT COALESCE(SUM(size_bytes),0),COUNT(*) "
                "FROM session_files WHERE owner_user_id=?", (owner,)).fetchone()
            max_bytes, max_files = files._storage_limits()
            if used_bytes + total > max_bytes or used_files + len(blobs) > max_files:
                raise files.StorageQuotaExceeded("账号资料库容量不足")
            root = files._artifact_session_dir(session)
            _no_links(root)
            files._make_private_dir(root)
            refs, now = [], time.time()
            for blob in blobs:
                file_id, name = uuid.uuid4().hex, files._safe_name(blob.path.rsplit("/", 1)[-1])
                target = root / (file_id + "_" + name)
                with target.open("xb") as stream:
                    created.append(target)
                    stream.write(blob.data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if os.name != "nt":
                    target.chmod(0o600)
                mime = files._guess_mime(name)
                conn.execute("INSERT INTO session_files(file_id,session_id,owner_user_id,filename,origin,mime_type,"
                    "size_bytes,sha256,storage_path,artifact_id,created_at,last_accessed_at,client_request_id,"
                    "producer_tool,dedupe_key,visibility) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (file_id, session, owner, name, files.ORIGIN_GENERATED, mime, len(blob.data), blob.sha256,
                     str(target), "", now, now, "", "code_execute", "", "internal"))
                version = current.get(blob.path, 0) + 1
                pin_id = workspace_repository._pin_id(request.workspace_id, blob.path, version)
                files._replace_resource_references_locked(conn, owner, workspace_module.RESOURCE_KIND, pin_id, [file_id])
                source = json.dumps({"execution_id": execution_id, "fence": fence,
                    "request_fingerprint": request.fingerprint, "kind": "derived",
                    "network_source_status": "unverified"}, separators=(",", ":"))
                conn.execute("INSERT INTO task_workspace_files VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (request.workspace_id, blob.path, version, file_id, blob.sha256, mime,
                     len(blob.data), source, "[]", now))
                refs.append(dict(path=blob.path, file_id=file_id, version=version,
                                 sha256=blob.sha256, size_bytes=len(blob.data), mime_type=mime))
            revision = request.workspace_revision + (1 if blobs else 0)
            if blobs:
                conn.execute("UPDATE task_workspaces SET revision=?,touched_at=? WHERE workspace_id=?",
                             (revision, now, request.workspace_id))
            receipt = {"execution_id": execution_id, "fence": fence,
                       "workspace_revision": revision, "files": refs}
            conn.execute("INSERT INTO task_execution_acceptance VALUES(?,?,?,?,?,?,?)",
                (execution_id, fence, owner, request.workspace_id, request.fingerprint,
                 content_hash, json.dumps(receipt, separators=(",", ":"))))
        return receipt
    except BaseException:
        # Commit/connection errors can be ambiguous. Never delete bytes that may
        # have committed; if even this check fails leave private orphans for GC.
        try:
            with files._connect() as check:
                unregistered = [path for path in created if check.execute(
                    "SELECT 1 FROM session_files WHERE storage_path=?", (str(path),)).fetchone() is None]
            for path in unregistered:
                path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        lease.release()

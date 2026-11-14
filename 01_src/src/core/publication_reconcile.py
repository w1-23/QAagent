"""Recover the artifact-DB -> session-file publication gap without rewriting bytes.

Only publisher-bound identities and immutable digests are recoverable. Legacy
ownerless records require an authorized live retry; background recovery never
infers ownership from a path, filename or caller-supplied account number.
"""
from __future__ import annotations

from contextlib import closing, nullcontext
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any

from src.config import PROJECT_ROOT

MAX_VERIFY_BYTES = 256 * 1024 * 1024


class PublicationRejected(ValueError):
    """A pending row cannot safely be adopted by the file index."""


def init_outbox(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS publication_outbox (
        artifact_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        owner_user_id INTEGER NOT NULL,
        artifact_sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','committed')),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        file_id TEXT NOT NULL DEFAULT '')""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_publication_pending "
                 "ON publication_outbox(state,updated_at,artifact_id)")


def _owner_valid(session_id: str, owner: int) -> bool:
    from src.auth import user_by_id
    from src.session.manager import session_manager

    user = user_by_id(owner) if owner > 0 else None
    return bool(user and user.get("active")
                and session_manager.get_session_owner(session_id) == owner)


def enqueue_publication(conn: sqlite3.Connection, *, artifact_id: str,
                        owner_user_id: int, sha256: str, size_bytes: int) -> None:
    """Call inside the SAME transaction that inserts an immutable artifact row.

    Also supports a live exact retry of a legacy row, after checking its current
    authenticated session owner. Background recovery never calls this binding.
    """
    if not conn.in_transaction:
        raise ValueError("publication enqueue requires an artifact write transaction")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
    if row is None or str(row["artifact_sha256"]) != sha256:
        raise PublicationRejected("artifact_digest_mismatch")
    owner = int(owner_user_id or 0)
    if int(row["owner_user_id"] or 0) != owner:
        if int(row["owner_user_id"] or 0) != 0 or not _owner_valid(row["session_id"], owner):
            raise PublicationRejected("artifact_owner_mismatch")
        conn.execute("UPDATE artifacts SET owner_user_id=?,artifact_size_bytes=? WHERE artifact_id=?",
                     (owner, int(size_bytes), artifact_id))
    elif int(row["artifact_size_bytes"]) != int(size_bytes):
        # Pre-outbox legacy rows have no size metadata. Only their authorized
        # live retry may fill it; a background job never trusts a fresh hash.
        old = conn.execute("SELECT 1 FROM publication_outbox WHERE artifact_id=?", (artifact_id,)).fetchone()
        if old or int(row["artifact_size_bytes"]) != 0:
            raise PublicationRejected("artifact_size_mismatch")
        conn.execute("UPDATE artifacts SET artifact_size_bytes=? WHERE artifact_id=?",
                     (int(size_bytes), artifact_id))
    now = time.time()
    conn.execute("INSERT OR IGNORE INTO publication_outbox "
                 "(artifact_id,session_id,owner_user_id,artifact_sha256,size_bytes,created_at,updated_at) "
                 "VALUES(?,?,?,?,?,?,?)", (artifact_id, row["session_id"], owner, sha256, int(size_bytes), now, now))
    pending = conn.execute("SELECT * FROM publication_outbox WHERE artifact_id=?", (artifact_id,)).fetchone()
    if (pending["session_id"] != row["session_id"] or int(pending["owner_user_id"]) != owner
            or pending["artifact_sha256"] != sha256 or int(pending["size_bytes"]) != int(size_bytes)):
        raise PublicationRejected("outbox_snapshot_mismatch")


def _snapshot(conn: sqlite3.Connection, artifact_id: str) -> tuple[dict, dict]:
    conn.row_factory = sqlite3.Row
    artifact = conn.execute("SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
    pending = conn.execute("SELECT * FROM publication_outbox WHERE artifact_id=?", (artifact_id,)).fetchone()
    if artifact is None or pending is None:
        raise PublicationRejected("missing_publication")
    a, p = dict(artifact), dict(pending)
    if (a["session_id"] != p["session_id"] or int(a["owner_user_id"]) != int(p["owner_user_id"])
            or a["artifact_sha256"] != p["artifact_sha256"]
            or int(a["artifact_size_bytes"]) != int(p["size_bytes"])):
        raise PublicationRejected("outbox_snapshot_mismatch")
    return a, p


def _verify_bytes(artifact: dict, *, project_root: Path) -> Path:
    sid, aid, filename = (str(artifact[key]) for key in ("session_id", "artifact_id", "filename"))
    if (not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", sid)
            or not re.fullmatch(r"[a-f0-9]{32}", aid)
            or not filename or filename in {".", ".."}
            or any(c in filename for c in "/\\:\x00\n\r")
            or not re.fullmatch(r"[a-f0-9]{64}", str(artifact["artifact_sha256"]))):
        raise PublicationRejected("invalid_artifact_metadata")
    root = Path(project_root).resolve()
    path = root / "state" / "artifacts" / sid / f"{aid}_{filename}"
    if Path(str(artifact["path"])) != path:
        raise PublicationRejected("unmanaged_artifact_path")
    try:
        for part in (root / "state", root / "state/artifacts", path.parent, path):
            info = part.lstat()
            if (stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))):
                raise PublicationRejected("linked_artifact_path")
        before = path.lstat()
        size = int(artifact["artifact_size_bytes"])
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 0 <= size <= MAX_VERIFY_BYTES or before.st_size != size):
            raise PublicationRejected("invalid_artifact_size_or_type")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not os.path.samestat(before, opened):
                raise PublicationRejected("artifact_changed")
            digest, total = hashlib.sha256(), 0
            while chunk := stream.read(min(1024 * 1024, size - total + 1)):
                total += len(chunk)
                if total > size:
                    raise PublicationRejected("artifact_changed")
                digest.update(chunk)
            after = os.fstat(stream.fileno())
        if (total != size or digest.hexdigest() != artifact["artifact_sha256"]
                or (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or not os.path.samestat(after, path.lstat())):
            raise PublicationRejected("artifact_digest_mismatch")
    except OSError as exc:
        raise PublicationRejected("artifact_unreadable") from exc
    return path


def _check_index(files, artifact: dict, owner: int, path: Path, *, file_id: str = "") -> None:
    """An existing immutable publication must never be refreshed into new bytes."""
    with closing(files._connect()) as conn, conn:
        if file_id:
            rows = conn.execute("SELECT * FROM session_files WHERE file_id=?", (file_id,)).fetchall()
            if not rows:
                raise PublicationRejected("registration_not_persisted")
        else:
            key = artifact.get("dedupe_key") or ""
            rows = conn.execute("SELECT * FROM session_files WHERE session_id=? AND "
                                "(artifact_id=? OR storage_path=? OR (?<>'' AND dedupe_key=?)) LIMIT 2",
                                (artifact["session_id"], artifact["artifact_id"], str(path), key, key)).fetchall()
        if len(rows) > 1:
            raise PublicationRejected("duplicate_publication_index")
        for row in rows:
            if (row["session_id"] != artifact["session_id"]
                    or row["artifact_id"] != artifact["artifact_id"]
                    or int(row["owner_user_id"]) != owner
                    or row["sha256"] != artifact["artifact_sha256"]
                    or int(row["size_bytes"]) != int(artifact["artifact_size_bytes"])
                    or Path(row["storage_path"]) != path
                    or row["origin"] != files.ORIGIN_GENERATED):
                raise PublicationRejected("existing_publication_index_mismatch")


def register_publication(*, db_path: Path, artifact_id: str,
                         project_root: Path, client_request_id: str = "",
                         allow_ownerless: bool = False) -> dict[str, Any]:
    """Idempotent live/recovery registration; mark committed only after verification.

    db_path/project_root are trusted application paths, never HTTP parameters.
    Recovery leaves allow_ownerless=False. Live legacy CLI publication may opt in.
    """
    from src.core.account_leases import account_lease
    from src.session import files

    with closing(sqlite3.connect(str(db_path))) as conn, conn:
        artifact, pending = _snapshot(conn, artifact_id)
    owner, sid = int(pending["owner_user_id"]), str(pending["session_id"])
    if owner <= 0 and not allow_ownerless:
        raise PublicationRejected("unbound_publication_owner")
    with account_lease(owner) if owner > 0 else nullcontext():
        if owner > 0 and not _owner_valid(sid, owner):
            raise PublicationRejected("publication_owner_inactive_or_session_mismatch")
        path = _verify_bytes(artifact, project_root=project_root)
        files.init_store()
        _check_index(files, artifact, owner, path)
        registered = files.register_file(
            session_id=sid, owner_user_id=owner, filename=artifact["filename"],
            storage_path=str(path), mime=artifact["mime"], origin=files.ORIGIN_GENERATED,
            artifact_id=artifact_id, client_request_id=client_request_id or artifact.get("request_id", ""),
            producer_tool=artifact.get("producer_tool", ""), dedupe_key=artifact.get("dedupe_key", ""))
        file_id = str((registered or {}).get("file_id", ""))
        _check_index(files, artifact, owner, path, file_id=file_id)
        entry = files.get_file(sid, file_id, owner_user_id=owner)
        if (not entry or not registered or not file_id
                or registered.get("sha256") != pending["artifact_sha256"]
                or int(registered.get("size_bytes", -1)) != int(pending["size_bytes"])
                or entry.get("artifact_id") != artifact_id
                or entry.get("sha256") != pending["artifact_sha256"]
                or int(entry.get("size_bytes", -1)) != int(pending["size_bytes"])
                or Path(entry.get("path", "")) != path
                or entry.get("origin") != files.ORIGIN_GENERATED):
            raise PublicationRejected("registered_artifact_mismatch")
        _verify_bytes(artifact, project_root=project_root)
        if owner > 0 and not _owner_valid(sid, owner):
            raise PublicationRejected("publication_owner_changed")
        with closing(sqlite3.connect(str(db_path))) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current_a, current_p = _snapshot(conn, artifact_id)
            if current_a != artifact or any(current_p[key] != pending[key] for key in
                    ("session_id", "owner_user_id", "artifact_sha256", "size_bytes")):
                raise PublicationRejected("publication_changed_before_commit")
            conn.execute("UPDATE publication_outbox SET state='committed',file_id=?,updated_at=?,last_error='' "
                         "WHERE artifact_id=?", (entry["file_id"], time.time(), artifact_id))
        return registered


def reconcile_publications(*, limit: int = 50) -> dict[str, int]:
    """Bounded startup/admin recovery, without accepting caller paths or owners."""
    limit = max(1, min(int(limit), 100))
    db = PROJECT_ROOT / "state/research_artifacts.sqlite3"
    result = {"scanned": 0, "committed": 0, "pending": 0}
    if not db.is_file():
        return result
    with closing(sqlite3.connect(str(db))) as conn, conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publication_outbox'").fetchone()
        if not exists:
            return result
        rows = conn.execute("SELECT artifact_id FROM publication_outbox WHERE state='pending' "
                            "ORDER BY updated_at,artifact_id LIMIT ?", (limit,)).fetchall()
    for (artifact_id,) in rows:
        result["scanned"] += 1
        try:
            register_publication(db_path=db, artifact_id=artifact_id, project_root=PROJECT_ROOT)
            result["committed"] += 1
        except Exception as exc:
            result["pending"] += 1
            error = str(exc) if isinstance(exc, PublicationRejected) else type(exc).__name__
            with closing(sqlite3.connect(str(db))) as conn, conn:
                conn.execute("UPDATE publication_outbox SET attempts=attempts+1,updated_at=?,last_error=? "
                             "WHERE artifact_id=? AND state='pending'", (time.time(), error[:120], artifact_id))
    return result


def audit_orphan_artifacts(*, limit: int = 100) -> dict[str, Any]:
    """Bounded, dry-run-only disk inventory. Never removes or adopts any file."""
    limit = max(1, min(int(limit), 200))
    root = PROJECT_ROOT / "state/artifacts"
    db = PROJECT_ROOT / "state/research_artifacts.sqlite3"
    result: dict[str, Any] = {"dry_run": True, "scanned": 0, "orphans": [], "truncated": False}
    if not root.is_dir() or root.is_symlink() or not db.is_file():
        return result
    with closing(sqlite3.connect(str(db))) as conn, conn, os.scandir(root) as sessions:
        for session_index, session in enumerate(sessions):
            if session_index >= limit:
                result["truncated"] = True
                break
            if not session.is_dir(follow_symlinks=False) or session.is_symlink():
                continue
            with os.scandir(session.path) as entries:
                for entry in entries:
                    if result["scanned"] >= limit:
                        result["truncated"] = True
                        return result
                    result["scanned"] += 1
                    if not conn.execute("SELECT 1 FROM artifacts WHERE session_id=? AND path=? LIMIT 1",
                                        (session.name, str(Path(entry.path).absolute()))).fetchone():
                        result["orphans"].append({"session_id": session.name, "name": entry.name,
                                                  "reason": "unindexed_disk_entry"})
    return result

"""Bounded Deep Agents file view backed by the existing private session library.

Only the version index is new. Bytes live once in session_files, and each
committed version pins its existing file ID. StoreBackend supplies file
semantics; no filesystem backend, process, sandbox or command tool is exposed.
Staging precedes atomic index+pin publication: an interrupted staging write is
an unreferenced internal library entry, eligible for the library's normal GC.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat

from deepagents.backends.store import StoreBackend
from langgraph.store.memory import InMemoryStore

MAX_FILE_BYTES = 65536
MAX_VIEW_BYTES = 1048576
MAX_FILES = 128
MAX_VERSIONS = 128
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".yaml", ".yml",
                  ".xml", ".html", ".css", ".js", ".py", ".r", ".sql", ".toml", ".log"}


class WorkspaceConflict(ValueError):
    def __init__(self, latest_ref):
        super().__init__("workspace_version_conflict")
        self.latest_ref = latest_ref


def virtual_path(path: str, *, directory=False, writable=False) -> str:
    """Validate logical keys, never convert caller paths into physical paths."""
    if not isinstance(path, str) or len(path) > 240:
        raise ValueError("workspace_invalid_path")
    if directory and path in {"/", "/input", "/workspace"}:
        return path
    parts = path.split("/")
    if (len(parts) < 3 or parts[0] or parts[1] not in {"input", "workspace"}
            or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_. -]{0,119}", p)
                   or p.endswith((".", " ")) or ".." in p for p in parts[2:])):
        raise ValueError("workspace_invalid_path")
    if writable and parts[1] != "workspace":
        raise PermissionError("workspace_input_readonly")
    if not directory and Path(parts[-1]).suffix.lower() not in _TEXT_SUFFIXES:
        raise ValueError("workspace_text_extension_required")
    return path


def _no_links(path: Path) -> None:
    # Check lexical ancestors before resolve(), including junctions on Windows.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise PermissionError("workspace_link_denied")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WorkspaceStorage:
    """One authenticated call's adapter; instances must never be globally cached."""

    def __init__(self, owner: int, session: str):
        from src.session import files
        if type(owner) is not int or owner <= 0 or not files.valid_session(session):
            raise PermissionError("workspace_scope_unavailable")
        self.owner, self.session, self.files = owner, session, files
        for suffix in ("", "-wal", "-shm", "-journal"):
            _no_links(Path(str(files.DB_PATH) + suffix))
        _no_links(files.FILES_ROOT)
        files.init_store()
        with files._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS workspace_file_versions (
                owner INTEGER NOT NULL, session TEXT NOT NULL, path TEXT NOT NULL,
                version INTEGER NOT NULL, file_id TEXT NOT NULL, sha256 TEXT NOT NULL,
                PRIMARY KEY(owner,session,path,version), UNIQUE(file_id))""")

    def _reference(self, row, path=None, version=None):
        return {"kind": "workspace_file" if row["visibility"] == "internal" else "workspace_input",
                "path": path or row["virtual_path"], "file_id": row["file_id"],
                "version": version if version is not None else row["version"],
                "sha256": row["sha256"]}

    def _read_bytes(self, row) -> str:
        path = Path(row["storage_path"])
        _no_links(path)
        # Require the exact current owner/session upload area, not legacy roots
        # or generated artifacts. A row cannot turn into an arbitrary host read.
        root = self.files.FILES_ROOT / f"user_{self.owner}" / self.session
        _no_links(root)
        if (row["owner_user_id"] != self.owner or row["session_id"] != self.session
                or row["origin"] != self.files.ORIGIN_UPLOADED
                or not path.resolve().is_relative_to(root.resolve())):
            raise PermissionError("workspace_file_scope_denied")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size > MAX_FILE_BYTES):
                raise ValueError("workspace_file_limit_or_type")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            _no_links(path)
            current = path.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise PermissionError("workspace_file_changed")
        finally:
            os.close(fd)
        if len(content) > MAX_FILE_BYTES or _digest(content) != row["sha256"]:
            raise ValueError("workspace_integrity_or_size_mismatch")
        text = content.decode("utf-8")
        if "\x00" in text:
            raise ValueError("workspace_text_required")
        return text

    def _rows(self, *, path=None, version=None):
        with self.files._connect() as conn:
            args = [self.owner, self.session]
            where = "v.owner=? AND v.session=?"
            if path is not None:
                where += " AND v.path=?"
                args.append(path)
            if version is not None:
                where += " AND v.version=?"
                args.append(version)
            else:
                where += " AND v.version=(SELECT MAX(x.version) FROM workspace_file_versions x WHERE x.owner=v.owner AND x.session=v.session AND x.path=v.path)"
            rows = [dict(r) for r in conn.execute(
                "SELECT f.*,v.path AS virtual_path,v.version,v.sha256 AS version_hash "
                "FROM workspace_file_versions v JOIN session_files f ON f.file_id=v.file_id "
                "WHERE " + where + " ORDER BY v.path LIMIT ?", (*args, MAX_FILES + 1))]
            for row in rows:
                if row["version_hash"] != row["sha256"] or row["visibility"] != "internal":
                    raise ValueError("workspace_index_integrity")
            if path is None or path.startswith("/input/"):
                inputs = conn.execute(
                    "SELECT * FROM session_files WHERE owner_user_id=? AND session_id=? "
                    "AND origin='uploaded' AND visibility='public' ORDER BY file_id LIMIT ?",
                    (self.owner, self.session, MAX_FILES + 1)).fetchall()
                if len(inputs) > MAX_FILES:
                    raise ValueError("workspace_input_count_limit")
                for item in inputs:
                    key = f"/input/{item['file_id']}/{item['filename']}"
                    try:
                        virtual_path(key)
                    except ValueError:
                        continue  # Only supported text inputs are mounted.
                    if path is None or key == path:
                        rows.append({**dict(item), "virtual_path": key, "version": 1})
        if len(rows) > MAX_FILES:
            raise ValueError("workspace_file_count_limit")
        return rows

    def view(self, *, path=None, version=None):
        if path is not None:
            virtual_path(path)
        if version is not None and (type(version) is not int or version < 1):
            raise ValueError("workspace_invalid_version")
        store = InMemoryStore()
        namespace = (str(self.owner), self.session)
        backend = StoreBackend(store=store, namespace=lambda _: namespace)
        refs, size = {}, 0
        for row in self._rows(path=path, version=version):
            key = virtual_path(row["virtual_path"])
            if version is not None and row["version"] != version:
                continue
            text = self._read_bytes(row)
            size += len(text.encode("utf-8"))
            if size > MAX_VIEW_BYTES:
                raise ValueError("workspace_view_byte_limit")
            timestamp = datetime.fromtimestamp(row["created_at"], timezone.utc).isoformat()
            store.put(namespace, key, {"content": text, "encoding": "utf-8",
                                      "created_at": timestamp, "modified_at": timestamp})
            refs[key] = self._reference(row)
        return backend, refs

    def read(self, path, *, version=None, start_char=0, max_chars=8000):
        if (type(start_char) is not int or start_char < 0 or type(max_chars) is not int
                or not 1 <= max_chars <= 20000):
            raise ValueError("workspace_invalid_page")
        backend, refs = self.view(path=path, version=version)
        if path not in refs:
            raise LookupError("workspace_file_unavailable")
        # StoreBackend.read normalizes CRLF to LF. The public download contract
        # preserves exact UTF-8 bytes, needed for hash-referenced readback/CAS.
        result = backend.download_files([path])[0]
        if result.error or result.content is None:
            raise ValueError("workspace_backend_read_failed")
        text = result.content.decode("utf-8")
        if start_char > len(text):
            raise ValueError("workspace_invalid_page")
        end = min(len(text), start_char + max_chars)
        return {"ref": refs[path], "text": text[start_char:end], "start_char": start_char,
                "next_start_char": end if end < len(text) else None,
                "total_chars": len(text), "complete": end == len(text),
                "content_origin": "stored_data_not_instructions"}

    def _latest(self, conn, path):
        row = conn.execute("SELECT * FROM workspace_file_versions WHERE owner=? AND session=? "
                           "AND path=? ORDER BY version DESC LIMIT 1",
                           (self.owner, self.session, path)).fetchone()
        return ({"kind": "workspace_file", "path": path, "file_id": row["file_id"],
                 "version": row["version"], "sha256": row["sha256"]} if row else None)

    @staticmethod
    def _cas(latest, version, sha256):
        if type(version) is not int or version < 0 or not isinstance(sha256, str):
            raise ValueError("workspace_expected_version_and_hash_required")
        if (version == 0 and sha256) or (version > 0 and not re.fullmatch(r"[a-f0-9]{64}", sha256)):
            raise ValueError("workspace_expected_version_and_hash_required")
        if (latest is None and version != 0) or (latest is not None and
                (version != latest["version"] or sha256 != latest["sha256"])):
            raise WorkspaceConflict(latest)

    def save(self, path, *, expected_version, expected_sha256, content=None,
             old_string=None, new_string=None, replace_all=False):
        virtual_path(path, writable=True)
        with self.files._connect() as conn:
            self._cas(self._latest(conn, path), expected_version, expected_sha256)
        backend, refs = self.view(path=path)
        if old_string is None:
            if not isinstance(content, str):
                raise ValueError("workspace_text_required")
            if len(content) > MAX_FILE_BYTES or len(content.encode("utf-8")) > MAX_FILE_BYTES:
                raise ValueError("workspace_text_or_byte_limit")
            result = backend.write(path, content)
        else:
            if not isinstance(old_string, str) or not old_string or not isinstance(new_string, str):
                raise ValueError("workspace_nonempty_old_string_required")
            if max(len(old_string), len(new_string)) > MAX_FILE_BYTES:
                raise ValueError("workspace_text_or_byte_limit")
            # Bound expansion BEFORE StoreBackend allocates the replaced text.
            # A 64-KiB file with a one-character needle must not allocate GiB.
            current = backend.download_files([path])[0]
            if current.content is not None:
                occurrences = current.content.decode("utf-8").count(old_string)
                growth = len(new_string.encode("utf-8")) - len(old_string.encode("utf-8"))
                if len(current.content) + occurrences * growth > MAX_FILE_BYTES:
                    raise ValueError("workspace_text_or_byte_limit")
            result = backend.edit(path, old_string, new_string, replace_all=replace_all)
        if result.error:
            raise ValueError(result.error)
        data = backend.download_files([path])[0].content
        if data is None:
            raise ValueError("workspace_backend_read_failed")
        text = data.decode("utf-8")
        if "\x00" in text or len(data) > MAX_FILE_BYTES:
            raise ValueError("workspace_text_or_byte_limit")
        if expected_version >= MAX_VERSIONS:
            raise ValueError("workspace_version_count_limit")
        # Existing library owns byte persistence and quota enforcement. No
        # request deduplication: each immutable version has a distinct file ID.
        _no_links(self.files.FILES_ROOT / f"user_{self.owner}" / self.session)
        staged = self.files.register_file(session_id=self.session, owner_user_id=self.owner,
            filename=Path(path).name, content=data, visibility="internal", producer_tool="workspace_write")
        try:
            with self.files._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                latest = self._latest(conn, path)
                self._cas(latest, expected_version, expected_sha256)
                count = conn.execute("SELECT COUNT(DISTINCT path) FROM workspace_file_versions "
                                     "WHERE owner=? AND session=?", (self.owner, self.session)).fetchone()[0]
                if latest is None and count >= MAX_FILES:
                    raise ValueError("workspace_file_count_limit")
                sizes = conn.execute("SELECT COALESCE(SUM(f.size_bytes),0) FROM workspace_file_versions v "
                    "JOIN session_files f ON f.file_id=v.file_id WHERE v.owner=? AND v.session=? "
                    "AND v.path<>? AND v.version=(SELECT MAX(x.version) FROM workspace_file_versions x "
                    "WHERE x.owner=v.owner AND x.session=v.session AND x.path=v.path)",
                    (self.owner, self.session, path)).fetchone()[0]
                if sizes + len(data) > MAX_VIEW_BYTES:
                    raise ValueError("workspace_view_byte_limit")
                staged_row = conn.execute("SELECT * FROM session_files WHERE file_id=?", (staged["file_id"],)).fetchone()
                if staged_row is None:
                    raise ValueError("workspace_staging_unavailable")
                self._read_bytes(staged_row)
                version = expected_version + 1
                # Pin and version are committed under the same library lock.
                self.files._replace_resource_references_locked(conn, self.owner, "workspace_version",
                    staged["file_id"], [staged["file_id"]])
                conn.execute("INSERT INTO workspace_file_versions VALUES(?,?,?,?,?,?)",
                             (self.owner, self.session, path, version, staged["file_id"], staged["sha256"]))
            return {"ref": {"kind": "workspace_file", "path": path, "version": version,
                            "file_id": staged["file_id"], "sha256": staged["sha256"]},
                    "previous_ref": refs.get(path), "size_bytes": len(data),
                    "occurrences": getattr(result, "occurrences", None)}
        except BaseException:
            # Clean only our uncommitted staging object, using the library's
            # shared delete lock/pin guard. Never migrate unrelated stores here.
            with self.files._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM session_files WHERE file_id=? AND owner_user_id=? AND session_id=?",
                    (staged["file_id"], self.owner, self.session)).fetchone()
                if row is not None:
                    self.files._delete_indexed_file(conn, row)
            raise

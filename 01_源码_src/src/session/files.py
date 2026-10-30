"""Account-scoped file library used by chat attachments and generated artifacts.

Design boundary (deliberate, per the agreed scope):

* This is a *light file manager and web previewer*, not a knowledge base, not a
  document-understanding system, and not an editor.
* Uploaded material and generated results share ONE account library, but a generated file
  is registered once - it is never copied into a second store.  The existing
  ``artifacts`` table keeps the ``artifact_id`` so old download links in chat
  history continue to work.
* Nothing here writes to the shared RAG corpus, and nothing here invokes a
  model.  Opening a preview is an ordinary HTTP file read.
* Access is always checked against account ownership; ``storage_path`` never
  leaves the server.

Retention: uploaded and generated file references expire after 30 days without
being opened, referenced, or viewed in the account library. Files are stored
under the owning account's private directory and can be referenced by any of
that account's sessions.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from src.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "state" / "session_files.sqlite3"
FILES_ROOT = PROJECT_ROOT / "state" / "session_files"

# Retention policy: sweep individual files after this many inactive days.
DEFAULT_IDLE_DAYS = 30
_SESSION_RE = re.compile(r"[A-Za-z0-9_-]{8,128}")

ORIGIN_UPLOADED = "uploaded"
ORIGIN_GENERATED = "generated"
ORIGINS = (ORIGIN_UPLOADED, ORIGIN_GENERATED)

# Kinds drive the preview strategy on the client.
KIND_BY_MIME = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/gif": "image",
    "text/html": "html",
    "text/csv": "table",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "table",
    "application/vnd.ms-excel": "table",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "docx",
    "text/plain": "text",
    "application/x-bibtex": "text",
    "application/x-research-info-systems": "text",
}
KIND_BY_SUFFIX = {
    ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".webp": "image", ".gif": "image", ".html": "html", ".htm": "html",
    ".csv": "table", ".xlsx": "table", ".xls": "table",
    ".docx": "docx", ".doc": "docx", ".txt": "text", ".ris": "text",
    ".bib": "text", ".bibtex": "text", ".md": "text",
}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    if os.name != "nt":
        try:
            os.chmod(DB_PATH, 0o600)
        except OSError:
            pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_store() -> None:
    """Create the index tables. Idempotent; safe to call at startup."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_files (
                file_id       TEXT PRIMARY KEY,
                session_id    TEXT NOT NULL,
                owner_user_id INTEGER NOT NULL DEFAULT 0,
                filename      TEXT NOT NULL,
                origin        TEXT NOT NULL,
                mime_type     TEXT NOT NULL,
                size_bytes    INTEGER NOT NULL,
                sha256        TEXT NOT NULL,
                storage_path  TEXT NOT NULL,
                artifact_id   TEXT NOT NULL DEFAULT '',
                created_at    REAL NOT NULL,
                last_accessed_at REAL NOT NULL
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_session "
                     "ON session_files(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_files_owner "
                     "ON session_files(owner_user_id, created_at)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_files_path "
                     "ON session_files(storage_path)")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(session_files)")}
        if "client_request_id" not in cols:
            conn.execute("ALTER TABLE session_files ADD COLUMN client_request_id TEXT NOT NULL DEFAULT ''")
        if "producer_tool" not in cols:
            # 哪个工具产出了这份文件。空值表示上传文件或历史行（无从判定），
            # 不猜、不回填——判定"本轮是否真的产出了某类成果"必须靠这个字段。
            conn.execute("ALTER TABLE session_files ADD COLUMN producer_tool TEXT NOT NULL DEFAULT ''")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_file_turn_refs (
                session_id TEXT NOT NULL,
                owner_user_id INTEGER NOT NULL,
                client_request_id TEXT NOT NULL,
                file_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(session_id, client_request_id, file_id),
                FOREIGN KEY(file_id) REFERENCES session_files(file_id) ON DELETE CASCADE
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_turn_file_refs_owner "
                     "ON session_file_turn_refs(owner_user_id, session_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_activity (
                session_id  TEXT PRIMARY KEY,
                owner_user_id INTEGER NOT NULL DEFAULT 0,
                last_active_at REAL NOT NULL
            )""")


# --------------------------------------------------------------- helpers
def valid_session(session_id: str) -> bool:
    return bool(_SESSION_RE.fullmatch(str(session_id or "")))


def kind_for(filename: str, mime: str) -> str:
    """Preview kind for a file; drives which viewer the client opens."""
    if mime and mime in KIND_BY_MIME:
        return KIND_BY_MIME[mime]
    return KIND_BY_SUFFIX.get(Path(str(filename or "")).suffix.lower(), "download")


def touch_session(session_id: str, owner_user_id: int = 0) -> None:
    """Record session activity. Retention is keyed on this, not file age."""
    if not valid_session(session_id):
        return
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO session_activity(session_id, owner_user_id, last_active_at) "
            "VALUES(?,?,?) ON CONFLICT(session_id) DO UPDATE SET "
            "last_active_at=excluded.last_active_at, "
            "owner_user_id=CASE WHEN excluded.owner_user_id>0 "
            "THEN excluded.owner_user_id ELSE session_activity.owner_user_id END",
            (session_id, int(owner_user_id or 0), now))


def session_last_activity(session_id: str) -> float | None:
    """Return the newest known activity timestamp for one session.

    Chat history is authoritative when available; the file index timestamp
    covers file previews and older/orphaned session records.
    """
    if not valid_session(session_id):
        return None
    timestamps: list[float] = []
    try:
        from src.session.manager import session_manager
        sessions_db = Path(session_manager.db_path)
        if sessions_db.is_file():
            with sqlite3.connect(str(sessions_db), timeout=5) as conn:
                row = conn.execute(
                    "SELECT updated_at FROM sessions WHERE session_id=?",
                    (session_id,)).fetchone()
            if row and row[0]:
                from datetime import datetime
                try:
                    timestamps.append(datetime.fromisoformat(str(row[0])).timestamp())
                except (TypeError, ValueError, OverflowError):
                    try:
                        timestamps.append(float(row[0]))
                    except (TypeError, ValueError, OverflowError):
                        pass
    except (OSError, sqlite3.Error, RuntimeError):
        pass
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT last_active_at FROM session_activity WHERE session_id=?",
                (session_id,)).fetchone()
        if row and row[0] is not None:
            timestamps.append(float(row[0]))
    except (OSError, sqlite3.Error, TypeError, ValueError):
        pass
    return max(timestamps) if timestamps else None


def remove_generated_entry(session_id: str, artifact_id: str) -> bool:
    """Remove a stale panel reference; artifact bytes remain lifecycle-owned."""
    if (not valid_session(session_id)
            or not re.fullmatch(r"[a-f0-9]{32}", str(artifact_id or ""))):
        return False
    init_store()
    with _connect() as conn:
        doomed = [row["file_id"] for row in conn.execute(
            "SELECT file_id FROM session_files WHERE session_id=? AND artifact_id=? "
            "AND origin=?", (session_id, artifact_id, ORIGIN_GENERATED))]
        cur = conn.execute(
            "DELETE FROM session_files WHERE session_id=? AND artifact_id=? "
            "AND origin=?", (session_id, artifact_id, ORIGIN_GENERATED))
        for file_id in doomed:
            conn.execute("DELETE FROM session_file_turn_refs "
                         "WHERE file_id=? AND session_id=?", (file_id, session_id))
    return cur.rowcount > 0


def _session_dir(session_id: str, owner_user_id: int = 0) -> Path:
    if not valid_session(session_id):
        raise ValueError("invalid session id")
    managed_root = FILES_ROOT.resolve()
    if int(owner_user_id or 0) > 0:
        candidate = _account_dir(int(owner_user_id), create=True) / session_id
    else:
        candidate = FILES_ROOT / session_id
    if candidate.is_symlink():
        raise ValueError("session upload directory is a symlink")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(managed_root):
        raise ValueError("session upload directory escaped the managed root")
    return resolved


def _account_dir(owner_user_id: int, *, create: bool = False) -> Path:
    """Resolve one account's private file-library directory."""
    uid = int(owner_user_id or 0)
    if uid <= 0:
        raise ValueError("invalid account id")
    if FILES_ROOT.is_symlink():
        raise ValueError("account file root is a symlink")
    if create:
        FILES_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            try:
                os.chmod(FILES_ROOT, 0o700)
            except OSError:
                pass
    managed_root = FILES_ROOT.resolve()
    candidate = FILES_ROOT / f"user_{uid}"
    if candidate.is_symlink():
        raise ValueError("account upload directory is a symlink")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(managed_root):
        raise ValueError("account upload directory escaped the managed root")
    if create:
        resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            try:
                os.chmod(resolved, 0o700)
            except OSError:
                pass
    return resolved


def _make_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def _artifact_session_dir(session_id: str) -> Path:
    if not valid_session(session_id):
        raise ValueError("invalid session id")
    managed_root = (PROJECT_ROOT / "state" / "artifacts").resolve()
    candidate = PROJECT_ROOT / "state" / "artifacts" / session_id
    if candidate.is_symlink():
        raise ValueError("session artifact directory is a symlink")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(managed_root):
        raise ValueError("session artifact directory escaped the managed root")
    return resolved


def _allowed_file_path(session_id: str, path: Path, origin: str,
                       owner_user_id: int = 0) -> bool:
    """Keep each row's file reference inside its own managed session area."""
    if origin not in ORIGINS:
        return False
    try:
        resolved = path.resolve()
        if origin == ORIGIN_UPLOADED:
            roots = [_session_dir(session_id, owner_user_id)]
            # Read files created before account-scoped storage was introduced.
            legacy = _session_dir(session_id, 0)
            if legacy not in roots:
                roots.append(legacy)
        else:
            roots = [_artifact_session_dir(session_id)]
        return any(resolved.is_relative_to(root) for root in roots) and resolved.is_file()
    except (OSError, ValueError):
        return False


def register_file(*, session_id: str, owner_user_id: int, filename: str,
                  content: bytes | None = None, storage_path: str = "",
                  mime: str = "", origin: str = ORIGIN_UPLOADED,
                  artifact_id: str = "", client_request_id: str = "",
                  producer_tool: str = "") -> dict[str, Any]:
    """Register a file in the session index.

    Either ``content`` is supplied (we write it into the controlled directory)
    or ``storage_path`` points at a file that already exists on disk (a
    generated artifact).  In the second case the file is NOT copied - the index
    references the existing object, so there is exactly one copy.
    """
    if origin not in ORIGINS:
        raise ValueError(f"unsupported origin: {origin!r}")
    if not valid_session(session_id):
        raise ValueError("invalid session id")

    safe = _safe_name(filename)
    if content is not None:
        digest = hashlib.sha256(content).hexdigest()
        if client_request_id:
            with _connect() as conn:
                existing = conn.execute(
                    "SELECT * FROM session_files WHERE owner_user_id=? "
                    "AND client_request_id=? AND sha256=? AND origin=? LIMIT 1",
                    (int(owner_user_id or 0), str(client_request_id)[:64], digest, origin),
                ).fetchone()
            if existing:
                return _row_to_public(existing)
        root = _session_dir(session_id, owner_user_id)
        _make_private_dir(root)
        file_id = uuid.uuid4().hex
        path = (root / f"{file_id}_{safe}").resolve()
        if not path.is_relative_to(root):
            raise ValueError("unsafe storage path")
        path.write_bytes(content)
        if os.name != "nt":
            try:
                path.chmod(0o600)
            except OSError:
                pass
        size = len(content)
    else:
        if not storage_path:
            raise ValueError("storage_path is required when content is absent")
        path = Path(storage_path).resolve()
        if not _allowed_file_path(session_id, path, origin):
            raise ValueError("storage_path is outside the managed session area or missing")
        file_id = uuid.uuid4().hex
        size = path.stat().st_size
        digest = _sha256_file(path)

    now = time.time()
    try:
        with _connect() as conn:
            # A generated artifact is registered once; re-registering the same
            # object returns the existing row instead of creating a duplicate.
            if artifact_id:
                existing = conn.execute(
                    "SELECT * FROM session_files WHERE session_id=? AND artifact_id=?",
                    (session_id, artifact_id)).fetchone()
                if existing:
                    if (int(owner_user_id or 0) > 0
                            and int(existing["owner_user_id"] or 0) != int(owner_user_id)):
                        raise ValueError("artifact owner does not match the registered session file")
                    return _row_to_public(existing)
            conn.execute(
                "INSERT INTO session_files(file_id, session_id, owner_user_id, filename,"
                " origin, mime_type, size_bytes, sha256, storage_path, artifact_id,"
                " created_at, last_accessed_at, client_request_id, producer_tool) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (file_id, session_id, int(owner_user_id or 0), safe, origin,
                 mime or _guess_mime(safe), size, digest, str(path), artifact_id,
                 now, now, str(client_request_id or "")[:64],
                 str(producer_tool or "")[:64]))
            # 生成物在登记时就绑定本轮，不等轮末补链接：否则"本轮产出了什么"
            # 在轮内不可判定，只能靠扩展名猜。
            if client_request_id and int(owner_user_id or 0) > 0:
                conn.execute(
                    "INSERT OR IGNORE INTO session_file_turn_refs "
                    "(session_id, owner_user_id, client_request_id, file_id, created_at) "
                    "VALUES(?,?,?,?,?)",
                    (session_id, int(owner_user_id or 0),
                     str(client_request_id)[:64], file_id, now))
            row = conn.execute("SELECT * FROM session_files WHERE file_id=?",
                               (file_id,)).fetchone()
    except Exception:
        # A crash or SQLite error between writing the upload and indexing it
        # must not leave an untracked private file on disk.
        if content is not None:
            try:
                path.unlink(missing_ok=True)
                path.parent.rmdir()
            except OSError:
                pass
        raise
    touch_session(session_id, owner_user_id)
    return _row_to_public(row)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(name: str) -> str:
    stem = Path(str(name or "")).name
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", stem).strip(" .")
    return (stem[:180] or "file")


def _guess_mime(name: str) -> str:
    import mimetypes
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


# 预览要用到的 MIME 只认"能被浏览器内联渲染"的那几种。老记录里可能存着空值、
# ``application/octet-stream`` 或带 ``; charset=`` 的怪值，浏览器拿到后不会渲染而是
# 下载，iframe 里于是空白——这看起来像"预览坏了"，其实是类型不对。
_INLINE_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".json": "application/json",
}


def _canonical_mime(name: str, mime: str = "") -> str:
    """Return a MIME type the browser will render inline when it can.

    Suffix wins for the formats we preview: a legacy row that stored
    ``application/octet-stream`` for ``report.pdf`` must still open as a PDF
    instead of silently downloading inside the preview frame.
    """
    suffix = Path(str(name or "")).suffix.lower()
    pinned = _INLINE_MIME_BY_SUFFIX.get(suffix)
    if pinned:
        return pinned
    value = str(mime or "").strip()
    if not value or value.lower() == "application/octet-stream":
        return _guess_mime(name)
    return value


def _row_to_public(row: sqlite3.Row | dict) -> dict[str, Any]:
    """Frontend-facing shape: never exposes storage_path."""
    d = dict(row)
    d.pop("storage_path", None)
    d.pop("owner_user_id", None)
    d["mime_type"] = _canonical_mime(d.get("filename", ""), d.get("mime_type", ""))
    d["kind"] = kind_for(d.get("filename", ""), d.get("mime_type", ""))
    # S3: expose the normalized turn key so the UI can jump from a file back to
    # the exact conversation turn that uploaded or produced it. Legacy rows store
    # the idempotency key (``crid:<id>``) while the turn-ref table stores the raw
    # client id; normalize here instead of rewriting history.
    _raw_turn = str(d.pop("client_request_id", "") or "")
    d["turn_request_id"] = (_raw_turn[5:] if _raw_turn.startswith("crid:") else _raw_turn)
    d["source_session_id"] = d.get("session_id", "")
    d["download_url"] = f"/api/v2/files/{d['file_id']}/download"
    d["preview_url"] = f"/api/v2/files/{d['file_id']}/preview"
    return d


# ------------------------------------------------------------------ read
def list_files(session_id: str, owner_user_id: int = 0) -> list[dict[str, Any]]:
    """Files of one session, newest first. Touches session activity."""
    if not valid_session(session_id):
        return []
    with _connect() as conn:
        if int(owner_user_id or 0) > 0:
            rows = conn.execute(
                "SELECT * FROM session_files WHERE session_id=? AND owner_user_id=? "
                "ORDER BY created_at DESC", (session_id, int(owner_user_id))).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM session_files WHERE session_id=? "
                "ORDER BY created_at DESC", (session_id,)).fetchall()
    valid: list[dict[str, Any]] = []
    stale_ids: list[str] = []
    for row in rows:
        if _allowed_file_path(session_id, Path(row["storage_path"]), row["origin"],
                              int(row["owner_user_id"] or 0)):
            valid.append(_row_to_public(row))
        else:
            stale_ids.append(str(row["file_id"]))
    if stale_ids:
        with _connect() as conn:
            conn.executemany(
                "DELETE FROM session_files WHERE session_id=? AND file_id=?",
                [(session_id, file_id) for file_id in stale_ids])
    if valid:
        touch_session(session_id, owner_user_id)
    return valid


def get_file(session_id: str, file_id: str, *, touch: bool = False,
             owner_user_id: int = 0) -> dict[str, Any] | None:
    """Resolve one file inside its session. Returns internal ``path`` too."""
    if not valid_session(session_id) or not re.fullmatch(r"[a-f0-9]{32}",
                                                         str(file_id or "")):
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM session_files WHERE file_id=? AND session_id=?",
            (file_id, session_id)).fetchone()
        if not row:
            return None
        if (int(owner_user_id or 0) > 0
                and int(row["owner_user_id"] or 0) != int(owner_user_id)):
            return None
        if touch:
            conn.execute("UPDATE session_files SET last_accessed_at=? "
                         "WHERE file_id=?", (time.time(), file_id))
    public = _row_to_public(row)
    path = Path(dict(row)["storage_path"]).resolve()
    if not _allowed_file_path(session_id, path, public.get("origin", ""),
                              int(row["owner_user_id"] or 0)):
        with _connect() as conn:
            conn.execute("DELETE FROM session_files WHERE file_id=? AND session_id=?",
                         (file_id, session_id))
        return None
    public["path"] = path
    if touch:
        touch_session(session_id, owner_user_id)
    return public


def list_account_files(owner_user_id: int, *, session_id: str = "",
                       touch: bool = True) -> list[dict[str, Any]]:
    """Return unexpired files in one account's library.

    ``session_id`` narrows the result to a single session.  The default is the
    whole account, which is what the file library page lists.  Filtering in SQL
    (rather than in the caller) keeps the contract honest: a session-scoped
    route never hands out rows that belong to another conversation.
    """
    init_store()
    uid = int(owner_user_id or 0)
    if uid <= 0:
        return []
    # Establish the per-account storage boundary on first library access.
    _account_dir(uid, create=True)
    migrate_legacy_files_for_account(uid)
    scope_sid = str(session_id or '')
    with _connect() as conn:
        if scope_sid:
            rows = conn.execute(
                "SELECT * FROM session_files WHERE owner_user_id=? AND session_id=?"
                " ORDER BY created_at DESC",
                (uid, scope_sid)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM session_files WHERE owner_user_id=? ORDER BY created_at DESC",
                (uid,)).fetchall()
    valid: list[dict[str, Any]] = []
    stale: list[str] = []
    for row in rows:
        sid = str(row["session_id"] or "")
        if _allowed_file_path(sid, Path(row["storage_path"]), row["origin"], uid):
            valid.append(_row_to_public(row))
        else:
            stale.append(str(row["file_id"]))
    if stale:
        with _connect() as conn:
            conn.executemany(
                "DELETE FROM session_files WHERE file_id=? AND owner_user_id=?",
                [(fid, uid) for fid in stale])
    # Opening the library counts as use. Context assembly can inspect the
    # catalog without extending retention on every chat turn.
    if valid and touch:
        now = time.time()
        with _connect() as conn:
            conn.executemany(
                "UPDATE session_files SET last_accessed_at=? "
                "WHERE file_id=? AND owner_user_id=?",
                [(now, item["file_id"], uid) for item in valid])
    return valid


def migrate_legacy_files_for_account(owner_user_id: int) -> int:
    """Safely attach pre-account files to the account that owns their session.

    Older installations stored uploaded bytes below ``state/session_files/<sid>``
    with owner_user_id=0. Those rows were invisible in the account library after
    account isolation was introduced. Migrate only when the sessions database
    explicitly maps the session to this account; unowned/ambiguous rows stay
    inaccessible. Uploaded bytes move into the account directory. Generated
    artifacts keep their original single-copy location and only gain ownership
    metadata.
    """
    uid = int(owner_user_id or 0)
    if uid <= 0:
        return 0
    try:
        from src.session.manager import session_manager
        with sqlite3.connect(str(session_manager.db_path), timeout=5) as sessions:
            owned = {str(row[0]) for row in sessions.execute(
                "SELECT session_id FROM sessions WHERE owner_user_id=?", (uid,))}
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "legacy account file ownership lookup failed: %s", type(exc).__name__)
        return 0
    if not owned:
        return 0

    migrated = 0
    logger = logging.getLogger(__name__)
    with _connect() as conn:
        legacy_rows = conn.execute(
            "SELECT * FROM session_files WHERE owner_user_id=0 ORDER BY created_at"
        ).fetchall()
    for row in legacy_rows:
        sid = str(row["session_id"] or "")
        if sid not in owned or not valid_session(sid):
            continue
        file_id = str(row["file_id"] or "")
        if not re.fullmatch(r"[a-f0-9]{32}", file_id):
            continue
        origin = str(row["origin"] or "")
        old_path = Path(str(row["storage_path"] or ""))
        moved_from: Path | None = None
        new_path = old_path
        try:
            # Serialize migrations so simultaneous library/history reads cannot
            # move the same file twice.
            with _connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute(
                    "SELECT * FROM session_files WHERE file_id=? AND owner_user_id=0",
                    (file_id,)).fetchone()
                if not current:
                    conn.rollback()
                    continue
                stored_path = Path(str(current["storage_path"] or ""))
                if stored_path.is_symlink():
                    raise ValueError("legacy file path is a symlink")
                if origin == ORIGIN_UPLOADED:
                    legacy_root = _session_dir(sid, 0)
                    account_root = _session_dir(sid, uid)
                    old_path = stored_path.resolve()
                    _make_private_dir(account_root)
                    name = _safe_name(str(current["filename"] or "file"))
                    candidate = account_root / (file_id + "_" + name)
                    new_path = candidate.resolve()
                    if not new_path.is_relative_to(account_root):
                        raise ValueError("account upload path escaped its directory")
                    expected = str(current["sha256"] or "")
                    if old_path == new_path:
                        if (not new_path.is_file()
                                or (expected and _sha256_file(new_path) != expected)):
                            raise ValueError("account upload is missing")
                    elif old_path.is_file() and old_path.is_relative_to(legacy_root):
                        if expected and _sha256_file(old_path) != expected:
                            raise ValueError("legacy upload checksum mismatch")
                        if new_path.exists():
                            if expected and _sha256_file(new_path) != expected:
                                raise FileExistsError("account upload target already exists")
                            old_path.unlink()
                        else:
                            moved_from = old_path
                            old_path.replace(new_path)
                            if os.name != "nt":
                                try:
                                    new_path.chmod(0o600)
                                except OSError:
                                    pass
                    elif new_path.is_file() and (not expected or _sha256_file(new_path) == expected):
                        # Recover a process interruption after moving bytes but
                        # before committing the index update.
                        pass
                    else:
                        raise ValueError("legacy upload is missing or outside its managed directory")
                elif origin == ORIGIN_GENERATED:
                    old_path = stored_path.resolve()
                    if (not _allowed_file_path(sid, old_path, ORIGIN_GENERATED, uid)
                            or (current["sha256"] and _sha256_file(old_path) != current["sha256"])):
                        raise ValueError("generated artifact is missing or outside its session")
                    new_path = old_path
                else:
                    raise ValueError("unknown legacy file type")

                conn.execute(
                    "UPDATE session_files SET owner_user_id=?, storage_path=? "
                    "WHERE file_id=? AND owner_user_id=0",
                    (uid, str(new_path), file_id))
                conn.execute(
                    "UPDATE session_file_turn_refs SET owner_user_id=? "
                    "WHERE file_id=? AND owner_user_id=0", (uid, file_id))
                conn.execute(
                    "UPDATE session_activity SET owner_user_id=? "
                    "WHERE session_id=? AND owner_user_id=0", (uid, sid))
                conn.commit()
                migrated += 1
        except Exception as exc:  # noqa: BLE001
            if moved_from is not None and new_path != moved_from and new_path.exists():
                try:
                    moved_from.parent.mkdir(parents=True, exist_ok=True)
                    new_path.replace(moved_from)
                except OSError:
                    logger.exception("legacy upload rollback failed for file %s", file_id)
            logger.warning("legacy account file migration skipped file=%s reason=%s",
                           file_id[:8], type(exc).__name__)
    return migrated


def get_account_file(file_id: str, owner_user_id: int, *, touch: bool = False
                     ) -> dict[str, Any] | None:
    """Resolve one file by opaque id while enforcing account ownership."""
    init_store()
    uid = int(owner_user_id or 0)
    if uid <= 0 or not re.fullmatch(r"[a-f0-9]{32}", str(file_id or "")):
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM session_files WHERE file_id=? AND owner_user_id=?",
            (file_id, uid)).fetchone()
        if not row:
            return None
        if touch:
            conn.execute("UPDATE session_files SET last_accessed_at=? WHERE file_id=?",
                         (time.time(), file_id))
    sid = str(row["session_id"] or "")
    public = _row_to_public(row)
    path = Path(row["storage_path"]).resolve()
    if not _allowed_file_path(sid, path, public.get("origin", ""), uid):
        with _connect() as conn:
            conn.execute("DELETE FROM session_files WHERE file_id=? AND owner_user_id=?",
                         (file_id, uid))
        return None
    public["path"] = path
    if touch:
        touch_session(sid, uid)
    return public


def link_turn_files(session_id: str, owner_user_id: int, client_request_id: str,
                    file_ids: Iterable[str]) -> None:
    """Attach account-library files to a visible user turn for history restore."""
    init_store()
    if (not valid_session(session_id) or int(owner_user_id or 0) <= 0
            or not str(client_request_id or "")):
        return
    uid, rid, now = int(owner_user_id), str(client_request_id)[:64], time.time()
    ids = [str(fid) for fid in file_ids if re.fullmatch(r"[a-f0-9]{32}", str(fid or ""))]
    if not ids:
        return
    with _connect() as conn:
        for fid in ids:
            row = conn.execute(
                "SELECT session_id FROM session_files WHERE file_id=? AND owner_user_id=?",
                (fid, uid)).fetchone()
            if not row:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO session_file_turn_refs "
                "(session_id, owner_user_id, client_request_id, file_id, created_at) "
                "VALUES(?,?,?,?,?)", (session_id, uid, rid, fid, now))
            conn.execute(
                "UPDATE session_files SET last_accessed_at=? WHERE file_id=? AND owner_user_id=?",
                (now, fid, uid))
            touch_session(str(row["session_id"]), uid)
    touch_session(session_id, uid)


def unlink_session_turns(session_id: str, owner_user_id: int) -> None:
    """Remove history links while keeping the account's library files."""
    if not valid_session(session_id) or int(owner_user_id or 0) <= 0:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM session_file_turn_refs WHERE session_id=? AND owner_user_id=?",
                     (session_id, int(owner_user_id)))


def list_turn_files(session_id: str, owner_user_id: int,
                    client_request_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    """Return safe file metadata keyed by the corresponding user request id."""
    init_store()
    uid = int(owner_user_id or 0)
    if uid <= 0 or not valid_session(session_id):
        return {}
    migrate_legacy_files_for_account(uid)
    ids = [str(x)[:64] for x in client_request_ids if str(x or "")]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT r.client_request_id AS turn_request_id, f.* FROM session_file_turn_refs r "
            "JOIN session_files f ON f.file_id=r.file_id "
            f"WHERE r.session_id=? AND r.owner_user_id=? AND r.client_request_id IN ({placeholders}) "
            "ORDER BY f.created_at, f.filename",
            (session_id, uid, *ids)).fetchall()
        # Compatibility recovery: earlier versions persisted the request id on
        # the file row but did not always create the explicit turn-reference
        # row. Recover those files by the normalized idempotency key without
        # moving or copying the account-owned bytes.
        legacy_rows = conn.execute(
            "SELECT CASE WHEN substr(client_request_id,1,5)='crid:' "
            "THEN substr(client_request_id,6) ELSE client_request_id END AS turn_request_id, "
            "* FROM session_files WHERE session_id=? AND owner_user_id=? "
            f"AND (client_request_id IN ({placeholders}) OR "
            f"(substr(client_request_id,1,5)='crid:' AND substr(client_request_id,6) IN ({placeholders}))) "
            "ORDER BY created_at, filename",
            (session_id, uid, *ids, *ids)).fetchall()
    seen = {(str(row["turn_request_id"] or ""), str(row["file_id"] or ""))
            for row in rows}
    rows = list(rows)
    rows.extend(row for row in legacy_rows
                if (str(row["turn_request_id"] or ""), str(row["file_id"] or "")) not in seen)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        source_sid = str(row["session_id"] or "")
        if not _allowed_file_path(source_sid, Path(row["storage_path"]), row["origin"], uid):
            continue
        request_id = str(row["turn_request_id"] or "")
        grouped.setdefault(request_id, []).append(_row_to_public(row))
    return grouped


def delete_account_file(file_id: str, owner_user_id: int) -> bool:
    """Delete one uploaded library file and revoke all conversation references."""
    info = get_account_file(file_id, owner_user_id)
    if not info or info.get("origin") != ORIGIN_UPLOADED:
        return False
    try:
        Path(info["path"]).unlink(missing_ok=True)
    except OSError:
        return False
    uid = int(owner_user_id)
    with _connect() as conn:
        conn.execute("DELETE FROM session_file_turn_refs WHERE file_id=? AND owner_user_id=?",
                     (file_id, uid))
        conn.execute("DELETE FROM session_files WHERE file_id=? AND owner_user_id=?",
                     (file_id, uid))
    try:
        Path(info["path"]).parent.rmdir()
    except OSError:
        pass
    return True


def delete_file(session_id: str, file_id: str, *,
                allow_generated: bool = False,
                owner_user_id: int = 0) -> bool:
    """Delete an uploaded file.

    Generated results are owned by the research pipeline, so the web layer may
    remove the index entry but not the underlying artifact unless explicitly
    allowed.
    """
    info = get_file(session_id, file_id, owner_user_id=owner_user_id)
    if not info:
        return False
    if info.get("origin") == ORIGIN_GENERATED and not allow_generated:
        return False
    if info.get("origin") == ORIGIN_UPLOADED:
        try:
            Path(info["path"]).unlink(missing_ok=True)
        except OSError:
            return False
    with _connect() as conn:
        conn.execute("DELETE FROM session_files WHERE file_id=? AND session_id=?",
                     (file_id, session_id))
        # A turn reference must not outlive the file it points at, otherwise a
        # deleted file keeps rows claiming this turn produced it. The lifetime
        # sweeper already did both statements; the single-file paths did not.
        conn.execute("DELETE FROM session_file_turn_refs "
                     "WHERE file_id=? AND session_id=?", (file_id, session_id))
    return True


# --------------------------------------------------------------- cleanup
def purge_session(session_id: str, *, strict: bool = False,
                  owner_user_id: int | None = None) -> int:
    """Remove a session's file index and uploaded bytes.

    In strict mode, a failed or out-of-scope unlink aborts before index removal;
    account deletion uses this so it never silently claims a complete purge.
    """
    if not valid_session(session_id):
        return 0
    with _connect() as conn:
        if owner_user_id is None:
            rows = conn.execute("SELECT storage_path, origin, owner_user_id FROM session_files "
                                "WHERE session_id=?", (session_id,)).fetchall()
        else:
            rows = conn.execute("SELECT storage_path, origin, owner_user_id FROM session_files "
                                "WHERE session_id=? AND owner_user_id=?",
                                (session_id, int(owner_user_id))).fetchall()
    removed = 0
    failures: list[str] = []
    for row in rows:
        p = Path(row["storage_path"])
        # Only unlink objects this module owns (uploaded); artifacts are
        # managed by their own TTL in research/tools.py.
        if row["origin"] == ORIGIN_UPLOADED:
            try:
                uid = int(row["owner_user_id"] or 0)
                if not _allowed_file_path(session_id, p, ORIGIN_UPLOADED, uid):
                    if p.exists() or p.is_symlink():
                        raise ValueError("uploaded path is outside the session directory")
                else:
                    existed = p.exists()
                    p.unlink(missing_ok=True)
                    removed += int(existed)
            except (OSError, ValueError) as exc:
                failures.append(type(exc).__name__)
    # Clean unindexed leftovers too (for example, a process stopping between
    # writing an upload and inserting its database row). Never follow a
    # symlinked session directory or escape the managed upload root.
    owners = {int(row["owner_user_id"] or 0) for row in rows}
    if not owners:
        owners = {int(owner_user_id or 0)}
    root = FILES_ROOT.resolve()
    for uid in owners:
        directory = _session_dir(session_id, uid)
        try:
            if directory.is_symlink():
                raise ValueError("session upload directory is a symlink")
            resolved = directory.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError("session upload directory escaped the managed root")
            if directory.exists():
                import shutil
                shutil.rmtree(directory)
        except (OSError, ValueError) as exc:
            failures.append(type(exc).__name__)

    if failures and strict:
        raise OSError(f"could not securely purge {len(failures)} uploaded session file(s)")
    if failures:
        import logging
        logging.getLogger(__name__).warning(
            "session purge removed index despite %d uploaded-file cleanup failure(s)",
            len(failures))
    with _connect() as conn:
        if owner_user_id is None:
            conn.execute("DELETE FROM session_files WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM session_file_turn_refs WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM session_activity WHERE session_id=?", (session_id,))
        else:
            conn.execute("DELETE FROM session_files WHERE session_id=? AND owner_user_id=?",
                         (session_id, int(owner_user_id)))
            conn.execute("DELETE FROM session_file_turn_refs WHERE session_id=? AND owner_user_id=?",
                         (session_id, int(owner_user_id)))
            conn.execute("DELETE FROM session_activity WHERE session_id=? AND owner_user_id=?",
                         (session_id, int(owner_user_id)))
    return removed


def cleanup_idle_sessions(idle_days: int = DEFAULT_IDLE_DAYS,
                          activity: Iterable[tuple[str, float]] | None = None) -> dict:
    """Expire account-library files after per-file inactivity.

    ``activity`` is retained for caller compatibility; file retention is now
    based on the file's own last access so using one old session cannot remove
    a file that was recently referenced from another conversation.
    """
    del activity
    if idle_days <= 0:
        return {"purged_sessions": 0, "removed_files": 0, "expired_records": 0}
    cutoff = time.time() - idle_days * 86400
    with _connect() as conn:
        rows = conn.execute(
            "SELECT file_id, session_id, owner_user_id, origin, storage_path "
            "FROM session_files WHERE last_accessed_at < ?", (cutoff,)).fetchall()
    removed = 0
    expired: list[tuple[str, int]] = []
    affected: set[str] = set()
    for row in rows:
        fid = str(row["file_id"])
        sid = str(row["session_id"])
        uid = int(row["owner_user_id"] or 0)
        path = Path(row["storage_path"])
        if row["origin"] == ORIGIN_UPLOADED:
            try:
                if _allowed_file_path(sid, path, ORIGIN_UPLOADED, uid):
                    existed = path.is_file()
                    path.unlink(missing_ok=True)
                    removed += int(existed)
                    if existed:
                        affected.add(sid)
                    try:
                        path.parent.rmdir()
                    except OSError:
                        pass
                elif path.exists() or path.is_symlink():
                    # Keep an unsafe record for manual cleanup; never delete an
                    # arbitrary path just because its database row is old.
                    continue
            except OSError:
                continue
        # Generated bytes are owned and expired by the artifact lifecycle;
        # expiring this library record merely removes the account-visible link.
        expired.append((fid, uid))
        affected.add(sid)
    if expired:
        with _connect() as conn:
            for fid, uid in expired:
                conn.execute("DELETE FROM session_file_turn_refs WHERE file_id=? AND owner_user_id=?",
                             (fid, uid))
                conn.execute("DELETE FROM session_files WHERE file_id=? AND owner_user_id=?",
                             (fid, uid))
    # purged_sessions is the number of sessions that actually lost something in
    # this pass. It used to be hardcoded 0, which made the field look calm while
    # files were being reclaimed.
    return {"purged_sessions": len(affected), "removed_files": removed,
            "expired_records": len(expired)}


def stats() -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT origin, COUNT(*) n, COALESCE(SUM(size_bytes),0) b "
            "FROM session_files GROUP BY origin").fetchall()
    return {r["origin"]: {"files": r["n"], "bytes": r["b"]} for r in rows}

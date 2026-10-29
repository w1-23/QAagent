"""Session-scoped file index for the Wweb "会话文件" panel.

Design boundary (deliberate, per the agreed scope):

* This is a *light file manager and web previewer*, not a knowledge base, not a
  document-understanding system, and not an editor.
* Uploaded material and generated results share ONE list, but a generated file
  is registered once - it is never copied into a second store.  The existing
  ``artifacts`` table keeps the ``artifact_id`` so old download links in chat
  history continue to work.
* Nothing here writes to the shared RAG corpus, and nothing here invokes a
  model.  Opening a preview is an ordinary HTTP file read.
* Access is always checked against session ownership; ``storage_path`` never
  leaves the server.

Retention: files expire on **session inactivity**, not per-file age, so a
research session that is still in use never loses its material mid-project.
The legacy 24-hour per-artifact TTL is superseded for files registered here.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from src.config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "state" / "session_files.sqlite3"
FILES_ROOT = PROJECT_ROOT / "state" / "session_files"

# Retention policy: sweep files of sessions idle longer than this.
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
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_files_path "
                     "ON session_files(storage_path)")
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


def _session_dir(session_id: str) -> Path:
    return (FILES_ROOT / session_id).resolve()


def register_file(*, session_id: str, owner_user_id: int, filename: str,
                  content: bytes | None = None, storage_path: str = "",
                  mime: str = "", origin: str = ORIGIN_UPLOADED,
                  artifact_id: str = "") -> dict[str, Any]:
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
        root = _session_dir(session_id)
        root.mkdir(parents=True, exist_ok=True)
        file_id = uuid.uuid4().hex
        path = (root / f"{file_id}_{safe}").resolve()
        if not path.is_relative_to(root):
            raise ValueError("unsafe storage path")
        path.write_bytes(content)
        size = len(content)
        digest = hashlib.sha256(content).hexdigest()
    else:
        if not storage_path:
            raise ValueError("storage_path is required when content is absent")
        path = Path(storage_path).resolve()
        if not path.is_file():
            raise ValueError("storage_path does not exist")
        file_id = uuid.uuid4().hex
        size = path.stat().st_size
        digest = _sha256_file(path)

    now = time.time()
    with _connect() as conn:
        # A generated artifact is registered once; re-registering the same
        # object returns the existing row instead of creating a duplicate.
        if artifact_id:
            existing = conn.execute(
                "SELECT * FROM session_files WHERE session_id=? AND artifact_id=?",
                (session_id, artifact_id)).fetchone()
            if existing:
                return _row_to_public(existing)
        conn.execute(
            "INSERT INTO session_files(file_id, session_id, owner_user_id, filename,"
            " origin, mime_type, size_bytes, sha256, storage_path, artifact_id,"
            " created_at, last_accessed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (file_id, session_id, int(owner_user_id or 0), safe, origin,
             mime or _guess_mime(safe), size, digest, str(path), artifact_id,
             now, now))
        row = conn.execute("SELECT * FROM session_files WHERE file_id=?",
                           (file_id,)).fetchone()
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


def _row_to_public(row: sqlite3.Row | dict) -> dict[str, Any]:
    """Frontend-facing shape: never exposes storage_path."""
    d = dict(row)
    d.pop("storage_path", None)
    d["kind"] = kind_for(d.get("filename", ""), d.get("mime_type", ""))
    d["download_url"] = (f"/api/v2/session/{d['session_id']}"
                         f"/files/{d['file_id']}/download")
    d["preview_url"] = (f"/api/v2/session/{d['session_id']}"
                        f"/files/{d['file_id']}/preview")
    return d


# ------------------------------------------------------------------ read
def list_files(session_id: str, owner_user_id: int = 0) -> list[dict[str, Any]]:
    """Files of one session, newest first. Touches session activity."""
    if not valid_session(session_id):
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM session_files WHERE session_id=? "
            "ORDER BY created_at DESC", (session_id,)).fetchall()
    if rows:
        touch_session(session_id, owner_user_id)
    return [_row_to_public(r) for r in rows]


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
        if touch:
            conn.execute("UPDATE session_files SET last_accessed_at=? "
                         "WHERE file_id=?", (time.time(), file_id))
    public = _row_to_public(row)
    path = Path(dict(row)["storage_path"]).resolve()
    root_ok = (path.is_relative_to(_session_dir(session_id).parent)
               or path.is_relative_to((PROJECT_ROOT / "state" / "artifacts").resolve()))
    if not root_ok or not path.is_file():
        return None
    public["path"] = path
    return public


def delete_file(session_id: str, file_id: str, *,
                allow_generated: bool = False) -> bool:
    """Delete an uploaded file.

    Generated results are owned by the research pipeline, so the web layer may
    remove the index entry but not the underlying artifact unless explicitly
    allowed.
    """
    info = get_file(session_id, file_id)
    if not info:
        return False
    if info.get("origin") == ORIGIN_GENERATED and not allow_generated:
        return False
    with _connect() as conn:
        conn.execute("DELETE FROM session_files WHERE file_id=?", (file_id,))
    if info.get("origin") == ORIGIN_UPLOADED:
        try:
            Path(info["path"]).unlink(missing_ok=True)
        except OSError:
            pass
    return True


# --------------------------------------------------------------- cleanup
def purge_session(session_id: str) -> int:
    """Remove every file of a session and its directory. Returns file count."""
    if not valid_session(session_id):
        return 0
    with _connect() as conn:
        rows = conn.execute("SELECT storage_path, origin FROM session_files "
                            "WHERE session_id=?", (session_id,)).fetchall()
        conn.execute("DELETE FROM session_files WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM session_activity WHERE session_id=?", (session_id,))
    removed = 0
    for row in rows:
        p = Path(row["storage_path"])
        # Only unlink objects this module owns (uploaded); artifacts are
        # managed by their own TTL in research/tools.py.
        if row["origin"] == ORIGIN_UPLOADED:
            try:
                p.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    try:
        d = _session_dir(session_id)
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    except OSError:
        pass
    return removed


def cleanup_idle_sessions(idle_days: int = DEFAULT_IDLE_DAYS,
                          activity: Iterable[tuple[str, float]] | None = None) -> dict:
    """Delete files of sessions inactive beyond ``idle_days``.

    ``activity`` lets the caller inject session last-activity times (from the
    sessions table) so retention follows the session, not individual files.
    When omitted, this module's own ``session_activity`` table is used.
    """
    if idle_days <= 0:
        return {"purged_sessions": 0, "removed_files": 0}
    cutoff = time.time() - idle_days * 86400
    if activity is None:
        with _connect() as conn:
            activity = [(r["session_id"], float(r["last_active_at"]))
                        for r in conn.execute(
                            "SELECT session_id, last_active_at FROM session_activity")]
    stale = [sid for sid, last in activity if last < cutoff]
    purged = 0
    files_removed = 0
    for sid in stale:
        n = purge_session(sid)
        purged += 1
        files_removed += n
    return {"purged_sessions": purged, "removed_files": files_removed}


def stats() -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT origin, COUNT(*) n, COALESCE(SUM(size_bytes),0) b "
            "FROM session_files GROUP BY origin").fetchall()
    return {r["origin"]: {"files": r["n"], "bytes": r["b"]} for r in rows}

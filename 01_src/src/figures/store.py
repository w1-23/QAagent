"""Where figure projects live.

A *figure project* is one figure the user can keep: its source (a
validated specification, or a script), the semantic manifest that describes
what may be edited, the user's own edits, and the exported files.

The store is a small SQLite table under the service's state directory. It
follows the same shape as the account file library, and it inherits the same
ownership rule: **every query carries the owner's id in its WHERE clause**.
A row that belongs to somebody else is not found, not found-and-refused —
an attacker must not be able to tell "this exists but is not yours" from
"this does not exist".

What is deliberately *not* here
------------------------------
The rendered files. They live in the account file library, registered as
ordinary account files, because that is where the service already knows how
to store, list, download and delete a user's files with the right
permissions. This table holds the pointers and the editing state; the bytes
live with the rest of the user's material.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

# The state directory is resolved lazily so a test can point the store at a
# temporary directory without importing the whole service.
_STATE_DIR: Path | None = None


def set_state_dir(path: Path | None) -> None:
    """Point the store at a different state directory (used by tests)."""
    global _STATE_DIR
    _STATE_DIR = Path(path) if path else None


def _db_path() -> Path:
    if _STATE_DIR is not None:
        root = _STATE_DIR
    else:
        from src.config import PROJECT_ROOT
        root = PROJECT_ROOT / "state"
    path = root / "figure_projects.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS figure_projects (
        figure_id      TEXT PRIMARY KEY,
        owner_user_id  INTEGER NOT NULL,
        session_id     TEXT NOT NULL DEFAULT '',
        title          TEXT NOT NULL DEFAULT '',
        source_kind    TEXT NOT NULL DEFAULT 'spec',
        source_json    TEXT NOT NULL DEFAULT '{}',
        manifest_json  TEXT NOT NULL DEFAULT '{}',
        overrides_json TEXT NOT NULL DEFAULT '[]',
        exports_json   TEXT NOT NULL DEFAULT '{}',
        status         TEXT NOT NULL DEFAULT 'ready',
        last_error     TEXT NOT NULL DEFAULT '',
        created_at     REAL NOT NULL,
        updated_at     REAL NOT NULL)""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(figure_projects)")}
    for name, declaration in (
        ("data_refs_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("render_version", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE figure_projects ADD COLUMN {name} {declaration}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_figure_projects_owner "
                 "ON figure_projects(owner_user_id, updated_at DESC)")
    return conn


def _row_to_project(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    for key, fallback in (("source_json", {}), ("manifest_json", {}),
                          ("overrides_json", []), ("exports_json", {}),
                          ("data_refs_json", [])):
        try:
            record[key] = json.loads(record.get(key) or json.dumps(fallback))
        except (TypeError, ValueError):
            record[key] = fallback
    return record


def create_project(*, owner_user_id: int, title: str = "",
                   source_kind: str = "spec", source: Any = None,
                   session_id: str = "",
                   data_refs: list[Any] | None = None) -> dict[str, Any]:
    """Create a project and return it."""
    figure_id = uuid.uuid4().hex
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO figure_projects (figure_id, owner_user_id, session_id, title,"
            " source_kind, source_json, manifest_json, overrides_json, exports_json,"
            " data_refs_json, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (figure_id, int(owner_user_id), str(session_id or ""), str(title or ""),
             str(source_kind or "spec"), json.dumps(source or {}, ensure_ascii=False),
             "{}", "[]", "{}", json.dumps(data_refs or [], ensure_ascii=False),
             "ready", now, now))
        row = conn.execute(
            "SELECT * FROM figure_projects WHERE figure_id=? AND owner_user_id=?",
            (figure_id, int(owner_user_id))).fetchone()
    return _row_to_project(row) or {}


def get_project(figure_id: str, owner_user_id: int) -> dict[str, Any] | None:
    """Fetch one project owned by this account, or ``None``."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM figure_projects WHERE figure_id=? AND owner_user_id=?",
            (str(figure_id), int(owner_user_id))).fetchone()
    return _row_to_project(row)


def list_projects(owner_user_id: int, *, limit: int = 100,
                  offset: int = 0) -> list[dict[str, Any]]:
    """The account's projects, most recently updated first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM figure_projects WHERE owner_user_id=?"
            " ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (int(owner_user_id), max(1, int(limit)), max(0, int(offset)))).fetchall()
    return [_row_to_project(row) or {} for row in rows]


def update_project(figure_id: str, owner_user_id: int, **fields: Any) -> bool:
    """Update the mutable fields of a project.

    Only the fields that describe the figure's *current state* may be
    written; the identity, the owner and the creation time are not among
    them. A project of somebody else is not updated — the WHERE clause
    matches nothing and the function reports failure.
    """
    allowed = ("title", "source_kind", "source_json", "manifest_json",
               "overrides_json", "exports_json", "status", "last_error",
               "data_refs_json", "render_version")
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return False
    assignments = []
    values: list[Any] = []
    for key, value in updates.items():
        if key in {"source_json", "manifest_json", "exports_json", "data_refs_json"}:
            value = json.dumps(value if value is not None else {}, ensure_ascii=False)
        elif key == "overrides_json":
            value = json.dumps(value if value is not None else [], ensure_ascii=False)
        assignments.append(f"{key}=?")
        values.append(value)
    assignments.append("updated_at=?")
    values.append(time.time())
    values.extend([str(figure_id), int(owner_user_id)])
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE figure_projects SET {', '.join(assignments)}"
            " WHERE figure_id=? AND owner_user_id=?", values)
        return cursor.rowcount > 0


def delete_project(figure_id: str, owner_user_id: int) -> bool:
    """Delete a project owned by this account."""
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM figure_projects WHERE figure_id=? AND owner_user_id=?",
            (str(figure_id), int(owner_user_id)))
        return cursor.rowcount > 0


def purge_account(owner_user_id: int) -> int:
    """Delete every project belonging to an account.

    Called when an account is deleted. The rows go before the account's
    identity does, so a half-completed purge cannot leave a resurrected
    figure behind.
    """
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM figure_projects WHERE owner_user_id=?",
            (int(owner_user_id),))
        return int(cursor.rowcount or 0)


def count_projects(owner_user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM figure_projects WHERE owner_user_id=?",
            (int(owner_user_id),)).fetchone()
    return int(row["n"]) if row else 0


__all__ = [
    "count_projects", "create_project", "delete_project", "get_project",
    "list_projects", "purge_account", "set_state_dir", "update_project",
]

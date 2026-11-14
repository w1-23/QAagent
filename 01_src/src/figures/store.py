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


class ProjectQuotaExceeded(ValueError):
    """Existing projects remain available when creation capacity is exhausted."""


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


class _StoreConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=30, isolation_level=None,
                           factory=_StoreConnection)
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
        ("pins_version", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE figure_projects ADD COLUMN {name} {declaration}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_figure_projects_owner "
                 "ON figure_projects(owner_user_id, updated_at DESC)")
    conn.execute("CREATE TABLE IF NOT EXISTS figure_project_pins "
                 "(resource_id TEXT PRIMARY KEY, figure_id TEXT NOT NULL, owner_user_id INTEGER NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS figure_revisions ("
                 "figure_id TEXT NOT NULL, owner_user_id INTEGER NOT NULL, "
                 "revision INTEGER NOT NULL, snapshot_json TEXT NOT NULL, created_at REAL NOT NULL, "
                 "PRIMARY KEY(figure_id, owner_user_id, revision))")
    return conn


def _pin_files(owner: int, figure_id: str, file_ids: list[str]) -> tuple[str, list[dict[str, Any]]]:
    if not file_ids:
        return "", []
    from src.session.files import replace_resource_references
    resource_id = f"{figure_id}:{uuid.uuid4().hex}"
    refs = replace_resource_references(owner, "figure", resource_id, file_ids)
    return resource_id, refs


def _release_pin(owner: int, resource_id: str) -> None:
    if resource_id:
        from src.session.files import release_resource_references
        release_resource_references(owner, "figure", resource_id)


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
    """Create a project while the account admits writes."""
    from src.core.account_leases import account_lease
    with account_lease(int(owner_user_id)):
        return _create_project(owner_user_id=owner_user_id, title=title,
                               source_kind=source_kind, source=source,
                               session_id=session_id, data_refs=data_refs)


def _create_project(*, owner_user_id: int, title: str, source_kind: str,
                    source: Any, session_id: str, data_refs: list[Any] | None) -> dict[str, Any]:
    figure_id = uuid.uuid4().hex
    now = time.time()
    data_refs = list(data_refs or [])
    resource_id, pinned = _pin_files(int(owner_user_id), figure_id,
        [str(ref["file_id"]) for ref in data_refs if isinstance(ref, dict) and ref.get("file_id")])
    hashes = {ref["file_id"]: ref["sha256"] for ref in pinned}
    data_refs = [{**ref, "sha256": hashes[ref["file_id"]]} if isinstance(ref, dict)
                 and ref.get("file_id") in hashes else ref for ref in data_refs]
    try:
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            import os
            maximum = int(os.environ.get("CITRUS_FIGURE_PROJECTS_PER_ACCOUNT", "500"))
            if maximum <= 0:
                raise ValueError("CITRUS_FIGURE_PROJECTS_PER_ACCOUNT must be positive")
            count = conn.execute("SELECT COUNT(*) FROM figure_projects WHERE owner_user_id=?",
                                 (int(owner_user_id),)).fetchone()[0]
            if count >= maximum:
                raise ProjectQuotaExceeded("账号图件项目数量达到上限，请删除不再需要的图件后重试。")
            conn.execute(
                "INSERT INTO figure_projects (figure_id, owner_user_id, session_id, title,"
                " source_kind, source_json, manifest_json, overrides_json, exports_json,"
                " data_refs_json, status, created_at, updated_at, pins_version)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (figure_id, int(owner_user_id), str(session_id or ""), str(title or ""),
                 str(source_kind or "spec"), json.dumps(source or {}, ensure_ascii=False),
                 "{}", "[]", "{}", json.dumps(data_refs, ensure_ascii=False),
                 "ready", now, now))
            if resource_id:
                conn.execute("INSERT INTO figure_project_pins VALUES (?,?,?)",
                             (resource_id, figure_id, int(owner_user_id)))
            row = conn.execute(
                "SELECT * FROM figure_projects WHERE figure_id=? AND owner_user_id=?",
                (figure_id, int(owner_user_id))).fetchone()
    except BaseException:
        _release_pin(int(owner_user_id), resource_id)
        raise
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


def update_project(figure_id: str, owner_user_id: int, *,
                   expected_revision: int | None = None, **fields: Any) -> bool:
    """Update a project while the account admits writes."""
    from src.core.account_leases import account_lease
    with account_lease(int(owner_user_id)):
        return _update_project(figure_id, owner_user_id,
                               expected_revision=expected_revision, **fields)


def _update_project(figure_id: str, owner_user_id: int, *,
                    expected_revision: int | None = None, **fields: Any) -> bool:
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
    condition = " WHERE figure_id=? AND owner_user_id=?"
    if expected_revision is not None:
        condition += " AND render_version=?"
        values.append(int(expected_revision))
    before = get_project(figure_id, owner_user_id)
    if before is None:
        return False
    exports = updates.get("exports_json", before.get("exports_json")) or {}
    refs = updates.get("data_refs_json", before.get("data_refs_json")) or []
    # One pin set protects the source and the latest saved render. Replacing
    # it never deletes artifact bytes; other consumers retain their own pins.
    file_ids = [str(item["file_id"]) for item in exports.values()
                if isinstance(item, dict) and item.get("file_id")] if isinstance(exports, dict) else []
    file_ids.extend(str(ref["file_id"]) for ref in refs
                    if isinstance(ref, dict) and ref.get("file_id"))
    resource_id, _ = _pin_files(int(owner_user_id), str(figure_id), file_ids)
    retired_pins = []
    committed = False
    try:
        with _connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT * FROM figure_projects WHERE figure_id=? AND owner_user_id=?",
                (str(figure_id), int(owner_user_id))).fetchone()
            current_before = _row_to_project(previous)
            if current_before != before or (expected_revision is not None
                    and int(before.get("render_version") or 0) != int(expected_revision)):
                return False
            if int(updates.get("render_version", before.get("render_version") or 0)) < int(before.get("render_version") or 0):
                raise ValueError("figure revisions must not move backwards")
            _record_revision(conn, previous)
            cursor = conn.execute(
                f"UPDATE figure_projects SET {', '.join(assignments)}" + condition, values)
            committed = cursor.rowcount > 0
            if committed:
                current = conn.execute(
                    "SELECT * FROM figure_projects WHERE figure_id=? AND owner_user_id=?",
                    (str(figure_id), int(owner_user_id))).fetchone()
                _record_revision(conn, current)
                conn.execute("DELETE FROM figure_revisions WHERE figure_id=? AND owner_user_id=? AND revision<>?",
                             (str(figure_id), int(owner_user_id), int(current["render_version"])))
                retired_pins = conn.execute(
                    "SELECT resource_id FROM figure_project_pins WHERE figure_id=? AND owner_user_id=?",
                    (str(figure_id), int(owner_user_id))).fetchall()
                if resource_id:
                    conn.execute("INSERT INTO figure_project_pins VALUES (?,?,?)",
                                 (resource_id, str(figure_id), int(owner_user_id)))
    except BaseException:
        committed = False
        raise
    finally:
        if not committed:
            _release_pin(int(owner_user_id), resource_id)
    # Release only after the new snapshot and pin identity have committed.
    # A cleanup error leaves recorded old pins, never an unprotected current
    # render, and the next save/delete can safely retry their release.
    try:
        _release_recorded_pins(int(owner_user_id), retired_pins)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Retired figure pins await cleanup")
    return committed


def _record_revision(conn: sqlite3.Connection, row: sqlite3.Row | None) -> None:
    project = _row_to_project(row)
    if not project or int(project.get("render_version") or 0) < 1:
        return
    revision = int(project["render_version"])
    identity = (project["figure_id"], int(project["owner_user_id"]), revision)
    existing = conn.execute(
        "SELECT snapshot_json FROM figure_revisions WHERE figure_id=? AND owner_user_id=? AND revision=?",
        identity).fetchone()
    if existing:
        snapshot = json.loads(existing["snapshot_json"])
        for key in ("source_kind", "source_json", "manifest_json", "overrides_json", "exports_json"):
            if snapshot.get(key) != project.get(key):
                raise ValueError("render changes require a new figure revision")
        return
    conn.execute("INSERT INTO figure_revisions VALUES (?,?,?,?,?)",
                 (*identity, json.dumps(project, ensure_ascii=False), time.time()))


def list_revisions(figure_id: str, owner_user_id: int) -> list[dict[str, Any]]:
    """Return immutable render snapshots only while the owner still has the project."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT r.snapshot_json FROM figure_revisions r JOIN figure_projects p "
            "ON p.figure_id=r.figure_id AND p.owner_user_id=r.owner_user_id "
            "WHERE r.figure_id=? AND r.owner_user_id=? ORDER BY r.revision DESC LIMIT 1",
            (str(figure_id), int(owner_user_id))).fetchall()
    return [json.loads(row["snapshot_json"]) for row in rows]


def delete_project(figure_id: str, owner_user_id: int) -> bool:
    """Delete the project first, then release its source and render pins."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        pins = conn.execute(
            "SELECT resource_id FROM figure_project_pins WHERE figure_id=? AND owner_user_id=?",
            (str(figure_id), int(owner_user_id))).fetchall()
        cursor = conn.execute(
            "DELETE FROM figure_projects WHERE figure_id=? AND owner_user_id=?",
            (str(figure_id), int(owner_user_id)))
        removed = cursor.rowcount > 0
        conn.execute("DELETE FROM figure_revisions WHERE figure_id=? AND owner_user_id=?",
                     (str(figure_id), int(owner_user_id)))
    _release_recorded_pins(int(owner_user_id), pins)
    return removed


def _release_recorded_pins(owner: int, pins: list) -> None:
    for pin in pins:
        resource_id = str(pin["resource_id"])
        _release_pin(owner, resource_id)
        with _connect() as conn:
            conn.execute("DELETE FROM figure_project_pins WHERE resource_id=? AND owner_user_id=?",
                         (resource_id, owner))


def purge_account(owner_user_id: int) -> int:
    """Delete every project belonging to an account.

    Called when an account is deleted. The rows go before the account's
    identity does, so a half-completed purge cannot leave a resurrected
    figure behind.
    """
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        pins = conn.execute("SELECT resource_id FROM figure_project_pins WHERE owner_user_id=?",
                            (int(owner_user_id),)).fetchall()
        cursor = conn.execute(
            "DELETE FROM figure_projects WHERE owner_user_id=?",
            (int(owner_user_id),))
        removed = int(cursor.rowcount or 0)
        conn.execute("DELETE FROM figure_revisions WHERE owner_user_id=?", (int(owner_user_id),))
    _release_recorded_pins(int(owner_user_id), pins)
    return removed


def migrate_resource_references() -> dict[str, int]:
    """Protect legacy projects before any file deletion or TTL sweep.

    Each project is resumable independently. Pin files first, then attach the
    pin and hashes only if the source snapshot is unchanged. Missing/corrupt
    legacy files stop cleanup: guessing that they are disposable is unsafe.
    This intentionally does not rewrite source, rendered bytes or timestamps.
    """
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM figure_projects WHERE pins_version < 1").fetchall()
    migrated = 0
    for row in rows:
        project = _row_to_project(row)
        owner = int(project["owner_user_id"])
        figure_id = str(project["figure_id"])
        refs = project["data_refs_json"]
        exports = project["exports_json"]
        file_ids = [str(ref["file_id"]) for ref in refs
                    if isinstance(ref, dict) and ref.get("file_id")]
        file_ids.extend(str(item["file_id"]) for item in exports.values()
                        if isinstance(item, dict) and item.get("file_id"))
        resource_id, pinned = _pin_files(owner, figure_id, file_ids)
        committed = False
        try:
            hashes = {ref["file_id"]: ref["sha256"] for ref in pinned}
            refs = [{**ref, "sha256": hashes[ref["file_id"]]} if isinstance(ref, dict)
                    and ref.get("file_id") in hashes else ref for ref in refs]
            with _connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    "UPDATE figure_projects SET data_refs_json=?, pins_version=1 "
                    "WHERE figure_id=? AND owner_user_id=? AND pins_version=0 AND updated_at=?",
                    (json.dumps(refs, ensure_ascii=False), figure_id, owner, project["updated_at"]))
                committed = cursor.rowcount > 0
                if committed:
                    current = conn.execute(
                        "SELECT * FROM figure_projects WHERE figure_id=? AND owner_user_id=?",
                        (figure_id, owner)).fetchone()
                    _record_revision(conn, current)
                if committed and resource_id:
                    conn.execute("INSERT INTO figure_project_pins VALUES (?,?,?)",
                                 (resource_id, figure_id, owner))
            migrated += int(committed)
        except BaseException:
            committed = False
            raise
        finally:
            if not committed:
                _release_pin(owner, resource_id)
    with _connect() as conn:
        pending = conn.execute("SELECT COUNT(*) FROM figure_projects WHERE pins_version < 1").fetchone()[0]
        abandoned = conn.execute(
            "SELECT p.resource_id, p.owner_user_id FROM figure_project_pins p "
            "LEFT JOIN figure_projects f ON f.figure_id=p.figure_id "
            "AND f.owner_user_id=p.owner_user_id WHERE f.figure_id IS NULL").fetchall()
    for pin in abandoned:
        _release_recorded_pins(int(pin["owner_user_id"]), [pin])
    if pending:
        raise RuntimeError("figure reference migration raced with an update; retry cleanup later")
    return {"migrated": migrated, "released_abandoned": len(abandoned)}


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

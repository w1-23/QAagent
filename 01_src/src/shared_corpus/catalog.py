"""Catalog for the approved-account shared literature corpus.

Proposals may name only an existing ``src.core.corpus_review`` candidate id.
They never carry a model-supplied path or URL.  Preparing a proposal re-reads
the archived candidate bytes, verifies every recorded hash, and creates an
immutable ``documents/<document_id>/<version_id>`` version.

Publication rights are independent of retention review: an administrator must
grant them explicitly, or the original candidate's server-written metadata
must carry a verified trusted open licence.  Retention approval alone is never
enough, so the default state is ``needs_review`` (fail closed).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import prepare

logger = logging.getLogger(__name__)

MAX_EVIDENCE_CHARS = 2000
MAX_REASON_CHARS = 500
MAX_LIST_LIMIT = 100
PROPOSAL_STATES = frozenset({
    "pending", "prepared", "needs_review", "published", "revoked", "failed",
})
RIGHT_STATES = frozenset({
    "needs_review", "trusted_metadata", "admin_grant", "revoked",
})
_PROPOSAL_RE = re.compile(r"^p_[a-f0-9]{32}$")
_AUDIT_FIELDS = frozenset({
    "candidate_id", "document_id", "version_id", "code", "state", "basis",
})


def _iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat(
        timespec="seconds")


def _user_id(value: object, name: str = "owner_user_id") -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name}_invalid")
    if isinstance(value, int):
        number = value
    else:
        text = str(value or "").strip()
        if not text.isdigit():
            raise ValueError(f"{name}_invalid")
        number = int(text)
    if number <= 0:
        raise ValueError(f"{name}_invalid")
    return number


def _candidate_id(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("candidate_id_invalid")
    if isinstance(value, int):
        number = value
    else:
        text = str(value or "").strip()
        if not text.isdigit():
            raise ValueError("candidate_id_invalid")
        number = int(text)
    if number <= 0:
        raise ValueError("candidate_id_invalid")
    return number


def _proposal_id(value: object) -> str:
    text = str(value or "").strip()
    if not _PROPOSAL_RE.fullmatch(text):
        raise ValueError("proposal_id_invalid")
    return text


def _evidence(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                              default=str)
        except (TypeError, ValueError):
            text = str(value)
    else:
        text = str(value)
    return prepare.public_text(text, MAX_EVIDENCE_CHARS, keep_newlines=True)


def _reason(value: Any) -> str:
    return prepare.public_text(value, MAX_REASON_CHARS)


def _row_value(row: sqlite3.Row, key: str, default: Any = "") -> Any:
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


class Catalog:
    """SQLite catalog plus immutable document storage under one root."""

    def __init__(self, root: str | Path):
        raw = Path(root)
        if raw.is_symlink():
            raise ValueError("catalog_root_symlink")
        self.root = raw.expanduser().resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.root / "catalog.sqlite3"
        self.documents = self.root / "documents"
        if self.documents.is_symlink():
            raise ValueError("catalog_documents_symlink")
        self.documents.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._ensure_schema()

    # ------------------------------------------------------------------ SQLite
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _read(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self):
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.executescript("""
                CREATE TABLE IF NOT EXISTS shared_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    candidate_id INTEGER NOT NULL,
                    owner_user_id INTEGER NOT NULL,
                    gap_evidence TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    rights TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    document_id TEXT,
                    version_id TEXT,
                    candidate_title TEXT NOT NULL DEFAULT '',
                    candidate_doi TEXT NOT NULL DEFAULT '',
                    candidate_year TEXT NOT NULL DEFAULT '',
                    candidate_source_kind TEXT NOT NULL DEFAULT '',
                    candidate_content_sha256 TEXT NOT NULL DEFAULT '',
                    candidate_raw_sha256 TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(candidate_id, owner_user_id)
                );
                CREATE INDEX IF NOT EXISTS ix_shared_proposals_owner
                    ON shared_proposals(owner_user_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS shared_documents (
                    document_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    candidate_id INTEGER NOT NULL,
                    proposal_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    doi TEXT NOT NULL DEFAULT '',
                    year TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT '',
                    content_sha256 TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    schema_version INTEGER NOT NULL,
                    section_path TEXT NOT NULL DEFAULT '[]',
                    prepared_at REAL NOT NULL,
                    superseded_by TEXT,
                    PRIMARY KEY(document_id, version_id)
                );
                CREATE INDEX IF NOT EXISTS ix_shared_documents_candidate
                    ON shared_documents(candidate_id);
                CREATE TABLE IF NOT EXISTS shared_rights (
                    document_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    basis TEXT NOT NULL,
                    actor_user_id INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(document_id, version_id)
                );
                CREATE INDEX IF NOT EXISTS ix_shared_rights_status
                    ON shared_rights(status);
                CREATE TABLE IF NOT EXISTS shared_audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    at REAL NOT NULL,
                    actor_user_id INTEGER NOT NULL DEFAULT 0,
                    action TEXT NOT NULL,
                    subject TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT ''
                );
                """)
                connection.commit()
            finally:
                connection.close()
            if hasattr(self.db_path, "chmod"):
                try:
                    self.db_path.chmod(0o600)
                except OSError:
                    pass

    def _audit(self, connection: sqlite3.Connection, actor: int, action: str,
               subject: str, detail: dict[str, Any] | None = None) -> None:
        safe = {key: value for key, value in (detail or {}).items()
                if key in _AUDIT_FIELDS}
        payload = json.dumps(safe, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))[:2000]
        connection.execute(
            "INSERT INTO shared_audit (at, actor_user_id, action, subject, detail) "
            "VALUES (?,?,?,?,?)",
            (time.time(), int(actor), str(action)[:64], str(subject)[:128],
             payload))

    # ------------------------------------------------------------- corpus store
    @staticmethod
    def _candidate_store() -> Path:
        from src.session.manager import DB_PATH
        return Path(DB_PATH).resolve().parent / "corpus_review.sqlite3"

    @staticmethod
    def _archive_root() -> Path:
        from src.config import settings
        return Path(settings.DATA_DIR).resolve() / "_review_queue" / "archive"

    def _load_candidate(self, candidate_id: int) -> dict[str, Any]:
        store = self._candidate_store()
        if not store.is_file():
            raise ValueError("candidate_store_unavailable")
        try:
            connection = sqlite3.connect(
                store.as_uri() + "?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            raise ValueError("candidate_store_unavailable") from exc
        try:
            row = connection.execute(
                "SELECT * FROM corpus_candidates WHERE id = ?",
                (int(candidate_id),)).fetchone()
        except sqlite3.Error as exc:
            raise ValueError("candidate_store_unavailable") from exc
        finally:
            connection.close()
        if row is None:
            raise KeyError(int(candidate_id))
        return {
            "candidate_id": int(_row_value(row, "id", 0)),
            "title": str(_row_value(row, "title", "") or ""),
            "doi": str(_row_value(row, "doi", "") or ""),
            "url": str(_row_value(row, "url", "") or ""),
            "year": str(_row_value(row, "year", "") or ""),
            "source_kind": str(_row_value(row, "source_kind", "") or
                               _row_value(row, "origin", "") or ""),
            "status": str(_row_value(row, "status", "") or ""),
            "stored_path": str(_row_value(row, "stored_path", "") or ""),
            "raw_sha256": str(_row_value(row, "raw_sha256", "") or ""),
            "content_sha256": str(_row_value(row, "content_sha256", "") or ""),
        }

    # ---------------------------------------------------------------- proposals
    def _row_to_proposal(self, row: sqlite3.Row, *, deduped: bool = False,
                         metadata: dict[str, Any] | None = None,
                         publishable: bool | None = None) -> dict[str, Any]:
        item = {
            "proposal_id": row["proposal_id"],
            "candidate_id": int(row["candidate_id"]),
            "owner_user_id": int(row["owner_user_id"]),
            "state": row["state"],
            "rights": row["rights"],
            "document_id": row["document_id"],
            "version_id": row["version_id"],
            "title": row["candidate_title"],
            "doi": row["candidate_doi"],
            "year": row["candidate_year"],
            "source_kind": row["candidate_source_kind"],
            "reason": row["reason"],
            "gap_evidence": row["gap_evidence"],
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
            "deduped": bool(deduped),
        }
        if metadata is not None:
            item["metadata"] = metadata
            item["content_sha256"] = metadata.get("content_sha256")
            item["char_count"] = int(metadata.get("char_count") or 0)
            item["chunk_count"] = int(metadata.get("chunk_count") or 0)
            item["schema_version"] = int(metadata.get("schema_version") or 0)
            item["section_path"] = list(metadata.get("section_path") or [])
        if publishable is not None:
            item["publishable"] = bool(publishable)
        return item
    def propose(self, candidate_id: int, owner_user_id: int, gap_evidence: Any = "") -> dict[str, Any]:
        cid = _candidate_id(candidate_id)
        owner = _user_id(owner_user_id)
        candidate = self._load_candidate(cid)
        if candidate["status"] == "rejected":
            raise ValueError("candidate_rejected")
        evidence = _evidence(gap_evidence)
        now = time.time()
        with self._write() as connection:
            existing = connection.execute(
                "SELECT * FROM shared_proposals WHERE candidate_id = ? "
                "AND owner_user_id = ?", (cid, owner)).fetchone()
            if existing is not None:
                return self._row_to_proposal(existing, deduped=True,
                                             publishable=self._publishable_state(
                                                 connection, existing["document_id"],
                                                 existing["version_id"]))
            proposal_id = "p_" + uuid.uuid4().hex
            connection.execute(
                "INSERT INTO shared_proposals (proposal_id, candidate_id, "
                "owner_user_id, gap_evidence, state, rights, reason, "
                "candidate_title, candidate_doi, candidate_year, "
                "candidate_source_kind, candidate_content_sha256, "
                "candidate_raw_sha256, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (proposal_id, cid, owner, evidence, "pending", "needs_review", "",
                 prepare.public_text(candidate["title"], 512),
                 prepare.public_text(candidate["doi"], 256),
                 prepare.public_text(candidate["year"], 16),
                 prepare.public_text(candidate["source_kind"], 64),
                 candidate["content_sha256"], candidate["raw_sha256"],
                 now, now))
            self._audit(connection, owner, "propose", proposal_id,
                        {"candidate_id": cid, "state": "pending"})
            row = connection.execute(
                "SELECT * FROM shared_proposals WHERE proposal_id = ?",
                (proposal_id,)).fetchone()
        return self._row_to_proposal(row, publishable=False)

    def prepare(self, proposal_id: str, owner_user_id: int) -> dict[str, Any]:
        pid = _proposal_id(proposal_id)
        owner = _user_id(owner_user_id)
        with self._read() as connection:
            proposal = connection.execute(
                "SELECT * FROM shared_proposals WHERE proposal_id = ?",
                (pid,)).fetchone()
        if proposal is None:
            raise KeyError(pid)
        if int(proposal["owner_user_id"]) != owner:
            raise PermissionError("proposal_owner_mismatch")
        cid = int(proposal["candidate_id"])
        try:
            candidate = self._load_candidate(cid)
        except KeyError:
            raise
        if candidate["status"] == "rejected":
            return self._prepare_failure(proposal, owner, "candidate_rejected")
        # A changed original is a new immutable version of the same document;
        # read_candidate_source re-validates the file against its metadata hashes.
        try:
            prepared = prepare.prepare_document(
                self.documents, candidate, self._archive_root())
        except prepare.PreparationError as exc:
            return self._prepare_failure(proposal, owner, exc.code)
        except ValueError as exc:
            code = str(exc) if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(exc)) \
                else "preparation_failed"
            return self._prepare_failure(proposal, owner, code)
        except OSError:
            logger.warning("shared corpus: candidate source read failed")
            return self._prepare_failure(proposal, owner, "source_unavailable")

        document_id = prepared["document_id"]
        version_id = prepared["version_id"]
        metadata = prepared["metadata"]
        decision = prepared["rights"]
        now = time.time()
        with self._write() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO shared_documents (document_id, version_id, "
                "candidate_id, proposal_id, title, doi, year, source_kind, "
                "content_sha256, char_count, chunk_count, schema_version, "
                "section_path, prepared_at, superseded_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (document_id, version_id, cid, pid, metadata.get("title", ""),
                 metadata.get("doi", ""), metadata.get("year", ""),
                 metadata.get("source_kind", ""),
                 metadata.get("content_sha256", ""),
                 int(metadata.get("char_count") or 0),
                 int(metadata.get("chunk_count") or 0),
                 int(metadata.get("schema_version") or 2),
                 json.dumps(metadata.get("section_path") or [], ensure_ascii=False),
                 now))
            existing_rights = connection.execute(
                "SELECT * FROM shared_rights WHERE document_id = ? "
                "AND version_id = ?", (document_id, version_id)).fetchone()
            if existing_rights is not None and existing_rights["status"] == "revoked":
                state, rights_state = "revoked", "revoked"
                reason = existing_rights["reason"] or "revoked"
            elif existing_rights is not None and existing_rights["status"] == "granted":
                state, rights_state = "published", existing_rights["basis"]
                reason = existing_rights["reason"]
            elif decision["status"] == "trusted":
                self._upsert_right(connection, document_id, version_id,
                                   "granted", "trusted_metadata", 0,
                                   decision["reason"], now)
                state, rights_state = "published", "trusted_metadata"
                reason = decision["reason"]
            else:
                state, rights_state = "needs_review", "needs_review"
                reason = decision["reason"]
            if state == "published":
                self._mark_older_versions(connection, document_id)
            connection.execute(
                "UPDATE shared_proposals SET state = ?, rights = ?, reason = ?, "
                "document_id = ?, version_id = ?, candidate_title = ?, "
                "candidate_doi = ?, candidate_year = ?, candidate_source_kind = ?, "
                "candidate_content_sha256 = ?, candidate_raw_sha256 = ?, "
                "updated_at = ? WHERE proposal_id = ?",
                (state, rights_state, reason, document_id, version_id,
                 prepare.public_text(candidate["title"], 512),
                 prepare.public_text(candidate["doi"], 256),
                 prepare.public_text(candidate["year"], 16),
                 prepare.public_text(candidate["source_kind"], 64),
                 candidate["content_sha256"], candidate["raw_sha256"],
                 now, pid))
            self._audit(connection, owner,
                        "prepare" if state == "published" else "prepare_needs_review",
                        pid, {"candidate_id": cid, "document_id": document_id,
                              "version_id": version_id, "state": state,
                              "basis": rights_state})
            row = connection.execute(
                "SELECT * FROM shared_proposals WHERE proposal_id = ?",
                (pid,)).fetchone()
            publishable = self._publishable_state(
                connection, document_id, version_id)
        return self._row_to_proposal(
            row, metadata=prepare.public_metadata(metadata),
            publishable=publishable)

    def _prepare_failure(self, proposal: sqlite3.Row, owner: int,
                         code: str) -> dict[str, Any]:
        document_id = proposal["document_id"]
        version_id = proposal["version_id"]
        state, rights_state = "failed", "needs_review"
        with self._write() as connection:
            if document_id and version_id:
                held = connection.execute(
                    "SELECT * FROM shared_rights WHERE document_id = ? "
                    "AND version_id = ?", (document_id, version_id)).fetchone()
                if held is not None and held["status"] == "granted":
                    state, rights_state = "published", held["basis"]
                elif held is not None and held["status"] == "revoked":
                    state, rights_state = "revoked", "revoked"
            connection.execute(
                "UPDATE shared_proposals SET state = ?, rights = ?, reason = ?, "
                "updated_at = ? WHERE proposal_id = ?",
                (state, rights_state, code, time.time(),
                 proposal["proposal_id"]))
            self._audit(connection, owner, "prepare_failed",
                        proposal["proposal_id"],
                        {"candidate_id": int(proposal["candidate_id"]),
                         "document_id": document_id, "version_id": version_id,
                         "code": code, "state": state})
            row = connection.execute(
                "SELECT * FROM shared_proposals WHERE proposal_id = ?",
                (proposal["proposal_id"],)).fetchone()
            publishable = self._publishable_state(
                connection, document_id, version_id)
        return self._row_to_proposal(row, publishable=publishable)

    def list_proposals(self, owner_user_id: int, *, state: str = "",
                       limit: int = 50) -> list[dict[str, Any]]:
        owner = _user_id(owner_user_id)
        try:
            maximum = max(1, min(int(limit), MAX_LIST_LIMIT))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit_invalid") from exc
        wanted = str(state or "").strip()
        if wanted and wanted not in PROPOSAL_STATES:
            raise ValueError("state_invalid")
        with self._read() as connection:
            if wanted:
                rows = connection.execute(
                    "SELECT * FROM shared_proposals WHERE owner_user_id = ? "
                    "AND state = ? ORDER BY created_at DESC, proposal_id LIMIT ?",
                    (owner, wanted, maximum)).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM shared_proposals WHERE owner_user_id = ? "
                    "ORDER BY created_at DESC, proposal_id LIMIT ?",
                    (owner, maximum)).fetchall()
        return [self._row_to_proposal(row) for row in rows]

    def get_proposal(self, proposal_id: str, owner_user_id: int
                     ) -> dict[str, Any] | None:
        pid = _proposal_id(proposal_id)
        owner = _user_id(owner_user_id)
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM shared_proposals WHERE proposal_id = ? "
                "AND owner_user_id = ?", (pid, owner)).fetchone()
        if row is None:
            return None
        return self._row_to_proposal(row)

    # ------------------------------------------------------------------- rights
    @staticmethod
    def _mark_older_versions(connection: sqlite3.Connection, document_id: str) -> None:
        latest = connection.execute(
            "SELECT d.version_id, d.candidate_id FROM shared_documents d "
            "JOIN shared_rights r USING (document_id, version_id) "
            "WHERE d.document_id = ? AND d.superseded_by IS NULL AND r.status = 'granted' "
            "ORDER BY d.candidate_id DESC, d.prepared_at DESC, d.version_id DESC LIMIT 1",
            (document_id,)).fetchone()
        if latest is not None:
            connection.execute(
                "UPDATE shared_documents SET superseded_by = ? WHERE document_id = ? "
                "AND candidate_id < ? AND superseded_by IS NULL",
                (latest["version_id"], document_id, latest["candidate_id"]))

    def _upsert_right(self, connection: sqlite3.Connection, document_id: str,
                      version_id: str, status: str, basis: str, actor: int,
                      reason: str, now: float) -> None:
        connection.execute(
            "INSERT INTO shared_rights (document_id, version_id, status, basis, "
            "actor_user_id, reason, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(document_id, version_id) DO UPDATE SET "
            "status=excluded.status, basis=excluded.basis, "
            "actor_user_id=excluded.actor_user_id, reason=excluded.reason, "
            "updated_at=excluded.updated_at",
            (document_id, version_id, status, basis, int(actor), reason, now, now))

    def _rights_row(self, document_id: str, version_id: str
                    ) -> dict[str, Any]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM shared_rights WHERE document_id = ? "
                "AND version_id = ?", (document_id, version_id)).fetchone()
        if row is None:
            return {
                "document_id": document_id,
                "version_id": version_id,
                "status": "none",
                "basis": "",
                "actor_user_id": 0,
                "reason": "",
                "updated_at": None,
            }
        return {
            "document_id": row["document_id"],
            "version_id": row["version_id"],
            "status": row["status"],
            "basis": row["basis"],
            "actor_user_id": int(row["actor_user_id"]),
            "reason": row["reason"],
            "updated_at": _iso(row["updated_at"]),
        }

    def rights(self, document_id: str, version_id: str) -> dict[str, Any]:
        document_id = prepare.identifier(document_id, "document_id")
        version_id = prepare.identifier(version_id, "version_id")
        return self._rights_row(document_id, version_id)

    def grant(self, document_id: str, version_id: str, admin_user_id: int, *,
              reason: str = "") -> dict[str, Any]:
        document_id = prepare.identifier(document_id, "document_id")
        version_id = prepare.identifier(version_id, "version_id")
        admin = _user_id(admin_user_id, "admin_user_id")
        safe_reason = _reason(reason)
        with self._read() as connection:
            known = connection.execute(
                "SELECT 1 FROM shared_documents WHERE document_id = ? "
                "AND version_id = ?", (document_id, version_id)).fetchone()
        if known is None:
            raise KeyError(f"{document_id}/{version_id}")
        try:
            metadata, _markdown = prepare.read_document_files(
                self.documents, document_id, version_id)
        except FileNotFoundError as exc:
            raise KeyError(f"{document_id}/{version_id}") from exc
        except ValueError as exc:
            raise ValueError("document_integrity_invalid") from exc
        now = time.time()
        with self._write() as connection:
            self._upsert_right(connection, document_id, version_id, "granted",
                               "admin_grant", admin, safe_reason, now)
            self._mark_older_versions(connection, document_id)
            connection.execute(
                "UPDATE shared_proposals SET state = 'published', "
                "rights = 'admin_grant', reason = ?, updated_at = ? "
                "WHERE document_id = ? AND version_id = ?",
                (safe_reason, now, document_id, version_id))
            self._audit(connection, admin, "grant",
                        f"{document_id}/{version_id}",
                        {"document_id": document_id, "version_id": version_id,
                         "state": "published", "basis": "admin_grant"})
        public = prepare.public_metadata(metadata)
        result = dict(public)
        result.update({
            "status": "granted",
            "basis": "admin_grant",
            "actor_user_id": admin,
            "reason": safe_reason,
            "updated_at": _iso(now),
            "metadata": public,
        })
        return result

    def revoke(self, document_id: str, version_id: str, admin_user_id: int, *,
               reason: str = "") -> dict[str, Any]:
        document_id = prepare.identifier(document_id, "document_id")
        version_id = prepare.identifier(version_id, "version_id")
        admin = _user_id(admin_user_id, "admin_user_id")
        safe_reason = _reason(reason)
        now = time.time()
        with self._write() as connection:
            known = connection.execute(
                "SELECT 1 FROM shared_documents WHERE document_id = ? "
                "AND version_id = ?", (document_id, version_id)).fetchone()
            if known is None:
                raise KeyError(f"{document_id}/{version_id}")
            self._upsert_right(connection, document_id, version_id, "revoked",
                               "revoked", admin, safe_reason, now)
            connection.execute(
                "UPDATE shared_proposals SET state = 'revoked', rights = 'revoked', "
                "reason = ?, updated_at = ? WHERE document_id = ? AND version_id = ?",
                (safe_reason, now, document_id, version_id))
            self._audit(connection, admin, "revoke",
                        f"{document_id}/{version_id}",
                        {"document_id": document_id, "version_id": version_id,
                         "state": "revoked", "basis": "revoked"})
        try:
            metadata, _markdown = prepare.read_document_files(
                self.documents, document_id, version_id)
            public = prepare.public_metadata(metadata)
        except (FileNotFoundError, ValueError):
            public = {}
        result = {
            "document_id": document_id,
            "version_id": version_id,
            "status": "revoked",
            "basis": "revoked",
            "actor_user_id": admin,
            "reason": safe_reason,
            "updated_at": _iso(now),
            "metadata": public or None,
        }
        return result

    # ------------------------------------------------------------- publication
    def _publishable_state(self, connection: sqlite3.Connection,
                           document_id: str | None,
                           version_id: str | None) -> bool:
        if not document_id or not version_id:
            return False
        row = connection.execute(
            "SELECT d.superseded_by, r.status FROM shared_documents d "
            "LEFT JOIN shared_rights r ON d.document_id = r.document_id "
            "AND d.version_id = r.version_id "
            "WHERE d.document_id = ? AND d.version_id = ?",
            (document_id, version_id)).fetchone()
        return bool(row is not None and row["status"] == "granted"
                    and row["superseded_by"] is None)

    def is_visible(self, document_id: str, version_id: str) -> bool:
        """Permission-only visibility: granted and not revoked.

        Activation and supersession are deliberately ignored: a historical
        version stays visible to readers until an administrator revokes it.
        """
        try:
            document_id = prepare.identifier(document_id, "document_id")
            version_id = prepare.identifier(version_id, "version_id")
        except ValueError:
            return False
        with self._read() as connection:
            row = connection.execute(
                "SELECT r.status FROM shared_rights r "
                "JOIN shared_documents d ON d.document_id = r.document_id "
                "AND d.version_id = r.version_id "
                "WHERE r.document_id = ? AND r.version_id = ?",
                (document_id, version_id)).fetchone()
        return bool(row is not None and row["status"] == "granted")

    def publishable(self) -> list[dict[str, Any]]:
        """Return latest, non-superseded, still-granted versions."""
        with self._read() as connection:
            rows = connection.execute(
                "SELECT d.*, r.basis AS rights_basis FROM shared_documents d "
                "JOIN shared_rights r ON d.document_id = r.document_id "
                "AND d.version_id = r.version_id "
                "WHERE r.status = 'granted' AND d.superseded_by IS NULL "
                "ORDER BY d.prepared_at, d.document_id, d.version_id").fetchall()
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (row["document_id"], row["version_id"])
            if key in seen:
                continue
            try:
                metadata, _markdown = prepare.read_document_files(
                    self.documents, row["document_id"], row["version_id"])
            except (FileNotFoundError, ValueError):
                logger.warning(
                    "shared corpus: skipping unreadable published version")
                continue
            if (str(metadata.get("content_sha256") or "") != row["content_sha256"]
                    or int(metadata.get("char_count") or -1) != int(row["char_count"])
                    or int(metadata.get("chunk_count") or -1) != int(row["chunk_count"])):
                logger.warning(
                    "shared corpus: skipping published version metadata mismatch")
                continue
            seen.add(key)
            item = prepare.public_metadata(metadata)
            item["rights"] = row["rights_basis"]
            result.append(item)
        return result

    def read_document(self, document_id: str, version_id: str
                      ) -> tuple[dict[str, Any], str]:
        document_id = prepare.identifier(document_id, "document_id")
        version_id = prepare.identifier(version_id, "version_id")
        if not self.is_visible(document_id, version_id):
            raise PermissionError("document_not_visible")
        try:
            metadata, markdown = prepare.read_document_files(
                self.documents, document_id, version_id)
        except FileNotFoundError as exc:
            raise KeyError(f"{document_id}/{version_id}") from exc
        except ValueError as exc:
            raise ValueError("document_integrity_invalid") from exc
        return prepare.public_metadata(metadata), markdown

    # ----------------------------------------------------------------- admin UI
    def admin_documents(self, *, include_superseded: bool = True,
                  limit: int = 200) -> list[dict[str, Any]]:
        """Bounded, path-free inventory for administrator review."""
        try:
            maximum = max(1, min(int(limit), 1000))
        except (TypeError, ValueError) as exc:
            raise ValueError("limit_invalid") from exc
        clause = "" if include_superseded else "WHERE d.superseded_by IS NULL"
        with self._read() as connection:
            rows = connection.execute(
                "SELECT d.*, r.status AS rights_status, r.basis AS rights_basis, "
                "r.updated_at AS rights_updated_at FROM shared_documents d "
                "LEFT JOIN shared_rights r ON d.document_id = r.document_id "
                "AND d.version_id = r.version_id "
                f"{clause} ORDER BY d.prepared_at DESC, d.document_id DESC LIMIT ?",
                (maximum,)).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata, _markdown = prepare.read_document_files(
                    self.documents, row["document_id"], row["version_id"])
            except (FileNotFoundError, ValueError):
                metadata = {}
            item = prepare.public_metadata(metadata) if metadata else {
                "document_id": row["document_id"],
                "version_id": row["version_id"],
                "title": row["title"],
                "doi": row["doi"],
                "year": row["year"],
                "source_kind": row["source_kind"],
                "content_sha256": row["content_sha256"],
                "char_count": int(row["char_count"]),
                "chunk_count": int(row["chunk_count"]),
                "schema_version": int(row["schema_version"]),
                "prepared_at": None,
                "section_path": json.loads(row["section_path"] or "[]"),
            }
            item["rights"] = row["rights_basis"] if row["rights_status"] == "granted" \
                else (row["rights_status"] or "needs_review")
            item["readable"] = bool(metadata)
            item["superseded_by"] = row["superseded_by"]
            result.append(item)
        return result


def content_sha256(markdown: str) -> str:
    """Small helper for callers that need the prepared-content digest."""
    return hashlib.sha256(str(markdown).encode("utf-8")).hexdigest()

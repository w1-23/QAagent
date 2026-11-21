"""Versioned integrity evidence and owner-scoped, append-only human review.

Automated evidence describes an observation, never a misconduct verdict. Reviews
are a separate projection: immutable reports are not rewritten after a decision.
Only authenticated application code may supply owner/session identities.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import uuid
from typing import Any

SCHEMA_VERSION = "integrity-finding/1"
METHOD_DECISIONS = frozenset({"registered", "rejected"})
MAX_METHOD_EVIDENCE_BYTES = 256 * 1024
STATES = frozenset({"pending", "needs_context", "explained", "confirmed_discrepancy", "dismissed"})
_SHA = re.compile(r"[a-f0-9]{64}")
MAX_REPORT_BYTES = 8 * 1024 * 1024


class ReviewConflict(ValueError):
    pass


def finding(*, rule: str, version: str, locations: list[dict], evidence: dict,
            applicability: list[str], counterexamples: list[str], coverage: dict,
            summary: str = "") -> dict:
    if not rule or not version or not locations or any(
            not _SHA.fullmatch(str(loc.get("sha256") or loc.get("source_sha256") or ""))
            for loc in locations):
        raise ValueError("finding requires a versioned rule and hashed source locations")
    payload = {"schema_version": SCHEMA_VERSION, "rule": rule, "rule_version": version,
               "locations": locations, "evidence": evidence, "applicability": applicability,
               "counterexamples": counterexamples, "coverage": coverage, "summary": summary,
               "origin": "automated", "interpretation": "observation_requires_human_context"}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    return {**payload, "finding_id": hashlib.sha256(encoded).hexdigest(),
            "review_status": "pending", "human_review_required": True}


def _connect():
    from importlib import import_module
    root = import_module("src.config").PROJECT_ROOT / "state"
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(root / "integrity_reviews.sqlite3"), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS integrity_reviews (
            review_id TEXT PRIMARY KEY, owner INTEGER NOT NULL, session_id TEXT NOT NULL,
            report TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS integrity_reviews_owner ON integrity_reviews(owner,created_at);
        CREATE TABLE IF NOT EXISTS integrity_decisions (
            review_id TEXT NOT NULL, finding_id TEXT NOT NULL, revision INTEGER NOT NULL,
            state TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(review_id,finding_id,revision));
        CREATE TABLE IF NOT EXISTS custom_method_candidates (
            candidate_id TEXT PRIMARY KEY, owner INTEGER NOT NULL,
            session_id TEXT NOT NULL, method_id TEXT NOT NULL,
            method_version TEXT NOT NULL, state TEXT NOT NULL,
            evidence TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS custom_method_candidates_owner
            ON custom_method_candidates(owner,created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS custom_method_active
            ON custom_method_candidates(owner,method_id,method_version)
            WHERE state!='rejected';
        CREATE TABLE IF NOT EXISTS custom_method_decisions (
            candidate_id TEXT NOT NULL, revision INTEGER NOT NULL,
            state TEXT NOT NULL, admin INTEGER NOT NULL, note TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(candidate_id,revision));
    """)
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def record_report(owner: int, session_id: str, report: dict) -> dict:
    from src.session.manager import session_manager
    from src.core.cancel import raise_if_cancelled
    from src.core.account_leases import account_lease
    if type(owner) is not int or owner <= 0 or session_manager.get_session_owner(session_id) != owner:
        raise ValueError("authorized session required")
    entries = report.get("common_findings", [])
    if len(entries) > 1000 or any(item.get("schema_version") != SCHEMA_VERSION for item in entries):
        raise ValueError("invalid common findings")
    # Only a server adapter may bind source_id to an account file. Resolve again
    # before persisting; evidence cannot manufacture a foreign file preview.
    from src.session import files
    for item in entries:
        for loc in item["locations"]:
            fid = loc.get("file_id")
            if fid:
                entry = files.get_account_file(fid, owner, touch=False)
                if not entry or entry.get("sha256") != (loc.get("sha256") or loc.get("source_sha256")):
                    raise ValueError("finding source unavailable or changed")
    source_ids = {loc["file_id"] for item in entries for loc in item["locations"] if loc.get("file_id")}
    # Zero-finding and truncated-location reports still depend on all scanned
    # originals for later export. Pin the authoritative source refs as well.
    for table in report.get("table_results", []):
        fid, digest = table.get("source_file_id"), table.get("source_sha256")
        if fid:
            entry = files.get_account_file(fid, owner, touch=False)
            if not entry or entry.get("sha256") != digest:
                raise ValueError("review source unavailable or changed")
            source_ids.add(fid)
    text = json.dumps(report, ensure_ascii=False, allow_nan=False)
    if len(text.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("review report exceeds limit")
    rid = uuid.uuid4().hex
    with account_lease(owner):
        raise_if_cancelled("integrity_review_publish")
        ids = sorted(source_ids)
        files.replace_resource_references(owner, "integrity_review", rid, ids)
        try:
            with closing(_connect()) as conn, conn:
                raise_if_cancelled("integrity_review_commit")
                conn.execute("INSERT INTO integrity_reviews VALUES(?,?,?,?,?)", (rid, owner, session_id, text, _now()))
        except BaseException:
            files.release_resource_references(owner, "integrity_review", rid)
            raise
    return {"review_id": rid, "review_url": "/integrity-review?review=" + rid}


def get_review(owner: int, review_id: str) -> dict | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM integrity_reviews WHERE review_id=? AND owner=?",
                           (review_id, owner)).fetchone()
        if not row:
            return None
        decisions = conn.execute("SELECT * FROM integrity_decisions WHERE review_id=? ORDER BY revision",
                                 (review_id,)).fetchall()
    report = json.loads(row["report"])
    history: dict[str, list] = {}
    for decision in decisions:
        history.setdefault(decision["finding_id"], []).append(dict(decision))
    file_cache = {}
    for item in report.get("common_findings", []):
        logs = history.get(item["finding_id"], [])
        item["review"] = {"state": "pending", "revision": 0, "note": ""} if not logs else logs[-1]
        item["review_history"] = logs
        from src.session import files
        for loc in item["locations"]:
            fid = loc.get("file_id", "")
            if fid and fid not in file_cache:
                file_cache[fid] = files.get_account_file(fid, owner, touch=False)
            entry = file_cache.get(fid)
            if entry and entry.get("sha256") == (loc.get("sha256") or loc.get("source_sha256")):
                loc["source_download_url"] = entry.get("download_url", "")
                loc["source_filename"] = entry.get("filename", "")
    return {"review_id": review_id, "session_id": row["session_id"],
            "created_at": row["created_at"], "report": report}


def list_reviews(owner: int, limit: int = 50) -> list[dict]:
    with closing(_connect()) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT review_id,session_id,created_at FROM integrity_reviews WHERE owner=? "
            "ORDER BY created_at DESC LIMIT ?", (owner, min(max(limit, 1), 100)))]


def decide(owner: int, review_id: str, finding_id: str, *, state: str,
           note: str, expected_revision: int) -> dict:
    if state not in STATES or type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("invalid review state or revision")
    if not isinstance(note, str) or len(note) > 4000 or (state != "pending" and not note.strip()):
        raise ValueError("a review decision requires a note of at most 4000 characters")
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT report FROM integrity_reviews WHERE review_id=? AND owner=?",
                           (review_id, owner)).fetchone()
        if not row or not any(item["finding_id"] == finding_id for item in json.loads(row[0]).get("common_findings", [])):
            raise KeyError("review finding unavailable")
        current = conn.execute("SELECT COALESCE(MAX(revision),0) FROM integrity_decisions "
                               "WHERE review_id=? AND finding_id=?", (review_id, finding_id)).fetchone()[0]
        if current != expected_revision:
            raise ReviewConflict("review changed; reload before saving")
        receipt = {"state": state, "note": note.strip(), "revision": current + 1, "created_at": _now()}
        conn.execute("INSERT INTO integrity_decisions VALUES(?,?,?,?,?,?)",
                     (review_id, finding_id, receipt["revision"], state, receipt["note"], receipt["created_at"]))
    return receipt


def record_method_candidate(owner: int, session_id: str, method_id: str,
                            method_version: str, evidence: dict) -> dict:
    """Store a retested method as a pending, owner-bound candidate."""
    from src.session.manager import session_manager
    from src.core.account_leases import account_lease
    from src.core.cancel import raise_if_cancelled
    if (type(owner) is not int or owner <= 0
            or session_manager.get_session_owner(session_id) != owner):
        raise ValueError("authorized session required")
    if (not isinstance(method_id, str) or not method_id
            or not isinstance(method_version, str) or not method_version
            or not isinstance(evidence, dict) or not evidence
            or evidence.get("method_id") != method_id
            or evidence.get("method_version") != method_version):
        raise ValueError("invalid method candidate evidence")
    text = json.dumps(evidence, ensure_ascii=False, allow_nan=False)
    if len(text.encode("utf-8")) > MAX_METHOD_EVIDENCE_BYTES:
        raise ValueError("method candidate evidence exceeds limit")
    cid, now = uuid.uuid4().hex, _now()
    with account_lease(owner):
        raise_if_cancelled("custom_method_candidate_publish")
        try:
            with closing(_connect()) as conn, conn:
                conn.execute("INSERT INTO custom_method_candidates VALUES(?,?,?,?,?,?,?,?,?)",
                             (cid, owner, session_id, method_id, method_version,
                              "candidate", text, now, now))
        except sqlite3.IntegrityError:
            raise ReviewConflict("method candidate already exists") from None
    return {"candidate_id": cid, "method_key": method_id + "@" + method_version,
            "state": "candidate", "method_id": method_id,
            "method_version": method_version, "created_at": now}


def list_method_candidates(owner: int, *, admin: bool = False, limit: int = 50) -> list[dict]:
    columns = "candidate_id,owner,session_id,method_id,method_version,state,created_at,updated_at"
    with closing(_connect()) as conn:
        if admin:
            rows = conn.execute("SELECT " + columns + " FROM custom_method_candidates "
                                "ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),))
        else:
            rows = conn.execute("SELECT " + columns + " FROM custom_method_candidates "
                                "WHERE owner=? ORDER BY created_at DESC LIMIT ?",
                                (owner, min(max(limit, 1), 100)))
        return [{**dict(row), "method_key": row["method_id"] + "@" + row["method_version"]}
                for row in rows]


def get_method_candidate(owner: int, candidate_id: str, *, admin: bool = False) -> dict | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM custom_method_candidates WHERE candidate_id=?",
                           (candidate_id,)).fetchone()
        if not row or (not admin and row["owner"] != owner):
            return None
        decisions = conn.execute("SELECT * FROM custom_method_decisions WHERE candidate_id=? "
                                 "ORDER BY revision", (candidate_id,)).fetchall()
    history = [dict(decision) for decision in decisions]
    return {"candidate_id": candidate_id, "owner": row["owner"],
            "session_id": row["session_id"], "method_id": row["method_id"],
            "method_version": row["method_version"],
            "method_key": row["method_id"] + "@" + row["method_version"],
            "state": row["state"], "evidence": json.loads(row["evidence"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "review": history[-1] if history else {"state": "candidate", "revision": 0, "note": ""},
            "review_history": history}


def decide_method_candidate(admin_id: int, candidate_id: str, *, state: str,
                            note: str, expected_revision: int) -> dict:
    """Register or reject a pending method; only a verified admin may decide."""
    from src.auth import require_admin, user_by_id
    if not require_admin(user_by_id(admin_id)):
        raise PermissionError("administrator_required")
    if (state not in METHOD_DECISIONS or type(expected_revision) is not int
            or expected_revision < 0 or not isinstance(note, str)
            or len(note) > 4000 or not note.strip()):
        raise ValueError("invalid method decision")
    with closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state FROM custom_method_candidates WHERE candidate_id=?",
                           (candidate_id,)).fetchone()
        if not row:
            raise KeyError("method candidate unavailable")
        if row["state"] != "candidate":
            raise ReviewConflict("method candidate already decided")
        current = conn.execute("SELECT COALESCE(MAX(revision),0) FROM custom_method_decisions "
                               "WHERE candidate_id=?", (candidate_id,)).fetchone()[0]
        if current != expected_revision:
            raise ReviewConflict("method candidate changed; reload before saving")
        receipt = {"state": state, "note": note.strip(), "revision": current + 1,
                   "created_at": _now(), "admin": admin_id}
        conn.execute("INSERT INTO custom_method_decisions VALUES(?,?,?,?,?,?)",
                     (candidate_id, receipt["revision"], state, admin_id,
                      receipt["note"], receipt["created_at"]))
        updated = conn.execute("UPDATE custom_method_candidates SET state=?, updated_at=? "
                               "WHERE candidate_id=? AND state='candidate'",
                               (state, receipt["created_at"], candidate_id))
        if updated.rowcount != 1:
            raise ReviewConflict("method candidate changed; reload before saving")
    return receipt


def source_entry(owner: int, review_id: str, finding_id: str, side: int):
    """Authorize from persisted evidence, never from caller-supplied paths/IDs."""
    from src.session import files
    review = get_review(owner, review_id)
    if not review:
        return None
    item = next((f for f in review["report"].get("common_findings", []) if f["finding_id"] == finding_id), None)
    if item is None or side < 0 or side >= len(item["locations"]):
        return None
    loc = item["locations"][side]
    entry = files.get_account_file(loc.get("file_id", ""), owner, touch=False)
    if not entry or entry.get("sha256") != (loc.get("sha256") or loc.get("source_sha256")):
        return None
    return entry, loc


def purge_account(owner: int) -> None:
    from src.session import files
    with closing(_connect()) as conn, conn:
        ids = [row[0] for row in conn.execute("SELECT review_id FROM integrity_reviews WHERE owner=?", (owner,))]
        for rid in ids:
            files.release_resource_references(owner, "integrity_review", rid)
        conn.execute("DELETE FROM integrity_decisions WHERE review_id IN "
                     "(SELECT review_id FROM integrity_reviews WHERE owner=?)", (owner,))
        conn.execute("DELETE FROM integrity_reviews WHERE owner=?", (owner,))
        conn.execute("DELETE FROM custom_method_decisions WHERE candidate_id IN "
                     "(SELECT candidate_id FROM custom_method_candidates WHERE owner=?)", (owner,))
        conn.execute("DELETE FROM custom_method_candidates WHERE owner=?", (owner,))

"""Durable build requests, activation transitions, and shared-only pointers."""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid

_OPEN = ("queued", "building", "waiting_for_idle", "maintenance", "rolling_back")
_ACTIVATING = ("maintenance", "rolling_back")
_GENERATION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}")


def valid_generation(value):
    return isinstance(value, str) and bool(_GENERATION.fullmatch(value))


def _sync_dir(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_pointer(root, name="active"):
    path = root / (name + "_index.json")
    try:
        with path.open(encoding="utf-8") as stream:
            record = json.load(stream)
    except FileNotFoundError:
        return None
    if not isinstance(record, dict) or not valid_generation(record.get("generation")):
        raise ValueError("shared_index_pointer_invalid")
    return record["generation"]


def write_pointer(root, generation, name="active"):
    path = root / (name + "_index.json")
    if generation is None:
        path.unlink(missing_ok=True)
        _sync_dir(root)
        return
    if not valid_generation(generation):
        raise ValueError("shared_generation_invalid")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as stream:
        staging = Path(stream.name)
        try:
            json.dump({"generation": generation}, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(staging, path)
            _sync_dir(root)
        finally:
            staging.unlink(missing_ok=True)


class Journal:
    def __init__(self, root):
        self.path = root / "catalog.sqlite3"
        with self.connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS shared_builds (
                id TEXT PRIMARY KEY, owner INTEGER NOT NULL, fingerprint TEXT NOT NULL,
                documents TEXT NOT NULL, state TEXT NOT NULL,
                generation TEXT, prior TEXT, error TEXT, created REAL NOT NULL,
                started REAL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS shared_build_events (
                seq INTEGER PRIMARY KEY, build_id TEXT NOT NULL,
                state TEXT NOT NULL, happened REAL NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS shared_preparations (
                candidate_id INTEGER PRIMARY KEY, owner INTEGER NOT NULL, state TEXT NOT NULL,
                created REAL NOT NULL, finished REAL);
            CREATE TABLE IF NOT EXISTS shared_activity (
                id INTEGER PRIMARY KEY CHECK(id=1), last_activity REAL NOT NULL);
            INSERT OR IGNORE INTO shared_activity VALUES(1, 0);
            """)
            conn.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(shared_preparations)")}
            if "verified_license" not in columns:
                conn.execute("ALTER TABLE shared_preparations ADD COLUMN verified_license INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def last_activity(self):
        with self.connection() as conn:
            return conn.execute("SELECT last_activity FROM shared_activity WHERE id=1").fetchone()[0]

    def touch(self, timestamp):
        with self.connection() as conn:
            conn.execute("UPDATE shared_activity SET last_activity=max(last_activity, ?) WHERE id=1",
                         (timestamp,))

    def enqueue(self, owner, documents):
        payload = json.dumps(documents, separators=(",", ":"))
        digest = sha256(payload.encode()).hexdigest()
        now = time.time()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            marks = ",".join("?" for _ in _OPEN)
            row = conn.execute(f"SELECT * FROM shared_builds WHERE state IN ({marks})", _OPEN).fetchone()
            if row:
                if row["owner"] == owner and row["fingerprint"] == digest:
                    return dict(row)
                raise ValueError("shared_build_pending")
            row = conn.execute("SELECT * FROM shared_builds WHERE state='active' ORDER BY updated DESC LIMIT 1").fetchone()
            if row and row["fingerprint"] == digest:
                if row["owner"] == owner:
                    return dict(row)
                raise ValueError("shared_documents_already_indexed")
            if conn.execute("SELECT count(*) FROM shared_builds WHERE created>?", (now - 86400,)).fetchone()[0] >= 4:
                raise ValueError("shared_daily_build_budget")
            job = uuid.uuid4().hex
            conn.execute("INSERT INTO shared_builds (id,owner,fingerprint,documents,state,created,updated) "
                         "VALUES(?,?,?,?,?,?,?)", (job, owner, digest, payload, "queued", now, now))
            self._event(conn, job, "queued", now)
            return dict(conn.execute("SELECT * FROM shared_builds WHERE id=?", (job,)).fetchone())

    @staticmethod
    def _event(conn, job, state, timestamp, error=None):
        conn.execute("INSERT INTO shared_build_events(build_id,state,happened,error) VALUES(?,?,?,?)",
                     (job, state, timestamp, error))

    def defer_notice(self, job):
        with self.connection() as conn:
            cursor = conn.execute("INSERT INTO shared_build_events(build_id,state,happened) "
                                  "SELECT ?, 'deferred', ? WHERE NOT EXISTS (SELECT 1 FROM shared_build_events "
                                  "WHERE build_id=? AND state='deferred')", (job["id"], time.time(), job["id"]))
            return cursor.rowcount == 1

    def pending(self, states=_OPEN):
        with self.connection() as conn:
            marks = ",".join("?" for _ in states)
            row = conn.execute(f"SELECT * FROM shared_builds WHERE state IN ({marks}) ORDER BY created LIMIT 1",
                               states).fetchone()
            return dict(row) if row else None

    def job(self, job_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM shared_builds WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def active_versions(self, generation, document):
        if not valid_generation(generation):
            return []
        with self.connection() as conn:
            row = conn.execute("SELECT documents FROM shared_builds WHERE state='active' AND generation=?",
                               (generation,)).fetchone()
        return [version for key, version in json.loads(row["documents"]) if key == document] if row else []

    def transition(self, job, state, *, generation=None, prior=None, error=None):
        now = time.time()
        with self.connection() as conn:
            cursor = conn.execute("UPDATE shared_builds SET state=?, generation=coalesce(?,generation), "
                                  "prior=coalesce(?,prior), error=?, updated=?, "
                                  "started=CASE WHEN ?='maintenance' THEN ? ELSE started END "
                                  "WHERE id=? AND state=?",
                                  (state, generation, prior, error, now, state, now, job["id"], job["state"]))
            if cursor.rowcount != 1:
                raise RuntimeError("shared_build_state_conflict")
            self._event(conn, job["id"], state, now, error)

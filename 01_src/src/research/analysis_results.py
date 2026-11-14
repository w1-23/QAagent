"""Owner-bound immutable inputs and full-precision scientific results.

The existing research artifact database owns these records. A snapshot contains
its data, so reproducing an analysis does not depend on an expiring upload. Refs
are opaque identities, never paths or authorization. Callers supply the server's
owner on every read; model-supplied owner fields are never used for authorization.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import math
import sqlite3
import time
import re
import uuid
from typing import Any

SCHEMA_VERSION = 1
IMPLEMENTATION_VERSION = "research-statistics/2"


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(k): _scalar(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_scalar(v) for v in value]
    import pandas as pd
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (date, datetime)):
        return {"$datetime": value.isoformat()}
    if hasattr(value, "item"):
        return _scalar(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return None
        raise ValueError("Infinite values cannot be silently removed from a scientific snapshot")
    if isinstance(value, (str, int, float, bool)):
        return value
    # pandas NA and NaT are missing, not the strings '<NA>' and 'NaT'.
    import pandas as pd
    if pd.isna(value):
        return None
    raise ValueError(f"Unsupported snapshot cell type: {type(value).__name__}")


def _json(value: Any) -> str:
    return json.dumps(_scalar(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _owner(owner_user_id: int) -> int:
    if isinstance(owner_user_id, bool) or not isinstance(owner_user_id, int) or owner_user_id <= 0:
        raise ValueError("An authenticated server owner is required")
    return owner_user_id


@contextmanager
def _connect():
    from src.config import PROJECT_ROOT
    path = PROJECT_ROOT / "state" / "research_artifacts.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("""CREATE TABLE IF NOT EXISTS scientific_snapshots (
            id TEXT PRIMARY KEY, owner_user_id INTEGER NOT NULL,
            kind TEXT NOT NULL, version INTEGER NOT NULL,
            sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("CREATE INDEX IF NOT EXISTS scientific_snapshots_owner "
                     "ON scientific_snapshots(owner_user_id, kind)")
        columns = {r[1] for r in conn.execute("PRAGMA table_info(scientific_snapshots)")}
        for name, declaration in (("payload_file_id", "TEXT NOT NULL DEFAULT ''"),
                                   ("expires_at", "REAL")):
            if name not in columns:
                conn.execute(f"ALTER TABLE scientific_snapshots ADD COLUMN {name} {declaration}")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@dataclass(frozen=True)
class DatasetSnapshot:
    id: str
    version: int
    sha256: str
    _payload_json: str

    @property
    def ref(self) -> dict[str, Any]:
        return {"kind": "dataset_snapshot", "id": self.id,
                "version": self.version, "sha256": self.sha256}

    @property
    def payload_file_ref(self) -> str | None:
        return json.loads(self._payload_json).get("payload_file_ref")

    def to_dict(self) -> dict[str, Any]:
        value = {**json.loads(self._payload_json), "ref": self.ref}
        if value.get("asset_type") == "prepared_dataset":
            value["dataset_ref"] = self.ref
        return value

    def to_frame(self):
        import pandas as pd
        value = self.to_dict()
        if value.get("asset_type") == "prepared_dataset":
            from src.figures.dataset_codec import decode_frame
            frame = decode_frame(_read_prepared_payload(value["owner_user_id"], self.ref))
        else:
            rows = [[pd.Timestamp(cell["$datetime"]) if isinstance(cell, dict) and "$datetime" in cell
                     else cell for cell in row] for row in value["data"]]
            # Old snapshots already recorded dtype names, but reads previously
            # discarded them. Build typed columns directly: constructing an
            # intermediate float column first would round large nullable ints.
            # Historical payloads, hashes and results are never rewritten.
            vectors = {}
            dtypes = value.get("dtypes", [])
            for position, column in enumerate(value["columns"]):
                dtype = dtypes[position] if position < len(dtypes) else None
                if not (isinstance(dtype, str) and (re.fullmatch(
                        r"object|bool|boolean|string|category|u?int(8|16|32|64)|[Uu]?Int(8|16|32|64)|float(16|32|64)|Float(32|64)", dtype)
                        or re.fullmatch(r"datetime64\[(s|ms|us|ns)(, [A-Za-z0-9_/+:. -]+)?\]", dtype))):
                    dtype = None
                vectors[column] = pd.Series([row[position] for row in rows],
                                             index=value["row_ids"], dtype=dtype)
            frame = pd.DataFrame(vectors, columns=value["columns"], index=value["row_ids"])
        frame.attrs["dataset_ref"] = self.ref
        return frame


@dataclass(frozen=True)
class AnalysisResult:
    id: str
    version: int
    sha256: str
    _payload_json: str

    @property
    def ref(self) -> dict[str, Any]:
        return {"kind": "analysis_result", "id": self.id,
                "version": self.version, "sha256": self.sha256}

    def to_dict(self) -> dict[str, Any]:
        return {**json.loads(self._payload_json), "ref": self.ref}


def _insert(owner: int, kind: str, payload: dict[str, Any], identity: str | None = None):
    owner = _owner(owner)
    identity = identity or uuid.uuid4().hex
    encoded = _json({**payload, "owner_user_id": owner, "schema_version": SCHEMA_VERSION})
    digest = _hash(encoded)
    with _connect() as conn:
        conn.execute("INSERT INTO scientific_snapshots "
                     "(id,owner_user_id,kind,version,sha256,payload_json) VALUES (?,?,?,?,?,?)",
                     (identity, owner, kind, 1, digest, encoded))
    cls = DatasetSnapshot if kind == "dataset_snapshot" else AnalysisResult
    return cls(identity, 1, digest, encoded)


def persist_dataset_snapshot(owner_user_id: int, frame: Any, *, source: dict[str, Any],
                             design: dict[str, Any] | None = None,
                             units: dict[str, str] | None = None,
                             coverage: dict[str, Any] | None = None,
                             row_ids: list[Any] | None = None) -> DatasetSnapshot:
    """Capture all parsed rows, their source identities, and the declared design."""
    owner = _owner(owner_user_id)
    from src.core.account_leases import account_lease
    if not frame.columns.is_unique or any(not isinstance(c, str) for c in frame.columns):
        raise ValueError("Snapshot column names must be unique strings")
    row_ids = list(row_ids) if row_ids is not None else list(frame.index)
    if len(row_ids) != len(frame) or len({_json(r) for r in row_ids}) != len(row_ids):
        raise ValueError("Every input row must have a unique original identity")
    units = dict(units or {})
    if any(c not in frame.columns or not isinstance(v, str) for c, v in units.items()):
        raise ValueError("Units must map input columns to unit text")
    identity = uuid.uuid4().hex
    pinned = False
    with account_lease(owner):
        try:
            file_id = source.get("file_id")
            if file_id and not str(file_id).startswith("upload:"):
                from src.session.files import replace_resource_references
                refs = replace_resource_references(owner, "dataset_snapshot", identity, [str(file_id)])
                pinned = True
                if not refs or refs[0]["sha256"] != source.get("sha256"):
                    raise ValueError("Snapshot source does not match the authorized file version")
            return _insert(owner, "dataset_snapshot", {
                "source": source, "columns": list(frame.columns),
                "dtypes": [str(t) for t in frame.dtypes], "row_ids": row_ids,
                "data": list(frame.itertuples(index=False, name=None)),
                "design": design or {}, "units": units,
                "coverage": coverage or {},
            }, identity)
        except BaseException:
            if pinned:
                from src.session.files import release_resource_references
                release_resource_references(owner, "dataset_snapshot", identity)
            raise


def persist_analysis_result(owner_user_id: int, dataset_ref: dict[str, Any], *,
                            outputs: dict[str, Any], parameters: dict[str, Any],
                            design: dict[str, Any], exclusions: Any,
                            coverage: dict[str, Any],
                            presentation: dict[str, Any] | None = None) -> AnalysisResult:
    owner = _owner(owner_user_id)
    from src.core.account_leases import account_lease
    import scipy
    import numpy
    import pandas
    import statsmodels
    with account_lease(owner):
        snapshot = load_dataset_snapshot(owner, dataset_ref)
        return _insert(owner, "analysis_result", {
            "dataset_ref": snapshot.ref, "outputs": outputs, "parameters": parameters,
            "design": design, "exclusions": exclusions, "coverage": coverage,
            "implementation": {"version": IMPLEMENTATION_VERSION, "scipy": scipy.__version__,
                               "numpy": numpy.__version__, "pandas": pandas.__version__,
                               "statsmodels": statsmodels.__version__},
            "presentation": presentation or {"significant_digits": 6},
        })


def _load(owner: int, ref: dict[str, Any] | str, kind: str):
    owner = _owner(owner)
    from src.core.account_leases import account_lease
    with account_lease(owner), _connect() as conn:
        return _load_locked(owner, ref, kind, conn)


def _load_locked(owner: int, ref: dict[str, Any] | str, kind: str, conn):
    expected = ref if isinstance(ref, dict) else {"id": ref}
    if expected.get("kind", kind) != kind or not isinstance(expected.get("id"), str):
        raise LookupError("Scientific result not found")
    if "version" in expected and (type(expected["version"]) is not int or expected["version"] < 1):
        raise ValueError("Invalid scientific snapshot version")
    if "sha256" in expected and (not isinstance(expected["sha256"], str) or
            len(expected["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in expected["sha256"])):
        raise ValueError("Invalid scientific snapshot hash")
    row = conn.execute("SELECT * FROM scientific_snapshots "
                       "WHERE id=? AND owner_user_id=? AND kind=?",
                       (expected["id"], owner, kind)).fetchone()
    if row is None:
        raise LookupError("Scientific result not found")
    if (_hash(row["payload_json"]) != row["sha256"] or
            json.loads(row["payload_json"]).get("owner_user_id") != owner):
        raise ValueError("Scientific snapshot integrity check failed")
    if any(expected.get(key, row[key]) != row[key] for key in ("version", "sha256")):
        raise ValueError("Scientific snapshot version/hash mismatch")
    cls = DatasetSnapshot if kind == "dataset_snapshot" else AnalysisResult
    return cls(row["id"], row["version"], row["sha256"], row["payload_json"])


def load_dataset_snapshot(owner_user_id: int, ref: dict[str, Any] | str) -> DatasetSnapshot:
    snapshot = _load(owner_user_id, ref, "dataset_snapshot")
    if snapshot.payload_file_ref:
        _read_prepared_payload(_owner(owner_user_id), snapshot.ref)
    return snapshot


def load_analysis_result(owner_user_id: int, ref: dict[str, Any] | str) -> AnalysisResult:
    result = _load(owner_user_id, ref, "analysis_result")
    load_dataset_snapshot(owner_user_id, result.to_dict()["dataset_ref"])
    return result


def _strict_dataset_ref(ref: Any) -> dict[str, Any]:
    if (not isinstance(ref, dict) or ref.get("kind") != "dataset_snapshot"
            or not {"id", "version", "sha256"}.issubset(ref)):
        raise ValueError("Dataset parents require an immutable kind/id/version/sha256 reference")
    return ref


def _file_parent(owner: int, ref: dict, file_conn) -> dict:
    from src.session import files
    if ref.get("owner_user_id", owner) != owner:
        raise ValueError("Dataset parent owner mismatch")
    fid, digest = ref.get("file_id"), ref.get("sha256")
    if (not isinstance(fid, str) or not re.fullmatch(r"[a-f0-9]{32}", fid)
            or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or ("version" in ref and (type(ref["version"]) is not int or ref["version"] != 1))):
        raise ValueError("File parents require an immutable file_id/sha256 reference")
    row = file_conn.execute("SELECT * FROM session_files WHERE file_id=? AND owner_user_id=?",
                            (fid, owner)).fetchone()
    if row is None or row["sha256"] != digest:
        raise ValueError("Dataset parent file is unavailable or its hash changed")
    from pathlib import Path
    path = Path(row["storage_path"])
    if (not files._allowed_file_path(row["session_id"], path, row["origin"], owner)
            or files._sha256_file(path) != digest):
        raise ValueError("Dataset parent file integrity check failed")
    return {"kind": "input_snapshot", "file_id": fid, "sha256": digest}


def _dataset_dependencies(owner: int, refs, conn, file_conn) -> tuple[list[dict], dict[str, str]]:
    """Validate the complete lineage under the file-store writer lock."""
    dependencies: dict[str, str] = {}
    visited: set[str] = set()
    active: set[str] = set()

    def add_file(ref):
        checked = _file_parent(owner, ref, file_conn)
        previous = dependencies.setdefault(checked["file_id"], checked["sha256"])
        if previous != checked["sha256"]:
            raise ValueError("Conflicting dataset parent file hashes")
        return checked

    def visit(ref, depth=0):
        if not isinstance(ref, dict) or ref.get("owner_user_id", owner) != owner:
            raise ValueError("Dataset parent owner mismatch")
        if ref.get("kind") in {"input_snapshot", "file"}:
            return add_file(ref)
        if depth > 128 or len(visited) > 1024:
            raise ValueError("Dataset lineage exceeds traversal limits")
        snapshot = _load_locked(owner, _strict_dataset_ref(ref), "dataset_snapshot", conn)
        if snapshot.id in active:
            raise ValueError("Dataset lineage contains a cycle")
        if snapshot.id in visited:
            return snapshot.ref
        active.add(snapshot.id)
        value = snapshot.to_dict()
        if value.get("asset_type") == "prepared_dataset":
            checked = add_file({"file_id": value["payload_file_ref"], "sha256": value["payload_sha256"]})
            row = file_conn.execute("SELECT visibility FROM session_files WHERE file_id=?",
                                    (checked["file_id"],)).fetchone()
            if row["visibility"] != "internal":
                raise ValueError("Dataset payload must be an internal asset")
            for parent in value["parents"]:
                visit(parent, depth + 1)
        else:
            source = value.get("source", {})
            if source.get("file_id") and not str(source["file_id"]).startswith("upload:"):
                add_file(source)
        active.remove(snapshot.id)
        visited.add(snapshot.id)
        return snapshot.ref

    if not isinstance(refs, (list, tuple)):
        raise ValueError("Dataset references must be a list")
    return [visit(ref) for ref in refs], dependencies


def persist_prepared_dataset(owner_user_id: int, frame: Any = None, *,
                             typed_payload: dict | None = None,
                             session_id: str, parents: list[dict] | tuple = (),
                             execution_id: str, script_sha256: str,
                             logical_task_id: str = "", source: dict | None = None,
                             ttl_days: int = 30) -> DatasetSnapshot:
    """Publish a private derived table, storing its typed bytes exactly once.

    The returned snapshot.ref is the dataset_ref. payload_file_ref is an
    internal file ID, never a path, URL or a user-visible deliverable. Parent
    refs are resolved against the authenticated server owner, never their own
    owner claims. Publication creates no permanent self/parent pins; consumers
    must pin_dataset_references for their actual lifetime.
    """
    owner = _owner(owner_user_id)
    from src.core.account_leases import account_lease
    from src.session import files
    from src.figures.dataset_codec import encode_frame, decode_frame
    if (frame is None) == (typed_payload is None):
        raise ValueError("Supply exactly one frame or typed_payload")
    if not files.valid_session(session_id):
        raise ValueError("invalid session id")
    if (not isinstance(execution_id, str) or not execution_id.strip() or len(execution_id) > 256
            or not isinstance(script_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", script_sha256)
            or not isinstance(logical_task_id, str) or len(logical_task_id) > 256
            or type(ttl_days) is not int or not 1 <= ttl_days <= 3650):
        raise ValueError("Invalid dataset execution provenance or retention")
    # A positional typed payload is accepted for frame-or-payload callers.
    if isinstance(frame, dict):
        typed_payload, frame = frame, None
    payload = encode_frame(frame) if typed_payload is None else typed_payload
    restored = decode_frame(payload)
    # decode_frame also verifies canonical re-encoding, rejecting dtype casts
    # that would silently truncate/overflow. JSON remains a restricted wire format.
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = _hash(encoded)
    identity = uuid.uuid4().hex
    with account_lease(owner):
        receipt = files.register_file(session_id=session_id, owner_user_id=owner,
            filename=f"dataset_{identity}.json", content=encoded.encode("utf-8"),
            mime="application/json", visibility="internal", producer_tool="prepared_dataset")
        try:
            with files._connect() as file_conn:
                file_conn.execute("BEGIN IMMEDIATE")
                with _connect() as conn:
                    checked_parents, _ = _dataset_dependencies(owner, parents, conn, file_conn)
                    _file_parent(owner, {"file_id": receipt["file_id"], "sha256": digest}, file_conn)
                    metadata = {"owner_user_id": owner, "schema_version": SCHEMA_VERSION,
                        "asset_type": "prepared_dataset", "visibility": "internal",
                        "session_id": session_id, "logical_task_id": logical_task_id,
                        "execution_id": execution_id, "script_sha256": script_sha256,
                        "version": 1, "parents": checked_parents, "source": source or {},
                        "payload_file_ref": receipt["file_id"], "payload_sha256": digest,
                        "columns": list(restored.columns), "dtypes": [str(t) for t in restored.dtypes],
                        "row_count": len(restored), "ttl_days": ttl_days}
                    text = _json(metadata)
                    metadata_hash = _hash(text)
                    conn.execute("INSERT INTO scientific_snapshots "
                        "(id,owner_user_id,kind,version,sha256,payload_json,payload_file_id,expires_at) "
                        "VALUES (?,?, 'dataset_snapshot',1,?,?,?,?)",
                        (identity, owner, metadata_hash, text, receipt["file_id"], time.time() + ttl_days * 86400))
            return DatasetSnapshot(identity, 1, metadata_hash, text)
        except BaseException:
            # Only remove this unpublished file. Never release another resource's
            # pins or remove a parent when publication/validation fails.
            with files._connect() as file_conn:
                file_conn.execute("BEGIN IMMEDIATE")
                row = file_conn.execute("SELECT * FROM session_files WHERE file_id=? AND owner_user_id=?",
                                        (receipt["file_id"], owner)).fetchone()
                if row is not None:
                    files._delete_indexed_file(file_conn, row)
            raise


def _read_prepared_payload(owner: int, ref: dict) -> dict:
    from src.core.account_leases import account_lease
    from src.session import files
    from pathlib import Path
    files.init_store()
    with account_lease(owner), files._connect() as file_conn:
        file_conn.execute("BEGIN IMMEDIATE")
        with _connect() as conn:
            snapshot = _load_locked(owner, ref, "dataset_snapshot", conn)
            value = snapshot.to_dict()
            checked = _file_parent(owner, {"file_id": value["payload_file_ref"],
                                          "sha256": value["payload_sha256"]}, file_conn)
            row = file_conn.execute("SELECT * FROM session_files WHERE file_id=? AND owner_user_id=?",
                                    (checked["file_id"], owner)).fetchone()
            if row["visibility"] != "internal":
                raise ValueError("Dataset payload must be an internal asset")
            content = Path(row["storage_path"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != value["payload_sha256"]:
                raise ValueError("Dataset payload integrity check failed")
            payload = json.loads(content)
            now = time.time()
            file_conn.execute("UPDATE session_files SET last_accessed_at=? WHERE file_id=?", (now, row["file_id"]))
            conn.execute("UPDATE scientific_snapshots SET expires_at=? WHERE id=? AND owner_user_id=?",
                         (now + value["ttl_days"] * 86400, snapshot.id, owner))
            return payload


def pin_dataset_references(owner_user_id: int, resource_kind: str, resource_id: str,
                           dataset_refs: list[dict] | tuple, *, file_ids=()) -> list[dict]:
    """Replace a consumer's pins with its files and complete dataset parent closure.

    Metadata survives because GC checks the same payload-file pins, under the
    same file writer lock. Releasing ordinary resource references is sufficient.
    """
    owner = _owner(owner_user_id)
    from src.core.account_leases import account_lease
    from src.session import files
    if isinstance(file_ids, (str, bytes)):
        raise ValueError("file_ids must be an iterable of file IDs")
    files.init_store()
    with account_lease(owner), files._connect() as file_conn:
        file_conn.execute("BEGIN IMMEDIATE")
        with _connect() as conn:
            _, dependencies = _dataset_dependencies(owner, dataset_refs, conn, file_conn)
            return files._replace_resource_references_locked(file_conn, owner, resource_kind,
                resource_id, [*file_ids, *dependencies])


def release_dataset_references(owner_user_id: int, resource_kind: str, resource_id: str) -> int:
    from src.session.files import release_resource_references
    return release_resource_references(_owner(owner_user_id), resource_kind, resource_id)


def discard_prepared_dataset(owner_user_id: int, ref: dict) -> bool:
    """Compensate a failed host publication; refuse to remove any pinned table."""
    owner = _owner(owner_user_id)
    from src.core.account_leases import account_lease
    from src.session import files
    files.init_store()
    with account_lease(owner), files._connect() as file_conn:
        file_conn.execute("BEGIN IMMEDIATE")
        with _connect() as conn:
            try:
                snapshot = _load_locked(owner, _strict_dataset_ref(ref), "dataset_snapshot", conn)
            except LookupError:
                return False
            value = snapshot.to_dict()
            if value.get("asset_type") != "prepared_dataset":
                raise ValueError("Only prepared datasets may be discarded")
            fid = value["payload_file_ref"]
            if file_conn.execute("SELECT 1 FROM session_file_resource_refs WHERE file_id=? LIMIT 1", (fid,)).fetchone():
                return False
            row = file_conn.execute("SELECT * FROM session_files WHERE file_id=? AND owner_user_id=?",
                                    (fid, owner)).fetchone()
            if row is not None and not files._delete_indexed_file(file_conn, row)[0]:
                return False
            conn.execute("DELETE FROM scientific_snapshots WHERE id=? AND owner_user_id=?", (snapshot.id, owner))
            return True


def cleanup_prepared_datasets(*, now: float | None = None,
                              owner_user_id: int | None = None) -> dict[str, int]:
    """Expire unconsumed derived tables and metadata using the existing stores.

    Legacy dataset snapshots/analysis results are retained unchanged. A consumer
    pins every ancestor payload; the asset itself never creates a perpetual pin.
    Lock order is file store, then scientific store, just as publication/pinning.
    """
    from src.session import files
    if owner_user_id is not None:
        _owner(owner_user_id)
    files.init_store()
    timestamp = time.time() if now is None else float(now)
    expired = removed = orphans = 0
    with files._connect() as file_conn:
        file_conn.execute("BEGIN IMMEDIATE")
        with _connect() as conn:
            query = "SELECT * FROM scientific_snapshots WHERE expires_at < ? AND payload_file_id != ''"
            args: tuple = (timestamp,)
            if owner_user_id is not None:
                query += " AND owner_user_id=?"
                args += (owner_user_id,)
            for row in conn.execute(query, args).fetchall():
                value = json.loads(row["payload_json"])
                if (row["kind"] != "dataset_snapshot" or _hash(row["payload_json"]) != row["sha256"]
                        or value.get("asset_type") != "prepared_dataset"
                        or value.get("owner_user_id") != row["owner_user_id"]
                        or value.get("payload_file_ref") != row["payload_file_id"]):
                    raise ValueError("Dataset metadata integrity check failed during cleanup")
                if file_conn.execute("SELECT 1 FROM session_file_resource_refs WHERE file_id=? LIMIT 1",
                                      (row["payload_file_id"],)).fetchone():
                    continue
                file_row = file_conn.execute("SELECT * FROM session_files WHERE file_id=? AND owner_user_id=?",
                                              (row["payload_file_id"], row["owner_user_id"])).fetchone()
                if file_row is not None:
                    deleted, count = files._delete_indexed_file(file_conn, file_row)
                    if not deleted:
                        continue
                    removed += count
                conn.execute("DELETE FROM scientific_snapshots WHERE id=? AND owner_user_id=?",
                             (row["id"], row["owner_user_id"]))
                expired += 1
            # Recover a process interruption after register_file committed but
            # before metadata publication. These unconsumed staging files have
            # no metadata retention policy; the normal account idle TTL applies.
            known = {r[0] for r in conn.execute(
                "SELECT payload_file_id FROM scientific_snapshots WHERE payload_file_id != ''")}
            orphan_query = ("SELECT * FROM session_files WHERE visibility='internal' "
                            "AND producer_tool='prepared_dataset' AND last_accessed_at < ?")
            orphan_args: tuple = (timestamp - files.DEFAULT_IDLE_DAYS * 86400,)
            if owner_user_id is not None:
                orphan_query += " AND owner_user_id=?"
                orphan_args += (owner_user_id,)
            for row in file_conn.execute(orphan_query, orphan_args).fetchall():
                if row["file_id"] not in known:
                    deleted, count = files._delete_indexed_file(file_conn, row)
                    if deleted:
                        orphans += 1
                        removed += count
    return {"expired_datasets": expired, "removed_files": removed, "orphan_files": orphans}


def purge_owner(owner_user_id: int) -> int:
    """Account deletion hook; admission must already be closed and drained."""
    owner = _owner(owner_user_id)
    from src.session.files import release_resource_references
    with _connect() as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM scientific_snapshots "
               "WHERE owner_user_id=? AND kind='dataset_snapshot'", (owner,))]
        # Keep records discoverable until every pin release succeeds. A retry
        # repeats idempotent releases instead of losing the cleanup inventory.
        for identity in ids:
            release_resource_references(owner, "dataset_snapshot", identity)
        return conn.execute("DELETE FROM scientific_snapshots WHERE owner_user_id=?", (owner,)).rowcount

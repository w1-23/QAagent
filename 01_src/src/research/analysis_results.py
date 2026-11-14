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

    def to_dict(self) -> dict[str, Any]:
        return {**json.loads(self._payload_json), "ref": self.ref}

    def to_frame(self):
        import pandas as pd
        value = self.to_dict()
        rows = [[pd.Timestamp(cell["$datetime"]) if isinstance(cell, dict) and "$datetime" in cell
                 else cell for cell in row] for row in value["data"]]
        frame = pd.DataFrame(rows, columns=value["columns"], index=value["row_ids"])
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
    expected = ref if isinstance(ref, dict) else {"id": ref}
    if expected.get("kind", kind) != kind or not isinstance(expected.get("id"), str):
        raise LookupError("Scientific result not found")
    if "version" in expected and (type(expected["version"]) is not int or expected["version"] < 1):
        raise ValueError("Invalid scientific snapshot version")
    if "sha256" in expected and (not isinstance(expected["sha256"], str) or
            len(expected["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in expected["sha256"])):
        raise ValueError("Invalid scientific snapshot hash")
    from src.core.account_leases import account_lease
    with account_lease(owner), _connect() as conn:
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
    return _load(owner_user_id, ref, "dataset_snapshot")


def load_analysis_result(owner_user_id: int, ref: dict[str, Any] | str) -> AnalysisResult:
    result = _load(owner_user_id, ref, "analysis_result")
    load_dataset_snapshot(owner_user_id, result.to_dict()["dataset_ref"])
    return result


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

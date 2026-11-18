"""Host authorization and runtime paths for newly persisted local evidence.

This adapter never restores a legacy candidate pool or re-runs a search. The
caller must possess an authenticated task scope and a stored evidence reference.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from citrus_retrieval.evidence import EvidenceStore


def _no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise PermissionError("evidence_link_denied")


def _component(value) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _session_root(owner: int, session: str) -> Path:
    from src.config import PROJECT_ROOT
    return Path(PROJECT_ROOT) / "workspace" / "evidence" / _component(owner) / _component(session)


def _require_session(owner: int, session: str, *, deleted: bool = False,
                     db_path: str | Path | None = None) -> None:
    """Check the real session row read-only, without importing its global manager."""
    from src.config import PROJECT_ROOT
    import sqlite3
    path = Path(db_path) if db_path is not None else Path(PROJECT_ROOT) / "state" / "sessions.db"
    _no_links(path)
    conn = None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        row = conn.execute("SELECT owner_user_id, deleted_at FROM sessions WHERE session_id=?",
                           (session,)).fetchone()
        if not row or row[0] != owner or bool(str(row[1] or "").strip()) != deleted:
            raise PermissionError("evidence_session_unavailable")
    except sqlite3.Error as exc:
        raise PermissionError("evidence_session_unavailable") from exc
    finally:
        if conn is not None:
            conn.close()


def purge_session_evidence(owner_user_id: int, session_id: str, *,
                           session_db_path: str | Path | None = None) -> None:
    """Remove only a verified soft-deleted owner's session evidence subtree."""
    import shutil
    if type(owner_user_id) is not int or owner_user_id <= 0 or not session_id:
        raise PermissionError("evidence_scope_unavailable")
    _require_session(owner_user_id, session_id, deleted=True, db_path=session_db_path)
    root = _session_root(owner_user_id, session_id)
    _no_links(root)
    if not root.exists():
        return
    # Refuse nested links/junctions before recursive removal, as well as linked
    # ancestors. Never follow them into other sessions or arbitrary host files.
    for entry in root.rglob("*"):
        _no_links(entry)
    shutil.rmtree(root)


def _scope(turn_id: str = "", *, writing: bool = False):
    from src.core import jobs, tracing
    owner, session = tracing.get_user_id(), str(tracing.get_session_id() or "")
    turn = str(turn_id or tracing.get_job_id() or "")
    if type(owner) is not int or owner <= 0 or not session or not turn:
        raise PermissionError("evidence_scope_unavailable")
    job = jobs.get_job(turn)
    if (not job or job.get("owner_user_id") != owner
            or job.get("session_id") != session):
        raise PermissionError("evidence_scope_unavailable")
    _require_session(owner, session)
    if writing and (turn != tracing.get_job_id() or job.get("status") not in {"running", "detached"}):
        raise PermissionError("evidence_task_not_running")
    return owner, session, turn


def _store(generation: str, *, turn_id: str = "", writing: bool = False):
    owner, session, turn = _scope(turn_id, writing=writing)
    if not isinstance(generation, str) or not generation.strip() or len(generation) > 240:
        raise ValueError("evidence_generation_required")
    # Raw caller IDs never become filesystem path components.
    root = _session_root(owner, session) / _component(turn) / _component(generation)
    _no_links(root)
    if not writing and not (root / "evidence.jsonl").is_file():
        return None
    return EvidenceStore(root, owner=owner, session_id=session,
                         turn_id=turn, generation=generation)


def _authorize_corpus(corpus_id: str) -> None:
    from src.core.resource_access import PUBLIC_CORPUS, authorize_resource
    from src.core import tracing
    from src.retrieval.corpus_registry import load_corpus_registry, CorpusAccessScope
    registry = load_corpus_registry()
    if not CorpusAccessScope.from_registry(registry).permits(corpus_id):
        raise PermissionError("evidence_corpus_unavailable")
    try:
        level = registry.access_level_for_corpus(corpus_id)
    except Exception as exc:
        raise PermissionError("evidence_corpus_unavailable") from exc
    decision = authorize_resource(PUBLIC_CORPUS,
        {"user_id": tracing.get_user_id(), "role": tracing.get_user_role()}, access_level=level)
    if not decision.allowed:
        raise PermissionError("evidence_corpus_forbidden")


def persist_local_rows(rows: list[dict], *, query: str, generation: str) -> tuple[list[dict], dict]:
    """Persist new rows only with verified host identity; return bounded observations."""
    from src.core import tracing
    from src.core.cancel import raise_if_cancelled
    raise_if_cancelled("persist_local_evidence")
    # Standalone/unscoped compatibility callers get no implicit filesystem scope.
    if not tracing.get_user_id() or not tracing.get_session_id() or not tracing.get_job_id():
        return rows, {"status": "unavailable", "reason": "evidence_scope_unavailable", "candidates": []}
    store = _store(generation, writing=True)
    query_ref = hashlib.sha256(query.encode("utf-8")).hexdigest()
    result, observations = [], []
    for row in rows:
        raise_if_cancelled("persist_local_evidence_row")
        _scope(writing=True)
        corpus_id = str(row.get("corpus_id") or "")
        _authorize_corpus(corpus_id)
        version = hashlib.sha256(str(row.get("text") or "").encode("utf-8")).hexdigest()
        record = {**row, "origin": "local", "evidence_level": row.get("evidence_level") or "passage",
                  "locator": {"corpus_id": corpus_id, "chunk_id": row.get("chunk_id"),
                              "paper_id": row.get("paper_id"), "chunk_index": row.get("chunk_index"),
                              "corpus_family_id": row.get("corpus_family_id") or row.get("corpus_family"),
                              "source_type": row.get("source_type"), "_src": row.get("_src"),
                              "doi": row.get("doi"), "year": row.get("year"),
                              "index_generation": generation, "input_sha256": version}}
        observation = store.put(record, query_ref=query_ref)
        source = store.read(ref=observation["ref"], max_chars=1)["source"]
        read_entry = {"ref": observation["ref"], "candidate_id": observation["ref"],
                      "tool": "read_candidate", "turn_id": store.scope["turn_id"],
                      "generation": generation}
        observation["read_entry"] = read_entry
        result.append({**row, **source, "read_entry": read_entry})
        if len(observations) < 6:
            observations.append(observation)
    _scope(writing=True)
    return result, {"status": "stored", "candidates": observations}


def read_evidence(ref: str, *, turn_id: str, generation: str,
                  offset: int = 0, max_chars: int = 4000) -> dict:
    """Read newly saved evidence under the current principal; never search on miss."""
    if not isinstance(ref, str) or not ref.startswith("ev:"):
        raise ValueError("invalid_evidence_reference")
    store = _store(generation, turn_id=turn_id)
    if store is None:
        return {"status": "evidence_expired", "ref": ref, "text": "", "next_offset": None}
    result = store.read(ref=ref, offset=offset, max_chars=max_chars)
    if result["status"] == "ok":
        locator = result["source"].get("locator")
        if not isinstance(locator, dict):
            raise PermissionError("evidence_corpus_unavailable")
        _authorize_corpus(str(locator.get("corpus_id") or ""))
    _scope(turn_id)
    return result

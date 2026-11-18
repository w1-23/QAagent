"""Authenticated candidate handoff across SDK tasks and executor threads.

ContextVar writes inside a tool never propagate back to the model task. Pools
are therefore indexed by verified host scope; durable ev: reads remain the
source of authority, including when a process-local pool has expired.
"""
from __future__ import annotations

from src.retrieval.candidate_pool import (
    CandidatePool, bind_candidate_pool, get_scoped_pool, put_pool,
)


def scoped_candidate_pool():
    from src.retrieval.evidence_adapter import _scope, _authorize_corpus
    owner, session, turn = _scope()
    pool = get_scoped_pool(owner, session, turn)
    if pool is not None:
        # Recheck current access rather than treating a cached pool as authority.
        for corpus_id in {c.corpus_id for c in pool.candidates}:
            _authorize_corpus(corpus_id)
    return pool


def publish_candidate_pool(artifact: dict, generation: str):
    """Publish only rows already persisted under the current authenticated task."""
    from src.core import tracing
    from src.retrieval.evidence_adapter import _scope
    if (artifact.get("evidence") or {}).get("status") != "stored":
        return None
    owner, session, turn = _scope(writing=True)
    rows = artifact.get("main_results") or []
    for row in rows:
        entry = row.get("read_entry") or {}
        if (not str(entry.get("candidate_id") or "").startswith("ev:")
                or entry.get("candidate_id") != row.get("source_ref")
                or entry.get("turn_id") != turn or entry.get("generation") != generation):
            raise PermissionError("candidate_evidence_scope_mismatch")
    pool = scoped_candidate_pool()
    if pool is None or pool.generation != generation:
        pool = CandidatePool.from_rows([], request_id=tracing.get_request_id(),
            session_id=session, query=str(artifact.get("query") or ""),
            translation_used=bool(artifact.get("translation_used")),
            families=artifact.get("corpus_types") or [])
        pool.owner_user_id, pool.turn_id, pool.generation = owner, turn, generation
    pool.families = tuple(sorted(set(pool.families) | set(artifact.get("corpus_types") or [])))
    pool.add_rows(rows)
    put_pool(pool)
    bind_candidate_pool(pool)
    return pool

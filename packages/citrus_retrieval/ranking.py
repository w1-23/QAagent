"""Shared hybrid computation with explicit legacy host selection policies.

``threshold`` is the MultiBatchRetriever policy: sort/dedupe each recall stream,
raise reranker errors, then apply max(absolute, top * ratio). ``adjacent`` is the
local tool policy: retain channel ordering, fall back to RRF on reranker failure,
cut at an adjacent score drop, then cap passages per document. These differences
are intentional; neither caller silently inherits the other's thresholds.

Models, cancellation and concurrency are injected by the execution host.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable

from .bm25 import rrf_fuse


@dataclass(frozen=True)
class RankingPolicy:
    mode: str
    candidate_window: int
    rerank_top_k: int
    limit: int
    rrf_k: int = 60
    threshold: float = 0.0
    dynamic_ratio: float = 0.0
    adjacent_ratio: float = 0.6

    def __post_init__(self):
        if self.mode not in {"threshold", "adjacent"}:
            raise ValueError("unknown_ranking_policy")
        if any(type(x) is not int or x < 1 for x in (
                self.candidate_window, self.rerank_top_k, self.limit, self.rrf_k)):
            raise ValueError("positive_ranking_budget_required")


def adjacent_decay_band(rows: list[dict], *, score_key: str,
                        ratio: float = 0.6) -> tuple[list[dict], int]:
    kept = []
    previous = None
    for index, row in enumerate(rows):
        try:
            score = float(row.get(score_key) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if previous is not None and previous > 0 and score / previous < ratio:
            return kept, len(rows) - index
        kept.append(row)
        previous = score
    return kept, 0


def dedupe_local_passages(rows: list[dict], limit: int) -> list[dict]:
    """Legacy tool identity: canonical family plus DOI/variety/title fallback."""
    counts = {}
    selected = []
    for row in rows:
        family = str(row.get("corpus_family_id") or row.get("corpus_family") or "")
        identity = (str(row.get("doi") or "").strip().lower()
                    or str(row.get("variety_id") or row.get("registry_id") or "").strip().lower()
                    or re.sub(r"\W+", " ", str(row.get("title") or row.get("variety_name") or "").lower()).strip()
                    or str(row.get("paper_id") or row.get("chunk_id") or row.get("_global_idx") or id(row)))
        key = family + ":" + identity
        if counts.get(key, 0) >= 2:
            continue
        counts[key] = counts.get(key, 0) + 1
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def rank_hybrid(*, streams: list[list[tuple[int, float]]], weights: list[float],
                query: str, load_chunk: Callable, rerank: Callable,
                policy: RankingPolicy, check: Callable | None = None,
                abort_exceptions: tuple[type[BaseException], ...] = ()) -> dict:
    """Fuse, score and select; cancellation must never become a scoring fallback.

    ``check`` observes the host token at phase boundaries. Explicit exception
    types also propagate when a model reports cancellation without a bound token.
    """
    def checkpoint():
        if check is not None:
            check()
    checkpoint()
    prepared = []
    for hits in streams:
        checkpoint()
        if policy.mode == "threshold":
            seen = set()
            hits = [(index, score) for index, score in sorted(hits, key=lambda x: x[1], reverse=True)
                    if not (index in seen or seen.add(index))]
        prepared.append(hits)
    fused = rrf_fuse(*prepared, k=policy.rrf_k, weights=weights)
    candidates = []
    for index, _ in fused[:policy.candidate_window]:
        checkpoint()
        candidates.append(load_chunk(index))
    checkpoint()
    error = ""
    started = time.perf_counter()
    try:
        # The legacy threshold path invokes even on an empty candidate list.
        ranked = rerank(query, candidates, top_k=policy.rerank_top_k) if (
            candidates or policy.mode == "threshold") else []
    except Exception as exc:
        if isinstance(exc, abort_exceptions):
            raise
        checkpoint()
        if policy.mode == "threshold":
            raise
        error = f"{type(exc).__name__}: {exc}"
        ranked = candidates
    checkpoint()
    rerank_ms = (time.perf_counter() - started) * 1000
    threshold = 0.0
    decay_filtered = 0
    if policy.mode == "threshold":
        threshold = max(policy.threshold, (ranked[0].get("rerank_score", 0)
                                          if ranked else 0) * policy.dynamic_ratio)
        selected = [row for row in ranked if row.get("rerank_score", 0) >= threshold]
    else:
        if not ranked and candidates:
            error = error or "reranker returned no candidates"
            ranked = candidates
        scores = dict(fused[:policy.candidate_window])
        for row in ranked:
            index = row.get("_global_idx")
            row["hybrid_score"] = scores.get(int(index), 0.0) if index is not None else 0.0
        kept, decay_filtered = adjacent_decay_band(
            ranked, score_key="hybrid_score" if error else "rerank_score",
            ratio=policy.adjacent_ratio)
        selected = dedupe_local_passages(kept, policy.limit)
    checkpoint()
    return {"streams": prepared, "fused": fused, "candidates": candidates,
            "ranked": ranked, "selected": selected, "threshold": threshold,
            "decay_filtered": decay_filtered, "rerank_error": error,
            "rerank_ms": rerank_ms}

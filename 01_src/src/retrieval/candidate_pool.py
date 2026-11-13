# -*- coding: utf-8 -*-
"""Request-scoped candidate pool for gap-driven local evidence.

A request may run one local hybrid search *before* the model sees it.  The
resulting chunks stay in this module's process memory; only a count/family
notice reaches the model.  Candidate text becomes visible exclusively through
bounded tool calls (``inspect_candidates`` / ``read_candidate`` /
``search_candidates``).

Design constraints:

* The pool is keyed by ``pool_id`` (returned in the graph state snapshot) and
  bound to a ``ContextVar`` for the duration of the supervisor turn.  Tool
  execution already copies the request context (``tools/registry.py``), so the
  worker thread sees the same pool.
* Full text is never serialized into ``AgentState``, session history, progress
  events, or logs.
* The pool is strictly request-scoped: no database, no cross-turn reuse, TTL and
  capacity bounds as a leak guard.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from src.core.record_label import display_label, scrub_internal_keys

MAX_POOLS = 64
DEFAULT_POOL_SIZE = 12
DEFAULT_POOL_MAX = 40
DEFAULT_TTL_SECONDS = 1800

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,}")
_WS_RE = re.compile(r"\s+")


def _setting_int(name: str, default: int) -> int:
    try:
        from src.config import settings

        return int(getattr(settings, name, default) or default)
    except Exception:
        return default


def _max_candidates() -> int:
    return max(1, min(200, _setting_int("CANDIDATE_POOL_MAX", DEFAULT_POOL_MAX)))


def _ttl_seconds() -> int:
    return max(60, _setting_int("CANDIDATE_POOL_TTL_SEC", DEFAULT_TTL_SECONDS))


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _chunk_key(row: dict) -> str:
    """Stable identity for dedupe; never shown to the model."""
    for key in ("chunk_id", "record_key"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    paper_id = str(row.get("paper_id") or "").strip()
    if paper_id:
        index = row.get("chunk_index")
        return f"{paper_id}:{'' if index is None else index}"
    text = str(row.get("text") or row.get("snippet") or "")
    seed = str(row.get("title") or "") + "|" + text[:200]
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _language(row: dict) -> str:
    """Prefer an explicit chunk language; otherwise use a known batch mapping.

    Every registered corpus ships an explicit ``lang`` field, so the batch
    mapping is only a fallback for rows that lost it. All registered batches
    hold English or translated-to-English text (the Chinese corpora were
    translated on the query side and stored in English), so the fallback maps
    them to ``en``. An unknown batch stays empty instead of being guessed.
    """
    direct = str(row.get("language") or row.get("lang") or "").strip().casefold()
    if direct in {"english", "en"}:
        return "en"
    if direct in {"chinese", "zh", "zh-cn", "zh-tw"}:
        return "zh"
    if direct:
        return direct
    batch = " ".join(str(row.get(key) or "") for key in (
        "_source_batch", "source_batch", "corpus_family", "corpus_family_id",
        "corpus_id")).casefold()
    if any(token in batch for token in (
            "literature", "methods", "ucr", "variet", "translated", "deng")):
        return "en"
    return ""


def _tokens(text) -> set[str]:
    """Lexical units for gap ranking: English words + CJK 2-grams."""
    raw = str(text or "").casefold()
    words = set(_WORD_RE.findall(raw))
    cjk = "".join(_CJK_RE.findall(raw))
    grams = {cjk[index:index + 2] for index in range(max(0, len(cjk) - 1))}
    return words | grams


def _ordered_terms(text) -> list[str]:
    raw = str(text or "").casefold()
    words = _WORD_RE.findall(raw)
    cjk = "".join(_CJK_RE.findall(raw))
    grams = [cjk[index:index + 2] for index in range(max(0, len(cjk) - 1))]
    return sorted(set(words + grams), key=len, reverse=True)


def snippet_around(text, gap: str = "", width: int = 240) -> str:
    """Whitespace-normalized excerpt around the first gap term (or the start)."""
    cleaned = _WS_RE.sub(" ", str(text or "")).strip()
    if not cleaned:
        return ""
    width = max(40, int(width or 240))
    if len(cleaned) <= width:
        return cleaned
    needle = ""
    for term in _ordered_terms(gap):
        if term and term in cleaned.casefold():
            needle = term
            break
    if not needle:
        return cleaned[:width].rstrip() + "…"
    index = cleaned.casefold().find(needle)
    start = max(0, index - width // 3)
    end = min(len(cleaned), start + width)
    if end - start < width:
        start = max(0, end - width)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(cleaned) else ""
    return prefix + cleaned[start:end].strip() + suffix


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    chunk_key: str
    record_key: str
    paper_id: str
    chunk_id: str
    chunk_index: int
    corpus_id: str
    corpus_family: str
    title: str
    doi: str
    year: str
    lang: str
    source_type: str
    src: str
    score: float
    rerank_score: float | None
    snippet: str
    text: str

    @property
    def text_chars(self) -> int:
        return len(self.text)

    def public_dict(self) -> dict:
        """Metadata safe for tool output; never contains text."""
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "doi": self.doi,
            "year": self.year,
            "lang": self.lang,
            "corpus_family": self.corpus_family,
            "score": round(float(self.score or 0.0), 6),
            "rerank_score": (round(float(self.rerank_score), 6)
                             if self.rerank_score is not None else None),
            "text_chars": self.text_chars,
        }

    def evidence_row(self, text: str = "") -> dict:
        """Internal row for ``turn_source_ledger`` (never shown to the model)."""
        row = {
            "record_key": self.record_key,
            "paper_id": self.paper_id,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "corpus_id": self.corpus_id,
            "corpus_family": self.corpus_family,
            "corpus_family_id": self.corpus_family,
            "title": self.title,
            "doi": self.doi,
            "year": self.year,
            "source_type": self.source_type,
            "_src": self.src,
        }
        if text:
            row["text"] = text
        else:
            row["snippet"] = self.snippet
        return row


def candidate_from_row(row: dict, index: int) -> Candidate:
    """Convert one full local-search row into a pool entry."""
    row = row if isinstance(row, dict) else {}
    text = str(row.get("text") or row.get("chunk_text") or "")
    chunk_index = row.get("chunk_index")
    try:
        chunk_index = int(chunk_index) if chunk_index not in (None, "") else -1
    except (TypeError, ValueError):
        chunk_index = -1
    title = scrub_internal_keys(display_label(row, with_section=True))
    family = str(row.get("corpus_family_id") or row.get("corpus_family")
                 or row.get("corpus") or "")
    return Candidate(
        candidate_id=f"c{int(index):02d}",
        chunk_key=_chunk_key(row),
        record_key=str(row.get("record_key") or row.get("paper_id")
                       or row.get("chunk_id") or ""),
        paper_id=str(row.get("paper_id") or ""),
        chunk_id=str(row.get("chunk_id") or ""),
        chunk_index=chunk_index,
        corpus_id=str(row.get("corpus_id") or ""),
        corpus_family=family,
        title=title,
        doi=str(row.get("doi") or ""),
        year=str(row.get("year") or ""),
        lang=_language(row),
        source_type=str(row.get("source_type") or row.get("_src") or ""),
        src=str(row.get("_src") or row.get("source") or ""),
        score=_float(row.get("hybrid_score") or row.get("rerank_score")),
        rerank_score=_optional_float(row.get("rerank_score")),
        snippet=snippet_around(text, "", 240),
        text=text,
    )


@dataclass
class CandidatePool:
    pool_id: str
    request_id: str = ""
    session_id: str = ""
    query: str = ""
    translation_used: bool = False
    families: tuple[str, ...] = ()
    status: str = "ready"
    error: str = ""
    created_at: float = field(default_factory=time.time)
    elapsed_ms: float = 0.0
    candidates: list[Candidate] = field(default_factory=list)
    _by_id: dict[str, Candidate] = field(default_factory=dict, repr=False)
    _keys: set[str] = field(default_factory=set, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @classmethod
    def from_rows(cls, rows, *, pool_id: str = "", request_id: str = "",
                  session_id: str = "", query: str = "",
                  translation_used: bool = False, families=(),
                  elapsed_ms: float = 0.0, error: str = "") -> "CandidatePool":
        pool = cls(
            pool_id=pool_id or uuid.uuid4().hex[:16],
            request_id=str(request_id or ""),
            session_id=str(session_id or ""),
            query=str(query or ""),
            translation_used=bool(translation_used),
            families=tuple(str(f) for f in (families or ()) if str(f).strip()),
            elapsed_ms=float(elapsed_ms or 0.0),
            error=str(error or ""),
        )
        pool.add_rows(rows)
        pool.status = "ready" if pool.candidates else "zero_hit"
        return pool

    def add_rows(self, rows) -> tuple[list[Candidate], bool]:
        """Append de-duplicated rows; existing candidate ids never change."""
        new: list[Candidate] = []
        limit = _max_candidates()
        with self._lock:
            for row in rows or ():
                if len(self.candidates) >= limit:
                    break
                if not isinstance(row, dict):
                    continue
                key = _chunk_key(row)
                if not key or key in self._keys:
                    continue
                candidate = candidate_from_row(row, len(self.candidates) + 1)
                self.candidates.append(candidate)
                self._by_id[candidate.candidate_id] = candidate
                self._keys.add(key)
                new.append(candidate)
            if new and self.status == "zero_hit":
                self.status = "ready"
        return new, bool(new)

    def find(self, candidate_id: str) -> Candidate | None:
        key = str(candidate_id or "").strip().casefold()
        with self._lock:
            return self._by_id.get(key)

    def inspect(self, gap: str = "", limit: int = 6, lang: str = "",
                family: str = "") -> list[Candidate]:
        """Return bounded metadata candidates, reordered for the stated gap."""
        try:
            limit = max(1, min(10, int(limit or 6)))
        except (TypeError, ValueError):
            limit = 6
        lang = str(lang or "").strip().casefold()
        family = str(family or "").strip().casefold()
        with self._lock:
            items = list(self.candidates)
        if lang:
            items = [c for c in items if c.lang.casefold() == lang]
        if family:
            items = [c for c in items if c.corpus_family.casefold() == family]
        if not items or not str(gap or "").strip():
            return items[:limit]
        tokens = _tokens(gap)
        if not tokens:
            return items[:limit]
        scored = []
        for candidate in items:
            score = (3 * len(tokens & _tokens(candidate.title))
                     + len(tokens & _tokens(candidate.snippet)))
            scored.append((score, candidate))
        # Stable sort: equal scores keep the prefetch (reranked) order.
        scored.sort(key=lambda pair: -pair[0])
        return [candidate for _score, candidate in scored[:limit]]

    def summary(self) -> dict:
        """Metadata-only snapshot safe for ``AgentState`` and traces."""
        return {
            "pool_id": self.pool_id,
            "status": self.status,
            "count": len(self.candidates),
            "families": list(self.families),
            "translation_used": bool(self.translation_used),
            "elapsed_ms": round(float(self.elapsed_ms or 0.0), 1),
            "error": str(self.error or "")[:160],
        }

    def notice(self) -> str:
        """Compact model-visible note: counts and usage only, never titles/text."""
        families = "、".join(self.families) or "未标注"
        translated = "；中文查询已译为英文" if self.translation_used else ""
        if self.status == "zero_hit":
            return (
                "本轮已在进入模型前同步检索本地语料，未命中候选"
                f"（语料族：{families}{translated}）。"
                "这不等于语料中没有资料：可换关键词调用 search_candidates 补充，"
                "或直接调用 local_hybrid_search。"
            )
        return (
            "本轮已在进入模型前同步检索本地语料"
            f"（语料族：{families}{translated}）。"
            f"候选池：{len(self.candidates)} 条，候选正文未放入上下文。"
            "需要证据时先调用 inspect_candidates(gap=\"当前缺口\") 查看候选元数据，"
            "再用 read_candidate(candidate_id=\"c01\") 读取有界原文；"
            "缺口变化时用 search_candidates(query=\"...\") 补充。"
            "不要一次读取全部候选。"
        )


_POOLS: dict[str, CandidatePool] = {}
_POOLS_LOCK = threading.RLock()
_CURRENT_POOL: ContextVar[CandidatePool | None] = ContextVar(
    "citrus_candidate_pool", default=None)


def build_candidate_pool(*, rows, request_id: str = "", session_id: str = "",
                         query: str = "", translation_used: bool = False,
                         families=(), elapsed_ms: float = 0.0,
                         size: int | None = None) -> CandidatePool:
    limit = int(size if size is not None else
                _setting_int("CANDIDATE_POOL_SIZE", DEFAULT_POOL_SIZE))
    limit = max(1, min(limit, _max_candidates()))
    pool = CandidatePool.from_rows(
        list(rows or ())[:limit],
        request_id=request_id,
        session_id=session_id,
        query=query,
        translation_used=translation_used,
        families=families,
        elapsed_ms=elapsed_ms,
    )
    put_pool(pool)
    return pool


def put_pool(pool: CandidatePool) -> None:
    now = time.time()
    with _POOLS_LOCK:
        _prune_locked(now)
        _POOLS[pool.pool_id] = pool
        while len(_POOLS) > MAX_POOLS:
            oldest = min(_POOLS.values(), key=lambda item: item.created_at)
            _POOLS.pop(oldest.pool_id, None)


def get_pool(pool_id: str = "", request_id: str = "") -> CandidatePool | None:
    """Look up by explicit id, request id, or the current request context."""
    explicit = str(pool_id or "").strip()
    if explicit:
        with _POOLS_LOCK:
            return _POOLS.get(explicit)
    rid = str(request_id or "").strip()
    if not rid:
        return current_candidate_pool()
    with _POOLS_LOCK:
        matches = [pool for pool in _POOLS.values() if pool.request_id == rid]
        return max(matches, key=lambda pool: pool.created_at) if matches else None


def clear_pool(pool_id: str = "") -> int:
    pid = str(pool_id or "").strip()
    if not pid:
        return 0
    with _POOLS_LOCK:
        return 1 if _POOLS.pop(pid, None) is not None else 0


def clear_request_pools(request_id: str) -> int:
    rid = str(request_id or "").strip()
    if not rid:
        return 0
    with _POOLS_LOCK:
        stale = [pid for pid, pool in _POOLS.items() if pool.request_id == rid]
        for pid in stale:
            _POOLS.pop(pid, None)
        return len(stale)


def _prune_locked(now: float) -> int:
    cutoff = now - _ttl_seconds()
    expired = [pid for pid, pool in _POOLS.items() if pool.created_at < cutoff]
    for pid in expired:
        _POOLS.pop(pid, None)
    return len(expired)


def prune_expired(now: float | None = None) -> int:
    with _POOLS_LOCK:
        return _prune_locked(float(now if now is not None else time.time()))


def clear_pools() -> None:
    """Test-only full reset (registry + current context binding)."""
    with _POOLS_LOCK:
        _POOLS.clear()
    _CURRENT_POOL.set(None)


def bind_candidate_pool(pool: CandidatePool | None) -> None:
    _CURRENT_POOL.set(pool)


def current_candidate_pool() -> CandidatePool | None:
    return _CURRENT_POOL.get()


def clear_current_candidate_pool() -> None:
    _CURRENT_POOL.set(None)


__all__ = [
    "Candidate", "CandidatePool", "candidate_from_row", "snippet_around",
    "build_candidate_pool", "put_pool", "get_pool", "clear_pool",
    "clear_request_pools", "prune_expired", "clear_pools",
    "bind_candidate_pool", "current_candidate_pool",
    "clear_current_candidate_pool", "MAX_POOLS",
]

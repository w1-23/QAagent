"""Independent reader handle and hybrid search for one shared-corpus generation.

A handle owns an immutable generation: its chunk dictionaries, one cached BM25
index and one LanceDB table connection.  It never writes generation files and
never reads the active/previous pointer, so the parent runtime may keep an old
handle alive while a replacement generation is validated and activated.

The public surface is deliberately small: ``manifest``, ``lookup`` and
``search``.  Every model call goes through the host-injected ``Embedder`` and
``Reranker`` instances.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path
from typing import Any

# Shared corpus identity and on-disk contract.  The parent runtime owns the
# durable active/previous pointers; these constants only describe one build.
CORPUS_ID = "shared_agent_literature"
FAMILY_ID = "agent_literature"
CHUNK_SCHEMA_VERSION = 2
INDEX_SCHEMA_VERSION = 2
BUILDER_VERSION = 1
EMBEDDING_DIM = 1024
TABLE_NAME = "chunks"
CHUNKS_FILENAME = "chunks.jsonl"
MANIFEST_FILENAME = "manifest.json"
LANCEDB_SUBDIR = "lancedb"
GENERATIONS_DIRNAME = "generations"
STAGING_DIRNAME = ".staging"
DOCUMENT_PREFIX = "passage:"
QUERY_PREFIX = "query:"

# Fixed, deterministic health probe used by the parent before activation.
HEALTH_QUERY = "scientific research methods data analysis"

DEFAULT_TOP_K_VECTOR = 40
DEFAULT_TOP_K_BM25 = 40
DEFAULT_CANDIDATE_WINDOW = 20
DEFAULT_RRF_K = 60
DEFAULT_DENSE_WEIGHT = 1.0
DEFAULT_BM25_WEIGHT = 1.0
MAX_SEARCH_LIMIT = 50


class GenerationError(ValueError):
    """Base class for shared generation contract and integrity failures."""


class GenerationNotFound(GenerationError):
    """The requested generation directory or manifest does not exist."""


class GenerationContractError(GenerationError):
    """A manifest, handle or model violates the shared generation contract."""


class GenerationIntegrityError(GenerationError):
    """Stored bytes, hashes or row identities do not match the manifest."""


class GenerationHandleClosed(GenerationError):
    """The handle was closed and can no longer answer queries."""


def chunk_identifier(document_id: str, chunk_index: int) -> str:
    """Return the schema-v2 chunk ID used by the shared chunker.

    The agent chunker derives ``chunk_id`` from corpus, document and per-document
    chunk index.  Builders and loaders use the same function so a stored ID can
    be independently recomputed during verification.
    """
    payload = f"{CORPUS_ID}\0{document_id}\0{int(chunk_index)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _bm25_backend():
    """Import the host BM25/RRF implementation lazily.

    Lazy import keeps ``import src.shared_corpus.search`` free of numpy and
    LanceDB, and lets the service reuse the exact tokenizer and weighted RRF
    already used by the legacy retriever.
    """
    try:
        from src.retrieval.bm25 import BM25Plus, rrf_fuse
    except ImportError:
        from citrus_retrieval.bm25 import BM25Plus, rrf_fuse
    return BM25Plus, rrf_fuse


def _retrieval_parameters() -> tuple[int, int, int, int, float, float]:
    top_vector = DEFAULT_TOP_K_VECTOR
    top_bm25 = DEFAULT_TOP_K_BM25
    window = DEFAULT_CANDIDATE_WINDOW
    rrf_k = DEFAULT_RRF_K
    dense_weight = DEFAULT_DENSE_WEIGHT
    bm25_weight = DEFAULT_BM25_WEIGHT
    try:
        from src.config import settings
        top_vector = int(settings.TOP_K_VECTOR)
        top_bm25 = int(settings.TOP_K_BM25)
        window = int(settings.CANDIDATE_WINDOW)
        rrf_k = int(settings.RRF_K)
        dense_weight = float(settings.RRF_WEIGHT_ORIG_DENSE)
        bm25_weight = float(settings.RRF_WEIGHT_BM25)
    except Exception:
        # Standalone callers and tests may not have the service config package.
        pass
    return top_vector, top_bm25, window, rrf_k, dense_weight, bm25_weight


def _validate_query_vector(vector: Any, dimension: int) -> list[float]:
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError) as exc:
        raise GenerationContractError("query_embedding_invalid") from exc
    if len(values) != int(dimension):
        raise GenerationContractError(
            f"query_embedding_dimension_mismatch: expected={dimension} actual={len(values)}")
    if not all(math.isfinite(value) for value in values):
        raise GenerationContractError("query_embedding_not_finite")
    return values


class GenerationHandle:
    """One independently readable, immutable generation."""

    def __init__(
        self,
        *,
        root: Path,
        generation: str,
        manifest: dict[str, Any],
        rows: list[dict[str, Any]],
        table: Any,
        embedder: Any,
        reranker: Any,
    ) -> None:
        self.root = Path(root)
        self.generation = generation
        self._manifest = deepcopy(manifest)
        self._rows = rows
        self._index = {str(row["chunk_id"]): position
                       for position, row in enumerate(rows)}
        if len(self._index) != len(rows):
            raise GenerationIntegrityError("generation_handle_duplicate_chunk_id")
        self._table = table
        self._embedder = embedder
        self._reranker = reranker
        self._closed = False
        try:
            BM25Plus, rrf_fuse = _bm25_backend()
            bm25 = BM25Plus()
            bm25.fit([row["text"] for row in rows])
        except Exception as exc:
            raise GenerationContractError("bm25_backend_unavailable") from exc
        self._bm25 = bm25
        self._rrf_fuse = rrf_fuse

    @property
    def manifest(self) -> dict[str, Any]:
        """Return a detached copy of the generation manifest."""
        return deepcopy(self._manifest)

    def __len__(self) -> int:
        return len(self._rows)

    def __enter__(self) -> "GenerationHandle":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise GenerationHandleClosed("generation_handle_closed")

    def lookup(self, chunk_id: str) -> dict[str, Any] | None:
        """Return one detached chunk dictionary, or ``None`` when absent."""
        if not isinstance(chunk_id, str) or not chunk_id:
            raise ValueError("chunk_id_required")
        self._check_open()
        position = self._index.get(chunk_id)
        if position is None:
            return None
        return deepcopy(self._rows[position])

    def search(self, query: str, limit: int = 6) -> list[dict[str, Any]]:
        """Hybrid cosine-vector + BM25 retrieval followed by BGE reranking.

        The returned dictionaries contain the canonical chunk fields plus
        ``score`` (weighted RRF) and ``rerank_score``.  The caller may keep the
        rows; they do not alias the handle's internal dictionaries.
        """
        self._check_open()
        text = str(query or "").strip()
        if not text:
            raise ValueError("query_required")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit_must_be_integer")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError("limit_out_of_range")
        if self._closed or self._table is None or self._bm25 is None:
            raise GenerationHandleClosed("generation_handle_closed")

        top_vector, top_bm25, window, rrf_k, dense_weight, bm25_weight = (
            _retrieval_parameters())
        top_vector = max(top_vector, limit)
        top_bm25 = max(top_bm25, limit)
        dimension = int(self._manifest["dimension"])

        vector = _validate_query_vector(self._embedder.embed_query(text), dimension)
        try:
            dense_rows = (
                self._table.search(vector)
                .distance_type("cosine")
                .limit(top_vector)
                .to_list()
            )
        except Exception as exc:
            raise GenerationIntegrityError("dense_search_failed") from exc

        dense_hits: list[tuple[int, float]] = []
        for row in dense_rows:
            chunk_id = str(row.get("chunk_id") or "")
            position = self._index.get(chunk_id)
            if position is None:
                raise GenerationIntegrityError("dense_result_outside_generation")
            try:
                distance = float(row["_distance"])
            except (KeyError, TypeError, ValueError) as exc:
                raise GenerationIntegrityError("dense_distance_missing") from exc
            dense_hits.append((position, 1.0 - distance))
        # Stable ordering means equal distances cannot reorder a generation.
        dense_hits.sort(key=lambda item: (-item[1], self._rows[item[0]]["chunk_id"]))

        lexical_hits = self._bm25.top_k(text, k=top_bm25)
        fused = self._rrf_fuse(dense_hits, lexical_hits, k=int(rrf_k),
                               weights=[float(dense_weight), float(bm25_weight)])
        fused.sort(key=lambda item: (-item[1], self._rows[item[0]]["chunk_id"]))

        candidates: list[dict[str, Any]] = []
        for position, score in fused[:min(len(fused), max(int(window), limit))]:
            row = deepcopy(self._rows[position])
            row["score"] = float(score)
            candidates.append(row)
        if not candidates:
            return []

        # BGE rerank is part of the contract, not an optional best-effort step.
        ranked = self._reranker.rerank(text, candidates, top_k=len(candidates))
        if not isinstance(ranked, list):
            raise GenerationContractError("reranker_result_invalid")
        selected: list[dict[str, Any]] = []
        for row in ranked:
            if not isinstance(row, dict):
                raise GenerationContractError("reranker_result_invalid")
            if str(row.get("chunk_id") or "") not in self._index:
                raise GenerationIntegrityError("reranker_result_outside_generation")
            selected.append(deepcopy(row))
            if len(selected) >= limit:
                break
        return selected

    def close(self) -> None:
        """Release the table and cached BM25 state.  Idempotent."""
        self._closed = True
        self._table = None
        self._bm25 = None
        self._rows = []
        self._index = {}


__all__ = [
    "BUILDER_VERSION",
    "CHUNKS_FILENAME",
    "CHUNK_SCHEMA_VERSION",
    "CORPUS_ID",
    "DOCUMENT_PREFIX",
    "EMBEDDING_DIM",
    "FAMILY_ID",
    "GENERATIONS_DIRNAME",
    "GenerationContractError",
    "GenerationError",
    "GenerationHandle",
    "GenerationHandleClosed",
    "GenerationIntegrityError",
    "GenerationNotFound",
    "HEALTH_QUERY",
    "INDEX_SCHEMA_VERSION",
    "LANCEDB_SUBDIR",
    "MANIFEST_FILENAME",
    "QUERY_PREFIX",
    "STAGING_DIRNAME",
    "TABLE_NAME",
    "chunk_identifier",
]

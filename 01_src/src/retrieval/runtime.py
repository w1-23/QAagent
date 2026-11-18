"""Process-wide retriever instance used by the local hybrid-search tool."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import threading

logger = logging.getLogger(__name__)
_RAG_INSTANCE = None
_RAG_LOCK = threading.Lock()


class RetrievalUnavailable(RuntimeError):
    """Only local retrieval is unavailable; importing other tools stays safe."""

    def __init__(self, reason_code: str, *, empty_allowed: bool):
        self.reason_code, self.empty_allowed = reason_code, empty_allowed
        super().__init__(reason_code + (
            ": local retrieval is unavailable in explicit empty-data mode" if empty_allowed else
            ": configure corpus data or CITRUSQA_ALLOW_EMPTY_DATA=1"))


def _require_data() -> None:
    from src.config import settings
    from src.runtime_paths import data_capability, require_retrieval_data
    root = Path(settings.DATA_DIR)
    allow_empty = os.getenv("CITRUSQA_ALLOW_EMPTY_DATA", "").strip() == "1"
    capability = data_capability(root, allow_empty=allow_empty)
    # Explicit legacy Qdrant stores have no active_index.json. They still cross
    # the registry/schema allow-list checks in MultiBatchRetriever before use.
    if (capability.reason_code == "index_missing"
            and str(settings.RETRIEVAL_BACKEND).casefold() in {"qdrant", "auto"}
            and any(path.is_dir() for path in root.glob("*/qdrant_data"))):
        return
    try:
        require_retrieval_data(root, allow_empty=allow_empty)
    except RuntimeError as exc:
        raise RetrievalUnavailable(capability.reason_code, empty_allowed=allow_empty) from exc
    if not capability.available:
        raise RetrievalUnavailable(capability.reason_code, empty_allowed=allow_empty)


def get_rag():
    """Load the shared corpus/index/models once per service process."""
    global _RAG_INSTANCE
    if _RAG_INSTANCE is None:
        with _RAG_LOCK:
            if _RAG_INSTANCE is None:
                _require_data()
                from src.retrieval.multi_retriever import MultiBatchRetriever
                _RAG_INSTANCE = MultiBatchRetriever()
                logger.info("Local retriever singleton initialized")
    return _RAG_INSTANCE

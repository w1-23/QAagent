"""Compatibility facade for the shared BM25 implementation and trusted cache codec.

Keep these names importable so existing host callers and trusted legacy pickles
continue to resolve their original module path.
"""
from citrus_retrieval.bm25 import (
    BM25Plus,
    BM25_CACHE_FORMAT,
    _tokenize,
    bm25_from_cache_dict,
    bm25_to_cache_dict,
    rrf_fuse,
)

__all__ = [
    "BM25Plus", "BM25_CACHE_FORMAT", "bm25_from_cache_dict",
    "bm25_to_cache_dict", "rrf_fuse",
]

from unittest.mock import Mock, patch

import pytest

from src.retrieval.local_query_language import (
    LocalQueryLanguageError,
    normalize_local_query,
)
from src.retrieval.multi_retriever import MultiBatchRetriever
from src.tools import deepseek_search_fetch as local_tools
from src.tools import search as search_module


def _has_cjk(text):
    return any("\u3400" <= char <= "\u9fff" for char in text)


def test_normalizer_passes_english_and_translates_chinese_preserving_doi():
    with patch.object(search_module, "_cached_translation") as translator:
        query, info = normalize_local_query("Citrus Huanglongbing resistance genes")
    assert query == "Citrus Huanglongbing resistance genes"
    assert info["translation_used"] is False
    translator.assert_not_called()

    with patch.object(
        search_module,
        "_cached_translation",
        return_value="Citrus Huanglongbing resistance genes",
    ):
        query, info = normalize_local_query("柑橘黄龙病抗性基因 DOI 10.1234/test")
    assert query.endswith("10.1234/test")
    assert not _has_cjk(query)
    assert info["translation_used"] is True


@pytest.mark.parametrize("translation", [None, "", "Citrus 黄龙病 resistance"])
def test_normalizer_fails_closed_when_translation_is_missing_or_not_english(translation):
    with patch.object(search_module, "_cached_translation", return_value=translation):
        query, info = normalize_local_query("黄龙病 resistance")
    assert query is None
    assert info["status"] == "translation_error"


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        (local_tools.local_hybrid_search, {"corpus_types": ["literature"]}),
        (local_tools.local_bm25_search, {"corpus_types": ["literature"]}),
        (local_tools.local_dense_search, {"corpus_types": ["literature"]}),
        (search_module.citrus_rag_search, {}),
    ],
)
def test_public_local_tools_do_not_open_rag_if_translation_fails(tool, kwargs):
    with (
        patch.object(search_module, "_cached_translation", return_value=None),
        patch.object(search_module, "_get_rag", side_effect=AssertionError("RAG must not start")),
        patch("src.core.diag.diag"),
    ):
        content, artifact = tool.func(query="柑橘黄龙病研究", **kwargs)
    assert "TRANSLATION_ERROR" in content
    assert artifact["local_status"] == "translation_error"


def test_multi_retriever_passes_only_english_to_bm25_and_embedder():
    retriever = object.__new__(MultiBatchRetriever)
    retriever.bm25 = Mock()
    retriever.bm25.top_k.return_value = []
    retriever.embedder = Mock()
    retriever.embedder.embed_query.return_value = [0.1]
    retriever.batches = {}
    retriever.lance_tables = {}
    retriever.backend = "qdrant"
    retriever._fuse_rerank_select = Mock(return_value=[])

    with patch.object(search_module, "_cached_translation", return_value="Citrus Huanglongbing"):
        retriever.search_multi(["黄龙病"], original_query="黄龙病")

    assert retriever.bm25.top_k.call_args.args[0] == "Citrus Huanglongbing"
    assert retriever.embedder.embed_query.call_args.args[0] == "Citrus Huanglongbing"

    retriever.bm25.reset_mock()
    retriever.embedder.embed_query.reset_mock()
    with patch.object(search_module, "_cached_translation", return_value=None):
        with pytest.raises(LocalQueryLanguageError):
            retriever.search_multi(["黄龙病"])
    retriever.bm25.top_k.assert_not_called()
    retriever.embedder.embed_query.assert_not_called()

def _filtered_retriever():
    retriever = object.__new__(MultiBatchRetriever)
    retriever.global_chunks = [
        {"_global_idx": 0, "_batch": "paper_test", "source_type": "literature"}
    ]
    retriever.bm25 = Mock()
    retriever.bm25.top_k.return_value = []
    retriever.embedder = Mock()
    retriever.embedder.embed_query.return_value = [0.1]
    retriever.batches = {}
    retriever.lance_tables = {}
    retriever.backend = "qdrant"
    return retriever


def test_filtered_bm25_and_dense_translate_before_backend_calls():
    retriever = _filtered_retriever()
    with patch.object(search_module, "_cached_translation", return_value="Citrus Huanglongbing"):
        retriever.search_bm25_filtered("黄龙病", corpus_types=["literature"])
        retriever.search_dense_filtered("黄龙病", corpus_types=["literature"])
    assert retriever.bm25.top_k.call_args.args[0] == "Citrus Huanglongbing"
    assert retriever.embedder.embed_query.call_args.args[0] == "Citrus Huanglongbing"


def test_filtered_bm25_and_dense_block_failed_translation_before_backend_calls():
    retriever = _filtered_retriever()
    with patch.object(search_module, "_cached_translation", return_value=None):
        with pytest.raises(LocalQueryLanguageError):
            retriever.search_bm25_filtered("黄龙病", corpus_types=["literature"])
        with pytest.raises(LocalQueryLanguageError):
            retriever.search_dense_filtered("黄龙病", corpus_types=["literature"])
    retriever.bm25.top_k.assert_not_called()
    retriever.embedder.embed_query.assert_not_called()

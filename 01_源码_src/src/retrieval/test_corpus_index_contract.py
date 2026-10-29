import json
from pathlib import Path

import pytest

from src.config import settings
from src.retrieval import corpus_admin
from src.retrieval import multi_retriever as retriever_module
from src.retrieval.corpus_registry import (
    CorpusMetadataError,
    _validate_registry,
    default_registry_payload,
    deterministic_id,
    iter_lance_rows,
)


def test_legacy_batches_canonicalize_to_registered_families(tmp_path, monkeypatch):
    monkeypatch.setattr(corpus_admin, "DATA_DIR", tmp_path)
    batches = {
        "cqa_literature": ("english_literature", "literature"),
        "cqa_ucr_varieties": ("ucr_varieties", "ucr"),
        "cqa_methods": ("cgi_methods", "methods"),
        "cqa_deng_book": ("deng_xiuxin", "deng"),
    }
    for batch, (category, source_type) in batches.items():
        path = tmp_path / batch / "chunks" / "chunks.jsonl"
        path.parent.mkdir(parents=True)
        row = {
            "paper_id": f"paper-{batch}",
            "chunk_index": 0,
            "text": f"A representative chunk from {batch}.",
            "category": category,
            "source_type": source_type,
            "source_file": f"{batch}/source.txt",
        }
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    registry = _validate_registry(default_registry_payload())
    generation_root = tmp_path / "corpus_generations" / "g-test"
    chunks_root = generation_root / "corpora"
    generation_root.mkdir(parents=True)
    payload, counts = corpus_admin._canonicalize_existing(generation_root, chunks_root, registry)

    assert counts == {f"corpus_{deterministic_id(family, batch, length=24)}": 1
                      for batch, (category, source_type) in batches.items()
                      for family in [
                          "literature" if category == "english_literature" else
                          "ucr_varieties" if category == "ucr_varieties" else
                          "experiment_methods" if category == "cgi_methods" else
                          "deng_variety_book"]}
    assert len(payload["corpora"]) == 4
    for corpus_id, spec in payload["corpora"].items():
        chunk_path = tmp_path / spec["chunks_path"]
        row = json.loads(chunk_path.read_text(encoding="utf-8").splitlines()[0])
        assert row["schema_version"] == 2
        assert row["corpus_id"] == corpus_id
        assert row["corpus_family_id"] == spec["family_id"]
        assert row["source"] in {"literature", "ucr", "methods", "deng"}


def test_dense_search_passes_family_and_topic_filters_to_vector_backend():
    rows = [
        {"_distance": 0.0, "chunk_id": "a", "corpus_id": "papers_a",
         "corpus_family_id": "literature", "l1_category": "病理与抗性"},
        {"_distance": 0.01, "chunk_id": "b", "corpus_id": "papers_a",
         "corpus_family_id": "literature", "l1_category": "育种与生物技术"},
        {"_distance": 0.02, "chunk_id": "c", "corpus_id": "ucr_records",
         "corpus_family_id": "ucr_varieties", "l1_category": "病理与抗性"},
    ]

    class Query:
        def __init__(self):
            self.filter_sql = ""
            self.max_rows = 0

        def where(self, value):
            self.filter_sql = value
            return self

        def limit(self, value):
            self.max_rows = value
            return self

        def to_list(self):
            assert "corpus_family_id IN ('literature')" in self.filter_sql
            assert "l1_category IN ('病理与抗性')" in self.filter_sql
            return [row for row in rows
                    if row["corpus_family_id"] == "literature"
                    and row["l1_category"] == "病理与抗性"][:self.max_rows]

    class Table:
        def search(self, _vector):
            return Query()

    table = Table()
    retriever = object.__new__(retriever_module.MultiBatchRetriever)
    retriever.lance_tables = {"papers_a": table}
    retriever._idx_map = {"a": 0, "b": 1, "c": 2}
    retriever.global_chunks = [{"_global_idx": i} for i in range(3)]

    hits = retriever._search_lance(
        "papers_a", table, [1.0, 0.0, 0.0], 10,
        {"literature"}, {"病理与抗性"})

    assert [index for index, _score in hits] == [0]


def test_canonical_family_hot_path_does_not_reload_registry(monkeypatch):
    def unexpected_registry_load():
        raise AssertionError("canonical chunks must not reload the registry per row")

    monkeypatch.setattr(retriever_module, "load_corpus_registry", unexpected_registry_load)
    assert retriever_module.local_corpus_family({
        "corpus_family_id": "literature",
        "_batch": "corpus_123",
    }) == "literature"


def test_bm25_and_dense_share_none_and_empty_family_filter_semantics():
    resolve = retriever_module._resolve_corpus_family_filter
    known = {"literature", "ucr_varieties", "experiment_methods"}
    available = {"literature", "ucr_varieties"}

    # Omitted filter means all loaded/available families for both methods.
    assert resolve(None, known_families=known, available_families=available) == available
    # Explicitly empty filter is distinct from omitted and cannot broaden scope.
    assert resolve([], known_families=known, available_families=available) == set()
    assert resolve([" UCR_VARIETIES "], known_families=known,
                   available_families=available) == {"ucr_varieties"}
    with pytest.raises(CorpusMetadataError, match="unknown corpus family"):
        resolve(["not_registered"], known_families=known,
                available_families=available)


def test_lance_metadata_verification_uses_projected_batches():
    projected = []

    class Batch:
        def to_pylist(self):
            return [{"chunk_id": "c1", "corpus_id": "p1",
                     "corpus_family_id": "literature"}]

    class Scanner:
        def to_batches(self):
            return [Batch()]

    class Dataset:
        def scanner(self, *, columns, batch_size):
            projected.append((columns, batch_size))
            return Scanner()

    class Table:
        def to_lance(self):
            return Dataset()

    assert list(iter_lance_rows(Table(), ["chunk_id", "corpus_id", "corpus_family_id"])) == [
        {"chunk_id": "c1", "corpus_id": "p1", "corpus_family_id": "literature"}
    ]
    assert projected == [(["chunk_id", "corpus_id", "corpus_family_id"], 8192)]


def test_retriever_initializes_embedder_before_validating_active_dimension(tmp_path, monkeypatch):
    lance_path = tmp_path / "lancedb_generations" / "test-generation"
    lance_path.mkdir(parents=True)
    pointer = {
        "generation": "test-generation",
        "lancedb_path": "lancedb_generations/test-generation",
        "index_schema_version": 2,
        "embedding_model": settings.EMBEDDING_MODEL,
        "dimension": 3,
        "document_prefix": "passage:",
        "query_prefix": "query:",
    }
    (tmp_path / "active_index.json").write_text(json.dumps(pointer), encoding="utf-8")

    class FakeEmbedder:
        model_name = settings.EMBEDDING_MODEL
        dim = 3

    class FakeReranker:
        pass

    cls = retriever_module.MultiBatchRetriever
    cls._instance = None
    cls._initialized = False
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "RETRIEVAL_BACKEND", "lancedb")
    monkeypatch.setattr(retriever_module, "Embedder", FakeEmbedder)
    monkeypatch.setattr(retriever_module, "Reranker", FakeReranker)
    monkeypatch.setattr(cls, "_load_data", lambda self: None)
    try:
        retriever = cls()
        assert retriever.index_generation == "test-generation"
        assert retriever.embedder.dim == 3
    finally:
        cls._instance = None
        cls._initialized = False


def test_registry_rejects_duplicate_ids_across_registered_corpora():
    registry_payload = default_registry_payload()
    registry_payload["corpora"] = {
        "papers_a": {"family_id": "literature", "batch": "batch_a"},
        "papers_b": {"family_id": "literature", "batch": "batch_b"},
    }
    registry = _validate_registry(registry_payload)
    row = {
        "schema_version": 2,
        "corpus_id": "papers_a",
        "corpus_family_id": "literature",
        "document_id": "same-doc",
        "paper_id": "same-doc",
        "chunk_index": 0,
        "chunk_id": deterministic_id("papers_a", "same-doc", 0),
        "text": "duplicate chunk identity test",
        "source": "literature",
    }
    canonical = retriever_module.normalize_chunk_metadata(
        row, batch="batch_a", registry=registry, ordinal=0)
    conflicting = dict(row, corpus_id="papers_b", chunk_id=deterministic_id("papers_b", "same-doc", 0))
    other = retriever_module.normalize_chunk_metadata(
        conflicting, batch="batch_b", registry=registry, ordinal=0)
    assert canonical["chunk_id"] != other["chunk_id"]


def test_family_registration_changes_manifest_without_touching_vector_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(corpus_admin, "DATA_DIR", tmp_path)
    lance_path = tmp_path / "lancedb_generations" / "current"
    lance_path.mkdir(parents=True)
    registry_path = tmp_path / "corpus_registry.json"
    registry_path.write_text(json.dumps(default_registry_payload()), encoding="utf-8")
    pointer = {
        "generation": "current",
        "registry_version": "old",
        "registry_path": "corpus_registry.json",
        "generation_root": "corpus_generations/current",
        "lancedb_path": "lancedb_generations/current",
        "index_schema_version": 2,
        "embedding_model": settings.EMBEDDING_MODEL,
        "dimension": 1024,
        "document_prefix": "passage:",
        "query_prefix": "query:",
    }
    active_path = tmp_path / "active_index.json"
    active_path.write_text(json.dumps(pointer), encoding="utf-8")

    assert corpus_admin.registry_main([
        "register-family", "--family-id", "pest_disease",
        "--display-name", "Pest and disease documents",
        "--description", "Disease reports and management references",
        "--routing-term", "citrus disease", "--topic", "病理与抗性",
    ]) == 0

    active = json.loads(active_path.read_text(encoding="utf-8"))
    updated_registry = json.loads((tmp_path / active["registry_path"]).read_text(encoding="utf-8"))
    assert "pest_disease" in updated_registry["families"]
    assert active["lancedb_path"] == pointer["lancedb_path"]
    assert (tmp_path / "active_index.previous.json").is_file()

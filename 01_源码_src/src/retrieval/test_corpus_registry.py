import pytest

from src.retrieval.corpus_registry import (
    CorpusMetadataError,
    _validate_registry,
    deterministic_id,
    normalize_chunk_metadata,
    validate_ingest_chunk,
)


def _registry():
    return _validate_registry({
        "schema_version": 1,
        "registry_version": "test-1",
        "families": {
            "citrus_literature": {
                "display_name": "Citrus literature",
                "routing_terms": ["citrus", "柑橘"],
                "legacy_categories": ["literature"],
            },
            "new_corpus_family": {"display_name": "New corpus family"},
        },
        "topic_aliases": {"plant pathology": "病理与抗性"},
        "corpora": {
            "papers_a": {"family_id": "citrus_literature", "batch": "papers-a"},
        },
    })


def test_registry_routes_custom_family_and_topic_alias():
    registry = _registry()
    assert registry.matching_families("citrus disease") == {"citrus_literature"}
    assert registry.normalize_topic("Plant Pathology") == "病理与抗性"
    assert "new_corpus_family" in registry.family_ids


def test_unknown_family_is_not_inferred_from_batch_name():
    registry = _registry()
    with pytest.raises(CorpusMetadataError, match="unregistered corpus_id"):
        normalize_chunk_metadata(
            {"paper_id": "p1", "chunk_index": 0, "text": "example"},
            batch="mystery-literature",
            registry=registry,
            ordinal=0,
        )


def test_registered_import_has_stable_ids_and_rejects_conflicting_chunk_id():
    registry = _registry()
    source = {"paper_id": "p1", "chunk_index": 0, "text": "sample text",
              "source_type": "literature"}
    first = validate_ingest_chunk(
        source, corpus_id="papers_a", family_id="citrus_literature", registry=registry)
    second = validate_ingest_chunk(
        source, corpus_id="papers_a", family_id="citrus_literature", registry=registry)
    assert first["chunk_id"] == second["chunk_id"]
    assert first["chunk_id"] == deterministic_id("papers_a", "p1", 0)
    with pytest.raises(CorpusMetadataError, match="conflicts"):
        validate_ingest_chunk(
            {**source, "chunk_id": "not-a-valid-id"}, corpus_id="papers_a",
            family_id="citrus_literature", registry=registry)


def test_registry_rejects_unsafe_generation_paths():
    payload = {
        "schema_version": 1,
        "families": {"valid_family": {"display_name": "Valid"}},
        "corpora": {"valid_corpus": {
            "family_id": "valid_family", "batch": "batch",
            "chunks_path": "../../outside.jsonl",
        }},
    }
    with pytest.raises(CorpusMetadataError, match="unsafe chunks_path"):
        _validate_registry(payload)


def test_unknown_topic_is_rejected_for_registered_import():
    registry = _registry()
    source = {"paper_id": "p1", "chunk_index": 0, "text": "sample text",
              "source_type": "literature", "l1_category": "made_up_topic"}
    with pytest.raises(CorpusMetadataError, match="unknown topic/category"):
        validate_ingest_chunk(
            source, corpus_id="papers_a", family_id="citrus_literature", registry=registry)


def test_canonical_chunk_requires_contract_fields_and_rejects_future_schema():
    registry = _registry()
    with pytest.raises(CorpusMetadataError, match="missing required field"):
        normalize_chunk_metadata(
            {"schema_version": 2, "paper_id": "p1", "chunk_index": 0,
             "text": "sample text", "source_type": "literature"},
            batch="papers-a", registry=registry, ordinal=0)
    with pytest.raises(CorpusMetadataError, match="unsupported chunk schema"):
        normalize_chunk_metadata(
            {"schema_version": 99, "text": "sample text"},
            batch="papers-a", registry=registry, ordinal=0)

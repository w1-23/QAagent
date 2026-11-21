"""Strict loader and manifest helpers for immutable shared-corpus generations.

A generation is a private directory below ``root/generations`` containing:

* ``manifest.json`` - schema v2 identity, model/dimension contract and hashes
* ``chunks.jsonl`` - canonical schema-v2 chunk dictionaries
* ``lancedb/`` - one LanceDB table with cosine vector search

``load_generation`` verifies every layer before returning an independent
``GenerationHandle``.  It never reads or writes the parent's active/previous
pointer files.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

from .search import (
    BUILDER_VERSION,
    CHUNKS_FILENAME,
    CHUNK_SCHEMA_VERSION,
    CORPUS_ID,
    DOCUMENT_PREFIX,
    EMBEDDING_DIM,
    FAMILY_ID,
    GENERATIONS_DIRNAME,
    GenerationContractError,
    GenerationError,
    GenerationHandle,
    GenerationIntegrityError,
    GenerationNotFound,
    HEALTH_QUERY,
    INDEX_SCHEMA_VERSION,
    LANCEDB_SUBDIR,
    MANIFEST_FILENAME,
    QUERY_PREFIX,
    TABLE_NAME,
    chunk_identifier,
)

_GENERATION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}")
_DOCUMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_HEX32_RE = re.compile(r"[0-9a-f]{32}")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")

_REQUIRED_ROW_FIELDS = frozenset({
    "schema_version",
    "corpus_id",
    "corpus_family_id",
    "source",
    "source_kind",
    "document_id",
    "version_id",
    "paper_id",
    "chunk_id",
    "chunk_index",
    "title",
    "doi",
    "year",
    "section_name",
    "section_index",
    "section_level",
    "section_path",
    "text",
    "md_start",
    "md_end",
    "char_count",
    "text_sha256",
})

_REQUIRED_LANCE_FIELDS = frozenset({
    "vector",
    "chunk_id",
    "document_id",
    "version_id",
    "md_start",
    "md_end",
})


def valid_generation(value: Any) -> bool:
    """Return whether ``value`` is a safe generation directory name."""
    return isinstance(value, str) and bool(_GENERATION_RE.fullmatch(value))


def canonical_json(value: Any) -> bytes:
    """Deterministic UTF-8 JSON used for every durable hash."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def manifest_core(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return the manifest body that the SHA covers."""
    return {key: value for key, value in manifest.items()
            if key != "manifest_sha256"}


def manifest_digest(core: dict[str, Any]) -> str:
    """Return the SHA-256 of a canonical manifest body."""
    return hashlib.sha256(canonical_json(core)).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non_finite_json_constant:{value}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise GenerationContractError(f"manifest_{name}_invalid")
    return value


def _require_int(value: Any, name: str, minimum: int = 0) -> int:
    if not _is_int(value) or value < minimum:
        raise GenerationContractError(f"manifest_{name}_invalid")
    return int(value)


def _resolve_root(root: Path) -> Path:
    if root is None:
        raise GenerationContractError("generation_root_required")
    try:
        path = Path(root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GenerationNotFound("generation_root_missing") from exc
    if not path.is_dir():
        raise GenerationNotFound("generation_root_missing")
    return path


def generation_directory(root: Path, generation: str, *,
                         must_exist: bool = True) -> Path:
    """Resolve one confined generation directory below ``root``.

    Symlinked generation paths are rejected outright so a manifest can never
    point outside the private corpus root.
    """
    if not valid_generation(generation):
        raise GenerationContractError("generation_id_invalid")
    root_path = _resolve_root(root)
    base = root_path / GENERATIONS_DIRNAME
    if base.is_symlink():
        raise GenerationContractError("generations_root_link_denied")
    if base.exists() and not base.is_dir():
        raise GenerationContractError("generations_root_not_directory")
    target = base / generation
    if target.is_symlink():
        raise GenerationContractError("generation_path_link_denied")
    try:
        resolved = target.resolve(strict=False)
        base_resolved = base.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise GenerationContractError("generation_path_invalid") from exc
    if not resolved.is_relative_to(base_resolved):
        raise GenerationContractError("generation_path_outside_root")
    if must_exist:
        if not resolved.is_dir():
            raise GenerationNotFound("generation_missing")
    elif resolved.exists() and not resolved.is_dir():
        raise GenerationContractError("generation_not_directory")
    return resolved


def _read_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise GenerationNotFound("generation_manifest_missing")
    try:
        text = path.read_text(encoding="utf-8")
        manifest = json.loads(text, parse_constant=_reject_json_constant)
    except (OSError, UnicodeError, ValueError) as exc:
        raise GenerationIntegrityError("generation_manifest_unreadable") from exc
    if not isinstance(manifest, dict):
        raise GenerationIntegrityError("generation_manifest_not_object")
    digest = manifest.get("manifest_sha256")
    if not isinstance(digest, str) or not _HEX64_RE.fullmatch(digest):
        raise GenerationIntegrityError("generation_manifest_digest_missing")
    expected = manifest_digest(manifest_core(manifest))
    if not hmac.compare_digest(digest, expected):
        raise GenerationIntegrityError("generation_manifest_digest_mismatch")
    return manifest


def read_generation_manifest(root: Path, generation: str) -> dict[str, Any]:
    """Read and SHA-verify one generation manifest without loading indexes."""
    directory = generation_directory(root, generation, must_exist=True)
    return _read_manifest(directory / MANIFEST_FILENAME)


def _require_manifest(manifest: dict[str, Any],
                      generation: str) -> dict[tuple[str, str], dict[str, Any]]:
    if _require_int(manifest.get("schema_version"), "schema_version", 2) != CHUNK_SCHEMA_VERSION:
        raise GenerationContractError("manifest_schema_version_mismatch")
    if (_require_int(manifest.get("chunk_schema_version"), "chunk_schema_version", 2)
            != CHUNK_SCHEMA_VERSION):
        raise GenerationContractError("manifest_chunk_schema_version_mismatch")
    if (_require_int(manifest.get("index_schema_version"), "index_schema_version", 2)
            != INDEX_SCHEMA_VERSION):
        raise GenerationContractError("manifest_index_schema_version_mismatch")
    if (_require_int(manifest.get("builder_version"), "builder_version", 1)
            != BUILDER_VERSION):
        raise GenerationContractError("manifest_builder_version_mismatch")
    if manifest.get("generation") != generation:
        raise GenerationContractError("manifest_generation_mismatch")
    if manifest.get("corpus_id") != CORPUS_ID:
        raise GenerationContractError("manifest_corpus_id_mismatch")
    if manifest.get("corpus_family_id") != FAMILY_ID:
        raise GenerationContractError("manifest_corpus_family_mismatch")
    if manifest.get("chunks_file") != CHUNKS_FILENAME:
        raise GenerationContractError("manifest_chunks_file_mismatch")
    if manifest.get("lancedb_subdir") != LANCEDB_SUBDIR:
        raise GenerationContractError("manifest_lancedb_subdir_mismatch")
    if manifest.get("table_name") != TABLE_NAME:
        raise GenerationContractError("manifest_table_name_mismatch")
    if manifest.get("document_prefix") != DOCUMENT_PREFIX:
        raise GenerationContractError("manifest_document_prefix_mismatch")
    if manifest.get("query_prefix") != QUERY_PREFIX:
        raise GenerationContractError("manifest_query_prefix_mismatch")
    _require_string(manifest.get("embedding_model"), "embedding_model")
    dimension = _require_int(manifest.get("dimension"), "dimension", 1)
    if dimension != EMBEDDING_DIM:
        raise GenerationContractError("manifest_dimension_mismatch")
    chunk_count = _require_int(manifest.get("chunk_count"), "chunk_count", 1)
    document_count = _require_int(manifest.get("document_count"), "document_count", 1)
    chunks_sha = manifest.get("chunks_sha256")
    if not isinstance(chunks_sha, str) or not _HEX64_RE.fullmatch(chunks_sha):
        raise GenerationContractError("manifest_chunks_sha256_invalid")

    documents = manifest.get("documents")
    if not isinstance(documents, list) or len(documents) != document_count:
        raise GenerationContractError("manifest_documents_invalid")
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    total = 0
    for item in documents:
        if not isinstance(item, dict):
            raise GenerationContractError("manifest_document_entry_invalid")
        document_id = item.get("document_id")
        version_id = item.get("version_id")
        if (not isinstance(document_id, str) or not _DOCUMENT_RE.fullmatch(document_id)
                or not isinstance(version_id, str)
                or not _DOCUMENT_RE.fullmatch(version_id)):
            raise GenerationContractError("manifest_document_identity_invalid")
        key = (document_id, version_id)
        if key in indexed:
            raise GenerationContractError("manifest_document_duplicate")
        count = _require_int(item.get("chunk_count"), "document_chunk_count", 1)
        digest = item.get("chunks_sha256")
        if not isinstance(digest, str) or not _HEX64_RE.fullmatch(digest):
            raise GenerationContractError("manifest_document_sha256_invalid")
        indexed[key] = {"chunk_count": count, "chunks_sha256": digest}
        total += count
    if total != chunk_count:
        raise GenerationContractError("manifest_chunk_count_mismatch")
    return indexed


def _validate_row(row: Any, documents: dict[tuple[str, str], dict[str, Any]],
                  line_number: int) -> None:
    if not isinstance(row, dict):
        raise GenerationIntegrityError(f"chunk_row_not_object:{line_number}")
    missing = _REQUIRED_ROW_FIELDS - set(row)
    if missing:
        raise GenerationIntegrityError(
            f"chunk_row_missing_fields:{line_number}:{','.join(sorted(missing))}")
    if row.get("schema_version") != CHUNK_SCHEMA_VERSION:
        raise GenerationContractError(f"chunk_schema_version_mismatch:{line_number}")
    if row.get("corpus_id") != CORPUS_ID:
        raise GenerationContractError(f"chunk_corpus_id_mismatch:{line_number}")
    if row.get("corpus_family_id") != FAMILY_ID:
        raise GenerationContractError(f"chunk_corpus_family_mismatch:{line_number}")

    document_id = row.get("document_id")
    version_id = row.get("version_id")
    if (not isinstance(document_id, str) or not _DOCUMENT_RE.fullmatch(document_id)
            or not isinstance(version_id, str)
            or not _DOCUMENT_RE.fullmatch(version_id)):
        raise GenerationContractError(f"chunk_document_identity_invalid:{line_number}")
    key = (document_id, version_id)
    if key not in documents:
        raise GenerationIntegrityError(f"chunk_document_not_in_manifest:{line_number}")

    chunk_index = row.get("chunk_index")
    if not _is_int(chunk_index) or chunk_index < 0:
        raise GenerationContractError(f"chunk_index_invalid:{line_number}")
    chunk_id = row.get("chunk_id")
    expected_id = chunk_identifier(document_id, int(chunk_index))
    if not isinstance(chunk_id, str) or not _HEX32_RE.fullmatch(chunk_id):
        raise GenerationContractError(f"chunk_id_invalid:{line_number}")
    if not hmac.compare_digest(chunk_id, expected_id):
        raise GenerationIntegrityError(f"chunk_id_mismatch:{line_number}")

    md_start = row.get("md_start")
    md_end = row.get("md_end")
    if not _is_int(md_start) or not _is_int(md_end):
        raise GenerationContractError(f"chunk_offsets_invalid:{line_number}")
    if md_start < 0 or md_end <= md_start:
        raise GenerationContractError(f"chunk_offsets_invalid:{line_number}")
    text = row.get("text")
    if not isinstance(text, str) or not text.strip():
        raise GenerationContractError(f"chunk_text_invalid:{line_number}")
    if len(text) != int(md_end) - int(md_start):
        raise GenerationIntegrityError(f"chunk_text_span_mismatch:{line_number}")
    if row.get("char_count") != len(text):
        raise GenerationIntegrityError(f"chunk_char_count_mismatch:{line_number}")
    digest = row.get("text_sha256")
    if not isinstance(digest, str) or not hmac.compare_digest(digest, _sha256_text(text)):
        raise GenerationIntegrityError(f"chunk_text_sha256_mismatch:{line_number}")

    for name in ("source", "source_kind", "paper_id"):
        if not isinstance(row.get(name), str) or not row.get(name):
            raise GenerationContractError(f"chunk_{name}_invalid:{line_number}")
    for name in ("title", "doi", "year", "section_name"):
        if not isinstance(row.get(name), str):
            raise GenerationContractError(f"chunk_{name}_invalid:{line_number}")
    for name in ("section_index", "section_level"):
        if not _is_int(row.get(name)) or row.get(name) < 0:
            raise GenerationContractError(f"chunk_{name}_invalid:{line_number}")
    path = row.get("section_path")
    if not isinstance(path, list) or any(not isinstance(item, str) for item in path):
        raise GenerationContractError(f"chunk_section_path_invalid:{line_number}")
    if documents[key]["chunk_count"] < 1:
        raise GenerationContractError(f"chunk_document_count_invalid:{line_number}")


def _read_chunks(
    path: Path,
    manifest: dict[str, Any],
    documents: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if path.is_symlink() or not path.is_file():
        raise GenerationNotFound("generation_chunks_missing")
    expected_sha = manifest["chunks_sha256"]
    expected_count = int(manifest["chunk_count"])
    digest = hashlib.sha256()
    rows: list[dict[str, Any]] = []
    index: dict[str, int] = {}
    counts: dict[tuple[str, str], int] = defaultdict(int)
    try:
        with path.open("rb") as stream:
            for line_number, raw in enumerate(stream, 1):
                digest.update(raw)
                if not raw.endswith(b"\n"):
                    raise GenerationIntegrityError(
                        f"chunk_line_without_newline:{line_number}")
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise GenerationIntegrityError(
                        f"chunk_line_not_utf8:{line_number}") from exc
                if not line.strip():
                    raise GenerationIntegrityError(f"chunk_blank_line:{line_number}")
                try:
                    row = json.loads(line, parse_constant=_reject_json_constant)
                except ValueError as exc:
                    raise GenerationIntegrityError(
                        f"chunk_json_invalid:{line_number}") from exc
                _validate_row(row, documents, line_number)
                chunk_id = str(row["chunk_id"])
                if chunk_id in index:
                    raise GenerationIntegrityError(
                        f"chunk_id_duplicate:{line_number}")
                index[chunk_id] = len(rows)
                rows.append(row)
                counts[(str(row["document_id"]), str(row["version_id"]))] += 1
    except OSError as exc:
        raise GenerationIntegrityError("generation_chunks_unreadable") from exc
    if not hmac.compare_digest(digest.hexdigest(), expected_sha):
        raise GenerationIntegrityError("generation_chunks_sha256_mismatch")
    if len(rows) != expected_count:
        raise GenerationIntegrityError("generation_chunk_count_mismatch")
    if set(counts) != set(documents):
        raise GenerationIntegrityError("generation_document_set_mismatch")

    by_document: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_document[(str(row["document_id"]), str(row["version_id"]))].append(row)
    for key, entry in documents.items():
        group = by_document.get(key, [])
        if len(group) != entry["chunk_count"]:
            raise GenerationIntegrityError("generation_document_count_mismatch")
        ordered = sorted(group, key=lambda item: str(item["chunk_id"]))
        actual = hashlib.sha256(canonical_json(ordered)).hexdigest()
        if not hmac.compare_digest(actual, entry["chunks_sha256"]):
            raise GenerationIntegrityError("generation_document_sha256_mismatch")
    return rows, index


def _lancedb_directory(generation_dir: Path) -> Path:
    path = generation_dir / LANCEDB_SUBDIR
    if path.is_symlink() or not path.is_dir():
        raise GenerationNotFound("generation_lancedb_missing")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GenerationIntegrityError("generation_lancedb_invalid") from exc
    if not resolved.is_relative_to(generation_dir):
        raise GenerationContractError("generation_lancedb_outside_root")
    return resolved


def _scan_lance_table(table: Any, columns: list[str]):
    dataset = table.to_lance()
    scanner = dataset.scanner(columns=columns, batch_size=8192)
    for batch in scanner.to_batches():
        yield from batch.to_pylist()


def _verify_lance(
    generation_dir: Path,
    manifest: dict[str, Any],
    rows_by_id: dict[str, dict[str, Any]],
) -> tuple[Any, Any]:
    lancedb_dir = _lancedb_directory(generation_dir)
    try:
        import lancedb
        db = lancedb.connect(str(lancedb_dir))
        names = set(db.table_names())
        if names != {TABLE_NAME}:
            raise GenerationContractError("generation_lancedb_table_set_mismatch")
        table = db.open_table(TABLE_NAME)
    except GenerationError:
        raise
    except Exception as exc:
        raise GenerationIntegrityError("generation_lancedb_open_failed") from exc

    try:
        count = int(table.count_rows())
    except Exception as exc:
        raise GenerationIntegrityError("generation_lancedb_count_failed") from exc
    if count != int(manifest["chunk_count"]):
        raise GenerationIntegrityError("generation_lancedb_count_mismatch")

    schema = table.schema
    missing = _REQUIRED_LANCE_FIELDS - set(schema.names)
    if missing:
        raise GenerationContractError(
            f"generation_lancedb_missing_fields:{','.join(sorted(missing))}")
    vector_type = schema.field("vector").type
    dimension = getattr(vector_type, "list_size", None)
    if not _is_int(dimension) or dimension != int(manifest["dimension"]):
        raise GenerationContractError("generation_lancedb_dimension_mismatch")
    value_type = getattr(vector_type, "value_type", None)
    if value_type is not None:
        try:
            import pyarrow as pa
            if not pa.types.is_float32(value_type):
                raise GenerationContractError(
                    "generation_lancedb_vector_type_mismatch")
        except ImportError:
            pass

    seen: set[str] = set()
    columns = ["chunk_id", "document_id", "version_id", "md_start", "md_end"]
    try:
        for row in _scan_lance_table(table, columns):
            chunk_id = row.get("chunk_id")
            if not isinstance(chunk_id, str) or chunk_id in seen:
                raise GenerationIntegrityError("generation_lancedb_id_invalid")
            source = rows_by_id.get(chunk_id)
            if source is None:
                raise GenerationIntegrityError("generation_lancedb_id_unknown")
            for name in ("document_id", "version_id", "md_start", "md_end"):
                if row.get(name) != source.get(name):
                    raise GenerationIntegrityError(
                        f"generation_lancedb_metadata_mismatch:{name}")
            seen.add(chunk_id)
    except GenerationError:
        raise
    except Exception as exc:
        raise GenerationIntegrityError("generation_lancedb_scan_failed") from exc
    if len(seen) != int(manifest["chunk_count"]):
        raise GenerationIntegrityError("generation_lancedb_id_count_mismatch")
    return db, table


def _verify_models(embedder: Any, reranker: Any, manifest: dict[str, Any]) -> None:
    if embedder is None:
        raise GenerationContractError("embedder_required")
    model_name = getattr(embedder, "model_name", None)
    if not isinstance(model_name, str) or not model_name:
        raise GenerationContractError("embedder_model_name_missing")
    if model_name != manifest["embedding_model"]:
        raise GenerationContractError("embedding_model_mismatch")
    dimension = getattr(embedder, "dim", None)
    if not _is_int(dimension) or dimension != int(manifest["dimension"]):
        raise GenerationContractError("embedding_dimension_mismatch")
    for name in ("embed_passages", "embed_query"):
        if not callable(getattr(embedder, name, None)):
            raise GenerationContractError(f"embedder_{name}_missing")
    if reranker is None or not callable(getattr(reranker, "rerank", None)):
        raise GenerationContractError("reranker_required")


def load_generation(root: Path, generation: str, *, embedder: Any,
                    reranker: Any) -> GenerationHandle:
    """Open one immutable generation after full strict verification.

    Returns an independent handle with ``search(query, limit=...)``,
    ``manifest`` and ``lookup(chunk_id)``.  The answer is deterministic for a
    given generation and query; no active-pointer state is consulted.
    """
    generation_dir = generation_directory(root, generation, must_exist=True)
    manifest = _read_manifest(generation_dir / MANIFEST_FILENAME)
    documents = _require_manifest(manifest, generation)

    chunks_path = generation_dir / CHUNKS_FILENAME
    rows, rows_by_id = _read_chunks(chunks_path, manifest, documents)
    chunks_by_id = {str(row["chunk_id"]): row for row in rows}
    db, table = _verify_lance(generation_dir, manifest, chunks_by_id)
    _verify_models(embedder, reranker, manifest)

    root_path = _resolve_root(root)
    try:
        return GenerationHandle(
            root=root_path,
            generation=generation,
            manifest=manifest,
            rows=rows,
            table=table,
            embedder=embedder,
            reranker=reranker,
        )
    except GenerationError:
        raise
    except Exception as exc:
        raise GenerationContractError("generation_handle_initialization_failed") from exc


__all__ = [
    "CHUNK_SCHEMA_VERSION",
    "CORPUS_ID",
    "EMBEDDING_DIM",
    "FAMILY_ID",
    "GenerationContractError",
    "GenerationError",
    "GenerationHandle",
    "GenerationIntegrityError",
    "GenerationNotFound",
    "HEALTH_QUERY",
    "canonical_json",
    "generation_directory",
    "load_generation",
    "manifest_core",
    "manifest_digest",
    "read_generation_manifest",
    "valid_generation",
]

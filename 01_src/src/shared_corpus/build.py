"""Crash-safe builder for one immutable shared-corpus generation.

All output stays below the caller-supplied private root.  A complete generation
is written into ``root/.staging`` first, verified, then published with one
directory rename into ``root/generations``.  The original five corpora and the
parent's active/previous pointers are never touched.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

from .generations import (
    canonical_json,
    generation_directory,
    manifest_digest,
    read_generation_manifest,
)
from .search import (
    BUILDER_VERSION,
    CHUNKS_FILENAME,
    CHUNK_SCHEMA_VERSION,
    CORPUS_ID,
    DOCUMENT_PREFIX,
    EMBEDDING_DIM,
    FAMILY_ID,
    GENERATIONS_DIRNAME,
    GenerationError,
    GenerationIntegrityError,
    INDEX_SCHEMA_VERSION,
    LANCEDB_SUBDIR,
    MANIFEST_FILENAME,
    QUERY_PREFIX,
    STAGING_DIRNAME,
    TABLE_NAME,
    chunk_identifier,
)

EMBED_BATCH = 64
COSINE_INDEX_MIN = 4096
STAGING_MAX_AGE_SEC = 24 * 60 * 60

_DOCUMENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_HEX32_RE = re.compile(r"[0-9a-f]{32}")


class GenerationBuildError(GenerationError):
    """Input chunks or a target generation cannot be built safely."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _identifier(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if (not text or text in {".", ".."} or ".." in text
            or not _DOCUMENT_RE.fullmatch(text)):
        raise GenerationBuildError(f"{name}_invalid")
    return text


def _optional_string(value: Any, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GenerationBuildError(f"{name}_invalid")
    return value


def _optional_int(value: Any, name: str, default: int = 0) -> int:
    if value is None:
        return default
    if not _is_int(value) or value < 0:
        raise GenerationBuildError(f"{name}_invalid")
    return int(value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fsync_dir(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # Some overlay filesystems reject directory fsync; file data is already
        # flushed and the rename remains atomic.
        pass


def _write_bytes(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _prepare_root(root: Path) -> Path:
    if root is None:
        raise GenerationBuildError("root_required")
    raw = Path(root).expanduser()
    if raw.is_symlink():
        raise GenerationBuildError("generation_root_link_denied")
    try:
        raw.mkdir(mode=0o700, parents=True, exist_ok=True)
        resolved = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GenerationBuildError("generation_root_unavailable") from exc
    if not resolved.is_dir():
        raise GenerationBuildError("generation_root_not_directory")
    return resolved


def _prepare_child_dir(base: Path, name: str) -> Path:
    path = base / name
    if path.is_symlink():
        raise GenerationBuildError(f"{name}_link_denied")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GenerationBuildError(f"{name}_unavailable") from exc
    if not resolved.is_relative_to(base.resolve()) or not resolved.is_dir():
        raise GenerationBuildError(f"{name}_outside_root")
    return resolved


def _cleanup_staging(staging_base: Path) -> None:
    now = time.time()
    try:
        entries = list(staging_base.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir():
                continue
            if now - entry.stat().st_mtime > STAGING_MAX_AGE_SEC:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _normalize_chunk(chunk: Any, position: int) -> tuple[dict[str, Any], str | None]:
    if not isinstance(chunk, dict):
        raise GenerationBuildError(f"chunk_not_object:{position}")
    document_id = _identifier(chunk.get("document_id") or chunk.get("doc"),
                              "document_id")
    version_id = _identifier(chunk.get("version_id") or chunk.get("version"),
                             "version_id")
    md_start = chunk.get("md_start")
    md_end = chunk.get("md_end")
    if not _is_int(md_start) or not _is_int(md_end):
        raise GenerationBuildError(f"chunk_offsets_invalid:{position}")
    if md_start < 0 or md_end <= md_start:
        raise GenerationBuildError(f"chunk_offsets_invalid:{position}")
    text = chunk.get("text")
    if not isinstance(text, str) or not text.strip():
        raise GenerationBuildError(f"chunk_text_invalid:{position}")
    if len(text) != int(md_end) - int(md_start):
        raise GenerationBuildError(f"chunk_text_span_mismatch:{position}")

    chunk_index = chunk.get("chunk_index")
    if chunk_index is not None:
        if not _is_int(chunk_index) or chunk_index < 0:
            raise GenerationBuildError(f"chunk_index_invalid:{position}")
        chunk_index = int(chunk_index)

    provided_id = chunk.get("chunk_id")
    if provided_id is not None:
        if not isinstance(provided_id, str) or not _HEX32_RE.fullmatch(provided_id):
            raise GenerationBuildError(f"chunk_id_invalid:{position}")

    source_kind = _optional_string(chunk.get("source_kind"), "source_kind")
    source_kind = source_kind or "shared_corpus"
    source = _optional_string(chunk.get("source"), "source")
    source = source or source_kind
    paper_id = _optional_string(chunk.get("paper_id"), "paper_id") or document_id
    section_path = chunk.get("section_path") or []
    if (not isinstance(section_path, list)
            or any(not isinstance(item, str) for item in section_path)):
        raise GenerationBuildError(f"chunk_section_path_invalid:{position}")
    section_name = _optional_string(chunk.get("section_name"), "section_name")
    if not section_name:
        section_name = _optional_string(chunk.get("heading"), "heading")
    char_count = _optional_int(chunk.get("char_count"), "char_count", len(text))
    if char_count != len(text):
        raise GenerationBuildError(f"chunk_char_count_mismatch:{position}")

    record = {
        "schema_version": CHUNK_SCHEMA_VERSION,
        "corpus_id": CORPUS_ID,
        "corpus_family_id": FAMILY_ID,
        "source": source,
        "source_kind": source_kind,
        "document_id": document_id,
        "version_id": version_id,
        "paper_id": paper_id,
        "chunk_id": "",  # assigned after indexes are known
        "chunk_index": chunk_index,
        "title": _optional_string(chunk.get("title"), "title"),
        "doi": _optional_string(chunk.get("doi"), "doi"),
        "year": _optional_string(chunk.get("year"), "year"),
        "section_name": section_name,
        "section_index": _optional_int(chunk.get("section_index"), "section_index"),
        "section_level": _optional_int(chunk.get("section_level"), "section_level"),
        "section_path": [str(item) for item in section_path],
        "text": text,
        "md_start": int(md_start),
        "md_end": int(md_end),
        "char_count": char_count,
        "text_sha256": _sha256_text(text),
    }
    return record, provided_id


def _normalize_chunks(chunks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    if chunks is None:
        raise GenerationBuildError("chunks_required")
    try:
        iterator = iter(chunks)
    except TypeError as exc:
        raise GenerationBuildError("chunks_must_be_iterable") from exc

    groups: dict[tuple[str, str], list[tuple[dict[str, Any], str | None]]] = (
        defaultdict(list))
    total = 0
    for position, chunk in enumerate(iterator):
        record, provided_id = _normalize_chunk(chunk, position)
        groups[(record["document_id"], record["version_id"])].append(
            (record, provided_id))
        total += 1
    if total == 0:
        raise GenerationBuildError("chunks_empty")

    records: list[dict[str, Any]] = []
    for items in groups.values():
        items.sort(key=lambda pair: (pair[0]["md_start"], pair[0]["md_end"]))
        used = {pair[0]["chunk_index"] for pair in items
                if pair[0]["chunk_index"] is not None}
        next_free = 0
        for record, provided_id in items:
            if record["chunk_index"] is None:
                while next_free in used:
                    next_free += 1
                record["chunk_index"] = next_free
                used.add(next_free)
                next_free += 1
        indexes = [int(record["chunk_index"]) for record, _ in items]
        if len(set(indexes)) != len(indexes):
            raise GenerationBuildError("chunk_index_duplicate")
        for record, provided_id in items:
            expected_id = chunk_identifier(str(record["document_id"]),
                                           int(record["chunk_index"]))
            if provided_id is not None and provided_id != expected_id:
                raise GenerationBuildError("chunk_id_mismatch")
            record["chunk_id"] = expected_id
            records.append(record)

    records.sort(key=lambda record: (
        record["document_id"], record["version_id"], record["md_start"],
        record["md_end"], record["chunk_index"], record["chunk_id"]))
    if len({str(record["chunk_id"]) for record in records}) != len(records):
        raise GenerationBuildError("chunk_id_duplicate")
    return records


def _documents(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(str(record["document_id"]), str(record["version_id"]))].append(record)
    documents = []
    for key in sorted(grouped):
        group = sorted(grouped[key], key=lambda item: str(item["chunk_id"]))
        documents.append({
            "document_id": key[0],
            "version_id": key[1],
            "chunk_count": len(group),
            "chunks_sha256": hashlib.sha256(canonical_json(group)).hexdigest(),
        })
    return documents


def _jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        for record in records
    )


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                       indent=2, allow_nan=False) + "\n").encode("utf-8")


def _validate_embedder(embedder: Any) -> tuple[str, int]:
    if embedder is None:
        raise GenerationBuildError("embedder_required")
    model_name = getattr(embedder, "model_name", None)
    if not isinstance(model_name, str) or not model_name:
        raise GenerationBuildError("embedder_model_name_missing")
    dimension = getattr(embedder, "dim", None)
    if not _is_int(dimension) or dimension != EMBEDDING_DIM:
        raise GenerationBuildError("embedding_dimension_mismatch")
    if not callable(getattr(embedder, "embed_passages", None)):
        raise GenerationBuildError("embedder_embed_passages_missing")
    return model_name, int(dimension)


def _batched(values: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _embed(embedder: Any, texts: list[str], dimension: int) -> list[list[float]]:
    try:
        vectors = list(embedder.embed_passages(texts))
    except Exception as exc:
        raise GenerationBuildError("embedding_failed") from exc
    if len(vectors) != len(texts):
        raise GenerationBuildError("embedding_count_mismatch")
    result: list[list[float]] = []
    for vector in vectors:
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise GenerationBuildError("embedding_vector_invalid") from exc
        if len(values) != dimension:
            raise GenerationBuildError("embedding_dimension_mismatch")
        if not all(math.isfinite(value) for value in values):
            raise GenerationBuildError("embedding_not_finite")
        result.append(values)
    return result


def _embedding_text(record: dict[str, Any]) -> str:
    section = str(record.get("section_name") or "")
    return f"{section} {record['text']}".strip()


def _lance_row(record: dict[str, Any], vector: list[float], np: Any) -> dict[str, Any]:
    row = {key: value for key, value in record.items()}
    row["section_path"] = list(record["section_path"])
    row["vector"] = np.asarray(vector, dtype=np.float32)
    return row


def _scan_lance(table: Any, columns: list[str]) -> Iterator[dict[str, Any]]:
    dataset = table.to_lance()
    scanner = dataset.scanner(columns=columns, batch_size=8192)
    for batch in scanner.to_batches():
        yield from batch.to_pylist()


def _verify_staged_table(table: Any, records: list[dict[str, Any]],
                         dimension: int) -> None:
    if int(table.count_rows()) != len(records):
        raise GenerationIntegrityError("staged_lancedb_count_mismatch")
    schema = table.schema
    for name in ("vector", "chunk_id", "document_id", "version_id",
                 "md_start", "md_end"):
        if name not in set(schema.names):
            raise GenerationIntegrityError("staged_lancedb_missing_field")
    vector_type = schema.field("vector").type
    if getattr(vector_type, "list_size", None) != dimension:
        raise GenerationIntegrityError("staged_lancedb_dimension_mismatch")

    expected = {str(record["chunk_id"]): record for record in records}
    seen: set[str] = set()
    columns = ["chunk_id", "document_id", "version_id", "md_start", "md_end"]
    for row in _scan_lance(table, columns):
        chunk_id = row.get("chunk_id")
        if not isinstance(chunk_id, str) or chunk_id in seen:
            raise GenerationIntegrityError("staged_lancedb_id_invalid")
        source = expected.get(chunk_id)
        if source is None:
            raise GenerationIntegrityError("staged_lancedb_id_unknown")
        for name in ("document_id", "version_id", "md_start", "md_end"):
            if row.get(name) != source.get(name):
                raise GenerationIntegrityError("staged_lancedb_metadata_mismatch")
        seen.add(chunk_id)
    if len(seen) != len(records):
        raise GenerationIntegrityError("staged_lancedb_id_count_mismatch")


def _existing_manifest(root: Path, generation: str,
                       chunks_sha256: str) -> dict[str, Any]:
    try:
        manifest = read_generation_manifest(root, generation)
        directory = generation_directory(root, generation, must_exist=True)
    except GenerationError as exc:
        raise GenerationBuildError("generation_path_conflict") from exc
    if (manifest.get("generation") != generation
            or manifest.get("chunks_sha256") != chunks_sha256):
        raise GenerationBuildError("generation_path_conflict")
    chunks_path = directory / CHUNKS_FILENAME
    if chunks_path.is_symlink() or not chunks_path.is_file():
        raise GenerationBuildError("generation_path_conflict")
    if _sha256_file(chunks_path) != chunks_sha256:
        raise GenerationBuildError("generation_path_conflict")
    if not (directory / LANCEDB_SUBDIR).is_dir():
        raise GenerationBuildError("generation_path_conflict")
    return manifest


def _publish(staging: Path, target: Path, root: Path, generation: str,
             chunks_sha256: str) -> dict[str, Any] | None:
    if os.path.lexists(target):
        return _existing_manifest(root, generation, chunks_sha256)
    try:
        os.rename(staging, target)
        _fsync_dir(target.parent)
        return None
    except OSError as exc:
        # A concurrent builder may have published the same content first.
        if os.path.isdir(target) and not os.path.islink(target):
            return _existing_manifest(root, generation, chunks_sha256)
        raise GenerationBuildError("generation_publish_failed") from exc


def build_generation(root: Path, chunks: list[dict], *,
                     embedder: Any) -> tuple[str, dict[str, Any]]:
    """Build and atomically publish one immutable generation.

    ``chunks`` are schema-v2 dictionaries from the shared chunker.  The builder
    validates their identity/offset contract, embeds bounded batches with the
    host E5 embedder, writes ``chunks.jsonl`` plus a cosine LanceDB table, and
    returns ``(generation_id, manifest)``.  Rebuilding identical content is
    idempotent and returns the existing generation without rewriting it.
    """
    records = _normalize_chunks(chunks)
    documents = _documents(records)
    model_name, dimension = _validate_embedder(embedder)
    chunks_bytes = _jsonl_bytes(records)
    chunks_sha256 = hashlib.sha256(chunks_bytes).hexdigest()

    identity_core = {
        "schema_version": CHUNK_SCHEMA_VERSION,
        "chunk_schema_version": CHUNK_SCHEMA_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "corpus_id": CORPUS_ID,
        "corpus_family_id": FAMILY_ID,
        "chunks_file": CHUNKS_FILENAME,
        "lancedb_subdir": LANCEDB_SUBDIR,
        "table_name": TABLE_NAME,
        "document_prefix": DOCUMENT_PREFIX,
        "query_prefix": QUERY_PREFIX,
        "embedding_model": model_name,
        "dimension": dimension,
        "chunk_count": len(records),
        "document_count": len(documents),
        "chunks_sha256": chunks_sha256,
        "documents": documents,
    }
    generation = "g" + manifest_digest(identity_core)[:32]
    core = dict(identity_core)
    core["generation"] = generation
    manifest = {**core, "manifest_sha256": manifest_digest(core)}

    root_path = _prepare_root(root)
    target = generation_directory(root_path, generation, must_exist=False)
    if os.path.lexists(target):
        return generation, _existing_manifest(root_path, generation, chunks_sha256)

    generations_dir = _prepare_child_dir(root_path, GENERATIONS_DIRNAME)
    staging_base = _prepare_child_dir(root_path, STAGING_DIRNAME)
    _cleanup_staging(staging_base)
    staging = staging_base / f"{generation}.{uuid.uuid4().hex}"
    try:
        os.mkdir(staging, 0o700)
    except OSError as exc:
        raise GenerationBuildError("staging_unavailable") from exc

    try:
        _write_bytes(staging / CHUNKS_FILENAME, chunks_bytes)

        try:
            import lancedb
            import numpy as np
        except ImportError as exc:
            raise GenerationBuildError("lancedb_backend_unavailable") from exc

        lancedb_dir = staging / LANCEDB_SUBDIR
        os.mkdir(lancedb_dir, 0o700)
        try:
            db = lancedb.connect(str(lancedb_dir))
        except Exception as exc:
            raise GenerationBuildError("lancedb_connect_failed") from exc

        table = None
        try:
            for batch in _batched(records, EMBED_BATCH):
                vectors = _embed(embedder, [_embedding_text(record) for record in batch],
                                 dimension)
                rows = [_lance_row(record, vector, np)
                        for record, vector in zip(batch, vectors)]
                if table is None:
                    table = db.create_table(TABLE_NAME, data=rows)
                else:
                    table.add(rows)
            if table is None or int(table.count_rows()) != len(records):
                raise GenerationIntegrityError("staged_lancedb_count_mismatch")
            if len(records) >= COSINE_INDEX_MIN:
                partitions = min(64, max(2, len(records) // 1024))
                table.create_index(metric="cosine", index_type="IVF_HNSW_FLAT",
                                   num_partitions=partitions, m=16,
                                   ef_construction=200, replace=True)
            _verify_staged_table(table, records, dimension)
        except GenerationError:
            raise
        except Exception as exc:
            raise GenerationBuildError("staged_lancedb_build_failed") from exc

        _write_bytes(staging / MANIFEST_FILENAME, _manifest_bytes(manifest))
        _fsync_dir(lancedb_dir)
        _fsync_dir(staging)

        existing = _publish(staging, target, root_path, generation, chunks_sha256)
        if existing is not None:
            return generation, existing
        return generation, manifest
    finally:
        if os.path.lexists(staging):
            shutil.rmtree(staging, ignore_errors=True)


__all__ = [
    "GenerationBuildError",
    "build_generation",
]

"""Transactional corpus import and full LanceDB generation rebuild helpers.

All generated files stay below DATA_DIR. Indexes are built beside the active
generation and activated by one atomic pointer replacement after validation.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import shutil
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Iterable

from src.config import settings
from src.retrieval.corpus_registry import (
    CHUNK_SCHEMA_VERSION,
    CorpusMetadataError,
    active_lancedb_path,
    deterministic_id,
    load_corpus_registry,
    normalize_chunk_metadata,
)

logger = logging.getLogger("citrusqa.corpus_admin")
DATA_DIR = Path(settings.DATA_DIR).resolve()
INDEX_SCHEMA_VERSION = 2
DOCUMENT_PREFIX = "passage:"
QUERY_PREFIX = "query:"
# Match FastEmbed's vectorized inference path; larger batches avoid excessive
# Lance append overhead during full corpus generation while remaining modest
# for both CPU and GPU builders.
EMBED_BATCH = 256


def _utc_generation() -> str:
    return datetime.now(timezone.utc).strftime("g%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]


def _registry_payload(registry) -> dict[str, Any]:
    return {
        "schema_version": registry.schema_version,
        "registry_version": registry.registry_version,
        "families": copy.deepcopy(registry.families),
        "corpora": copy.deepcopy(registry.corpora),
        "topic_aliases": copy.deepcopy(registry.topic_aliases),
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        if os.name != "nt":
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def _corpus_admin_lock():
    """Serialize registry/index mutations across CLI processes and OS platforms."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = DATA_DIR / ".corpus_admin.lock"
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _serialize_mutations(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _corpus_admin_lock():
            return function(*args, **kwargs)
    return wrapped


def _activate_pointer(pointer: dict[str, Any]) -> None:
    active_path = DATA_DIR / "active_index.json"
    if active_path.is_file():
        try:
            previous = json.loads(active_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CorpusMetadataError(f"refusing to replace unreadable active index pointer: {exc}") from exc
        _write_json_atomic(DATA_DIR / "active_index.previous.json", previous)
    _write_json_atomic(active_path, pointer)


def _new_generation_paths(generation: str) -> tuple[Path, Path, Path]:
    generation_root = DATA_DIR / "corpus_generations" / generation
    lance_root = DATA_DIR / "lancedb_generations" / generation
    chunks_root = generation_root / "corpora"
    generation_root.mkdir(parents=True, exist_ok=False)
    lance_root.parent.mkdir(parents=True, exist_ok=True)
    return generation_root, lance_root, chunks_root


def _copy_active_index(lance_root: Path) -> None:
    old = active_lancedb_path(DATA_DIR)
    if old.is_dir():
        shutil.copytree(old, lance_root)
    else:
        lance_root.mkdir(parents=True, exist_ok=False)


def _serialize_record(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"


def _find_chunks(batch_dir: Path) -> Path | None:
    for relative in (Path("chunks") / "chunks.jsonl", Path("chunks.jsonl")):
        path = batch_dir / relative
        if path.is_file():
            return path
    return None


def _iter_source_batches(registry) -> list[tuple[str, Path, dict[str, Any]]]:
    sources: list[tuple[str, Path, dict[str, Any]]] = []
    if registry.corpora:
        for corpus_id, spec in sorted(registry.corpora.items()):
            path_text = str(spec.get("chunks_path") or "").strip()
            if path_text:
                chunks_path = (DATA_DIR / path_text).resolve()
                if DATA_DIR not in chunks_path.parents or not chunks_path.is_file():
                    raise CorpusMetadataError(f"registered corpus {corpus_id} has missing/unsafe chunks_path")
            else:
                chunks_path = _find_chunks(DATA_DIR / str(spec["batch"]))
                if chunks_path is None:
                    raise CorpusMetadataError(f"registered corpus {corpus_id} has no chunks file")
            sources.append((str(corpus_id), chunks_path, dict(spec)))
    else:
        paths = set(DATA_DIR.glob("*/chunks/chunks.jsonl")) | set(DATA_DIR.glob("*/chunks.jsonl"))
        for chunks_path in sorted(paths):
            batch = chunks_path.parent.parent.name if chunks_path.parent.name == "chunks" else chunks_path.parent.name
            sources.append((batch, chunks_path, {"batch": batch}))
    return sources


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                raise CorpusMetadataError(f"invalid JSONL at {path.name}:{line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise CorpusMetadataError(f"record at {path.name}:{line_number} must be an object")
            yield row


def _family_for_record(row: dict[str, Any], batch: str, spec: dict[str, Any], registry) -> str:
    explicit = str(row.get("corpus_family_id") or row.get("family_id") or "").strip().casefold()
    if explicit and explicit not in registry.family_ids:
        raise CorpusMetadataError(f"record declares unknown corpus family {explicit!r}")
    declared = str(spec.get("family_id") or "").strip().casefold()
    if declared and declared not in registry.family_ids:
        raise CorpusMetadataError(f"registry corpus {batch} references unknown family {declared!r}")
    inferred = registry.family_for_legacy(row, batch)
    family = explicit or inferred or declared
    if not family or family not in registry.family_ids:
        raise CorpusMetadataError(f"cannot assign record in {batch!r} to a registered family")
    if declared and family != declared:
        raise CorpusMetadataError(f"record family {family!r} conflicts with registered family {declared!r}")
    return family


def _canonicalize_existing(generation_root: Path, chunks_root: Path, registry) -> tuple[dict, dict[str, int]]:
    """Normalize all source chunks into the staged generation before embedding."""
    registry_payload = _registry_payload(registry)
    old_corpora = registry_payload.get("corpora") or {}
    grouped: dict[str, dict[str, Any]] = {}
    chunk_ids: set[str] = set()
    writers: dict[str, Any] = {}
    category_sets: dict[str, set[str]] = {}
    total = 0

    for source_key, source_path, spec in _iter_source_batches(registry):
        registered_id = source_key if source_key in old_corpora else ""
        source_batch = str(spec.get("batch") or source_key)
        for ordinal, raw in enumerate(_read_jsonl(source_path)):
            family_id = _family_for_record(raw, source_batch, spec, registry)
            row_corpus_id = str(raw.get("corpus_id") or registered_id).strip().casefold()
            if row_corpus_id and row_corpus_id not in old_corpora:
                raise CorpusMetadataError(
                    f"record references unregistered corpus_id {row_corpus_id!r}; register it before reindexing")
            if row_corpus_id:
                corpus_id = row_corpus_id
            else:
                corpus_id = "corpus_" + deterministic_id(family_id, source_batch, length=24)
            old_spec = old_corpora.get(corpus_id) or {}
            if old_spec and str(old_spec.get("family_id")) != family_id:
                raise CorpusMetadataError(f"corpus {corpus_id!r} family conflicts with its manifest")
            if corpus_id not in grouped:
                grouped[corpus_id] = {
                    "family_id": family_id,
                    "batch": corpus_id,
                    "display_name": str(old_spec.get("display_name") or source_batch),
                    "source": str(old_spec.get("source") or old_spec.get("source_type") or source_batch),
                    "source_type": str(old_spec.get("source_type") or raw.get("source_type") or source_batch),
                    "table_name": str(old_spec.get("table_name") or corpus_id),
                    "source_batches": set(),
                    "topics": set(),
                    "count": 0,
                }
            group = grouped[corpus_id]
            if group["family_id"] != family_id:
                raise CorpusMetadataError(f"corpus {corpus_id!r} mixes multiple family IDs")
            group["source_batches"].add(source_batch)
            row = dict(raw)
            row["corpus_id"] = corpus_id
            row["corpus_family_id"] = family_id
            row["_source_batch"] = source_batch
            # Drop pre-contract IDs once at the migration boundary; canonical
            # IDs are then deterministic and validated on all later generations.
            if int(row.get("schema_version", 0) or 0) != CHUNK_SCHEMA_VERSION:
                row.pop("chunk_id", None)
            normalized = normalize_chunk_metadata(
                row, batch=corpus_id, registry=registry, ordinal=ordinal)
            chunk_id = str(normalized["chunk_id"])
            if chunk_id in chunk_ids:
                raise CorpusMetadataError(f"duplicate chunk_id across corpora: {chunk_id}")
            chunk_ids.add(chunk_id)
            topic = str(normalized.get("l1_category") or "").strip()
            if topic:
                group["topics"].add(topic)
                category_sets.setdefault(family_id, set()).add(topic)
            dest = chunks_root / corpus_id / "chunks.jsonl"
            stream = writers.get(corpus_id)
            if stream is None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                stream = dest.open("w", encoding="utf-8")
                writers[corpus_id] = stream
            stream.write(_serialize_record(normalized))
            group["count"] += 1
            total += 1

    for stream in writers.values():
        stream.close()

    if total == 0:
        raise CorpusMetadataError("no corpus chunks were found")

    corpora = {}
    for corpus_id, group in grouped.items():
        if len(group["source_batches"]) != 1:
            raise CorpusMetadataError(f"corpus {corpus_id!r} spans multiple source batches")
        corpora[corpus_id] = {
            "family_id": group["family_id"],
            "batch": group["batch"],
            "display_name": group["display_name"],
            "source": group["source"],
            "source_type": group["source_type"],
            "source_batch": next(iter(group["source_batches"])),
            "table_name": group["table_name"],
            "chunks_path": (Path("corpus_generations") / generation_root.name
                            / "corpora" / corpus_id / "chunks.jsonl").as_posix(),
            "chunk_count": group["count"],
            "topics": sorted(group["topics"]),
        }
    for family_id, topics in category_sets.items():
        current = set(registry_payload["families"][family_id].get("topics") or [])
        registry_payload["families"][family_id]["topics"] = sorted(current | topics)
    registry_payload["corpora"] = corpora
    registry_payload["registry_version"] = _utc_generation()
    return registry_payload, {key: value["count"] for key, value in grouped.items()}


def _validate_topic(topic: str, family_spec: dict[str, Any], registry) -> str:
    value = registry.normalize_topic(topic)
    if not value:
        return ""
    known = {str(item).strip().casefold() for item in family_spec.get("topics", []) if str(item).strip()}
    known.update(str(key).strip().casefold() for key in registry.topic_aliases)
    known.update(str(value).strip().casefold() for value in registry.topic_aliases.values())
    if value.casefold() not in known and topic.casefold() not in known:
        raise CorpusMetadataError(
            f"topic {topic!r} is not registered for this family; add it to corpus_registry.json first")
    return value


def _make_index_table(db, table_name: str, chunks_path: Path, embedder,
                      expected_count: int, *, overwrite: bool = False) -> int:
    import numpy as np
    from src.retrieval.corpus_registry import deterministic_id

    if overwrite and table_name in set(db.table_names()):
        db.drop_table(table_name)
    table = None
    pending: list[dict[str, Any]] = []
    seen: set[str] = set()
    expected_metadata: dict[str, tuple[str, str]] = {}
    processed = 0

    def make_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        vectors = embedder.embed_passages([
            f"{row.get('section_name') or ''} {row.get('text') or ''}".strip()
            for row in records
        ])
        rows = []
        for record, vector in zip(records, vectors):
            chunk_id = str(record.get("chunk_id") or "")
            if chunk_id in seen:
                raise CorpusMetadataError(f"duplicate chunk_id in corpus table: {chunk_id}")
            seen.add(chunk_id)
            expected_metadata[chunk_id] = (
                str(record["corpus_id"]), str(record["corpus_family_id"]))
            rows.append({
                "vector": np.asarray(vector, dtype=np.float32),
                "chunk_id": chunk_id,
                "corpus_id": str(record["corpus_id"]),
                "corpus_family_id": str(record["corpus_family_id"]),
                "document_id": str(record["document_id"]),
                "paper_id": str(record.get("paper_id") or record["document_id"]),
                "chunk_index": int(record["chunk_index"]),
                "l1_category": str(record.get("l1_category") or ""),
            })
        return rows

    for record in _read_jsonl(chunks_path):
        pending.append(record)
        if len(pending) < EMBED_BATCH:
            continue
        rows = make_rows(pending)
        if table is None:
            table = db.create_table(table_name, data=rows)
        else:
            table.add(rows)
        processed += len(rows)
        pending.clear()
    if pending:
        rows = make_rows(pending)
        if table is None:
            table = db.create_table(table_name, data=rows)
        else:
            table.add(rows)
        processed += len(rows)
    if processed != expected_count or table is None or int(table.count_rows()) != expected_count:
        raise CorpusMetadataError(
            f"table {table_name}: embedded={processed}, expected={expected_count}, "
            f"stored={table.count_rows() if table else 0}")

    from src.retrieval.corpus_registry import iter_lance_rows
    actual = list(iter_lance_rows(table, ["chunk_id", "corpus_id", "corpus_family_id"]))
    actual_ids = [str(row.get("chunk_id") or "") for row in actual]
    if len(actual_ids) != expected_count or len(set(actual_ids)) != expected_count:
        raise CorpusMetadataError(f"table {table_name} has duplicate or missing chunk IDs")
    if set(actual_ids) != seen:
        raise CorpusMetadataError(f"table {table_name} chunk IDs do not match its chunk file")
    for row in actual:
        if expected_metadata.get(str(row.get("chunk_id"))) != (
                str(row.get("corpus_id")), str(row.get("corpus_family_id"))):
            raise CorpusMetadataError(f"table {table_name} corpus metadata does not match chunk file")
    if expected_count >= 4096:
        partitions = min(64, max(2, expected_count // 1024))
        table.create_index(metric="cosine", index_type="IVF_HNSW_FLAT",
                           num_partitions=partitions, m=16, ef_construction=200,
                           replace=True)
    return processed


def _verify_generation(db, corpus_rows: dict[str, int], registry_payload: dict[str, Any]) -> None:
    expected_tables = {str(spec["table_name"]): int(corpus_rows[cid])
                       for cid, spec in registry_payload["corpora"].items()}
    actual_names = set(db.table_names())
    missing = set(expected_tables) - actual_names
    extra = actual_names - set(expected_tables)
    if missing or extra:
        raise CorpusMetadataError(f"staged table set mismatch: missing={sorted(missing)}, extra={sorted(extra)}")
    for table_name, count in expected_tables.items():
        corpus_id = next(cid for cid, spec in registry_payload["corpora"].items()
                         if str(spec["table_name"]) == table_name)
        spec = registry_payload["corpora"][corpus_id]
        chunks_path = (DATA_DIR / str(spec["chunks_path"])).resolve()
        if DATA_DIR not in chunks_path.parents or not chunks_path.is_file():
            raise CorpusMetadataError(f"staged chunks missing or unsafe for {corpus_id}")
        expected = {}
        for record in _read_jsonl(chunks_path):
            chunk_id = str(record.get("chunk_id") or "")
            if not chunk_id or chunk_id in expected:
                raise CorpusMetadataError(f"staged chunks contain missing/duplicate IDs in {corpus_id}")
            expected[chunk_id] = (str(record.get("corpus_id") or ""),
                                  str(record.get("corpus_family_id") or ""))
        if len(expected) != count:
            raise CorpusMetadataError(f"chunk file count mismatch for {corpus_id}")
        table = db.open_table(table_name)
        if int(table.count_rows()) != count:
            raise CorpusMetadataError(f"staged table {table_name} count mismatch")
        from src.retrieval.corpus_registry import iter_lance_rows
        records = list(iter_lance_rows(
            table, ["chunk_id", "corpus_id", "corpus_family_id"]))
        vector_ids = [str(row.get("chunk_id") or "") for row in records]
        if len(set(vector_ids)) != count or set(vector_ids) != set(expected):
            raise CorpusMetadataError(f"staged vector/chunk IDs are not a one-to-one match for {corpus_id}")
        for row in records:
            if expected.get(str(row.get("chunk_id"))) != (
                    str(row.get("corpus_id") or ""), str(row.get("corpus_family_id") or "")):
                raise CorpusMetadataError(f"staged vector/chunk family mapping mismatch for {corpus_id}")


def _write_rebuild_manifest(generation_root: Path, payload: dict[str, Any]) -> Path:
    path = generation_root / "corpus_registry.json"
    _write_json_atomic(path, payload)
    return path


@_serialize_mutations
def reindex_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="旁路重建统一 E5 passage 向量索引")
    parser.add_argument("--all", action="store_true", help="重建全部已登记/可映射语料")
    parser.add_argument("--activate", action="store_true", help="校验通过后原子切换 active_index.json")
    parser.add_argument("--cpu", action="store_true", help="本次重建强制使用 CPU")
    args = parser.parse_args(argv)
    if not args.all:
        parser.error("当前安全迁移只支持 --all；不允许单表覆盖活动索引")
    if args.cpu:
        os.environ["EMBEDDER_FORCE_CPU"] = "true"
        settings.EMBEDDER_FORCE_CPU = True

    import lancedb
    from src.engine.embedder import Embedder

    registry = load_corpus_registry(DATA_DIR)
    generation = _utc_generation()
    generation_root, lance_root, chunks_root = _new_generation_paths(generation)
    try:
        registry_payload, corpus_rows = _canonicalize_existing(
            generation_root, chunks_root, registry)
        staged_registry = _write_rebuild_manifest(generation_root, registry_payload)
        db = lancedb.connect(str(lance_root))
        embedder = Embedder()
        total = sum(corpus_rows.values())
        print(f"Staging generation {generation}: {total} chunks across {len(corpus_rows)} corpora")
        for corpus_id, count in corpus_rows.items():
            spec = registry_payload["corpora"][corpus_id]
            chunks_path = DATA_DIR / spec["chunks_path"]
            done = _make_index_table(db, spec["table_name"], chunks_path, embedder, count)
            print(f"  indexed {corpus_id}: {done}/{count}")
        _verify_generation(db, corpus_rows, registry_payload)
        pointer = {
            "generation": generation,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "registry_version": registry_payload["registry_version"],
            "generation_root": generation_root.relative_to(DATA_DIR).as_posix(),
            "registry_path": staged_registry.relative_to(DATA_DIR).as_posix(),
            "lancedb_path": lance_root.relative_to(DATA_DIR).as_posix(),
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "embedding_model": embedder.model_name,
            "dimension": int(embedder.dim),
            "document_prefix": DOCUMENT_PREFIX,
            "query_prefix": QUERY_PREFIX,
            "chunk_count": total,
            "table_count": len(corpus_rows),
        }
        if args.activate:
            _activate_pointer(pointer)
            print(f"ACTIVATED: {DATA_DIR / 'active_index.json'}")
        else:
            _write_json_atomic(generation_root / "index_manifest.json", pointer)
            print("VERIFIED BUT NOT ACTIVATED; pass --activate after review")
        print(f"Staged LanceDB: {lance_root}")
        print(f"Staged registry: {staged_registry}")
        return 0
    except Exception as exc:
        print(f"REBUILD FAILED; current active index was not changed. Stage retained at {generation_root}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


@_serialize_mutations
def ingest_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="登记并事务式导入一个新语料")
    parser.add_argument("--dir", required=True, help="输入目录，支持 PDF/TXT/Markdown")
    parser.add_argument("--corpus-id", required=True, help="manifest 中唯一 corpus ID")
    parser.add_argument("--family-id", required=True, help="已登记的 corpus family ID")
    parser.add_argument("--display-name", default="", help="新语料显示名；新 corpus 必填")
    parser.add_argument("--topic", default="", help="可选主题，必须已在 family manifest 注册")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--activate", action="store_true", help="校验通过后切换索引")
    parser.add_argument("--cpu", action="store_true", help="本次导入强制使用 CPU")
    args = parser.parse_args(argv)
    if args.cpu:
        os.environ["EMBEDDER_FORCE_CPU"] = "true"
        settings.EMBEDDER_FORCE_CPU = True
    corpus_id = args.corpus_id.strip().casefold()
    family_id = args.family_id.strip().casefold()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", corpus_id):
        parser.error("--corpus-id must match [a-z][a-z0-9_-]{1,63}")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", family_id):
        parser.error("--family-id must match [a-z][a-z0-9_-]{1,63}")
    input_dir = Path(args.dir).expanduser().resolve()
    if not input_dir.is_dir():
        parser.error(f"input directory does not exist: {input_dir}")

    registry = load_corpus_registry(DATA_DIR)
    if family_id not in registry.family_ids:
        parser.error(f"unknown family {family_id!r}; register family metadata first")
    corpora = copy.deepcopy(registry.corpora)
    existing = corpora.get(corpus_id)
    if existing and str(existing.get("family_id")) != family_id:
        parser.error("existing corpus ID is registered under a different family")
    if not existing and not args.display_name.strip():
        parser.error("--display-name is required when registering a new corpus")
    family_spec = registry.family(family_id) or {}
    topic = _validate_topic(args.topic.strip(), family_spec, registry) if args.topic.strip() else ""

    extensions = {".pdf", ".txt", ".md", ".markdown"}
    files = sorted(p for p in input_dir.rglob("*") if p.is_file() and p.suffix.casefold() in extensions)
    if not files:
        parser.error("no PDF/TXT/Markdown inputs found")

    generation = _utc_generation()
    generation_root, lance_root, chunks_root = _new_generation_paths(generation)
    chunks_path = chunks_root / corpus_id / "chunks.jsonl"
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    chunk_ids: set[str] = set()
    count = 0
    try:
        from src.retrieval.corpus_registry import _validate_registry
        provisional = dict(existing or {})
        provisional.update({
            "family_id": family_id,
            "batch": str((existing or {}).get("batch") or corpus_id),
            "display_name": str((existing or {}).get("display_name") or args.display_name.strip()),
            "table_name": str((existing or {}).get("table_name") or corpus_id),
        })
        corpora[corpus_id] = provisional
        payload = _registry_payload(registry)
        payload["corpora"] = corpora
        staged_registry_obj = _validate_registry(payload)
        with chunks_path.open("w", encoding="utf-8") as output:
            for path in files:
                relative = path.relative_to(input_dir).as_posix()
                if path.suffix.casefold() in {".txt", ".md", ".markdown"}:
                    text = path.read_text(encoding="utf-8", errors="strict")
                else:
                    import fitz
                    doc = fitz.open(str(path))
                    try:
                        text = "\n\n".join(page.get_text() for page in doc)
                    finally:
                        doc.close()
                paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
                parts: list[str] = []
                current = ""
                for paragraph in paragraphs:
                    if len(current) + len(paragraph) + 2 > args.chunk_size and current:
                        parts.append(current)
                        current = paragraph
                    else:
                        current = f"{current}\n\n{paragraph}" if current else paragraph
                if current:
                    parts.append(current)
                if not parts:
                    raise CorpusMetadataError(f"no extractable text in {relative}")
                document_id = "doc_" + deterministic_id(corpus_id, relative, length=24)
                for index, part in enumerate(parts):
                    row = {
                        "document_id": document_id,
                        "paper_id": document_id,
                        "chunk_index": index,
                        "text": part,
                        "title": path.stem,
                        "source_file": relative,
                        "source_type": str(provisional.get("source_type") or family_id),
                        "corpus_id": corpus_id,
                        "corpus_family_id": family_id,
                        "l1_category": topic,
                        "l2_subcategories": [],
                    }
                    normalized = normalize_chunk_metadata(
                        row, batch=provisional["batch"], registry=staged_registry_obj, ordinal=index)
                    if normalized["chunk_id"] in chunk_ids:
                        raise CorpusMetadataError(f"duplicate generated chunk_id for {relative}")
                    chunk_ids.add(normalized["chunk_id"])
                    output.write(_serialize_record(normalized))
                    count += 1

        if not count:
            raise CorpusMetadataError("no chunks generated")
        if not (DATA_DIR / "active_index.json").exists():
            raise CorpusMetadataError("no active versioned index; run reindex_lance.py --all first")
        _copy_active_index(lance_root)
        import lancedb
        db = lancedb.connect(str(lance_root))
        from src.engine.embedder import Embedder
        embedder = Embedder()
        table_name = provisional["table_name"]
        _make_index_table(db, table_name, chunks_path, embedder, count, overwrite=True)

        generation_spec = dict(provisional)
        generation_spec.update({
            "chunks_path": chunks_path.relative_to(DATA_DIR).as_posix(),
            "chunk_count": count,
            "source": str(provisional.get("source") or provisional["display_name"]),
            "source_type": str(provisional.get("source_type") or family_id),
            "topics": sorted(set(provisional.get("topics") or []) | ({topic} if topic else set())),
        })
        corpora[corpus_id] = generation_spec
        payload = _registry_payload(registry)
        payload["registry_version"] = generation
        payload["corpora"] = corpora
        staged_registry = _write_rebuild_manifest(generation_root, payload)
        pointer = {
            "generation": generation,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "registry_version": payload["registry_version"],
            "generation_root": generation_root.relative_to(DATA_DIR).as_posix(),
            "registry_path": staged_registry.relative_to(DATA_DIR).as_posix(),
            "lancedb_path": lance_root.relative_to(DATA_DIR).as_posix(),
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "embedding_model": embedder.model_name,
            "dimension": int(embedder.dim),
            "document_prefix": DOCUMENT_PREFIX,
            "query_prefix": QUERY_PREFIX,
            "chunk_count": sum(int(spec.get("chunk_count") or 0) for spec in corpora.values()),
            "table_count": len(corpora),
        }
        _verify_generation(db, {cid: int(spec.get("chunk_count") or 0)
                                for cid, spec in corpora.items()}, payload)
        _write_json_atomic(generation_root / "index_manifest.json", pointer)
        if args.activate:
            _activate_pointer(pointer)
            print(f"ACTIVATED corpus {corpus_id} ({count} chunks) in generation {generation}")
        else:
            print(f"VERIFIED corpus {corpus_id} ({count} chunks); not activated, generation={generation}")
        print(f"Staged LanceDB: {lance_root}")
        return 0
    except Exception as exc:
        print(f"IMPORT FAILED; active index was not changed. Stage retained at {generation_root}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


@_serialize_mutations
def registry_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="更新活动语料注册清单，不改动向量表")
    sub = parser.add_subparsers(dest="action", required=True)
    family = sub.add_parser("register-family", help="登记一个可扩展的语料族")
    family.add_argument("--family-id", required=True)
    family.add_argument("--display-name", required=True)
    family.add_argument("--description", default="")
    family.add_argument("--routing-term", action="append", default=[])
    family.add_argument("--topic", action="append", default=[])
    topic = sub.add_parser("register-topic", help="为已登记语料族增加主题别名")
    topic.add_argument("--family-id", required=True)
    topic.add_argument("--topic", required=True)
    args = parser.parse_args(argv)

    pointer_path = DATA_DIR / "active_index.json"
    if not pointer_path.is_file():
        parser.error("no active index; first run reindex_lance.py --all --activate")
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    registry = load_corpus_registry(DATA_DIR)
    payload = _registry_payload(registry)
    if args.action == "register-family":
        family_id = args.family_id.strip().casefold()
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", family_id):
            parser.error("--family-id must match [a-z][a-z0-9_-]{1,63}")
        current = dict(payload["families"].get(family_id) or {})
        current["display_name"] = args.display_name.strip()
        if args.description.strip():
            current["description"] = args.description.strip()
        current["routing_terms"] = sorted(set(current.get("routing_terms") or [])
                                          | {str(x).strip() for x in args.routing_term if str(x).strip()})
        current["topics"] = sorted(set(current.get("topics") or [])
                                    | {str(x).strip() for x in args.topic if str(x).strip()})
        payload["families"][family_id] = current
    else:
        family_id = args.family_id.strip().casefold()
        if family_id not in payload["families"]:
            parser.error(f"unknown family {family_id!r}; register it first")
        value = args.topic.strip()
        if not value:
            parser.error("--topic must not be empty")
        topics = set(payload["families"][family_id].get("topics") or [])
        topics.add(value)
        payload["families"][family_id]["topics"] = sorted(topics)

    from src.retrieval.corpus_registry import _validate_registry
    registry = _validate_registry(payload)
    payload = _registry_payload(registry)
    generation = _utc_generation()
    registry_root = DATA_DIR / "corpus_generations" / generation
    registry_root.mkdir(parents=True, exist_ok=False)
    payload["registry_version"] = generation
    staged_registry = _write_rebuild_manifest(registry_root, payload)
    pointer.update({
        "generation": generation,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "registry_version": generation,
        "registry_path": staged_registry.relative_to(DATA_DIR).as_posix(),
    })
    _activate_pointer(pointer)
    print(f"ACTIVATED registry generation {generation}; "
          f"families={len(registry.families)}; corpora={len(registry.corpora)}; "
          f"vector tables unchanged")
    return 0


@_serialize_mutations
def rollback_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验并切回上一版活动索引")
    parser.parse_args(argv)
    previous_path = DATA_DIR / "active_index.previous.json"
    if not previous_path.is_file():
        parser.error("no previous active index pointer is available")
    try:
        pointer = json.loads(previous_path.read_text(encoding="utf-8"))
        registry_rel = str(pointer.get("registry_path") or "").replace("\\", "/")
        lance_rel = str(pointer.get("lancedb_path") or "").replace("\\", "/")
        if (not registry_rel or registry_rel.startswith("/") or ".." in Path(registry_rel).parts
                or not lance_rel or lance_rel.startswith("/") or ".." in Path(lance_rel).parts):
            raise CorpusMetadataError("previous pointer contains unsafe relative paths")
        registry_path = (DATA_DIR / registry_rel).resolve()
        lance_path = (DATA_DIR / lance_rel).resolve()
        if DATA_DIR not in registry_path.parents or not registry_path.is_file():
            raise CorpusMetadataError("previous registry is missing or outside data directory")
        if DATA_DIR not in lance_path.parents or not lance_path.is_dir():
            raise CorpusMetadataError("previous LanceDB is missing or outside data directory")
        from src.retrieval.corpus_registry import _validate_registry
        registry = _validate_registry(json.loads(registry_path.read_text(encoding="utf-8")))
        corpus_rows = {cid: int(spec.get("chunk_count") or 0)
                       for cid, spec in registry.corpora.items()}
        import lancedb
        db = lancedb.connect(str(lance_path))
        _verify_generation(db, corpus_rows, _registry_payload(registry))
        _activate_pointer(pointer)
        print(f"ROLLED BACK to verified index generation {pointer.get('generation')}")
        return 0
    except Exception as exc:
        print(f"ROLLBACK REFUSED; active index was not changed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


@_serialize_mutations
def activate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验并激活已旁路构建的索引代次")
    parser.add_argument("--generation", required=True)
    args = parser.parse_args(argv)
    generation = args.generation.strip()
    if not re.fullmatch(r"g[0-9]{8}T[0-9]{6}Z_[a-f0-9]{8}", generation):
        parser.error("--generation is not a valid staged generation ID")
    generation_root = (DATA_DIR / "corpus_generations" / generation).resolve()
    if DATA_DIR not in generation_root.parents or not generation_root.is_dir():
        parser.error("staged generation directory is missing or unsafe")
    manifest_path = generation_root / "index_manifest.json"
    try:
        pointer = json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(pointer.get("generation")) != generation:
            raise CorpusMetadataError("staged manifest generation does not match request")
        contract = {
            "index_schema_version": INDEX_SCHEMA_VERSION,
            "embedding_model": settings.EMBEDDING_MODEL,
            "document_prefix": DOCUMENT_PREFIX,
            "query_prefix": QUERY_PREFIX,
        }
        mismatches = [f"{key}={pointer.get(key)!r} (expected {value!r})"
                      for key, value in contract.items() if pointer.get(key) != value]
        if mismatches:
            raise CorpusMetadataError("staged index contract mismatch: " + "; ".join(mismatches))
        registry_rel = str(pointer.get("registry_path") or "").replace("\\", "/")
        lance_rel = str(pointer.get("lancedb_path") or "").replace("\\", "/")
        if (not registry_rel or registry_rel.startswith("/") or ".." in Path(registry_rel).parts
                or not lance_rel or lance_rel.startswith("/") or ".." in Path(lance_rel).parts):
            raise CorpusMetadataError("staged pointer contains unsafe relative paths")
        registry_path = (DATA_DIR / registry_rel).resolve()
        lance_path = (DATA_DIR / lance_rel).resolve()
        if DATA_DIR not in registry_path.parents or not registry_path.is_file():
            raise CorpusMetadataError("staged registry is missing or outside data directory")
        if DATA_DIR not in lance_path.parents or not lance_path.is_dir():
            raise CorpusMetadataError("staged LanceDB is missing or outside data directory")
        from src.retrieval.corpus_registry import _validate_registry
        registry = _validate_registry(json.loads(registry_path.read_text(encoding="utf-8")))
        corpus_rows = {cid: int(spec.get("chunk_count") or 0)
                       for cid, spec in registry.corpora.items()}
        if int(pointer.get("chunk_count", -1)) != sum(corpus_rows.values()):
            raise CorpusMetadataError("staged pointer chunk_count does not match registry")
        if int(pointer.get("table_count", -1)) != len(corpus_rows):
            raise CorpusMetadataError("staged pointer table_count does not match registry")
        import lancedb
        db = lancedb.connect(str(lance_path))
        _verify_generation(db, corpus_rows, _registry_payload(registry))
        _activate_pointer(pointer)
        print(f"ACTIVATED verified generation {generation}: "
              f"chunks={sum(corpus_rows.values())}; tables={len(corpus_rows)}")
        return 0
    except Exception as exc:
        print(f"ACTIVATION REFUSED; active index was not changed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


def main(argv: list[str] | None = None) -> int:
    commands = {"reindex": reindex_main, "ingest": ingest_main,
                "registry": registry_main, "rollback": rollback_main,
                "activate": activate_main}
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in commands:
        print("Usage: python -m src.retrieval.corpus_admin {reindex|ingest|registry|activate|rollback} ...",
              file=sys.stderr)
        return 2
    return commands[args[0]](args[1:])


if __name__ == "__main__":
    raise SystemExit(main())

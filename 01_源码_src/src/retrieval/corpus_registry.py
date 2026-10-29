"""Versioned, manifest-driven corpus metadata and identity contract."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from src.config import settings


class CorpusMetadataError(ValueError):
    """Raised when a corpus record cannot be safely assigned and indexed."""


def iter_lance_rows(table, columns: list[str], batch_size: int = 8192) -> Iterator[dict[str, Any]]:
    """Stream a narrow projection from a LanceDB table using the current Lance API.

    Do not use ``to_lance().select(...)``: ``select`` is not a LanceDataset API.
    Projection at scan time also avoids loading embedding vectors merely to
    verify chunk IDs and corpus metadata.
    """
    dataset = table.to_lance()
    scanner = dataset.scanner(columns=columns, batch_size=batch_size)
    for batch in scanner.to_batches():
        yield from batch.to_pylist()


_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_DEFAULT_PATH = Path(__file__).with_name("corpus_registry.default.json")
CHUNK_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class CorpusRegistry:
    schema_version: int
    registry_version: str
    families: dict[str, dict[str, Any]]
    corpora: dict[str, dict[str, Any]]
    topic_aliases: dict[str, str]

    @property
    def family_ids(self) -> set[str]:
        return set(self.families)

    def family(self, family_id: str) -> dict[str, Any] | None:
        return self.families.get(str(family_id or "").strip().casefold())

    def display_name(self, family_id: str) -> str:
        item = self.family(family_id) or {}
        return str(item.get("display_name") or family_id)

    def describe_families(self, active: set[str] | None = None) -> str:
        ids = sorted((active if active is not None else self.family_ids) & self.family_ids)
        if not ids:
            return "当前没有可检索的本地语料类别。"
        return "\n".join(
            f"- `{family_id}`：{self.display_name(family_id)}。{self.families[family_id].get('description', '')}"
            for family_id in ids
        )

    def matching_families(self, query: object) -> set[str]:
        text = str(query or "").casefold()
        matched = set()
        for family_id, item in self.families.items():
            terms = item.get("routing_terms") or []
            if any(str(term).casefold() in text for term in terms if str(term).strip()):
                matched.add(family_id)
        return matched

    def normalize_topic(self, value: object) -> str:
        text = str(value or "").strip().casefold()
        return self.topic_aliases.get(text, text)

    def normalize_topics(self, values: list[object] | None,
                         family_ids: set[str] | None = None) -> set[str]:
        selected = set(family_ids or self.family_ids)
        unknown_families = selected - self.family_ids
        if unknown_families:
            raise CorpusMetadataError(
                f"unknown corpus family id(s): {', '.join(sorted(unknown_families))}")
        known = {self.normalize_topic(key) for key in self.topic_aliases}
        known.update(self.normalize_topic(value) for value in self.topic_aliases.values())
        for family_id in selected:
            known.update(self.normalize_topic(value)
                         for value in self.families[family_id].get("topics", []) if str(value).strip())
        result = set()
        for item in values or []:
            text = str(item or "").strip()
            if not text:
                continue
            normalized = self.normalize_topic(text)
            if normalized not in known:
                raise CorpusMetadataError(f"unknown topic/category: {text!r}")
            result.add(normalized)
        return result

    def family_for_legacy(self, row: dict[str, Any], batch: str = "") -> str:
        explicit = str(row.get("corpus_family_id") or row.get("family_id") or "").strip().casefold()
        if explicit:
            return explicit if explicit in self.families else ""

        category = str(row.get("category") or "").strip().casefold()
        source_type = str(row.get("source_type") or "").strip().casefold()
        source_label = str(row.get("_src") or "").strip().casefold()
        source_file = str(row.get("source_file") or "").replace("\\", "/").casefold()
        batch_name = str(row.get("_batch") or batch or "").strip().casefold()
        sources = " ".join((source_type, source_label, batch_name, source_file))
        for family_id, item in self.families.items():
            if category and category in {str(x).casefold() for x in item.get("legacy_categories", [])}:
                return family_id
            if source_type and source_type in {str(x).casefold() for x in item.get("legacy_source_types", [])}:
                return family_id
            if batch_name in {str(x).casefold() for x in item.get("legacy_batch_exact", [])}:
                return family_id
            if any(batch_name.startswith(str(x).casefold()) for x in item.get("legacy_batch_prefixes", [])):
                return family_id
            if any(str(x).casefold() in sources for x in item.get("legacy_source_substrings", [])):
                return family_id
            if any(source_file.startswith(str(x).replace("\\", "/").casefold())
                   for x in item.get("legacy_source_file_prefixes", [])):
                return family_id
        return ""

    def corpus_for_batch(self, batch: str) -> tuple[str, str]:
        batch_key = str(batch or "").strip()
        for corpus_id, spec in self.corpora.items():
            if str(spec.get("batch") or corpus_id).strip() == batch_key:
                return corpus_id, str(spec["family_id"])
        # Existing data batches are migrated by record metadata; the batch label
        # is an identity namespace only and never implies a family.
        return batch_key, ""


def _validate_registry(payload: dict[str, Any]) -> CorpusRegistry:
    if not isinstance(payload, dict) or int(payload.get("schema_version", 0)) != 1:
        raise CorpusMetadataError("registry schema_version must be 1")
    families = payload.get("families")
    corpora = payload.get("corpora", {})
    aliases = payload.get("topic_aliases", {})
    if not isinstance(families, dict) or not families:
        raise CorpusMetadataError("registry must define at least one family")
    if not isinstance(corpora, dict) or not isinstance(aliases, dict):
        raise CorpusMetadataError("corpora and topic_aliases must be objects")
    normalized: dict[str, dict[str, Any]] = {}
    for family_id, spec in families.items():
        family_id = str(family_id).strip().casefold()
        if not _ID_RE.fullmatch(family_id) or not isinstance(spec, dict):
            raise CorpusMetadataError(f"invalid corpus family: {family_id!r}")
        if not str(spec.get("display_name") or "").strip():
            raise CorpusMetadataError(f"family {family_id} requires display_name")
        for field in ("topics", "routing_terms", "legacy_categories", "legacy_source_types",
                      "legacy_batch_exact", "legacy_batch_prefixes", "legacy_source_substrings",
                      "legacy_source_file_prefixes"):
            values = spec.get(field, [])
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise CorpusMetadataError(f"family {family_id} field {field} must be a list of strings")
        normalized[family_id] = dict(spec)
    clean_corpora: dict[str, dict[str, Any]] = {}
    seen_batches: set[str] = set()
    seen_tables: set[str] = set()
    for corpus_id, spec in corpora.items():
        corpus_id = str(corpus_id).strip().casefold()
        if not _ID_RE.fullmatch(corpus_id) or not isinstance(spec, dict):
            raise CorpusMetadataError(f"invalid corpus id: {corpus_id!r}")
        family_id = str(spec.get("family_id") or "").strip().casefold()
        if family_id not in normalized:
            raise CorpusMetadataError(f"corpus {corpus_id} references unknown family {family_id!r}")
        batch = str(spec.get("batch") or corpus_id).strip()
        if not batch or Path(batch).name != batch or batch in {".", ".."}:
            raise CorpusMetadataError(f"corpus {corpus_id} has unsafe batch name")
        chunks_path = str(spec.get("chunks_path") or "").strip()
        if chunks_path:
            normalized_path = chunks_path.replace("\\", "/")
            if normalized_path.startswith("/") or ".." in Path(normalized_path).parts:
                raise CorpusMetadataError(f"corpus {corpus_id} has unsafe chunks_path")
            chunks_path = normalized_path
        table_name = str(spec.get("table_name") or corpus_id).strip()
        if not table_name or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", table_name):
            raise CorpusMetadataError(f"corpus {corpus_id} has unsafe table_name")
        if batch.casefold() in seen_batches:
            raise CorpusMetadataError(f"duplicate corpus batch name: {batch!r}")
        if table_name.casefold() in seen_tables:
            raise CorpusMetadataError(f"duplicate LanceDB table name: {table_name!r}")
        seen_batches.add(batch.casefold())
        seen_tables.add(table_name.casefold())
        clean_corpora[corpus_id] = {
            **spec, "family_id": family_id, "batch": batch,
            "chunks_path": chunks_path, "table_name": table_name,
        }
    clean_aliases = {str(k).strip().casefold(): str(v).strip()
                     for k, v in aliases.items() if str(k).strip() and str(v).strip()}
    return CorpusRegistry(
        schema_version=1,
        registry_version=str(payload.get("registry_version") or "1"),
        families=normalized,
        corpora=clean_corpora,
        topic_aliases=clean_aliases,
    )


def _load_payload(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CorpusMetadataError(f"cannot read corpus registry {path.name}: {exc}") from exc


@lru_cache(maxsize=8)
def _load_registry_cached(path_text: str, mtime_ns: int, size: int) -> CorpusRegistry:
    return _validate_registry(_load_payload(Path(path_text)))


def registry_path(data_dir: Path | None = None) -> Path:
    override = os.getenv("CITRUSQA_CORPUS_REGISTRY")
    if override:
        return Path(override).expanduser().resolve()
    root = Path(data_dir or settings.DATA_DIR)
    pointer = root / "active_index.json"
    if pointer.exists():
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        rel = str(payload.get("registry_path") or "").strip().replace("\\", "/")
        if rel:
            if rel.startswith("/") or ".." in Path(rel).parts:
                raise CorpusMetadataError("active index registry_path escapes data root")
            candidate = (root / rel).resolve()
            if root.resolve() not in candidate.parents or not candidate.is_file():
                raise CorpusMetadataError("active index registry is outside data root or missing")
            return candidate
    return root / "corpus_registry.json"


def active_lancedb_path(data_dir: Path | None = None) -> Path:
    """Resolve the active LanceDB generation; legacy data/lancedb is read-only fallback."""
    root = Path(data_dir or settings.DATA_DIR).resolve()
    pointer = root / "active_index.json"
    if pointer.exists():
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            rel = str(payload.get("lancedb_path") or "").strip()
            if not rel:
                raise CorpusMetadataError("active_index.json has no lancedb_path")
            candidate = (root / rel).resolve()
            if root not in candidate.parents or not candidate.is_dir():
                raise CorpusMetadataError("active LanceDB path is outside data root or missing")
            return candidate
        except CorpusMetadataError:
            raise
        except Exception as exc:
            raise CorpusMetadataError(f"invalid active_index.json: {exc}") from exc
    return root / "lancedb"


def active_index_generation(data_dir: Path | None = None) -> str:
    """Return a stable cache namespace for the active index/manifest pair."""
    root = Path(data_dir or settings.DATA_DIR).resolve()
    pointer = root / "active_index.json"
    if pointer.exists():
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        return str(payload.get("generation") or payload.get("lancedb_path") or "")
    return "legacy:" + str((root / "lancedb").resolve())


def load_corpus_registry(data_dir: Path | None = None) -> CorpusRegistry:
    """Load the runtime registry, falling back to the shipped legacy catalog."""
    path = registry_path(data_dir)
    if not path.exists():
        path = _DEFAULT_PATH
    st = path.stat()
    return _load_registry_cached(str(path.resolve()), st.st_mtime_ns, st.st_size)


def default_registry_payload() -> dict[str, Any]:
    return _load_payload(_DEFAULT_PATH)


def deterministic_id(*parts: object, length: int = 32) -> str:
    payload = "\0".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def normalize_chunk_metadata(
    chunk: dict[str, Any], *, batch: str, registry: CorpusRegistry,
    ordinal: int,
) -> dict[str, Any]:
    """Normalize a legacy or new chunk to the canonical schema or reject it."""
    row = dict(chunk)
    try:
        input_schema = int(row.get("schema_version", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise CorpusMetadataError("schema_version must be an integer") from exc
    if input_schema > CHUNK_SCHEMA_VERSION:
        raise CorpusMetadataError(
            f"unsupported chunk schema_version {input_schema}; max supported is {CHUNK_SCHEMA_VERSION}")
    declared_corpus_id = str(row.get("corpus_id") or "").strip()
    if declared_corpus_id and not _ID_RE.fullmatch(declared_corpus_id.casefold()):
        raise CorpusMetadataError(f"invalid declared corpus_id {declared_corpus_id!r}")
    corpus_id = declared_corpus_id
    registered_corpus, registered_family = registry.corpus_for_batch(batch)
    corpus_id = corpus_id or registered_corpus
    if registry.corpora and corpus_id not in registry.corpora:
        raise CorpusMetadataError(f"unregistered corpus_id {corpus_id!r}")
    declared_family = str(row.get("corpus_family_id") or row.get("family_id") or "").strip().casefold()
    if declared_family and declared_family not in registry.families:
        raise CorpusMetadataError(f"unregistered corpus family {declared_family!r}")
    if registered_family and declared_family and declared_family != registered_family:
        raise CorpusMetadataError(
            f"chunk family {declared_family!r} conflicts with registered family {registered_family!r}")
    family_id = registry.family_for_legacy(row, batch)
    family_id = family_id or registered_family
    if family_id not in registry.families:
        raise CorpusMetadataError(f"unknown or missing family for batch {batch!r}")
    if input_schema >= CHUNK_SCHEMA_VERSION:
        required = ("corpus_id", "corpus_family_id", "document_id", "chunk_id", "source")
        missing = [name for name in required if not str(row.get(name) or "").strip()]
        if missing:
            raise CorpusMetadataError(
                f"canonical chunk missing required field(s): {', '.join(missing)}")
    if not corpus_id or not _ID_RE.fullmatch(corpus_id.casefold()):
        # Legacy batch names may contain spaces and punctuation. Namespace them
        # deterministically without deriving a semantic family from the name.
        corpus_id = "legacy_" + deterministic_id(batch, length=24)
    document_id = str(row.get("document_id") or row.get("paper_id") or "").strip()
    if not document_id:
        document_id = str(row.get("doi") or row.get("source_file") or row.get("title") or "").strip()
    if not document_id:
        document_id = "content_" + deterministic_id(row.get("text", ""), length=24)
    try:
        raw_chunk_index = row.get("chunk_index", ordinal)
        chunk_index = int(raw_chunk_index)
    except (TypeError, ValueError) as exc:
        raise CorpusMetadataError("chunk_index must be a non-negative integer") from exc
    if isinstance(raw_chunk_index, bool) or str(raw_chunk_index).strip() not in {
            str(chunk_index), f"{chunk_index}.0"}:
        raise CorpusMetadataError("chunk_index must be a non-negative integer")
    if chunk_index < 0:
        raise CorpusMetadataError("chunk_index must be a non-negative integer")
    if not isinstance(row.get("text"), str) or not row["text"].strip():
        raise CorpusMetadataError("chunk text must not be empty")
    if len(document_id) > 512:
        raise CorpusMetadataError("document_id must not exceed 512 characters")
    chunk_id = str(row.get("chunk_id") or "").strip()
    expected_id = deterministic_id(corpus_id, document_id, chunk_index)
    if chunk_id and chunk_id != expected_id:
        raise CorpusMetadataError("chunk_id conflicts with corpus/document/chunk identity")
    topic = registry.normalize_topic(row.get("l1_category"))
    if topic:
        registry.normalize_topics([topic], {family_id})
    subtopics = row.get("l2_subcategories") or []
    if not isinstance(subtopics, list):
        raise CorpusMetadataError("l2_subcategories must be a list")
    corpus_spec = registry.corpora.get(corpus_id, {})
    source = str(row.get("source") or row.get("source_type") or row.get("source_file")
                 or corpus_spec.get("source") or corpus_spec.get("source_type") or "").strip()
    if not source:
        raise CorpusMetadataError("chunk source/source_type/source_file is required")
    row.update({
        "schema_version": CHUNK_SCHEMA_VERSION,
        "corpus_id": corpus_id.casefold(),
        "corpus_family_id": family_id,
        "document_id": document_id,
        "chunk_index": chunk_index,
        "chunk_id": expected_id,
        "source": source,
        "l1_category": topic,
        "l2_subcategories": [str(value).strip() for value in subtopics if str(value).strip()],
    })
    if not row.get("paper_id"):
        row["paper_id"] = document_id
    return row


def validate_ingest_chunk(
    chunk: dict[str, Any], *, corpus_id: str, family_id: str,
    registry: CorpusRegistry,
) -> dict[str, Any]:
    corpus_id = str(corpus_id or "").strip().casefold()
    family_id = str(family_id or "").strip().casefold()
    if corpus_id not in registry.corpora:
        raise CorpusMetadataError(f"corpus {corpus_id!r} is not registered")
    if family_id not in registry.families:
        raise CorpusMetadataError(f"family {family_id!r} is not registered")
    if registry.corpora[corpus_id]["family_id"] != family_id:
        raise CorpusMetadataError(f"corpus {corpus_id!r} is not in family {family_id!r}")
    row = dict(chunk)
    row["corpus_id"] = corpus_id
    row["corpus_family_id"] = family_id
    return normalize_chunk_metadata(row, batch=registry.corpora[corpus_id]["batch"],
                                    registry=registry, ordinal=int(row.get("chunk_index", 0)))


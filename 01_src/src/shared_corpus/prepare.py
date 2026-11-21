"""Immutable Markdown preparation for shared-corpus candidate bytes.

Only text candidates already archived by ``src.core.corpus_review`` may be
prepared.  The caller supplies the corpus candidate row; this module re-reads
the archived bytes, requires every recorded hash to match, normalizes line
endings deterministically, and writes one immutable version directory
``documents/<document_id>/<version_id>/{fulltext.md,metadata.json}``.

Nothing here accepts a model-supplied path or URL.  All caller-visible errors
use stable codes and never contain filesystem paths.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import chunks, rights

MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_CHARS = chunks.MAX_DOCUMENT_CHARS
MAX_METADATA_BYTES = 256 * 1024
MAX_ARCHIVE_METADATA_BYTES = 64 * 1024
MAX_HEADER_CHARS = 512
ALLOWED_SUFFIXES = frozenset({".md", ".txt"})

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_URL_RE = re.compile(r"(?i)\b(?:https?|ftp|file)://[^\s<>'\"]+")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_WINPATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s<>'\"]+")
_POSIXPATH_RE = re.compile(r"(?<![A-Za-z0-9])/(?:[^/\s]+/)+[^/\s]+")
_CRED_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|"
    r"password|passwd|authorization)\s*[:=]\s*\S+")


class PreparationError(RuntimeError):
    """A bounded, path-free preparation failure code."""

    def __init__(self, code: str):
        self.code = str(code or "preparation_failed")
        super().__init__(self.code)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def identifier(value: object, kind: str) -> str:
    text = str(value or "").strip()
    if (not text or text in {".", ".."} or ".." in text
            or not _ID_RE.fullmatch(text)):
        raise ValueError(f"{kind}_invalid")
    return text


def public_text(value: object, limit: int = MAX_HEADER_CHARS,
                keep_newlines: bool = False) -> str:
    """Bound and redact text that is safe to persist or return."""
    text = "".join(ch for ch in str(value or "")
                   if ch in "\n\t" or ord(ch) >= 32)
    text = _URL_RE.sub("[url]", text)
    text = _CRED_RE.sub("[redacted]", text)
    text = _WINPATH_RE.sub("[path]", text)
    text = _POSIXPATH_RE.sub("[path]", text)
    text = _IPV4_RE.sub("[ip]", text)
    if keep_newlines:
        text = "\n".join(" ".join(line.split()) for line in text.splitlines())
    else:
        text = " ".join(text.split())
    return text[:max(0, int(limit))]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_inside(path: Path, root: Path) -> Path:
    try:
        resolved_root = root.resolve(strict=False)
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise PreparationError("candidate_path_invalid") from exc
    if resolved_root not in resolved.parents:
        raise PreparationError("candidate_path_invalid")
    return resolved


def _candidate_path(candidate: dict[str, Any], archive_root: Path) -> Path:
    raw = str(candidate.get("stored_path") or "").strip()
    if not raw:
        raise PreparationError("candidate_source_unavailable")
    path = _resolve_inside(Path(raw), archive_root)
    if path.is_symlink():
        raise PreparationError("candidate_path_invalid")
    if not path.is_file():
        raise PreparationError("candidate_source_unavailable")
    return path


def _read_bounded(path: Path, maximum: int) -> bytes:
    if path.is_symlink():
        raise PreparationError("candidate_path_invalid")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PreparationError("candidate_source_unavailable") from exc
    if size > maximum:
        raise PreparationError("document_too_large")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PreparationError("candidate_source_unavailable") from exc
    with os.fdopen(descriptor, "rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise PreparationError("document_too_large")
    return data


def read_candidate_source(candidate: dict[str, Any], archive_root: Path
                          ) -> tuple[str, str, int]:
    """Return (normalized markdown, sha256, char_count) for one candidate."""
    declared_raw = str(candidate.get("raw_sha256") or "").strip().casefold()
    declared_text = str(candidate.get("content_sha256") or "").strip().casefold()
    if not declared_raw and not declared_text:
        raise PreparationError("candidate_hash_missing")
    path = _candidate_path(candidate, archive_root)
    if path.suffix.casefold() not in ALLOWED_SUFFIXES:
        raise PreparationError("unsupported_source_format")
    data = _read_bounded(path, MAX_SOURCE_BYTES)
    if declared_raw and _sha256_bytes(data) != declared_raw:
        raise PreparationError("candidate_hash_mismatch")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PreparationError("unsupported_source_format") from exc
    if "\x00" in text:
        raise PreparationError("unsupported_source_format")
    if declared_text and _sha256_bytes(text.encode("utf-8")) != declared_text:
        raise PreparationError("candidate_hash_mismatch")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise PreparationError("empty_document")
    if len(text) > MAX_DOCUMENT_CHARS:
        raise PreparationError("document_too_large")
    if not text.endswith("\n"):
        text += "\n"
    return text, _sha256_bytes(text.encode("utf-8")), len(text)


def load_archive_metadata(candidate: dict[str, Any],
                          archive_root: Path) -> dict[str, Any]:
    """Load the server-written sidecar for the original candidate, if any."""
    try:
        source = _candidate_path(candidate, archive_root)
        meta_path = _resolve_inside(source.parent / "meta.json", archive_root)
        if meta_path.is_symlink() or not meta_path.is_file():
            return {}
        payload = _read_bounded(meta_path, MAX_ARCHIVE_METADATA_BYTES)
        record = json.loads(payload.decode("utf-8"))
        if not isinstance(record, dict):
            return {}
        if candidate.get("source_kind") == "fetch_paper":
            # Fetch rights are attested only by the sidecar bound to the saved bytes.
            for key in ("license", "license_verified", "license_evidence", "license_source_url"):
                record.pop(key, None)
        evidence = _read_license(source.parent / "license.json", candidate.get("raw_sha256"))
        if evidence:
            record.update({key: evidence[key] for key in ("license", "license_verified", "license_evidence")})
            record["license_source_url"] = evidence["url"]
        return record
    except (PreparationError, OSError, UnicodeDecodeError, ValueError):
        return {}


def _read_license(path: Path, digest: str) -> dict[str, Any]:
    try:
        record = json.loads(_read_bounded(path, MAX_ARCHIVE_METADATA_BYTES).decode("utf-8"))
    except (PreparationError, OSError, UnicodeDecodeError, ValueError):
        return {}
    if (not isinstance(record, dict) or record.get("sha256_of_saved_bytes") != digest
            or not isinstance(record.get("url"), str)
            or rights.evaluate_source_rights(record)["status"] != "trusted"):
        return {}
    from .pmc import PMC_CLOUD_EVIDENCE, PMC_CLOUD_METADATA_URL
    source = record.get("license_evidence")
    if source == "ncbi_pmc_oa" or (source == PMC_CLOUD_EVIDENCE and PMC_CLOUD_METADATA_URL.fullmatch(record["url"])):
        return record
    return {}


def document_identity(candidate: dict[str, Any], content_sha256: str
                      ) -> tuple[str, str, str, str, str, str]:
    try:
        candidate_id = int(candidate.get("candidate_id"))
    except (TypeError, ValueError) as exc:
        raise PreparationError("candidate_id_invalid") from exc
    if candidate_id <= 0:
        raise PreparationError("candidate_id_invalid")
    from citrus_refcheck.providers import norm_title, normalize_doi
    from urllib.parse import urlsplit

    title = public_text(candidate.get("title"), 512)
    doi = public_text(normalize_doi(candidate.get("doi")), 256)
    year = public_text(candidate.get("year"), 16)
    source_kind = public_text(candidate.get("source_kind"), 64) or "shared_corpus"
    paper_key = f"corpus_candidate\0{candidate_id}"
    if source_kind == "fetch_paper":
        url = urlsplit(str(candidate.get("url") or ""))
        if doi:
            paper_key = f"doi\0{doi}"
        elif url.scheme in {"http", "https"} and url.hostname and title:
            paper_key = "\0".join((url._replace(fragment="").geturl(), norm_title(title)))
    document_id = "doc_" + hashlib.sha256(paper_key.encode("utf-8")).hexdigest()[:24]
    seed = "\0".join((content_sha256, title, doi, year, source_kind))
    version_id = "v_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    return document_id, version_id, title, doi, year, source_kind


def _document_dir(documents_root: Path, document_id: str, version_id: str) -> Path:
    document_id = identifier(document_id, "document_id")
    version_id = identifier(version_id, "version_id")
    try:
        root = Path(documents_root).resolve(strict=False)
    except OSError as exc:
        raise PreparationError("document_path_invalid") from exc
    if root.is_symlink():
        raise PreparationError("document_path_invalid")
    target = root / document_id / version_id
    try:
        resolved = target.resolve(strict=False)
    except OSError as exc:
        raise PreparationError("document_path_invalid") from exc
    if root not in resolved.parents or resolved.name != version_id:
        raise PreparationError("document_path_invalid")
    parent = root / document_id
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise PreparationError("document_path_invalid")
    if target.is_symlink():
        raise PreparationError("document_path_invalid")
    return target


def _canonical_section_path(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:chunks.MAX_SECTIONS]:
        text = public_text(item, chunks.MAX_SECTION_NAME)
        if text:
            result.append(text)
    return result


def read_document_files(documents_root: Path, document_id: str,
                        version_id: str) -> tuple[dict[str, Any], str]:
    """Read and fully validate one immutable version directory."""
    target = _document_dir(documents_root, document_id, version_id)
    if not target.exists():
        raise FileNotFoundError("document_missing")
    if not target.is_dir():
        raise ValueError("document_integrity_invalid")
    markdown_path = target / "fulltext.md"
    metadata_path = target / "metadata.json"
    for path in (markdown_path, metadata_path):
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("document_missing")
    try:
        markdown_bytes = _read_bounded(markdown_path, MAX_SOURCE_BYTES)
        metadata_bytes = _read_bounded(metadata_path, MAX_METADATA_BYTES)
    except PreparationError as exc:
        raise ValueError("document_integrity_invalid") from exc
    try:
        metadata = json.loads(metadata_bytes.decode("utf-8"))
        markdown = markdown_bytes.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("document_integrity_invalid") from exc
    if not isinstance(metadata, dict):
        raise ValueError("document_integrity_invalid")
    try:
        schema_version = int(metadata.get("schema_version", 0))
        char_count = int(metadata.get("char_count", -1))
    except (TypeError, ValueError) as exc:
        raise ValueError("document_integrity_invalid") from exc
    if schema_version != chunks.SCHEMA_VERSION:
        raise ValueError("document_integrity_invalid")
    if (str(metadata.get("document_id") or "") != document_id
            or str(metadata.get("version_id") or "") != version_id):
        raise ValueError("document_integrity_invalid")
    digest = str(metadata.get("content_sha256") or "").casefold()
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("document_integrity_invalid")
    if _sha256_bytes(markdown_bytes) != digest or char_count != len(markdown):
        raise ValueError("document_integrity_invalid")
    if not markdown.strip():
        raise ValueError("document_integrity_invalid")
    metadata = dict(metadata)
    evidence = _read_license(target / "license.json", digest)
    if evidence:
        metadata.update(license=rights.license_token(evidence["license"]),
                        license_evidence=evidence["license_evidence"], license_source_url=evidence["url"])
    metadata["section_path"] = _canonical_section_path(metadata.get("section_path"))
    return metadata, markdown


def public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return only bounded, caller-safe document metadata."""
    fields = (
        "document_id",
        "version_id",
        "title",
        "doi",
        "year",
        "source_kind",
        "source_format",
        "license",
        "license_evidence",
        "content_sha256",
        "char_count",
        "chunk_count",
        "schema_version",
        "prepared_at",
        "section_path",
    )
    result = {key: metadata.get(key) for key in fields}
    result["section_path"] = [str(item) for item in
                              (metadata.get("section_path") or [])
                              if str(item).strip()]
    return result


def _write_file(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_document(documents_root: Path, document_id: str, version_id: str,
                   metadata: dict[str, Any], markdown: str) -> dict[str, Any]:
    """Create the immutable version atomically, or reuse an identical one."""
    target = _document_dir(documents_root, document_id, version_id)
    if target.exists():
        try:
            existing, _ = read_document_files(documents_root, document_id, version_id)
        except (FileNotFoundError, ValueError) as exc:
            raise PreparationError("document_version_conflict") from exc
        if str(existing.get("content_sha256") or "") != str(
                metadata.get("content_sha256") or ""):
            raise PreparationError("document_version_conflict")
        return existing
    root = Path(documents_root).resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    markdown_bytes = markdown.encode("utf-8")
    metadata_bytes = (json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2)
                      + "\n").encode("utf-8")
    if len(metadata_bytes) > MAX_METADATA_BYTES:
        raise PreparationError("metadata_too_large")
    staging = root / (".tmp-" + uuid.uuid4().hex)
    staging.mkdir(mode=0o700)
    try:
        _write_file(staging / "fulltext.md", markdown_bytes)
        _write_file(staging / "metadata.json", metadata_bytes)
        _sync_dir(staging)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.rename(staging, target)
        except OSError as exc:
            if not target.exists():
                raise PreparationError("document_write_failed") from exc
            existing, _ = read_document_files(documents_root, document_id, version_id)
            if str(existing.get("content_sha256") or "") != str(
                    metadata.get("content_sha256") or ""):
                raise PreparationError("document_version_conflict") from exc
            return existing
        _sync_dir(target.parent)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return metadata


def prepare_document(documents_root: Path, candidate: dict[str, Any],
                     archive_root: Path) -> dict[str, Any]:
    """Prepare one immutable version and return its validated result."""
    archive_meta = load_archive_metadata(candidate, archive_root)
    decision = rights.evaluate_source_rights(archive_meta)
    markdown, content_sha256, _char_count = read_candidate_source(
        candidate, archive_root)
    (document_id, version_id, title, doi, year,
     source_kind) = document_identity(candidate, content_sha256)

    target = _document_dir(documents_root, document_id, version_id)
    if target.exists():
        try:
            existing, _ = read_document_files(documents_root, document_id, version_id)
        except (FileNotFoundError, ValueError) as exc:
            raise PreparationError("document_version_conflict") from exc
        if str(existing.get("content_sha256") or "") != content_sha256:
            raise PreparationError("document_version_conflict")
        if not existing.get("license") and decision["license"] and archive_meta.get("license_source_url"):
            from tempfile import NamedTemporaryFile
            evidence = {"license": decision["license"], "license_verified": True,
                        "license_evidence": archive_meta["license_evidence"],
                        "url": archive_meta["license_source_url"],
                        "sha256_of_saved_bytes": content_sha256}
            with NamedTemporaryFile(dir=target, delete=False) as stream:
                staging = Path(stream.name)
                try:
                    stream.write(json.dumps(evidence, ensure_ascii=False).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                    try:
                        (target / "license.json").hardlink_to(staging)
                    except FileExistsError:
                        pass
                    _sync_dir(target)
                finally:
                    staging.unlink(missing_ok=True)
            existing, _ = read_document_files(documents_root, document_id, version_id)
        return {
            "document_id": document_id,
            "version_id": version_id,
            "metadata": existing,
            "markdown": markdown,
            "content_sha256": content_sha256,
            "chunk_count": int(existing.get("chunk_count") or 0),
            "section_path": list(existing.get("section_path") or []),
            "rights": decision,
        }

    outline = chunks.section_outline(markdown)
    section_path = [item["section_name"] for item in outline
                    if item["section_name"] != "__preamble__"]
    metadata: dict[str, Any] = {
        "schema_version": chunks.SCHEMA_VERSION,
        "document_id": document_id,
        "version_id": version_id,
        "title": title,
        "doi": doi,
        "year": year,
        "source_kind": source_kind,
        "source": source_kind,
        "source_url": str(archive_meta.get("url") or ""),
        "source_sha256": str(archive_meta.get("sha256_of_source_text") or candidate.get("raw_sha256") or ""),
        "source_format": public_text(archive_meta.get("raw_format"), 64),
        "conversion": public_text(archive_meta.get("conversion"), 120),
        "license": decision["license"],
        "license_evidence": public_text(archive_meta.get("license_evidence"), 64) if decision["license"] else "",
        "license_source_url": str(archive_meta.get("license_source_url") or "") if decision["license"] else "",
        "content_sha256": content_sha256,
        "char_count": len(markdown),
        "prepared_at": utc_now(),
        "section_path": section_path,
        "sections": [
            {
                "section_index": int(item["section_index"]),
                "section_name": item["section_name"],
                "section_level": int(item["section_level"]),
                "section_path": list(item["section_path"]),
                "start": int(item["start"]),
                "end": int(item["end"]),
            }
            for item in outline
        ],
    }
    records = chunks.chunk_document(metadata, markdown)
    metadata["chunk_count"] = len(records)
    written = write_document(documents_root, document_id, version_id,
                             metadata, markdown)
    return {
        "document_id": document_id,
        "version_id": version_id,
        "metadata": written,
        "markdown": markdown,
        "content_sha256": content_sha256,
        "chunk_count": int(written.get("chunk_count") or len(records)),
        "section_path": list(written.get("section_path") or []),
        "rights": decision,
    }

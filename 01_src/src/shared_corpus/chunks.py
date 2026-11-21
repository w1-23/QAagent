"""Deterministic, offset-exact chunking for the shared literature corpus.

Chunks use retrieval schema v2.  Every chunk carries ``md_start``/``md_end``
offsets into the exact Markdown string returned by ``Catalog.read_document``;
the invariant is ``markdown[md_start:md_end] == text``.  Offsets are Python
(Unicode code point) character offsets in a half-open interval.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

SCHEMA_VERSION = 2
CORPUS_ID = "shared_agent_literature"
CORPUS_FAMILY_ID = "agent_literature"

CHUNK_TARGET = 1600
CHUNK_MAX = 2400
CHUNK_MIN = 240
MAX_DOCUMENT_CHARS = 4_000_000
MAX_SECTIONS = 1000
MAX_CHUNKS_PER_DOCUMENT = 5000
MAX_SECTION_NAME = 256

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_URL_RE = re.compile(r"(?i)\b(?:https?|ftp|file)://[^\s<>'\"]+")


def identifier(value: object, kind: str) -> str:
    """Validate a document/version identifier that will become a path part."""
    text = str(value or "").strip()
    if (not text or text in {".", ".."} or ".." in text
            or not _ID_RE.fullmatch(text)):
        raise ValueError(f"{kind}_invalid")
    return text


def _clean_label(value: object, limit: int = MAX_SECTION_NAME) -> str:
    text = "".join(ch for ch in str(value or "")
                   if ch in "\n\t" or ord(ch) >= 32)
    text = _URL_RE.sub("[url]", text)
    return " ".join(text.split())[:limit]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_identity(document_id: str, chunk_index: int) -> str:
    payload = f"{CORPUS_ID}\0{document_id}\0{int(chunk_index)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _headings(markdown: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    offset = 0
    fence: str | None = None
    for line in markdown.splitlines(keepends=True):
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
        elif fence is None:
            heading = _HEADING_RE.match(line.rstrip("\n").rstrip("\r"))
            if heading:
                name = _clean_label(heading.group(2))
                if name:
                    items.append({
                        "start": offset,
                        "line_end": offset + len(line),
                        "level": len(heading.group(1)),
                        "name": name,
                    })
        offset += len(line)
    return items


def section_outline(markdown: str) -> list[dict[str, Any]]:
    """Return exact section spans (heading start to next heading start)."""
    headings = _headings(markdown)
    sections: list[dict[str, Any]] = []
    stack: list[tuple[int, str]] = []
    first_heading = headings[0]["start"] if headings else len(markdown)
    if first_heading > 0 and markdown[:first_heading].strip():
        sections.append({
            "section_index": 0,
            "section_name": "__preamble__",
            "section_level": 0,
            "section_path": [],
            "start": 0,
            "end": first_heading,
            "body_start": 0,
        })
    for index, heading in enumerate(headings):
        while stack and stack[-1][0] >= heading["level"]:
            stack.pop()
        stack.append((heading["level"], heading["name"]))
        end = headings[index + 1]["start"] if index + 1 < len(headings) else len(markdown)
        sections.append({
            "section_index": len(sections),
            "section_name": heading["name"],
            "section_level": heading["level"],
            "section_path": [name for _level, name in stack],
            "start": heading["start"],
            "end": end,
            "body_start": heading["line_end"],
        })
    if len(sections) > MAX_SECTIONS:
        raise ValueError("document_too_complex")
    return sections


def _trim(markdown: str, start: int, end: int) -> tuple[int, int]:
    while start < end and markdown[start].isspace():
        start += 1
    while end > start and markdown[end - 1].isspace():
        end -= 1
    return start, end


def _split_bounds(markdown: str, start: int, end: int):
    start, end = _trim(markdown, start, end)
    while start < end:
        if end - start <= CHUNK_MAX:
            yield start, end
            return
        limit = start + CHUNK_MAX
        floor = start + CHUNK_TARGET
        cut = -1
        lower = max(floor, start + CHUNK_MIN)
        for position in range(limit, lower - 1, -1):
            if markdown[position - 1] in "\n.!?;:。！？；：":
                cut = position
                break
        if cut < 0:
            newline = markdown.rfind("\n", floor, limit)
            if newline >= 0:
                cut = newline + 1
        if cut < 0:
            space = markdown.rfind(" ", floor, limit)
            if space >= 0:
                cut = space + 1
        if cut <= start or cut > limit:
            cut = limit
        piece_start, piece_end = _trim(markdown, start, cut)
        if piece_end > piece_start:
            yield piece_start, piece_end
        start = cut
        while start < end and markdown[start].isspace():
            start += 1


def chunk_document(metadata: dict[str, Any], markdown: str) -> list[dict[str, Any]]:
    """Chunk one immutable Markdown document into schema-v2 records.

    ``metadata`` must contain ``document_id`` and ``version_id``.  If it also
    contains ``content_sha256``, the supplied Markdown must match it; this
    keeps chunk offsets tied to the exact bytes that were published.
    """
    if not isinstance(metadata, dict):
        raise ValueError("metadata_invalid")
    if not isinstance(markdown, str):
        raise ValueError("markdown_invalid")
    if not markdown.strip():
        raise ValueError("markdown_empty")
    if len(markdown) > MAX_DOCUMENT_CHARS:
        raise ValueError("document_too_large")
    document_id = identifier(metadata.get("document_id"), "document_id")
    version_id = identifier(metadata.get("version_id"), "version_id")
    expected_digest = str(metadata.get("content_sha256") or "").strip()
    if expected_digest and expected_digest != _sha256(markdown):
        raise ValueError("markdown_hash_mismatch")
    title = _clean_label(metadata.get("title"), 512)
    doi = _clean_label(metadata.get("doi"), 256)
    year = _clean_label(metadata.get("year"), 16)
    source_kind = _clean_label(metadata.get("source_kind"), 64) or "shared_corpus"
    source = _clean_label(metadata.get("source"), 128) or source_kind

    chunks: list[dict[str, Any]] = []
    for section in section_outline(markdown):
        for start, end in _split_bounds(markdown, section["body_start"], section["end"]):
            text = markdown[start:end]
            if not text.strip():
                continue
            index = len(chunks)
            if index >= MAX_CHUNKS_PER_DOCUMENT:
                raise ValueError("chunk_budget_exhausted")
            chunks.append({
                "schema_version": SCHEMA_VERSION,
                "corpus_id": CORPUS_ID,
                "corpus_family_id": CORPUS_FAMILY_ID,
                "source": source,
                "source_kind": source_kind,
                "document_id": document_id,
                "version_id": version_id,
                "paper_id": document_id,
                "chunk_id": chunk_identity(document_id, index),
                "chunk_index": index,
                "title": title,
                "doi": doi,
                "year": year,
                "section_name": section["section_name"],
                "section_index": int(section["section_index"]),
                "section_level": int(section["section_level"]),
                "section_path": list(section["section_path"]),
                "text": text,
                "md_start": start,
                "md_end": end,
                "char_count": end - start,
            })
    if not chunks:
        raise ValueError("markdown_empty")
    return chunks

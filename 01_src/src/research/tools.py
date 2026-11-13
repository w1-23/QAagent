"""Citrus research workflow tools and session-scoped downloadable artifacts.

Uploaded tabular data is parsed in-process. Model-generated code is never run.
The model interprets field semantics; deterministic checks report findings
without modifying the uploaded source.
"""
from __future__ import annotations

import csv
import base64
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import sqlite3
import tarfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zipfile
from difflib import SequenceMatcher
from itertools import islice
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import numpy as np

from langchain_core.tools import tool

from src.config import PROJECT_ROOT
from src.research import literature as _lit
from src.core.tool_errors import ToolArgumentError
from src.core.deliverable_check import (
    DeliverableRejected,
    RetryLedger,
    dedupe_key as _deliverable_dedupe_key,
    logical_key as _deliverable_logical_key,
    validate_deliverable,
    validate_html_document,
    validate_markdown_payload,
)
from src.core.tracing import get_session_id, get_user_id
from src.core.ooxml_determinism import is_ooxml_filename, normalize_ooxml_bytes
from src.research.context import get_request_tables

logger = logging.getLogger(__name__)
# Retention for generated artifacts.
#
# This used to be 24 hours, which was fine while exports were throwaway chat
# attachments but wrong now that they surface in the "会话文件" panel: a
# research session that is still active would keep showing a report whose
# bytes had already been deleted. Retention follows SESSION inactivity (see
# src/session/files.py), not the creation age of an individual artifact.
_ARTIFACT_TTL_SEC = 30 * 24 * 60 * 60
_MAX_TABLE_ROWS = 100_000
_MAX_TABLE_COLUMNS = 200
_MAX_TABLE_CELLS = 2_000_000
_MAX_EXTRACTED_TABLES = 500
_MAX_SAMPLES = 8
_HASH_CHUNK_SIZE = 1024 * 1024


def _sha256_file(path: Path) -> str:
    """Hash a file without loading its complete contents into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_format_matches(path: Path, filename: str, mime: str) -> bool:
    """Check the declared artifact format against extension and a small header sample."""
    suffix = Path(str(filename or "")).suffix.casefold()
    mime_l = str(mime or "").casefold()
    try:
        with open(path, "rb") as handle:
            sample = handle.read(8192)
            handle.seek(max(0, path.stat().st_size - 1024))
            tail = handle.read(1024)
    except OSError:
        return False
    if not sample:
        return False
    if suffix in {".xlsx", ".xlsm", ".docx", ".pptx"}:
        return sample.startswith(b"PK\x03\x04") and (
            "spreadsheetml" in mime_l or "wordprocessingml" in mime_l
            or "presentationml" in mime_l or mime_l == "application/zip")
    if suffix == ".pdf":
        return sample.startswith(b"%PDF-") and "pdf" in mime_l and b"%%EOF" in tail
    if suffix in {".gz", ".tgz"} or "gzip" in mime_l:
        return sample.startswith(b"\x1f\x8b") and "gzip" in mime_l
    if suffix == ".svg":
        head = sample.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        return (head.startswith(b"<svg") or (
            head.startswith(b"<?xml") and b"<svg" in head)) \
            and "svg" in mime_l
    if suffix in {".html", ".htm"}:
        head = sample.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        return (b"<html" in head and b"</html" in tail.lower()
                and "text/html" in mime_l)
    if suffix in {".md", ".txt", ".csv", ".tsv", ".json", ".bib", ".ris", ".xml"}:
        try:
            sample.decode("utf-8-sig")
            return mime_l.startswith("text/") or mime_l in {
                "application/json", "application/xml", "application/x-bibtex",
                "application/x-research-info-systems",
            }
        except UnicodeDecodeError:
            return False
    return False


def _db_path() -> Path:
    path = PROJECT_ROOT / "state" / "research_artifacts.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, filename TEXT NOT NULL,
            mime TEXT NOT NULL, created_at REAL NOT NULL, path TEXT NOT NULL)""")
        # Publication dedupe. Exact retries reuse a row; changed source bytes or
        # output bytes create a new immutable version instead of replacing the
        # content behind an older download URL.
        artifact_columns = {row[1] for row in conn.execute("PRAGMA table_info(artifacts)")}
        if "dedupe_key" not in artifact_columns:
            conn.execute("ALTER TABLE artifacts ADD COLUMN dedupe_key TEXT")
        version_columns = {
            "logical_artifact_id": "TEXT",
            "version": "INTEGER NOT NULL DEFAULT 1",
            "source_file_id": "TEXT NOT NULL DEFAULT ''",
            "source_sha256": "TEXT NOT NULL DEFAULT ''",
            "artifact_sha256": "TEXT NOT NULL DEFAULT ''",
            "producer_tool": "TEXT NOT NULL DEFAULT ''",
            "logical_task_id": "TEXT NOT NULL DEFAULT ''",
            "request_id": "TEXT NOT NULL DEFAULT ''",
            "tool_call_id": "TEXT NOT NULL DEFAULT ''",
            "supersedes_artifact_id": "TEXT NOT NULL DEFAULT ''",
        }
        for name, declaration in version_columns.items():
            if name not in artifact_columns:
                conn.execute(f"ALTER TABLE artifacts ADD COLUMN {name} {declaration}")
        conn.execute(
            "UPDATE artifacts SET logical_artifact_id=artifact_id "
            "WHERE COALESCE(logical_artifact_id,'')=''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_dedupe "
                     "ON artifacts(session_id, dedupe_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_logical_version "
                     "ON artifacts(session_id, logical_artifact_id, version)")
        conn.execute("""CREATE TABLE IF NOT EXISTS research_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
            owner_user_id INTEGER, project_name TEXT NOT NULL,
            note TEXT NOT NULL, created_at REAL NOT NULL)""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(research_notes)")}
        if "owner_user_id" not in columns:
            conn.execute("ALTER TABLE research_notes ADD COLUMN owner_user_id INTEGER")
            sessions_path = PROJECT_ROOT / "state" / "sessions.db"
            if sessions_path.exists():
                try:
                    conn.execute("ATTACH DATABASE ? AS session_store", (str(sessions_path),))
                    session_columns = {row[1] for row in conn.execute(
                        "PRAGMA session_store.table_info(sessions)")}
                    if "owner_user_id" in session_columns:
                        conn.execute("""UPDATE research_notes
                            SET owner_user_id=(SELECT s.owner_user_id FROM session_store.sessions s
                                               WHERE s.session_id=research_notes.session_id)
                            WHERE owner_user_id IS NULL AND EXISTS (
                                SELECT 1 FROM session_store.sessions s
                                WHERE s.session_id=research_notes.session_id
                                  AND s.owner_user_id IS NOT NULL)""")
                        conn.commit()
                    conn.execute("DETACH DATABASE session_store")
                except sqlite3.Error:
                    logger.warning("Could not migrate legacy research-note ownership; unverified rows remain private")
                    try:
                        conn.execute("DETACH DATABASE session_store")
                    except sqlite3.Error:
                        pass
        conn.execute("CREATE INDEX IF NOT EXISTS idx_research_notes_owner_project "
                     "ON research_notes(owner_user_id,project_name,id)")
    return path


def _safe_name(value: str, fallback: str = "research-output", max_len: int = 80) -> str:
    """规范化下载文件名，长名截断时**保留扩展名**。

    为什么单独处理扩展名：超长原标题（如期刊表格的 Table_S4_… 全名）会先拼上
    “_标注.xlsx”，再按 80 字符硬截断，.xlsx 被一起切掉，下载后 Excel 认不出格式。
    """
    value = re.sub(r"[^\w\-. ]+", "_", str(value or ""), flags=re.UNICODE).strip(" .")
    value = re.sub(r"\s+", "_", value)
    if len(value) <= max_len:
        return value or fallback
    stem, dot, ext = value.rpartition(".")
    if dot and 0 < len(ext) <= 8 and stem:
        keep = max(1, max_len - len(ext) - 1)
        stem = stem[:keep].rstrip(" ._-")
        value = f"{stem or fallback}.{ext}"
    else:
        value = value[:max_len].rstrip(" ._-")
    return value or fallback


def _short_stem(name: str, limit: int = 36, fallback: str = "表格") -> str:
    """把附件名压成短基名，给下载文件留出语义后缀的位置。

    为什么需要：期刊附表常带整段英文题名（如 Table_S4_Identified_metabolites_
    actually_belong_to_the_Gold_Nugget__Tango___Orah__）。直接拼上“_数据审查.xlsx”
    再截断，既丢掉“数据审查”这个含义，又可能切掉扩展名。
    """
    stem = Path(str(name or "")).stem.strip() or fallback
    stem = re.sub(r"\s+", "_", stem).strip(" ._-")
    if len(stem) <= limit:
        return stem
    cut = stem[:limit]
    # 拉丁文题名按词边界收尾，避免出现 “..._actu” 这种半截单词
    for sep in ("_", "-", " "):
        pos = cut.rfind(sep)
        if pos >= max(8, limit // 2):
            return cut[:pos].rstrip(" ._-") or cut.rstrip(" ._-") or fallback
    return cut.rstrip(" ._-") or fallback


_SVG_TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
             "polygon", "text", "tspan", "title", "desc", "defs", "linearGradient",
             "radialGradient", "stop", "clipPath", "marker", "use", "symbol", "filter",
             "feGaussianBlur", "feOffset", "feFlood", "feComposite", "feMerge", "feMergeNode",
             "feColorMatrix", "feBlend", "feComponentTransfer", "feFuncA", "feFuncR", "feFuncG", "feFuncB"}
_SVG_ATTRS = {"id", "viewBox", "preserveAspectRatio", "width", "height", "x", "y", "x1", "y1",
              "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "stroke",
              "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset",
              "fill-opacity", "stroke-opacity", "opacity", "transform", "offset", "font-family", "font-size",
              "font-weight", "text-anchor", "dominant-baseline", "gradientUnits", "gradientTransform",
              "stop-color", "stop-opacity", "clipPathUnits", "markerWidth", "markerHeight", "markerUnits",
              "marker-start", "marker-mid", "marker-end", "clip-path", "filter", "role", "aria-label", "class",
              "style", "href"}
_SVG_STYLE = {"fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
              "stroke-dashoffset", "fill-opacity", "stroke-opacity", "opacity", "font-family", "font-size",
              "font-weight", "text-anchor", "dominant-baseline", "stop-color", "stop-opacity", "clip-path",
              "filter", "marker-start", "marker-mid", "marker-end"}


def _sanitize_svg_for_export(source: str) -> str:
    raw = str(source or "").strip()
    if not raw or len(raw) > 100_000 or re.search(r"<!DOCTYPE|<!ENTITY", raw, re.I):
        return ""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return ""
    if root.tag.split("}")[-1].lower() != "svg":
        return ""

    def clean(node):
        tag = node.tag.split("}")[-1]
        if tag not in _SVG_TAGS:
            return None
        out = ET.Element(tag)
        for key, raw_value in node.attrib.items():
            name = key.split("}")[-1]
            value = str(raw_value)
            if name not in _SVG_ATTRS or len(value) > 4000 or re.search(r"[<>\x00-\x1f]", value):
                continue
            if name == "href" and not re.fullmatch(r"#[A-Za-z][\w:.-]{0,100}", value):
                continue
            if name == "id" and not re.fullmatch(r"[A-Za-z][\w:.-]{0,100}", value):
                continue
            if name == "class" and not re.fullmatch(r"[A-Za-z0-9 _-]{1,200}", value):
                continue
            if re.search(r"url\s*\(", value, re.I) and not re.fullmatch(
                    r"url\(\s*#[A-Za-z][\w:.-]{0,100}\s*\)", value, re.I):
                continue
            if re.search(r"javascript:|data:|expression|@import", value, re.I):
                continue
            if name == "style":
                declarations = []
                for item in value.split(";"):
                    if ":" not in item:
                        continue
                    prop, val = (part.strip() for part in item.split(":", 1))
                    if prop.lower() in _SVG_STYLE and val and not re.search(r"[{}]", val):
                        declarations.append(f"{prop.lower()}:{val}")
                value = ";".join(declarations)
                if not value:
                    continue
            out.set(name, value)
        out.text = node.text
        for child in list(node):
            safe_child = clean(child)
            if safe_child is not None:
                out.append(safe_child)
        return out

    safe = clean(root)
    if safe is None or not (safe.get("viewBox") or safe.get("width")):
        return ""
    safe.set("xmlns", "http://www.w3.org/2000/svg")
    return ET.tostring(safe, encoding="unicode", short_empty_elements=True)


def _markdown_to_safe_html(content: str) -> str:
    """Render the common Markdown used by exports; only sanitized inline SVG is raw HTML."""
    svg_blocks: list[tuple[str, str]] = []

    def stash_svg(raw_svg: str):
        token = f"CITRUS_EXPORT_SVG_{len(svg_blocks)}_TOKEN"
        svg_blocks.append((token, _sanitize_svg_for_export(raw_svg)))
        return f"\n\n{token}\n\n"

    source = re.sub(r"```svg\s*\n([\s\S]*?)```",
                    lambda match: stash_svg(match.group(1)), str(content or ""), flags=re.I)
    source = re.sub(r"<svg\b[\s\S]*?</svg\s*>",
                    lambda match: stash_svg(match.group(0)), source, flags=re.I)

    def inline(value: str) -> str:
        value = html.escape(value, quote=True)
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
        value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", value)
        value = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
                       lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', value)
        return value

    lines = source.splitlines()
    output: list[str] = []
    paragraph: list[str] = []
    list_tag = ""
    i = 0

    def flush_paragraph():
        if paragraph:
            output.append("<p>" + " ".join(inline(line.strip()) for line in paragraph) + "</p>")
            paragraph.clear()

    def close_list():
        nonlocal list_tag
        if list_tag:
            output.append(f"</{list_tag}>")
            list_tag = ""

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            flush_paragraph(); close_list(); i += 1; continue
        if stripped.startswith("CITRUS_EXPORT_SVG_") and stripped.endswith("_TOKEN"):
            flush_paragraph(); close_list(); output.append(stripped); i += 1; continue
        if stripped.startswith("```"):
            flush_paragraph(); close_list(); code = []; i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i]); i += 1
            output.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
            i += 1; continue
        if i + 1 < len(lines) and "|" in line and re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*", lines[i + 1]):
            flush_paragraph(); close_list()
            headers = [cell.strip() for cell in stripped.strip("|").split("|")]
            output.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in headers) + "</tr></thead><tbody>")
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
                output.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
                i += 1
            output.append("</tbody></table>"); continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush_paragraph(); close_list(); level = len(heading.group(1))
            output.append(f"<h{level}>{inline(heading.group(2))}</h{level}>"); i += 1; continue
        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        numbered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet or numbered:
            flush_paragraph(); wanted = "ul" if bullet else "ol"
            if list_tag != wanted:
                close_list(); list_tag = wanted; output.append(f"<{wanted}>")
            output.append("<li>" + inline((bullet or numbered).group(1)) + "</li>")
            i += 1; continue
        close_list(); paragraph.append(line); i += 1
    flush_paragraph(); close_list()
    rendered = "\n".join(output)
    for token, svg in svg_blocks:
        rendered = rendered.replace(token, svg or '<p class="muted">SVG 未通过安全检查，未嵌入。</p>')
    return rendered


def _safe_spreadsheet_value(value: Any) -> Any:
    """Prevent formula injection when exported files are opened in office apps."""
    if value is None:
        return ""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        return "'" + value
    return value


_ANNOTATION_COLUMN_RE = re.compile(
    r"^(?:note|notes|remark|remarks|comment|comments|memo|annotation|"
    r"备注|注|说明|描述|附注|注释)$",
    re.I,
)


def _is_annotation_column(name: str) -> bool:
    """备注/说明类自由文本列：设计上允许留空，不应逐格报「缺失」。"""
    text = str(name or "").strip()
    if not text:
        return False
    # 去掉括号注释、序号后缀与列名前缀噪声，例如「Note（可选）」「备注2」「9.备注」
    text = re.sub(r"[（(][^）)]*[）)]\s*$", "", text).strip()
    text = re.sub(r"^\s*\d+[.、．]\s*", "", text).strip()
    text = re.sub(r"\s*[（(]?\d+[）)]?$", "", text).strip()
    return bool(_ANNOTATION_COLUMN_RE.match(text))


def _header_cell_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _row_is_header_like(cells: list[str]) -> bool:
    """至少两个非空、且没有明显的数据特征（长文本、数值为主）。"""
    values = [cell for cell in cells if cell]
    if len(values) < 2:
        return False
    if len(set(values)) < 2:
        return False
    if any(len(value) > 40 for value in values):
        return False
    numeric = sum(1 for value in values if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value))
    return numeric < max(1, len(values) // 2)


def _auto_header_rows(raw_rows: list[list[Any]], width: int, scan: int = 8) -> list[int]:
    """推断表头行区间：跳过单标题行，合并连续的「分组 + 字段名」多层表头。

    判定顺序：首个像表头的行作为起点；只要下一行的去重标签数更多（分组行标签会重复），
    且不含数值单元格（数据行特征），就继续并入同一表头区间。
    """
    limit = min(len(raw_rows), max(1, scan))
    start = -1
    for index in range(limit):
        cells = [_header_cell_text(v) for v in (raw_rows[index] or [])][:width]
        if _row_is_header_like(cells):
            start = index
            break
    if start < 0:
        return []
    rows: list[int] = []
    previous_distinct = 0
    for index in range(start, min(len(raw_rows), start + 4)):
        cells = [_header_cell_text(v) for v in (raw_rows[index] or [])][:width]
        values = [cell for cell in cells if cell]
        if not _row_is_header_like(cells):
            break
        distinct = len(set(values))
        if rows and distinct <= previous_distinct:
            break
        rows.append(index + 1)
        previous_distinct = distinct
    return rows or [start + 1]


def _resolve_headers(raw_rows: list[list[Any]], header_rows: list[int],
                     width: int) -> tuple[list[str], list[int]]:
    """合并一或多行（1 基）表头，返回 (规范列名, 实际使用的表头行)。

    规范列名取**叶子名**（最下一层表头的字段名），分组名只作为可读路径保留在
    ``meta["header_paths"]``。此前的实现把每一层拼成「分组 / 字段」，导致按列名
    配置的 ``optional_fields`` / ``ranges`` / ``relations`` / ``key_columns``
    全部匹配不上，整列被误判为缺失并错标到相邻列。
    """
    requested = sorted({int(row) for row in (header_rows or [1])})
    selected: list[list[str]] = []
    used: list[int] = []
    for row_no in requested:
        row = raw_rows[row_no - 1] if 0 < row_no <= len(raw_rows) else []
        values = [_header_cell_text(v) for v in row]
        values = (values + [""] * width)[:width]
        if sum(bool(v) for v in values) <= 1:  # 单标题/日期说明行，不是字段头
            continue
        # Excel 的合并表头只在最左格保存可见值：向右补齐，保证每列都带分组名。
        filled: list[str] = []
        horizontal = ""
        for value in values:
            if value:
                horizontal = value
            filled.append(value or horizontal)
        selected.append(filled)
        used.append(row_no)
    if not selected:
        auto = _auto_header_rows(raw_rows, width)
        if auto:
            logger.warning("Header rows %s look like a title row; auto-detected %s",
                           requested, auto)
            return _resolve_headers(raw_rows, auto, width)
        fallback_row = requested[-1] if requested else 1
        selected = [[_header_cell_text(v)
                     for v in (raw_rows[fallback_row - 1] if 0 < fallback_row <= len(raw_rows) else [])]]
        used = [fallback_row]
    labels: list[str] = []
    paths: list[str] = []
    for column in range(width):
        parts: list[str] = []
        carry = ""
        for row in selected:
            value = row[column] if column < len(row) else ""
            if value:
                carry = value
            if carry and (not parts or carry != parts[-1]):
                parts.append(carry)
        leaf = parts[-1] if parts else ""
        paths.append(" / ".join(parts))
        labels.append(leaf or f"未命名列{column + 1}")
    seen: dict[str, int] = {}
    for index, label in enumerate(labels):
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            # 叶子名重复时退回完整路径（例如两组的同名字段），并保证唯一。
            label = paths[index] or label
            suffix = 2
            unique = label
            while unique in labels[:index]:
                unique = f"{label} ({suffix})"
                suffix += 1
            labels[index] = unique
    return labels, used


def _normalize_headers(raw_rows: list[list[Any]], header_rows: list[int], width: int) -> list[str]:
    """兼容旧接口：只取规范列名。"""
    return _resolve_headers(raw_rows, header_rows, width)[0]


def _name_set(value: Any) -> set[str]:
    """把列名参数归一化成集合：兼容 list/tuple、JSON 字符串、逗号分隔字符串。"""
    if value is None:
        return set()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return set()
        if text[:1] in "[{":
            try:
                return _name_set(json.loads(text))
            except json.JSONDecodeError:
                text = text.strip("[]{}")
        return {part.strip().strip("\"'") for part in re.split(r"[,;，；、]", text)
                if part.strip().strip("\"'")}
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item or "").strip()}
    return {str(value).strip()}


def _discard_artifact(path: Path, session_id: str, artifact_id: str) -> None:
    """S1：撤销一个已落盘/已入库的产物（文件 + artifacts 行 + 会话文件条目）。"""
    try:
        path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover
        logger.warning("Could not remove artifact file %s", path.name)
    try:
        with sqlite3.connect(str(_db_path())) as conn:
            conn.execute("DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,))
    except Exception:  # pragma: no cover
        logger.warning("Could not remove artifact row %s", artifact_id)
    try:
        from src.session import files as session_files
        session_files.remove_generated_entry(session_id, artifact_id)
    except Exception:  # pragma: no cover
        logger.warning("Could not remove session file entry %s", artifact_id)


def _artifact_row_by_name(db, session_id: str, filename: str, producer_tool: str = ""):
    """Latest artifact of this name; a producer filter avoids picking another tool's file."""
    try:
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            if producer_tool:
                return conn.execute(
                    "SELECT * FROM artifacts WHERE session_id=? AND filename=? AND producer_tool=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (session_id, filename, producer_tool)).fetchone()
            return conn.execute(
                "SELECT * FROM artifacts WHERE session_id=? AND filename=? "
                "ORDER BY created_at DESC LIMIT 1", (session_id, filename)).fetchone()
    except sqlite3.Error:
        return None


def _merge_artifact_bytes(path: Path, addition: bytes, mime: str) -> bytes:
    """Append new content to an existing deliverable instead of creating another file."""
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook
        keep_vba = suffix == ".xlsm"
        base = load_workbook(io.BytesIO(path.read_bytes()), keep_vba=keep_vba)
        extra = load_workbook(io.BytesIO(addition), keep_vba=keep_vba)
        try:
            if base.sheetnames != extra.sheetnames:
                raise DeliverableRejected("append_schema_mismatch", "追加工作簿的工作表与原文件不一致。")
            for target, src in zip(base.worksheets, extra.worksheets):
                if next(target.iter_rows(max_row=1, values_only=True), ()) != next(src.iter_rows(max_row=1, values_only=True), ()):
                    raise DeliverableRejected("append_schema_mismatch", "追加表头与原文件不一致。")
                for row in src.iter_rows(min_row=2, values_only=True):
                    if any(cell is not None and str(cell).strip() for cell in row):
                        target.append(list(row))
            out = io.BytesIO()
            base.save(out)
            return out.getvalue()
        finally:
            base.close()
            extra.close()
    if suffix not in {".html", ".htm", ".csv", ".tsv", ".txt", ".md", ".ris", ".bib"}:
        raise DeliverableRejected("append_unsupported", "此格式不支持文本追加；DOCX 请使用 word_document 分块编辑。")
    existing_text = path.read_text(encoding="utf-8-sig")
    new_text = addition.decode("utf-8-sig")
    if suffix in {".html", ".htm"}:
        body = re.search(r"<body\b[^>]*>([\s\S]*?)</body\s*>", new_text, re.I)
        if "<html" in new_text.lower():
            if not body:
                raise DeliverableRejected("append_invalid_html", "追加 HTML 缺少完整 body。")
            new_text = body.group(1)
        idx = existing_text.lower().rfind("</body>")
        if idx < 0:
            raise DeliverableRejected("append_invalid_html", "原 HTML 缺少 body 结束标签。")
        merged = existing_text[:idx] + new_text + existing_text[idx:]
    elif suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        original = list(csv.reader(io.StringIO(existing_text), delimiter=delimiter))
        incoming = list(csv.reader(io.StringIO(new_text), delimiter=delimiter))
        if not original or not incoming or original[0] != incoming[0]:
            raise DeliverableRejected("append_schema_mismatch", "追加表头与原文件不一致。")
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, delimiter=delimiter)
        writer.writerows(original + incoming[1:])
        merged = buffer.getvalue()
    else:
        merged = existing_text.rstrip() + "\n\n" + new_text
    return merged.encode("utf-8")


_ARTIFACT_PUBLISH_LOCK = threading.RLock()


def _save_artifact(filename: str, content: bytes, mime: str, *,
                   source_file_id: str = "", source_sha256: str = "",
                   logical_task_id: str = "",
                   replace_in_request: bool = False,
                   append: bool = False) -> str:
    """Serialize legacy append read/merge/publish; publish immutable versions.

    replace_in_request is retained as a compatibility argument. New bytes always
    get a new version; exact same-turn retries reuse the previous publication.
    """
    with _ARTIFACT_PUBLISH_LOCK:
        return _publish_artifact(
            filename, content, mime, source_file_id=source_file_id,
            source_sha256=source_sha256, logical_task_id=logical_task_id,
            replace_in_request=replace_in_request, append=append)


def _publish_artifact(filename: str, content: bytes, mime: str, *,
                      source_file_id: str = "", source_sha256: str = "",
                      logical_task_id: str = "",
                      replace_in_request: bool = False,
                      append: bool = False) -> str:
    """Publish one immutable artifact version.

    An exact retry (same source, tool, label and output bytes) reuses its row.
    A different source or different output always gets a new ``artifact_id``;
    historical download URLs therefore continue to return their original bytes.
    """
    from src.core.cancel import raise_if_cancelled
    from src.core.tracing import get_client_request_id, get_current_tool_name

    session_id = str(get_session_id() or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", session_id):
        raise ValueError("当前请求没有有效会话，无法保存下载文件")
    safe = _safe_name(filename)
    tool_name = str(get_current_tool_name() or "")
    request_id = str(get_client_request_id() or "")
    source_file_id = str(source_file_id or "")[:256]
    source_sha256 = str(source_sha256 or "")
    db = _db_path()

    append_key = ""
    if append:
        # Complete inputs only. In particular a half-HTML or DOCX-as-text must
        # never bypass validation through the legacy append branch.
        validate_deliverable(filename=safe, content=content, mime=mime, strict=False)
        incoming_sha256 = hashlib.sha256(content).hexdigest()
        append_key = _deliverable_dedupe_key(
            session_id=session_id, tool_name=tool_name, filename=safe,
            source_key=source_file_id, source_sha256=source_sha256,
            output_sha256=incoming_sha256, request_id=request_id)
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("CREATE TABLE IF NOT EXISTS artifact_append_batches "
                         "(batch_key TEXT PRIMARY KEY, artifact_id TEXT NOT NULL)")
            row = conn.execute(
                "SELECT a.* FROM artifact_append_batches b JOIN artifacts a "
                "ON a.artifact_id=b.artifact_id WHERE b.batch_key=?",
                (append_key,)).fetchone()
        if row:
            # Recover the exact committed bytes, then use the standard path to
            # verify/register again. A registration failure is recoverable.
            prior_path = Path(str(row["path"])).resolve()
            root_dir = (PROJECT_ROOT / "state" / "artifacts" / session_id).resolve()
            if (not prior_path.is_relative_to(root_dir) or not prior_path.is_file()
                    or _sha256_file(prior_path) != row["artifact_sha256"]):
                raise DeliverableRejected("append_checkpoint_invalid", "已提交追加批次的文件不可用。")
            content = prior_path.read_bytes()
        else:
            prior = _artifact_row_by_name(db, session_id, safe, tool_name)
            if prior is None and _artifact_row_by_name(db, session_id, safe) is not None:
                raise DeliverableRejected(
                    "append_source_mismatch",
                    "同名文件由其他工具生成，无法追加；请改用不同文件名或对应工具续写。")
            if prior is not None:
                prior_path = Path(str(prior["path"] or "")).resolve()
                root_dir = (PROJECT_ROOT / "state" / "artifacts" / session_id).resolve()
                if not prior_path.is_relative_to(root_dir) or not prior_path.is_file():
                    raise DeliverableRejected("append_source_invalid", "追加源文件不可用。")
                if str(prior["mime"]) != mime or str(prior["producer_tool"]) != tool_name:
                    raise DeliverableRejected("append_source_mismatch", "同名文件类型或生产工具不一致，无法追加。")
                content = _merge_artifact_bytes(prior_path, content, mime)

    # OOXML containers carry the wall clock in every ZIP entry and in
    # docProps/core.xml. Fixing them here — after any merge, before hashing —
    # is what lets an exact re-export reuse its row instead of publishing a
    # duplicate version. Different content still gets a new artifact_id.
    if is_ooxml_filename(safe):
        content = normalize_ooxml_bytes(content)
    output_sha256 = hashlib.sha256(content).hexdigest()

    ledger = RetryLedger(db)
    logical = _deliverable_logical_key(
        session_id=session_id, request_id=request_id or logical_task_id,
        tool_name=tool_name, filename=safe)
    ledger.begin(logical)
    validate_deliverable(filename=safe, content=content, mime=mime, strict=False)

    key = _deliverable_dedupe_key(
        session_id=session_id, tool_name=tool_name, filename=safe,
        source_key=source_file_id, source_sha256=source_sha256,
        output_sha256=output_sha256, request_id=request_id)
    existing: sqlite3.Row | None = None
    try:
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT * FROM artifacts WHERE session_id=? AND dedupe_key=? "
                "ORDER BY created_at DESC LIMIT 1", (session_id, key)).fetchone()
    except sqlite3.Error:
        logger.warning("Artifact exact-dedupe lookup failed", exc_info=True)

    root = (PROJECT_ROOT / "state" / "artifacts" / session_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if existing is not None:
        try:
            existing_path = Path(str(existing["path"] or "")).resolve()
            exact_existing = (
                existing_path.is_file()
                and existing_path.is_relative_to(root)
                and _sha256_file(existing_path) == output_sha256
            )
        except (OSError, ValueError):
            exact_existing = False
        if not exact_existing:
            existing = None

    if existing is not None:
        artifact_id = str(existing["artifact_id"])
        path = Path(str(existing["path"])).resolve()
    else:
        raise_if_cancelled("artifact:before_write")
        artifact_id = uuid.uuid4().hex
        logical_artifact_id = artifact_id
        supersedes = ""
        version = 1
        path = (root / f"{artifact_id}_{safe}").resolve()
        if not path.is_relative_to(root):
            raise ValueError("无效的导出文件名")

        part = path.with_name(path.name + ".part")
        try:
            with open(part, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            raise_if_cancelled("artifact:before_commit")
            os.replace(part, path)
        except BaseException:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        written_path = path

        now = time.time()
        published_existing: sqlite3.Row | None = None
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db), isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            # Two identical requests can both pass the pre-write lookup. The
            # write transaction performs a second lookup so only one row and
            # one version are published; the loser's temporary file is removed.
            published_existing = conn.execute(
                "SELECT * FROM artifacts WHERE session_id=? AND dedupe_key=? "
                "ORDER BY created_at DESC LIMIT 1", (session_id, key)).fetchone()
            if published_existing is not None:
                try:
                    published_path = Path(str(published_existing["path"] or "")).resolve()
                    exact_published = (
                        published_path.is_file()
                        and published_path.is_relative_to(root)
                        and _sha256_file(published_path) == output_sha256
                    )
                except (OSError, ValueError):
                    exact_published = False
                if not exact_published:
                    published_existing = None
            if published_existing is None:
                prior = conn.execute(
                    "SELECT artifact_id, logical_artifact_id, "
                    "COALESCE(version,1) AS version FROM artifacts "
                    "WHERE session_id=? AND producer_tool=? AND filename=? "
                    "AND source_file_id=? AND source_sha256=? "
                    "ORDER BY COALESCE(version,1) DESC, created_at DESC LIMIT 1",
                    (session_id, tool_name, safe, source_file_id, source_sha256),
                ).fetchone()
                if prior is not None:
                    logical_artifact_id = str(
                        prior["logical_artifact_id"] or logical_artifact_id)
                    supersedes = str(prior["artifact_id"])
                    version = int(prior["version"]) + 1
                conn.execute(
                    "INSERT INTO artifacts("
                    "artifact_id, session_id, filename, mime, created_at, path, dedupe_key, "
                    "logical_artifact_id, version, source_file_id, source_sha256, "
                    "artifact_sha256, producer_tool, logical_task_id, request_id, "
                    "tool_call_id, supersedes_artifact_id"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (artifact_id, session_id, safe, mime, now, str(path), key,
                     logical_artifact_id, version, source_file_id, source_sha256,
                     output_sha256, tool_name, str(logical_task_id or "")[:128],
                     request_id[:128], "", supersedes),
                )
            conn.execute("COMMIT")
            conn.close()
            conn = None
            if published_existing is not None:
                artifact_id = str(published_existing["artifact_id"])
                path = Path(str(published_existing["path"])).resolve()
                # The newly written candidate becomes redundant once another
                # exact request has committed the same logical output.
                try:
                    written_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove superseded concurrent artifact bytes")
        except BaseException:
            if conn is not None:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            _discard_artifact(path, session_id, artifact_id)
            raise

    if append_key:
        with sqlite3.connect(str(db)) as conn:
            conn.execute("INSERT OR REPLACE INTO artifact_append_batches VALUES(?,?)",
                         (append_key, artifact_id))
    try:
        from src.session import files as session_files
        session_files.init_store()
        registered = session_files.register_file(
            session_id=session_id, owner_user_id=int(get_user_id() or 0),
            filename=safe, storage_path=str(path),
            mime=mime, origin=session_files.ORIGIN_GENERATED,
            artifact_id=artifact_id,
            client_request_id=request_id,
            producer_tool=tool_name, dedupe_key=key)
        if (not registered or not registered.get("file_id")
                or registered.get("sha256") != output_sha256
                or int(registered.get("size_bytes", -1)) != len(content)):
            raise ValueError("资料库登记结果与产物字节不一致")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not register artifact in session files: %s", exc)
        # Keep the verified bytes/index so a retry can finish registration.
        ledger.clear(logical)
        raise DeliverableRejected("artifact_registration_failed", "文件已保存，但资料库登记失败；可重试发布。") from exc
    ledger.clear(logical)
    return f"/api/v2/session/{session_id}/artifacts/{artifact_id}"


def resolve_artifact(session_id: str, artifact_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", str(session_id or "")) or not re.fullmatch(
            r"[a-f0-9]{32}", str(artifact_id or "")):
        return None
    now = time.time()
    expired_path: str | None = None
    with sqlite3.connect(str(_db_path())) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM artifacts WHERE artifact_id=? AND session_id=?",
                           (artifact_id, session_id)).fetchone()
        if not row:
            return None
        last_active = _session_last_activity(session_id) or float(row["created_at"])
        if now - last_active > _ARTIFACT_TTL_SEC:
            conn.execute("DELETE FROM artifacts WHERE artifact_id=?", (artifact_id,))
            expired_path = str(row["path"])
    if expired_path is not None:
        try:
            root = (PROJECT_ROOT / "state" / "artifacts" / session_id).resolve()
            candidate = Path(expired_path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                candidate.unlink(missing_ok=True)
            else:
                logger.warning("Expired artifact path is outside its session root; left untouched")
        except OSError:
            pass
        try:
            from src.session.files import remove_generated_entry
            remove_generated_entry(session_id, artifact_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not remove expired artifact panel entry: %s", exc)
        return None
    root = (PROJECT_ROOT / "state" / "artifacts" / session_id).resolve()
    path = Path(row["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        with sqlite3.connect(str(_db_path())) as conn:
            conn.execute("DELETE FROM artifacts WHERE artifact_id=? AND session_id=?",
                         (artifact_id, session_id))
        try:
            from src.session.files import remove_generated_entry
            remove_generated_entry(session_id, artifact_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not remove invalid artifact panel entry: %s", exc)
        return None
    expected_sha = str(row["artifact_sha256"] or "") if "artifact_sha256" in row.keys() else ""
    if expected_sha:
        try:
            actual_sha = _sha256_file(path)
        except OSError:
            actual_sha = ""
        if actual_sha != expected_sha:
            logger.error("Artifact integrity check failed id=%s", artifact_id)
            return None
    return {"path": path, "filename": row["filename"], "mime": row["mime"]}


def validate_artifact_links(
    links: Iterable[str],
    *,
    session_id: str = "",
    owner_user_id: int | None = None,
    request_id: str = "",
) -> list[dict[str, Any]]:
    """Validate generated-artifact URLs against the registry, ownership, disk and SHA.

    Model-authored Markdown links never establish delivery by themselves. Only a
    registered row in the current session with the expected owner and matching
    bytes can become a verified artifact.
    """
    expected_session = str(session_id or get_session_id() or "")
    expected_request_id = str(request_id or "")[:128]
    owner = int(owner_user_id) if owner_user_id is not None else get_user_id()
    root = (PROJECT_ROOT / "state" / "artifacts" / expected_session).resolve()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"^/api/v2/session/([A-Za-z0-9_-]{8,128})/artifacts/([a-f0-9]{32})$")
    db = _db_path()
    try:
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            for raw in links:
                link = str(raw or "").strip()
                if link in seen:
                    continue
                seen.add(link)
                match = pattern.fullmatch(link)
                artifact_id = match.group(2) if match else (
                    link if re.fullmatch(r"[a-f0-9]{32}", link) else "")
                linked_session = match.group(1) if match else expected_session
                base = {
                    "link": link,
                    "artifact_id": artifact_id,
                    "filename": "",
                    "mime": "",
                    "producer_tool": "",
                    "request_id": "",
                    "request_matches": not expected_request_id,
                    "valid": False,
                    "exists": False,
                    "owner_matches": False,
                    "hash_matches": False,
                    "format_matches": False,
                    "reason_code": "",
                }
                if not artifact_id:
                    base["reason_code"] = "invalid_artifact_url"
                    results.append(base)
                    continue
                if linked_session != expected_session:
                    base["reason_code"] = "session_mismatch"
                    results.append(base)
                    continue
                row = conn.execute(
                    "SELECT * FROM artifacts WHERE artifact_id=? AND session_id=?",
                    (artifact_id, expected_session)).fetchone()
                if row is None:
                    base["reason_code"] = "not_registered"
                    results.append(base)
                    continue
                base["filename"] = str(row["filename"] or "")
                base["mime"] = str(row["mime"] or "")
                base["producer_tool"] = str(row["producer_tool"] or "")
                base["request_id"] = str(row["request_id"] or "")
                base["request_matches"] = (
                    not expected_request_id or base["request_id"] == expected_request_id
                )
                if not base["request_matches"]:
                    base["reason_code"] = "request_mismatch"
                    results.append(base)
                    continue
                try:
                    path = Path(str(row["path"] or "")).resolve()
                    base["exists"] = path.is_relative_to(root) and path.is_file()
                except (OSError, ValueError):
                    path = None
                    base["exists"] = False
                if owner is not None:
                    try:
                        from src.session.manager import session_manager
                        actual_owner = session_manager.get_session_owner(expected_session)
                        base["owner_matches"] = actual_owner == int(owner)
                    except Exception:
                        logger.warning("Could not verify artifact owner", exc_info=True)
                if base["exists"] and path is not None:
                    base["format_matches"] = _artifact_format_matches(
                        path, str(row["filename"] or ""), str(row["mime"] or ""))
                    expected_sha = (
                        str(row["artifact_sha256"] or "").strip()
                        if "artifact_sha256" in row.keys() else ""
                    )
                    if expected_sha:
                        try:
                            actual_sha = _sha256_file(path)
                        except OSError:
                            actual_sha = ""
                        base["hash_matches"] = actual_sha == expected_sha
                    else:
                        # Legacy rows had no stored hash. They remain readable
                        # after the path/ownership checks, but are labelled so
                        # callers can distinguish a fully verified new version.
                        base["hash_matches"] = True
                        base["reason_code"] = "legacy_without_hash"
                if not base["reason_code"]:
                    if not base["exists"]:
                        base["reason_code"] = "file_missing"
                    elif owner is not None and not base["owner_matches"]:
                        base["reason_code"] = "owner_mismatch"
                    elif not base["hash_matches"]:
                        base["reason_code"] = "hash_mismatch"
                    elif not base["format_matches"]:
                        base["reason_code"] = "format_mismatch"
                    else:
                        base["reason_code"] = "verified"
                base["valid"] = (
                    base["exists"] and base["hash_matches"] and base["format_matches"]
                    and base["request_matches"]
                    and (owner is None or base["owner_matches"])
                )
                results.append(base)
    except sqlite3.Error:
        logger.warning("Artifact validation store unavailable", exc_info=True)
        for raw in links:
            link = str(raw or "").strip()
            if link in seen:
                continue
            seen.add(link)
            match = pattern.fullmatch(link)
            results.append({
                "link": link,
                "artifact_id": match.group(2) if match else "",
                "filename": "", "mime": "", "producer_tool": "",
                "request_id": "", "request_matches": False,
                "valid": False, "exists": False, "owner_matches": False,
                "hash_matches": False, "format_matches": False,
                "reason_code": "artifact_store_unavailable",
            })
    return results


def cleanup_expired_artifacts() -> int:
    """Remove artifacts whose owning session has been idle for 30 days."""
    now = time.time()
    db = _db_path()
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute(
            "SELECT artifact_id,session_id,path,created_at FROM artifacts").fetchall()
    removed = 0
    for artifact_id, session_id, raw_path, created_at in rows:
        last_active = _session_last_activity(str(session_id)) or float(created_at)
        if now - last_active <= _ARTIFACT_TTL_SEC:
            continue
        with sqlite3.connect(str(db)) as conn:
            conn.execute("DELETE FROM artifacts WHERE artifact_id=? AND session_id=?",
                         (artifact_id, session_id))
        try:
            root = (PROJECT_ROOT / "state" / "artifacts" / session_id).resolve()
            path = Path(raw_path).resolve()
            if path.is_relative_to(root) and path.is_file():
                path.unlink()
                removed += 1
        except (OSError, ValueError):
            logger.warning("Could not clean expired artifact id=%s", artifact_id)
        try:
            from src.session.files import remove_generated_entry
            remove_generated_entry(str(session_id), str(artifact_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not remove expired artifact panel entry: %s", exc)
    return removed


def _session_last_activity(session_id: str) -> float | None:
    """Consult the shared session/file activity source for artifact retention."""
    try:
        from src.session.files import session_last_activity
        return session_last_activity(session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read session activity for artifact retention: %s", exc)
        return None


def _attachment_choices(tables) -> str:
    """列出合法序号与文件名，让模型能自己改对参数而不是再猜一次。"""
    return "；".join(
        f"{index}={str(item.get('name') or '未命名附件')}"
        for index, item in enumerate(tables))


def _missing_sheet_message(requested: str, available, *, kind: str = "工作表") -> str:
    """错误必须带上可用名称：PDF/Word 的表标签是合成的，模型无从猜起。"""
    names = [str(item) for item in (available or ())]
    listing = "、".join(names[:40]) + ("…" if len(names) > 40 else "")
    return (f"{kind}不存在：{requested}；本文件可用{kind}（共 {len(names)} 个）："
            f"{listing or '（无）'}。请从上述名称中选择，或先调用 "
            "inspect_uploaded_table 查看结构。")


def _attachment(file_index: int) -> dict[str, Any]:
    tables = get_request_tables()
    if not tables:
        raise ValueError("本轮没有可审查的表格或论文附件")
    if file_index < 0 or file_index >= len(tables):
        raise ValueError(
            f"审查文件序号无效：{file_index}；本轮共有 {len(tables)} 个审查附件，"
            f"可用序号（从 0 开始）：{_attachment_choices(tables)}")
    return tables[file_index]


def _decode_csv(data: bytes, header_row: int = 1, header_rows: list[int] | None = None):
    text = None
    encoding = ""
    for candidate in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            text = data.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("CSV 编码无法识别（尝试 UTF-8 与常见中文编码均失败）")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    header_rows = sorted(set(header_rows or [header_row]))
    if not header_rows or any(row < 1 or row > 20 for row in header_rows):
        raise ValueError("header_row 必须在 1 到 20 之间")
    row_limit = _MAX_TABLE_ROWS + max(header_rows) + 1
    raw_rows = []
    raw_cells = 0
    truncated = False
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        if len(raw_rows) >= row_limit:
            truncated = True
            break
        if len(row) > _MAX_TABLE_COLUMNS:
            raise ValueError(f"列数超过限制 {_MAX_TABLE_COLUMNS}")
        if raw_cells + len(row) > _MAX_TABLE_CELLS:
            truncated = True
            break
        raw_rows.append(row)
        raw_cells += len(row)
    raw_preview = raw_rows[:8]
    if not raw_rows:
        return _pd().DataFrame(), encoding, raw_preview, header_rows, False
    header, header_rows = _resolve_headers(raw_rows, header_rows, max(len(row) for row in raw_rows))
    body_start = max(header_rows)
    width = len(header)
    if width > _MAX_TABLE_COLUMNS:
        raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
    data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, width))
    body = [(row + [""] * width)[:width]
            for row in raw_rows[body_start:body_start + data_row_limit]]
    frame = _pd().DataFrame(body, columns=header)
    frame = frame.replace(r"^\s*$", _pd().NA, regex=True)
    # Keep literal CSV values (including zero-padded IDs) intact. Numeric
    # calculations use temporary coercions and the raw source is never rewritten.
    truncated = truncated or len(raw_rows) > body_start + data_row_limit
    return frame, encoding, raw_preview, header_rows, truncated


def _pd():
    import pandas as pd
    return pd


def _bound_raw_table(rows: list[list[Any]], header_rows: list[int],
                     cell_budget: int, label: str) -> tuple[list[list[Any]], bool, int]:
    width = max((len(row) for row in rows), default=0)
    if width > _MAX_TABLE_COLUMNS:
        raise ValueError(f"{label}列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}，未执行审查")
    if width <= 0:
        return [], len(rows) > 0, 0
    header_limit = max(header_rows)
    row_limit = min(_MAX_TABLE_ROWS + header_limit, header_limit + max(1, cell_budget // width))
    bounded = rows[:row_limit]
    return bounded, len(rows) > row_limit, len(bounded) * width


def _load_frame(file_index: int, sheet_name: str = "", header_row: int = 1,
                header_rows: list[int] | None = None,
                discover_only: bool = False) -> tuple[Any, dict[str, Any]]:
    """Read one of this turn's uploaded tables into a DataFrame.

    The bytes are handed to :func:`_load_frame_from_bytes`, which holds the
    whole parsing path — CSV encoding sniffing, XLSX read-only loading, the
    PDF table extraction and every size budget. Splitting it this way lets
    the figure tools read an account-library file through exactly the same
    parser, with exactly the same limits, instead of growing a second one.
    """
    item = _attachment(file_index)
    name = str(item.get("name") or "table")
    data = item.get("data") or b""
    return _load_frame_from_bytes(data, name, sheet_name=sheet_name,
                                  header_row=header_row, header_rows=header_rows,
                                  discover_only=discover_only)


def _load_frame_from_bytes(data: bytes, name: str = "table", sheet_name: str = "",
                           header_row: int = 1, header_rows: list[int] | None = None,
                           discover_only: bool = False) -> tuple[Any, dict[str, Any]]:
    """Parse table bytes into a DataFrame under the service's read budgets."""
    suffix = Path(name).suffix.lower()
    if suffix in {".csv", ".tsv"}:
        frame, encoding, raw_preview, used_header_rows, truncated = _decode_csv(data, header_row, header_rows)
        meta = {"name": name, "sheet": "", "encoding": encoding, "delimiter": "auto",
                "raw_preview": raw_preview, "header_row": max(used_header_rows),
                "header_rows": used_header_rows, "truncated": truncated,
                "source_rows": int(frame.shape[0]), "source_columns": int(frame.shape[1]),
                "processed_rows": int(frame.shape[0]),
                "processed_columns": int(frame.shape[1]),
                "truncation_reasons": (["CSV 行数或单元格数超过安全解析预算"]
                                       if truncated else [])}
    elif suffix in {".xlsx", ".xlsm", ".xls", ".xlsb"}:
        try:
            used_header_rows = sorted(set(header_rows or [header_row]))
            if not used_header_rows or any(row < 1 or row > 20 for row in used_header_rows):
                raise ValueError("header_row 必须在 1 到 20 之间")
            if suffix in {".xls", ".xlsb"}:
                try:
                    import python_calamine
                    reader = python_calamine.CalamineWorkbook.from_filelike(io.BytesIO(data))
                    names = reader.sheet_names
                    requested_sheet = sheet_name.strip()
                    if requested_sheet and requested_sheet not in names:
                        raise ValueError(_missing_sheet_message(requested_sheet, names))
                    chosen = requested_sheet or (names[0] if names else "")
                    if not chosen:
                        raise ValueError("工作簿中没有可读取的工作表")
                    sheet = reader.get_sheet_by_name(chosen)
                    width = getattr(sheet, "width", None)
                    height = getattr(sheet, "height", None)
                    width = width() if callable(width) else width
                    height = height() if callable(height) else height
                    if not isinstance(width, int) or not isinstance(height, int):
                        raise ValueError("无法在读取前确认工作表尺寸；为避免无界内存占用，已停止解析")
                    if width > _MAX_TABLE_COLUMNS:
                        raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
                    row_limit = max(1, min(_MAX_TABLE_ROWS + max(used_header_rows) + 1,
                                           _MAX_TABLE_CELLS // max(1, width) + max(used_header_rows)))
                    if height > row_limit:
                        raise ValueError(
                            f"工作表有 {height:,} 行，当前 XLS/XLSB 安全解析上限为 {row_limit:,} 行；"
                            "请先拆分工作表后重试，未对该表执行审查")
                    data_rows = sheet.to_python()
                    if len(data_rows) > row_limit:
                        raise ValueError("工作表实际行数超出安全解析范围，未执行审查")
                except ImportError as exc:
                    raise ValueError(f"读取 {suffix.upper()} 需要安装 python-calamine；文件未被判定为无异常") from exc
            else:
                import openpyxl
                from zipfile import ZipFile
                from src.core.file_safety import validate_zip_bytes
                validate_zip_bytes(data, label=name)
                with ZipFile(io.BytesIO(data)) as archive:
                    if "xl/workbook.xml" not in archive.namelist():
                        raise ValueError("工作簿缺少必要的 OOXML 结构")
                wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True,
                                            keep_vba=False)
                names = wb.sheetnames
                requested_sheet = sheet_name.strip()
                if requested_sheet and requested_sheet not in names:
                    wb.close()
                    raise ValueError(_missing_sheet_message(requested_sheet, names))
                chosen = requested_sheet or next(
                    (s for s in names if wb[s].max_row and wb[s].max_column),
                    names[0] if names else "")
                if not chosen:
                    wb.close()
                    raise ValueError("工作簿中没有可读取的工作表")
                worksheet = wb[chosen]
                width = int(worksheet.max_column or 0)
                height = int(worksheet.max_row or 0)
                if width > _MAX_TABLE_COLUMNS:
                    wb.close()
                    raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
                header_span = max(used_header_rows)
                if width and height:
                    row_limit = max(1, min(_MAX_TABLE_ROWS + header_span + 1,
                                           _MAX_TABLE_CELLS // max(1, width) + header_span))
                    data_rows = list(worksheet.iter_rows(
                        min_row=1, max_row=min(height, row_limit), max_col=width,
                        values_only=True))
                else:
                    # 有些工作簿没有写 sheet dimension，read_only 模式下 max_row/max_column 为 None；
                    # 此时按有界惰性遍历读取真实内容，再按实际行列重算尺寸，避免把整张表判定为空。
                    row_limit = max(1, _MAX_TABLE_ROWS + header_span + 1)
                    data_rows = []
                    for row in worksheet.iter_rows(values_only=True):
                        data_rows.append(row if isinstance(row, tuple) else tuple(row or ()))
                        if len(data_rows) >= row_limit:
                            break
                    while data_rows and not any(
                            cell is not None and str(cell).strip() for cell in data_rows[-1]):
                        data_rows.pop()
                    width = max((len(row) for row in data_rows), default=0)
                    height = len(data_rows)
                    if width > _MAX_TABLE_COLUMNS:
                        wb.close()
                        raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
                wb.close()
            data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, width))
            if len(data_rows) < max(used_header_rows):
                frame = _pd().DataFrame()
            else:
                width = max((len(row) for row in data_rows), default=0)
                if width > _MAX_TABLE_COLUMNS:
                    raise ValueError(f"列数 {width} 超过限制 {_MAX_TABLE_COLUMNS}")
                header, used_header_rows = _resolve_headers(data_rows, used_header_rows, width)
                data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, len(header)))
                frame = _pd().DataFrame(
                    data_rows[max(used_header_rows):max(used_header_rows) + data_row_limit],
                    columns=header)
            meta = {"name": name, "sheet": chosen, "sheets": names, "encoding": suffix[1:],
                    "header_row": max(used_header_rows), "header_rows": used_header_rows,
                    "raw_preview": [[str(v)[:120] if v is not None else "" for v in row]
                                    for row in data_rows[:8]],
                    "source_rows": max(0, int(height) - max(used_header_rows)),
                    "source_columns": int(width),
                    "processed_rows": int(frame.shape[0]),
                    "processed_columns": int(frame.shape[1]),
                    "truncation_reasons": ([
                        f"工作表源数据行数 {max(0, height - max(used_header_rows)):,} "
                        "超过单次审查行数或单元格预算"
                    ] if max(0, height - max(used_header_rows)) > frame.shape[0] else [])}
            meta["truncated"] = (height > row_limit) or (
                len(data_rows) > max(used_header_rows) + data_row_limit)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"无法读取工作簿：{exc}") from exc
    elif suffix == ".pdf":
        try:
            import pdfplumber
            all_tables = []
            cell_budget = _MAX_TABLE_CELLS
            extraction_truncated = False
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                pages = pdf.pages[:200]
                extraction_truncated = len(pdf.pages) > len(pages)
                for page_no, page in enumerate(pages, start=1):
                    for table_no, rows in enumerate(page.extract_tables() or [], start=1):
                        if rows and any(any(str(cell or "").strip() for cell in row) for row in rows):
                            if len(all_tables) >= _MAX_EXTRACTED_TABLES or cell_budget <= 0:
                                extraction_truncated = True
                                break
                            label = f"{name}!p{page_no}_t{table_no}"
                            bounded_rows, clipped, used_cells = _bound_raw_table(
                                rows, sorted(set(header_rows or [header_row])), cell_budget, label)
                            all_tables.append((label, bounded_rows))
                            cell_budget -= used_cells
                            extraction_truncated = extraction_truncated or clipped
                    if extraction_truncated and (len(all_tables) >= _MAX_EXTRACTED_TABLES or cell_budget <= 0):
                        break
            if not all_tables:
                raise ValueError("PDF 中没有可提取的结构化表格；扫描件/OCR 未覆盖，审查未完成")
            if sheet_name.strip():
                matching = next(((label, rows) for label, rows in all_tables
                                 if label == sheet_name.strip()), None)
                if matching is None:
                    raise ValueError(_missing_sheet_message(
                        sheet_name.strip(), [label for label, _rows in all_tables],
                        kind="PDF 表格"))
                chosen, rows = matching
            elif len(all_tables) > 1 and not discover_only:
                raise ValueError(
                    "PDF 提取到多个表格，请用 sheet_name 指定目标（例如 "
                    f"{all_tables[0][0]}）；未默认选择第一张表")
            else:
                chosen, rows = all_tables[0]
            used_header_rows = sorted(set(header_rows or [header_row]))
            if any(row < 1 or row > 20 for row in used_header_rows):
                raise ValueError("header_row 必须在 1 到 20 之间")
            width = max((len(row) for row in rows), default=0)
            header, used_header_rows = _resolve_headers(rows, used_header_rows, width)
            data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, width))
            frame = _pd().DataFrame(
                [(row + [None] * width)[:width]
                 for row in rows[max(used_header_rows):max(used_header_rows) + data_row_limit]],
                columns=header)
            meta = {"name": name, "sheet": chosen, "sheets": [label for label, _ in all_tables],
                    "encoding": "pdf-table", "header_row": max(used_header_rows),
                    "header_rows": used_header_rows, "raw_preview": rows[:8],
                    "source_rows": max(0, len(rows) - max(used_header_rows)),
                    "source_columns": int(width),
                    "processed_rows": int(frame.shape[0]),
                    "processed_columns": int(frame.shape[1]),
                    "truncated": extraction_truncated or
                                 len(rows) > max(used_header_rows) + data_row_limit,
                    "truncation_reasons": (
                        ["PDF 页数、表格数、行数或单元格数超过提取预算"]
                        if extraction_truncated or
                        len(rows) > max(used_header_rows) + data_row_limit else [])}
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"无法提取 PDF 表格：{exc}") from exc
    elif suffix == ".docx":
        try:
            from src.core.file_safety import validate_zip_bytes
            validate_zip_bytes(data, label=name)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                document_info = archive.getinfo("word/document.xml")
                if document_info.file_size > 16 * 1024 * 1024:
                    raise ValueError("Word 正文 XML 超过 16 MB，已停止解析")
                root = ET.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            word_tables = []
            cell_budget = _MAX_TABLE_CELLS
            extraction_truncated = False
            for number, table in enumerate(root.findall(".//w:tbl", ns), start=1):
                rows = []
                for row in table.findall("./w:tr", ns):
                    cells = row.findall("./w:tc", ns)
                    if len(cells) > _MAX_TABLE_COLUMNS:
                        raise ValueError(f"Word 表格 {number} 列数超过限制 {_MAX_TABLE_COLUMNS}，未执行审查")
                    rows.append(["".join(t.text or "" for t in cell.findall(".//w:t", ns))
                                 for cell in cells])
                    if sum(len(item) for item in rows) > _MAX_TABLE_CELLS:
                        extraction_truncated = True
                        break
                if rows:
                    if len(word_tables) >= _MAX_EXTRACTED_TABLES or cell_budget <= 0:
                        extraction_truncated = True
                        break
                    label = f"{name}!t{number}"
                    bounded_rows, clipped, used_cells = _bound_raw_table(
                        rows, sorted(set(header_rows or [header_row])), cell_budget, label)
                    word_tables.append((label, bounded_rows))
                    cell_budget -= used_cells
                    extraction_truncated = extraction_truncated or clipped
                    if cell_budget <= 0:
                        extraction_truncated = True
                        break
            if not word_tables:
                raise ValueError("Word 中没有可提取的结构化表格，审查未完成")
            if sheet_name.strip():
                matching = next(((label, rows) for label, rows in word_tables
                                 if label == sheet_name.strip()), None)
                if matching is None:
                    raise ValueError(_missing_sheet_message(
                        sheet_name.strip(), [label for label, _rows in word_tables],
                        kind="Word 表格"))
                chosen, rows = matching
            elif len(word_tables) > 1 and not discover_only:
                raise ValueError(
                    "Word 提取到多个表格，请用 sheet_name 指定目标（例如 "
                    f"{word_tables[0][0]}）；未默认选择第一张表")
            else:
                chosen, rows = word_tables[0]
            used_header_rows = sorted(set(header_rows or [header_row]))
            width = max((len(row) for row in rows), default=0)
            header, used_header_rows = _resolve_headers(rows, used_header_rows, width)
            data_row_limit = min(_MAX_TABLE_ROWS, _MAX_TABLE_CELLS // max(1, width))
            frame = _pd().DataFrame(
                [(row + [None] * width)[:width]
                 for row in rows[max(used_header_rows):max(used_header_rows) + data_row_limit]],
                columns=header)
            meta = {"name": name, "sheet": chosen, "sheets": [label for label, _ in word_tables],
                    "encoding": "docx-table", "header_row": max(used_header_rows),
                    "header_rows": used_header_rows, "raw_preview": rows[:8],
                    "source_rows": max(0, len(rows) - max(used_header_rows)),
                    "source_columns": int(width),
                    "processed_rows": int(frame.shape[0]),
                    "processed_columns": int(frame.shape[1]),
                    "truncated": extraction_truncated or
                                 len(rows) > max(used_header_rows) + data_row_limit,
                    "truncation_reasons": (
                        ["Word 表格数、行数或单元格数超过提取预算"]
                        if extraction_truncated or
                        len(rows) > max(used_header_rows) + data_row_limit else [])}
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"无法提取 Word 表格：{exc}") from exc
    else:
        raise ValueError("当前审查支持 CSV/TSV/XLSX/XLS/XLSM/PDF/DOCX")
    if frame.shape[1] > _MAX_TABLE_COLUMNS:
        raise ValueError(f"列数 {frame.shape[1]} 超过限制 {_MAX_TABLE_COLUMNS}")
    if frame.shape[0] > _MAX_TABLE_ROWS:
        frame = frame.iloc[:_MAX_TABLE_ROWS].copy()
        meta["truncated"] = True
    else:
        meta["truncated"] = bool(meta.get("truncated", False))
    if meta["truncated"] and not meta.get("truncation_reasons"):
        meta["truncation_reasons"] = ["输入行数或单元格数超过单次审查预算"]
    return frame, meta


def _json_arg(raw: Any, expected: type | tuple[type, ...], default, *,
              shape: str = ""):
    if isinstance(raw, expected):
        return raw
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    expected_name = "/".join(t.__name__ for t in expected) if isinstance(expected, tuple) else expected.__name__
    hint = f"，期望形状：{shape}" if shape else ""

    def _wrong_type(value) -> ToolArgumentError:
        # "参数需要 JSON list 格式" alone leaves the model guessing. Say what
        # arrived and what one element must look like.
        detail = type(value).__name__
        if isinstance(value, dict):
            detail += f"（键：{sorted(str(k) for k in value)[:8]}）"
        elif isinstance(value, list) and value:
            detail += f"（首元素 {type(value[0]).__name__}）"
        return ToolArgumentError(
            f"参数需要 JSON {expected_name} 格式，实际收到 {detail}{hint}")

    if not isinstance(raw, str):
        raise _wrong_type(raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Closing a truncated string invents valid-looking identifiers, DOI values
        # and scientific parameters. Keep the last committed batch intact and ask
        # for a smaller, complete call rather than publishing repaired data.
        raise ToolArgumentError(
            f"参数 JSON 不完整（{exc.msg}，位置 {exc.pos}，共 {len(raw)} 字符），"
            f"通常是被单次输出长度上限截断。本批未写入；请精简该参数、减少单次条数"
            f"（例如每批不超过 20 条），或改用 source=last_verification 让服务"
            f"端直接取上一步的核验结果，不要再手写整份清单。") from None
    if not isinstance(value, expected):
        raise _wrong_type(value)
    return value


def _parse_header_rows(raw: str, default_header_row: int) -> list[int]:
    rows = _json_arg(raw, list, None,
                     shape="扁平整数数组 [1,2]，不是按工作表名分组的对象") if raw else None
    if rows is None:
        rows = [default_header_row]
    if not rows or any(type(row) is not int for row in rows):
        raise ValueError(
            "表头行参数必须是非空整数数组，例如 [1,2]；"
            f"实际收到 {json.dumps(rows, ensure_ascii=False)[:120]}")
    return sorted(set(rows))


def _profile(frame) -> dict[str, Any]:
    result = {"rows": int(frame.shape[0]), "columns": int(frame.shape[1]), "fields": []}
    if frame.empty or frame.shape[1] == 0:
        return result
    for col in frame.columns:
        series = frame[col]
        nonempty = series.dropna()
        field: dict[str, Any] = {
            "name": str(col), "inferred_type": str(series.dtype),
            "missing_count": int(series.isna().sum()),
            "missing_fraction": round(float(series.isna().mean()), 4),
            "unique_count": int(series.nunique(dropna=True)),
            "samples": [str(x)[:100] for x in nonempty.drop_duplicates().head(_MAX_SAMPLES).tolist()],
        }
        numeric = _pd().to_numeric(series, errors="coerce").dropna()
        source_nonempty = int(series.notna().sum())
        if len(numeric):
            field.update(numeric_count=int(len(numeric)), min=float(numeric.min()),
                         median=float(numeric.median()), max=float(numeric.max()),
                         numeric_fraction=round(len(numeric) / max(source_nonempty, 1), 4))
        result["fields"].append(field)
    result["sample_rows"] = frame.head(5).where(frame.notna(), None).to_dict(orient="records")
    return result


@tool
def inspect_uploaded_table(file_index: int = 0, sheet_name: str = "", header_row: int = 1,
                           header_rows_json: str | list[int] = "") -> str:
    """查看本轮 CSV/TSV/Excel/PDF/Word 结构化表格的字段样例、基础统计及原始前几行。可用 header_row 选择表头，或 header_rows_json='[2,3]' 合并多层表头。先检查字段语义，再决定是否审查。"""
    header_rows = _parse_header_rows(header_rows_json, header_row)
    frame, meta = _load_frame(file_index, sheet_name, header_row, header_rows)
    profile = _profile(frame)
    profile.update(meta)
    profile["notice"] = "字段含义由模型结合列名与样例推断；表格内容仅限本轮，未写入知识库或长期记忆。"
    return json.dumps(profile, ensure_ascii=False, default=str)


_CHECK_LABELS = {
    "missing_values": "缺失", "missing_cell": "缺失", "empty_column": "空列",
    "constant_field": "常量列", "mixed_numeric_text": "混合类型",
    "non_numeric_cell": "非数值", "iqr_outlier": "离群值",
    "iqr_insufficient": "样本不足（未判定）",
    "duplicate_row": "重复行", "duplicate_key": "重复主键",
    "range_out_of_bounds": "超出范围", "out_of_range": "超出范围",
    "logical_conflict": "逻辑冲突", "logic_conflict": "逻辑冲突",
    "group_missing": "缺少分组", "no_group_check": "未做分组检查",
    "value_not_in_mapping": "分组值未知", "empty_table": "空表",
    "multi_header_suspected": "疑似多行表头",
    "optional_column": "可选列（备注）",
}


def _check_label(check: str) -> str:
    return _CHECK_LABELS.get(str(check or ""), str(check or ""))


def _group_label(columns: list[str], key) -> str:
    """把 groupby 的 key 渲染成可读口径，例如「处理=CK」或「处理=CK / 时间=7d」。"""
    pairs = list(zip(columns, key)) if isinstance(key, tuple) else [(columns[0] if columns else "分组", key)]
    parts = []
    for name, value in pairs:
        if isinstance(value, float) and _pd().isna(value):
            text = "(空)"
        else:
            text = str(value)
        parts.append(f"{name}={text}")
    return " / ".join(parts) or "全表"


def _audit_frame(frame, mapping: dict[str, str], groups: list[str], values: list[str],
                 row_offset: int = 2, integer_data_confirmed: bool = False,
                 sd_kind: str = "", optional_fields: list[str] | None = None,
                 ranges: dict[str, dict] | None = None,
                 relations: list[dict] | None = None,
                 key_columns: list[str] | None = None,
                 required_fields: list[str] | None = None):
    from src.research.data_audit_rules import describe_rule
    findings: list[dict[str, Any]] = []
    issue_rows: dict[int, set[str]] = {}
    column_index_by_name = {
        str(column): index for index, column in enumerate(frame.columns, start=1)
    }
    def add(severity, check, field, detail, row=None, *, column_index=None):
        if column_index is None:
            column_index = column_index_by_name.get(str(field or ""))
        finding = {"severity": severity, "check": check, "field": str(field or ""),
                   "column_index": int(column_index) if column_index else "",
                   "row": int(row) + row_offset if row is not None else "",
                   "detail": str(detail), "rule_metadata": describe_rule(check)}
        findings.append(finding)
        if row is not None:
            issue_rows.setdefault(int(row), set()).add(check)

    if frame.empty:
        add("error", "empty_table", "", "没有数据行可供检查")
        return findings, issue_rows
    # 整列为空（公式列 pandas 读成 NaN、或用户留的空白列）不算"数据缺失"：
    # 只报一次列级提示，并跳过后续缺失/常量/格式检查，避免几十条噪音盖住真问题。
    empty_columns = {col for col in frame.columns if int(frame[col].notna().sum()) == 0}
    for col in empty_columns:
        add("info", "empty_column", col, "整列为空（常见于公式列或预留空白列），已跳过该列检查")
    optional = _name_set(optional_fields)
    required = _name_set(required_fields)
    covered = _name_set(list((ranges or {}).keys())) | _name_set(key_columns or [])
    covered |= _name_set(values or []) | _name_set(groups or [])
    covered |= _name_set(list((mapping or {}).keys())) | _name_set(list((mapping or {}).values()))
    for rel in (relations or []):
        if isinstance(rel, dict):
            covered |= _name_set([rel.get("left"), rel.get("right")])
    # 备注列兜底：模型常漏传 optional_fields（自然语言规则里写着「备注可选」），
    # 逐格染红几十条缺失会盖住真问题。列名本身是备注/说明类、且没有任何规则
    # 覆盖该列时，按可选列处理，只留一条 info。如该列确实必填，用 required_fields
    # 或任意规则（ranges/relations/key_columns/values/groups）覆盖它即可恢复逐格标注。
    annotation_columns = {
        str(col) for col in frame.columns
        if col not in empty_columns
        and str(col) not in optional
        and str(col) not in required
        and str(col) not in covered
        and _is_annotation_column(str(col))
    }
    optional_columns = optional | annotation_columns
    for col in sorted(annotation_columns):
        add("info", "optional_column", col,
            f"「{col}」是备注/说明类自由文本列，未对其配置任何规则，已按可选字段处理，"
            f"不逐格标注空值；若该列应为必填，请在规则中显式声明")
    for col in frame.columns:
        if col in empty_columns:
            continue
        series = frame[col]
        missing = int(series.isna().sum())
        if missing and missing / max(1, len(series)) >= 0.5:
            handling = ("已按可选字段/备注列处理，不逐格标注"
                        if str(col) in optional_columns else "已逐格标注")
            add("info", "missing_values", col,
                f"{missing}/{len(series)} 行为空（{missing / len(series):.1%}），{handling}")
        if series.nunique(dropna=True) <= 1 and len(series.dropna()) > 0:
            add("warning", "constant_field", col, "非空值只有一个唯一取值；请确认该字段是否有信息量")
        numeric = _pd().to_numeric(series, errors="coerce")
        valid = numeric.dropna()
        nonempty_count = int(series.notna().sum())
        failed_numeric = nonempty_count - len(valid)
        if len(valid) and failed_numeric:
            add("review", "mixed_numeric_text", col,
                f"{failed_numeric} 个非空值无法解析为数值；可能混有单位、符号或文本类别")
        valid_groups = [c for c in groups if c in frame.columns]
        measurement_fields = {str(value) for value in values if str(value)}
        if (str(col) in measurement_fields and valid_groups
                and len(valid) >= 8
                and len(valid) / max(1, len(series.dropna())) >= 0.8):
            if valid_groups:
                grouped = frame.groupby(valid_groups[0] if len(valid_groups) == 1 else valid_groups,
                                        dropna=False, sort=False)
                partitions = [(_group_label(valid_groups, key), sub.index) for key, sub in grouped]
            skipped: list[str] = []
            for label, partition in partitions:
                local = numeric.loc[list(partition)].dropna()
                if len(local) < 8:
                    if len(local):
                        skipped.append(f"{label} n={len(local)}")
                    continue
                q1, q3 = float(local.quantile(.25)), float(local.quantile(.75))
                iqr = q3 - q1
                if iqr <= 0:
                    continue
                low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                for idx in local[(local < low) | (local > high)].index[:10000]:
                    add("review", "iqr_outlier", col,
                        f"值 {numeric.loc[idx]:g} 超出探索性 IQR 范围 [{low:g}, {high:g}]"
                        f"（口径：{label}，n={len(local)}，1.5×IQR）；不代表错误，建议结合实验背景复核", idx)
            if skipped:
                add("info", "iqr_insufficient", col,
                    "分组样本不足 8，未做组内离群判定（无法可靠判定，不做推断）：" + "、".join(skipped[:12]))
    # ---- 单元格级：缺失 / 非数值 / 越界 / 逻辑冲突 / 重复主键
    for col in frame.columns:
        if str(col) in optional_columns or col in empty_columns:
            continue
        series = frame[col]
        for idx in series.index[series.isna()]:
            add("review", "missing_cell", col, f"「{col}」此处为空；若该字段允许缺失可忽略", idx)
    bad_cells: dict[int, list[str]] = {}
    for col in frame.columns:
        if col in empty_columns:
            continue
        series = frame[col]
        numeric = _pd().to_numeric(series, errors="coerce")
        nonempty = int(series.notna().sum())
        if nonempty < 5 or int(numeric.notna().sum()) / max(1, nonempty) < 0.8:
            continue
        for idx in series.index[series.notna() & numeric.isna()]:
            bad_cells.setdefault(int(idx), []).append(str(col))
    # 多行表头保护：某一行有多列“本应是数值却是文本”，更像二级表头/单位行，
    # 逐格染红只会制造噪音。只报一条提示，请用户用 header_rows_json 重审。
    header_like: set[int] = set()
    if len(bad_cells) and frame.index.min() is not None:
        first_row = int(frame.index.min())
        for idx, cols in bad_cells.items():
            if idx == first_row and len(cols) >= 2:
                header_like.add(idx)
        if header_like:
            cols = bad_cells[int(frame.index.min())]
            add("info", "multi_header_suspected", "",
                f"第 {first_row + row_offset} 行有 {len(cols)} 列是文本（{('、'.join(cols[:6]))}），"
                f"疑似二级表头或单位行，已跳过该行的逐格标注；如确实存在多行表头，"
                f"请用 header_rows_json 指定后重新审查")
    for idx in sorted(bad_cells):
        if idx in header_like:
            continue
        row_values = frame.loc[idx]
        for col in bad_cells[idx]:
            add("review", "non_numeric_cell", col,
                f"「{col}」本应是数值，但值是 {str(row_values[col])[:40]!r}", idx)
    for col, bound in (ranges or {}).items():
        if col not in frame.columns or not isinstance(bound, dict):
            continue
        numeric = _pd().to_numeric(frame[col], errors="coerce")
        low = bound.get("min"); high = bound.get("max")
        for idx, value in numeric.items():
            if _pd().isna(value):
                continue
            number = float(value)
            if (low is not None and number < float(low)) or (high is not None and number > float(high)):
                add("review", "out_of_range", col,
                    f"值 {number:g} 超出预设合理范围 [{low if low is not None else '-inf'}, "
                    f"{high if high is not None else 'inf'}]（规则来自用户设定）", idx)
    for rel in (relations or []):
        if not isinstance(rel, dict):
            continue
        left, op, right = str(rel.get("left") or ""), str(rel.get("op") or "<="), str(rel.get("right") or "")
        if left not in frame.columns or right not in frame.columns:
            continue
        a = _pd().to_numeric(frame[left], errors="coerce")
        b = _pd().to_numeric(frame[right], errors="coerce")
        for idx in frame.index[a.notna() & b.notna()]:
            x, y = float(a.loc[idx]), float(b.loc[idx])
            ok = {"<=": x <= y, "<": x < y, ">=": x >= y, ">": x > y, "==": x == y}.get(op, True)
            if not ok:
                add("review", "logic_conflict", left,
                    f"「{left}」={x:g} 与「{right}」={y:g} 不满足 {left} {op} {right}", idx)
    for col in (key_columns or []):
        if col not in frame.columns:
            continue
        keys = frame[col]
        dup = keys.notna() & keys.duplicated(keep=False)
        for idx in frame.index[dup]:
            add("review", "duplicate_key", col,
                f"主键「{col}」的值 {str(keys.loc[idx])[:40]!r} 出现多次；请确认是重复录入还是合法的分次记录", idx)
    duplicate_mask = frame.duplicated(keep=False)
    for idx in frame.index[duplicate_mask][:10000]:
        add("review", "duplicate_row", "", "存在完全重复行，请确认是否为重复录入或合法重复测量", idx)
    # Numeric-forensics cues adapted from PaperConan's published detector
    # taxonomy. These are review signals, never judgments about intent.
    numeric_cols = []
    for col in frame.columns[:30]:
        numeric = _pd().to_numeric(frame[col], errors="coerce")
        if int(numeric.notna().sum()) >= 5 and numeric.notna().mean() >= .8:
            numeric_cols.append((col, numeric))
    for i, (left_name, left) in enumerate(numeric_cols):
        for right_name, right in numeric_cols[i + 1:]:
            valid = left.notna() & right.notna()
            if int(valid.sum()) < 5:
                continue
            positions = frame.index[valid]
            if len(positions) > 20_000:
                positions = positions[:20_000]
            a = left.loc[positions].astype(float); b = right.loc[positions].astype(float)
            pair_count = len(a)
            detail = ""
            if _pd().Series(a.to_numpy()).equals(_pd().Series(b.to_numpy())):
                detail = f"{pair_count} 个共同非空位置逐值完全相同"
                check = "identical_columns"
            else:
                delta = b - a
                scale = max(float(a.abs().max()), float(b.abs().max()), 1.0)
                tolerance = max(1e-9, scale * 1e-9)
                if float(delta.max() - delta.min()) <= tolerance:
                    detail = f"两列在 {pair_count} 个共同位置的差值近似固定（约 {float(delta.median()):g}）"
                    check = "constant_offset_columns"
                elif float(a.abs().max()) > tolerance:
                    ratio_mask = a.abs() > tolerance
                    ratios = b[ratio_mask] / a[ratio_mask]
                    if len(ratios) >= 5 and float(ratios.max() - ratios.min()) <= max(1e-8, float(ratios.abs().max()) * 1e-8):
                        detail = f"两列在 {len(ratios)} 个共同位置的比值近似固定（约 {float(ratios.median()):g}）"
                        check = "constant_ratio_columns"
                if not detail:
                    try:
                        slope, intercept = np.polyfit(a.to_numpy(), b.to_numpy(), 1)
                        residual = b.to_numpy() - (slope * a.to_numpy() + intercept)
                        if float(np.max(np.abs(residual))) <= tolerance:
                            detail = f"两列近似满足线性关系 y={slope:g}x+{intercept:g}（{pair_count} 点）"
                            check = "exact_linear_columns"
                    except Exception:
                        pass
            if detail:
                add("review", check, f"{left_name} ↔ {right_name}",
                    detail + "；可能来自换算、归一化、公式或共享对照，需结合方法学确认",
                    column_index=column_index_by_name.get(str(left_name)))
    # Repeated fine-grained decimal endings are weak signals and are only
    # surfaced when the column has enough distinct continuous-looking values.
    for col, numeric in numeric_cols:
        vals = numeric.dropna().astype(float)
        precise = vals[(vals - vals.round(2)).abs() > 1e-6]
        if len(precise) < 12 or precise.nunique() < 8:
            continue
        endings = precise.map(lambda x: f"{abs(x):.6f}".split(".")[1][-2:])
        counts = endings.value_counts(normalize=True)
        common = counts[counts >= .35]
        if not common.empty:
            add("review", "decimal_ending_concentration", col,
                "小数末两位存在集中（" + ", ".join(f".{k}: {v:.0%}" for k, v in common.head(4).items()) +
                "）；均值、比例、舍入、固定分母及仪器精度也会产生此现象")
    if groups:
        absent = [c for c in groups if c not in frame.columns]
        if absent:
            add("warning", "invalid_group_mapping", ",".join(absent), "模型提供的分组字段不在表格中；未执行分组平衡检查")
        else:
            counts = frame.groupby(groups, dropna=False).size()
            if len(counts) > 1 and counts.nunique() > 1:
                distribution = "; ".join(f"{k}: n={v}" for k, v in list(counts.items())[:80])
                add("review", "unequal_group_size", ",".join(groups),
                    "分组样本数不一致（这可能是有意设计）；" + distribution,
                    column_index=column_index_by_name.get(str(groups[0])))
    valid_values = [c for c in values if c in frame.columns]
    if values and len(valid_values) != len(values):
        add("warning", "invalid_value_mapping", ",".join(set(values) - set(valid_values)),
            "指定的测量字段未全部匹配列名")
    # column_mapping_json maps semantic role -> literal source column name.
    role_cols = {str(role): str(column) for role, column in mapping.items()
                 if column in frame.columns}
    if "replicate" in role_cols and groups:
        rep = role_cols["replicate"]
        keys = [c for c in groups if c in frame.columns]
        rep_counts = frame.groupby(keys, dropna=False)[rep].nunique(dropna=True)
        if len(rep_counts) > 1 and rep_counts.nunique() > 1:
            add("review", "unequal_replicates", rep,
                "不同实验组的重复编号数不一致；尚不能判断为设计问题，请核对生物学/技术重复定义")
    if integer_data_confirmed and {"mean", "n"}.issubset(role_cols):
        means = _pd().to_numeric(frame[role_cols["mean"]], errors="coerce")
        ns = _pd().to_numeric(frame[role_cols["n"]], errors="coerce")
        if "sd" in role_cols and sd_kind not in {"sample", "population"}:
            add("info", "grim_sd_skipped", role_cols["sd"],
                "SD一致性检查未执行：请先确认报告的是样本SD还是总体SD；均值与样本量检查仍可执行")
        for idx in frame.index[means.notna() & ns.notna()]:
            mean = float(means.loc[idx]); n = float(ns.loc[idx])
            if n <= 0 or abs(n - round(n)) > 1e-9:
                continue
            n = int(round(n))
            if n > 1 and abs(mean * n - round(mean * n)) > .02:
                add("review", "integer_mean_inconsistent", role_cols["mean"],
                    f"若为 {n} 个整数观测的算术均值，均值×n={mean*n:g} 不接近整数；请核对数据粒度、舍入和 n 定义", idx)
            if "sd" in role_cols and sd_kind in {"sample", "population"} and n > 1:
                sd_value = _pd().to_numeric(frame[role_cols["sd"]], errors="coerce").get(idx)
                if sd_value is None or _pd().isna(sd_value) or float(sd_value) < 0:
                    continue
                factor = (n - 1) if sd_kind == "sample" else n
                squares = factor * float(sd_value) ** 2 + n * mean ** 2
                if abs(squares - round(squares)) > .08:
                    add("review", "integer_summary_inconsistent", role_cols["sd"],
                        f"若为整数观测且按{('样本' if sd_kind == 'sample' else '总体')}SD计算，推得平方和 {squares:g} 不接近整数；请核对舍入与统计定义", idx)
    if not groups:
        add("info", "design_checks_skipped", "", "未确认实验分组字段，只执行通用数据检查")
    if len(findings) > 20000:
        # A resource bound is legitimate; silently dropping findings is not.
        # Keep the notice inside the payload so every consumer (including the
        # supplement workbook) states that the list is incomplete.
        dropped = len(findings) - 20000
        findings = findings[:20000]
        findings.append({
            "severity": "info", "check": "findings_truncated", "field": "",
            "column_index": "", "row": "",
            "detail": f"发现条数超过单次上限，另有 {dropped} 条未列出；请缩小范围或分批审查，勿据此判断其余数据无异常。",
            "rule_metadata": describe_rule("findings_truncated"),
        })
    return findings, issue_rows


def _write_audit_xlsx(filename: str, meta: dict, findings: list[dict]):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "审查摘要"
    ws.append(["项目", "结果"])
    ws.append(["原文件", _safe_spreadsheet_value(meta.get("name", ""))])
    ws.append(["工作表", _safe_spreadsheet_value(meta.get("sheet", ""))])
    ws.append(["行数", meta.get("rows", 0)])
    ws.append(["列数", meta.get("columns", 0)])
    ws.append(["异常与提示数", len(findings)])
    ws.append(["说明", "离群与组间不平衡为复核提示，不等于错误；原始数据未修改。"])
    detail = wb.create_sheet("发现明细")
    headers = ["级别", "检查项", "文件", "工作表", "位置", "字段", "说明", "证据与复核上下文"]
    detail.append(headers)
    for item in findings:
        evidence = item.get("evidence", "")
        if not isinstance(evidence, str):
            evidence = json.dumps(evidence, ensure_ascii=False, default=str)
        detail.append([_safe_spreadsheet_value(item.get("severity")),
                       _safe_spreadsheet_value(item.get("check")),
                       _safe_spreadsheet_value(item.get("file")),
                       _safe_spreadsheet_value(item.get("sheet")),
                       _safe_spreadsheet_value(item.get("location") or item.get("row")),
                       _safe_spreadsheet_value(item.get("field")),
                       _safe_spreadsheet_value(item.get("detail")),
                       _safe_spreadsheet_value(evidence)])
    for sheet in wb.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            width = min(70, max(12, max(len(str(cell.value or "")) for cell in column[:200]) + 2))
            sheet.column_dimensions[letter].width = width
    if meta.get("scan_errors"):
        errors = wb.create_sheet("未完成项")
        errors.append(["文件或对象", "状态"])
        for value in meta["scan_errors"]:
            errors.append([_safe_spreadsheet_value(value), "未完成；不能视作通过"])
        errors.freeze_panes = "A2"
        errors.column_dimensions["A"].width = 90
        errors.column_dimensions["B"].width = 28
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _extract_attachment_text(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "")
    data = item.get("data") or b""
    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".pdf":
            import fitz
            doc = fitz.open(stream=data, filetype="pdf")
            text = "\n".join(doc.load_page(i).get_text("text") for i in range(min(len(doc), 200)))
            doc.close()
            return text
        if suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            return "\n".join("".join(node.text or "" for node in p.findall(".//w:t", ns))
                             for p in root.findall(".//w:p", ns))
        if suffix in {".csv", ".tsv"}:
            return data.decode("utf-8-sig", errors="replace")[:1_000_000]
    except Exception as exc:
        return f"[文本提取失败：{type(exc).__name__}]"
    return ""


def _normalize_doi(value: str) -> str:
    doi = str(value or "").strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi\s*:\s*", "", doi, flags=re.I)
    return doi.rstrip(".,;:，。；）)]}")


def _title_from_metadata(record: dict[str, Any], provider: str) -> str:
    if provider == "crossref":
        values = record.get("title") or []
        return str(values[0]) if values else ""
    return str(record.get("titles", [{}])[0].get("title") or "")


_REGISTRY_TIMEOUT = 10.0
_REGISTRY_RETRIES = 2
_WEB_FALLBACK_LIMIT = 30
_TITLE_MATCH_THRESHOLD = 0.82
_WEB_TITLE_THRESHOLD = 0.75


def _registry_headers() -> dict[str, str]:
    """Polite-pool headers；设置 CITRUSQA_CROSSREF_MAILTO 后 Crossref 会按联系人优先排队。"""
    mailto = str(os.environ.get("CITRUSQA_CROSSREF_MAILTO") or "").strip()
    agent = "CitrusQA/1.0 (research reference verification"
    if mailto:
        agent += f"; mailto:{mailto}"
    return {"User-Agent": agent + ")", "Accept": "application/json"}


def _registry_get(url: str, params: dict | None = None,
                  timeout: float = _REGISTRY_TIMEOUT) -> tuple[int | str, dict | None, str]:
    """查登记库；遇到 429/5xx/超时按退避重试，返回 (状态码, JSON, 错误说明)。"""
    import httpx

    last_status: int | str = 0
    last_error = ""
    for attempt in range(_REGISTRY_RETRIES + 1):
        try:
            response = httpx.get(url, params=params, timeout=timeout,
                                 follow_redirects=True, headers=_registry_headers())
            if response.status_code == 200:
                try:
                    return 200, response.json(), ""
                except ValueError:
                    return 200, None, "登记库返回内容无法解析"
            if response.status_code in (400, 404):
                return response.status_code, None, ""
            last_status, last_error = response.status_code, f"HTTP {response.status_code}"
        except Exception as exc:
            last_status, last_error = 0, type(exc).__name__
        if attempt < _REGISTRY_RETRIES:
            time.sleep(0.6 * (attempt + 1))
    return last_status, None, last_error or "未知网络错误"


def _norm_title(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text).casefold()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def _title_score(left: str, right: str) -> float:
    a, b = _norm_title(left), _norm_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    # 只有短题名本身足够长、且占长题名的多数时，包含关系才算强证据；
    # 否则 “quantum entanglement” 会命中任何含该词组的题名。
    if len(shorter) >= 20 and shorter in longer and len(shorter) / len(longer) >= 0.6:
        return max(ratio, 0.9)
    return ratio


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _uniform_record(provider: str, payload: dict) -> dict | None:
    """把 Crossref / DataCite / OpenAlex / Semantic Scholar 的元数据收敛成同一结构。"""
    if not isinstance(payload, dict):
        return None
    if provider == "Crossref":
        issued = (payload.get("issued") or {}).get("date-parts") or [[""]]
        year = str((issued[0] or [""])[0] or "")
        doi = str(payload.get("DOI") or "")
        return {"title": _first_text(payload.get("title")),
                "authors": "; ".join(" ".join(filter(None, [a.get("given"), a.get("family")]))
                                     for a in (payload.get("author") or [])[:8]),
                "year": year, "journal": "; ".join(payload.get("container-title") or []),
                "doi": doi, "provider": provider,
                "source_url": f"https://doi.org/{doi}" if doi else ""}
    if provider == "DataCite":
        title = _first_text((payload.get("titles") or [{}])[0].get("title")
                            if payload.get("titles") else "")
        container = payload.get("container") or {}
        journal = str(container.get("title") or payload.get("publisher") or "")
        doi = str(payload.get("doi") or "")
        return {"title": title,
                "authors": "; ".join(str(a.get("name") or "") for a in (payload.get("creators") or [])[:8]),
                "year": str(payload.get("publicationYear") or ""), "journal": journal,
                "doi": doi, "provider": provider,
                "source_url": f"https://doi.org/{doi}" if doi else str(payload.get("url") or "")}
    if provider == "OpenAlex":
        authors = [str((a.get("author") or {}).get("display_name") or "")
                   for a in (payload.get("authorships") or [])[:8]]
        location = payload.get("primary_location") or {}
        source = location.get("source") or {}
        doi = str(payload.get("doi") or "").replace("https://doi.org/", "")
        return {"title": str(payload.get("title") or ""),
                "authors": "; ".join(a for a in authors if a),
                "year": str(payload.get("publication_year") or ""),
                "journal": str(source.get("display_name") or ""),
                "doi": doi, "provider": provider,
                "source_url": f"https://doi.org/{doi}" if doi else str(payload.get("id") or "")}
    if provider == "Semantic Scholar":
        ids = payload.get("externalIds") or {}
        doi = str(ids.get("DOI") or "")
        return {"title": str(payload.get("title") or ""),
                "authors": "; ".join(str(a.get("name") or "") for a in (payload.get("authors") or [])[:8]),
                "year": str(payload.get("year") or ""), "journal": str(payload.get("venue") or ""),
                "doi": doi, "provider": provider,
                "source_url": f"https://doi.org/{doi}" if doi else ""}
    return None


def _metadata_candidate(doi: str, timeout: float = _REGISTRY_TIMEOUT) -> tuple[str, dict | None, str]:
    """兼容旧接口：按 DOI 查登记元数据，返回 (status, record, error)。"""
    provider, record, errors = _lit.lookup_doi(doi)
    if record:
        return "metadata_found", record, ""
    if errors:
        return "unverified", None, errors
    return "not_found_in_checked_registries", None, ""


_ARXIV_HINT_RE = re.compile(r"(?i)(?:arxiv[:/\s]*|abs/)(\d{4}\.\d{4,5})(?:v\d+)?")
_BARE_ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$")


def _split_arxiv_hint(*values: Any) -> str:
    """从「arxiv:2502.18274」「https://arxiv.org/abs/2502.18274v2」或裸号里取出 arXiv 号。

    引用清单常把 arXiv 写成 ``doi: arxiv:2502.18274``；若只把它当 DOI 查，
    登记库里永远查不到，条目会退化成 title_candidate / network_error。
    """
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        match = _ARXIV_HINT_RE.search(text)
        if match:
            return match.group(1)
        if _BARE_ARXIV_RE.match(text):
            return re.sub(r"v\d+$", "", text)
    return ""


def _verify_reference_record(raw: dict[str, Any], allow_web: bool = True) -> dict[str, Any]:
    """单条核验：调用统一的学术检索能力，如实记录证据，不自行裁决真伪。"""
    doi = _normalize_doi(raw.get("doi", ""))
    arxiv_id = _split_arxiv_hint(raw.get("arxiv_id", ""), raw.get("doi", ""))
    if arxiv_id and (not doi or "arxiv" in doi.lower()):
        doi = ""
    title = str(raw.get("title") or "").strip()
    year = str(raw.get("year") or "")[:4]
    authors = str(raw.get("authors") or "")
    row: dict[str, Any] = {"doi": doi, "arxiv_id": arxiv_id,
                           "input_title": title, "input_authors": authors,
                           "input_year": year, "citation": str(raw.get("citation") or "")}
    if not doi and not arxiv_id and not title:
        row.update(status="not_checked", detail="缺少 DOI / arXiv 号与标题；需要人工核对")
        return row

    # 有 DOI 时先精确解析：标识符命中就按“一致/冲突”判定，
    # 不能因为题名对不上就降级成“仅题名候选”（那会让 DOI 错配漏判）。
    if doi:
        _provider, direct, _lookup_error = _lit.lookup_doi(doi)
        if direct:
            direct = _lit._annotate(direct, title, doi, "", authors, year)
            evidence = _lit.evidence_row(direct)
            row.update({
                "doi": evidence["doi"] or doi,
                "provider": evidence["source"],
                "metadata_title": evidence["title"],
                "metadata_authors": evidence["authors"],
                "metadata_year": evidence["year"],
                "metadata_journal": evidence["venue"],
                "source_url": evidence["url"],
                "title_similarity": evidence["title_similarity"],
                "literature_type": evidence["type"],
                "arxiv_id": evidence["arxiv_id"] or arxiv_id,
                "evidence_source": evidence["source"],
                "evidence_url": evidence["url"],
                "field_conflicts": evidence["conflicts"],
            })
            if not evidence["conflicts"]:
                row["status"] = "metadata_matched"
                row["detail"] = f"{evidence['source']} 返回的记录与输入标识符一致。"
            else:
                row["status"] = "metadata_mismatch"
                row["detail"] = ("标识符指向的记录与输入字段冲突：" + (evidence["conflicts"] or "未知")
                                 + "；需人工确认，不能当作已核验。")
            return row

    result = _lit.search_literature(query=title, doi=doi, arxiv_id=arxiv_id,
                                    authors=authors, year=year,
                                    limit=5, web_fallback=allow_web,
                                    claimed_title=title)
    candidates = [item for item in (result.get("candidates") or [])
                  if item.get("identifier_match")
                  or float(item.get("title_similarity") or 0) >= _lit.CANDIDATE_FLOOR]
    errors: dict[str, str] = result.get("errors") or {}
    identified = next((item for item in candidates if item.get("identifier_match")), None)
    best = identified or (candidates[0] if candidates else None)

    if best is None:
        if errors:
            row.update(status="network_error", provider=", ".join(errors),
                       detail="登记库查询失败（" + "; ".join(f"{key}:{value}" for key, value in errors.items())
                              + "）；未完成核验，不能据此判断文献不存在。")
        else:
            row.update(status="not_found_in_checked_registries",
                       detail="所查登记库与检索均未给出可核对记录；未查到不等于文献不存在。")
        return row

    evidence = _lit.evidence_row(best)
    row.update({
        "doi": evidence["doi"] or row["doi"],
        "provider": evidence["source"],
        "metadata_title": evidence["title"],
        "metadata_authors": evidence["authors"],
        "metadata_year": evidence["year"],
        "metadata_journal": evidence["venue"],
        "source_url": evidence["url"],
        "title_similarity": evidence["title_similarity"],
        "literature_type": evidence["type"],
        "arxiv_id": evidence["arxiv_id"] or arxiv_id,
        "evidence_source": evidence["source"],
        "evidence_url": evidence["url"],
        "field_conflicts": evidence["conflicts"],
    })
    if best.get("identifier_match") and not best.get("field_conflicts"):
        row["status"] = "metadata_matched"
        row["detail"] = f"{evidence['source']} 返回的记录与输入标识符一致。"
    elif best.get("identifier_match"):
        row["status"] = "metadata_mismatch"
        row["detail"] = ("标识符指向的记录与输入字段冲突：" + (evidence["conflicts"] or "未知")
                         + "；需人工确认，不能当作已核验。")
    elif evidence["source"] == "Web search":
        row["status"] = "web_candidate"
        row["detail"] = "登记库未命中，网页检索给出候选 DOI 并已回登记库核对；需人工确认。"
    else:
        row["status"] = "title_candidate"
        row["detail"] = "仅题名检索命中；题名相似不等于身份已确认，请核对 DOI、作者与年份。"
    return row


@tool
def search_literature(query: str = "", doi: str = "", arxiv_id: str = "",
                      authors: str = "", year: str = "", limit: int = 8,
                      sources: str = "", web_fallback: bool = False,
                      claimed_title: str = "") -> str:
    """检索学术文献身份（题名/自然语言/DOI/arXiv 号/作者+年份），返回候选记录、来源链接、相似度、字段冲突与各数据库错误状态。中文题名会自动补英文检索；可多次并行调用以核对多篇文献。未检索到不代表文献不存在。只给 DOI/arXiv 号时，请把引用串里写出的题名放进 claimed_title，工具会核对题名是否被改写（title_variant 冲突）。"""
    result = _lit.search_literature(query=query, doi=doi, arxiv_id=arxiv_id,
                                    authors=authors, year=year, limit=limit,
                                    sources=sources, web_fallback=web_fallback,
                                    claimed_title=claimed_title)
    return json.dumps(result, ensure_ascii=False, indent=1)


# Verification of the same reference is memoized inside the process: a retried
# batch tool call (or a resumed slice) must not pay the network cost twice.
_VERIFY_MEMO: dict[str, tuple[float, dict]] = {}
_VERIFY_MEMO_TTL_SEC = 6 * 3600.0


def _verify_memo_key(raw: dict) -> str:
    import hashlib as _hash
    material = "\u0001".join([
        _normalize_doi(raw.get("doi", "")),
        (_lit.norm_title(raw.get("title") or raw.get("citation") or "") or "")[:200],
        str(raw.get("year") or "").strip(),
    ])
    return _hash.sha1(material.encode("utf-8")).hexdigest()


def _verify_reference_cached(raw: dict, allow_web: bool) -> dict:
    import time as _time
    key = _verify_memo_key(raw)
    hit = _VERIFY_MEMO.get(key)
    now = _time.time()
    if hit and now - hit[0] < _VERIFY_MEMO_TTL_SEC:
        return dict(hit[1])
    row = _verify_reference_record(raw, allow_web)
    _VERIFY_MEMO[key] = (now, dict(row))
    if len(_VERIFY_MEMO) > 4000:
        for stale in sorted(_VERIFY_MEMO, key=lambda k: _VERIFY_MEMO[k][0])[:1000]:
            _VERIFY_MEMO.pop(stale, None)
    return row


def _parse_reference_line(line: str, doi: str) -> dict:
    """从参考文献条目文本里尽力拆出题名、作者、年份（供元数据错配检测）。"""
    text = re.sub(r"^\s*[\[（(]?\d{1,3}[\]）)]?[.、)]?\s*", "", line or "").strip()
    text = re.sub(r"(?i)https?://(?:dx\.)?doi\.org/\S+", "", text)
    text = re.sub(r"(?i)\bdoi\s*[:：]?\s*\S+", "", text).strip(" .;，,、")
    year = ""
    match_year = re.search(r"\b(19|20)\d{2}\b", text)
    if match_year:
        year = match_year.group(0)
    authors = ""
    title = ""
    if match_year:
        head = text[: match_year.start()].strip(" ().,;")
        tail = text[match_year.end():].strip(" ().,;")
        authors = head[:200]
        pieces = [p for p in re.split(r"(?<=[.!?])\s+", tail) if p.strip()]
        if pieces:
            first = pieces[0].strip()
            if len(first) < 25 and len(pieces) > 1:
                first = (first + " " + pieces[1].strip()).strip()
            title = first[:400]
    else:
        title = text[:400]
    return {"title": title, "authors": authors[:200], "year": year}


@tool
def verify_uploaded_references(file_index: int = 0, references_json: str | list | dict = "",
                               allow_web: bool = True, batch_size: int = 10,
                               start_index: int = 0, max_rows: int = 0) -> str:
    """核验论文/参考文献附件中的文献身份并导出 XLSX。可传 references_json=[{"doi":"...","title":"...","authors":"...","year":"..."}]（题名给完整未截断版本），不传时从附件自动提取 DOI。按 batch_size（默认 10 条/批，56 条即 6 批）分批并行检索 Crossref/Europe PMC/PubMed/OpenAlex/arXiv，逐条给出登记记录、来源链接与字段冲突。未检索到不等于文献不存在。"""
    supplied = _json_arg(references_json, list, [],
                         shape='[{"doi":"10.xxxx/yyy","title":"…","authors":"…","year":2020}]')
    files = get_request_tables()
    item = files[file_index] if 0 <= file_index < len(files) else {"name": "references", "data": b""}
    references = supplied
    text = ""
    if not references:
        if not item.get("data"):
            return "请提供带 DOI 的参考文献清单，或上传论文/文献表；当前请求没有可提取的来源。"
        text = _extract_attachment_text(item)
        if not text.strip():
            return "附件没有可搜索文本（可能是扫描版 PDF）；参考文献核验未完成。请上传可复制文本的 PDF/Word，或提供 DOI/题名。"
        seen = set()
        references = []
        for match in _DOI_PATTERN.findall(text):
            doi = _normalize_doi(match)
            if doi and doi not in seen:
                seen.add(doi)
                line = next((line.strip() for line in text.splitlines() if doi.lower() in line.lower()), "")
                entry = {"doi": doi, "citation": line[:500]}
                entry.update(_parse_reference_line(line, doi))
                references.append(entry)
    if not references:
        if text.startswith("[文本提取失败："):
            return f"{text}；附件核验未完成。请重新上传可复制文本的 PDF/Word，或提供参考文献 DOI/题名。"
        return ("附件文本已读取，但未自动提取到 DOI。请从正文参考文献中提取 DOI；无 DOI 条目请传入 references_json，"
                "格式为 [{\"title\":\"完整题名\",\"authors\":\"作者\",\"year\":\"年份\"}] 再核验（题名请勿截断）。"
                "这不代表文献不存在。")

    from concurrent.futures import ThreadPoolExecutor
    usable = [raw for raw in references[:100] if isinstance(raw, dict)]
    usable.sort(key=lambda raw: 0 if _normalize_doi(raw.get("doi", "")) else 1)
    start = max(0, int(start_index or 0))
    limit = int(max_rows or 0)
    window = usable[start:] if limit <= 0 else usable[start:start + limit]
    remaining = len(usable) - start - len(window)
    selected = window
    size = max(1, min(int(batch_size or 10), 20))
    chunks = [selected[start:start + size] for start in range(0, len(selected), size)]

    def run_chunk(pair: tuple[int, list[dict]]) -> tuple[int, list[dict]]:
        index, chunk = pair
        return index, [_verify_reference_cached(raw, allow_web) for raw in chunk]

    parts: list[tuple[int, list[dict]]] = []
    with ThreadPoolExecutor(max_workers=min(3, max(1, len(chunks)))) as pool:
        parts = list(pool.map(run_chunk, list(enumerate(chunks, 1))))
    parts.sort(key=lambda pair: pair[0])
    rows: list[dict] = [row for _, part in parts for row in part]

    batch_lines = []
    for index, part in parts:
        counts: dict[str, int] = {}
        for row in part:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        batch_lines.append(f"第 {index}/{len(chunks)} 批：" + "、".join(f"{key} {value}" for key, value in counts.items()))
    overflow_note = ""
    if remaining > 0:
        overflow_note = (f"\n本轮核验第 {start + 1}–{start + len(selected)} 条，"
                         f"剩余 {remaining} 条可继续核验（再次调用 start_index={start + len(selected)}）。")
        rows.append({"doi": "", "status": "not_checked",
                     "detail": f"剩余 {remaining} 条未在本轮核验。"})
    if len(references) > len(usable) and not remaining:
        skipped = len(references) - len(selected)
        overflow_note = f"\n另有 {skipped} 条未核验（单次上限 100 条），请分批提交。"
        rows.append({"doi": "", "status": "not_checked",
                     "detail": f"另有 {skipped} 条未核验（单次上限 100 条）。"})

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "DOI核验"
    headers = ["DOI", "状态", "登记库", "输入标题", "登记标题", "输入作者", "登记作者",
               "输入年份", "登记年份", "期刊", "标题相似度", "来源链接", "说明",
               "文献类型", "arXiv ID", "命中来源", "证据链接", "字段冲突"]
    ws.append(headers)
    for row in rows:
        ws.append([_safe_spreadsheet_value(row.get(key, "")) for key in
                   ("doi", "status", "provider", "input_title", "metadata_title", "input_authors",
                    "metadata_authors", "input_year", "metadata_year", "metadata_journal",
                    "title_similarity", "source_url", "detail",
                    "literature_type", "arxiv_id", "evidence_source", "evidence_url", "field_conflicts")])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for column in ws.columns:
        letter = column[0].column_letter
        width = min(60, max(12, max(len(str(cell.value or "")) for cell in column[:200]) + 2))
        ws.column_dimensions[letter].width = width
    out = io.BytesIO()
    wb.save(out)
    filename = _safe_name(Path(item.get("name", "paper")).stem, "文献") + "_DOI核验.xlsx"
    try:
        url = _save_artifact(filename, out.getvalue(),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             logical_task_id="verify_uploaded_references",
                             replace_in_request=True)
    except DeliverableRejected as exc:
        return str(exc)
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return (f"已处理 {len(rows)} 条文献，分 {len(chunks)} 批（每批 ≤{size} 条）："
            + "、".join(f"{key} {value} 条" for key, value in counts.items())
            + "。" + overflow_note + "\n" + "\n".join(batch_lines)
            + "\n\n登记记录只证明文献身份存在，不证明其内容支持某项结论；未查到或网络失败不会判作虚假文献。\n\n"
            f"[下载 DOI 核验表]({url})")


# 2026-10-29 恢复：_DOI_PATTERN 在合并中丢失（引用点未删、定义丢失 → NameError）。
# 定义逐字取自 deploy_backups/20261027_function_convergence_01。
_DOI_PATTERN = re.compile(r"(?i)(?:https?://(?:dx\.)?doi\.org/)?\b10\.\d{4,9}/[^\s<>\[\]{}\"']+")


_ANNOTATION_COLUMNS = ("审查结论", "审查理由", "涉及字段")
_VERDICT_STYLES = {
    "error": ("FBE3DF", "A32B12"),
    "review": ("FDF1D6", "8A5A00"),
    "ok": ("E7F4EA", "1F6B33"),
}
_LEVEL_WORDS = {"ok": "正常", "review": "需核对", "error": "错误"}
_LEVEL_ALIASES = {
    "ok": "ok", "正常": "ok", "无误": "ok", "合理": "ok", "通过": "ok", "一致": "ok",
    "error": "error", "错误": "error", "异常": "error", "不符": "error", "错": "error",
    "review": "review", "需核对": "review", "可疑": "review", "存疑": "review",
    "复核": "review", "不确定": "review",
}


def _annotation_level(verdict: str, level: str = "") -> str:
    """把模型给的结论收敛到三档，保证不同表述进表后口径一致。"""
    for raw in (level, verdict):
        text = str(raw or "").strip()
        if not text:
            continue
        if text.lower() in _LEVEL_ALIASES:
            return _LEVEL_ALIASES[text.lower()]
        for key in ("错误", "异常", "不符", "需核", "可疑", "存疑", "复核", "正常", "无误", "合理", "通过", "一致"):
            if key in text:
                return _LEVEL_ALIASES[key]
    return "review"


def _annotation_style(level: str) -> tuple[str, str]:
    return _VERDICT_STYLES.get(str(level or ""), ("EEF2F6", "33475B"))


_ANNOTATION_SHAPE = ('{"row": 3, "verdict": "可疑", "reason": "……", '
                     '"field": "列名（可选）", "sheet": "工作表名（多表时必填）"}')


def _normalize_annotations(raw: Any, *, errors: list[str] | None = None) -> list[dict[str, Any]]:
    """Validate each entry; a batch caller can keep valid entries and collect errors."""
    if isinstance(raw, dict):
        raw = [raw]
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = []
    if not isinstance(raw, list):
        raise ValueError(
            f"annotations_json 需要数组，元素形如 {_ANNOTATION_SHAPE}；"
            f"实际收到 {type(raw).__name__}")

    def reject(message: str) -> None:
        if errors is None:
            raise ToolArgumentError(message)
        errors.append(message)

    cleaned: list[dict[str, Any]] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            reject(f"annotations_json 第 {position} 个元素必须是对象，形如 {_ANNOTATION_SHAPE}；"
                   f"实际收到 {type(item).__name__}")
            continue
        value = item.get("row")
        if not (type(value) is int or isinstance(value, str)
                and re.fullmatch(r"[+-]?\d+", value.strip())):
            reject(f"annotations_json 第 {position} 个元素需要整数 row"
                   f"（原文件行号，含表头偏移）；实际收到的键：{sorted(item)}；"
                   f"期望元素：{_ANNOTATION_SHAPE}")
            continue
        row = int(value)
        if row < 1:
            reject(f"annotations_json 第 {position} 个元素标注行号无效：{row}，必须从 1 开始")
            continue
        verdict = str(item.get("verdict") or "").strip()[:24]
        reason = str(item.get("reason") or "").strip()[:400]
        field = str(item.get("field") or "").strip()[:60]
        if not verdict and not reason:
            reject(f"annotations_json 第 {position} 个元素（第 {row} 行）的 verdict 与 reason 都为空；"
                   f"实际收到的键：{sorted(item)}；期望元素：{_ANNOTATION_SHAPE}")
            continue
        level = _annotation_level(verdict, str(item.get("level") or ""))
        cleaned.append({"row": row, "level": level, "verdict": _LEVEL_WORDS[level],
                        "reason": reason, "field": field,
                        "sheet": str(item.get("sheet") or "").strip()[:60]})
    return cleaned


def _write_annotated_xlsx(name: str, frame, meta: dict[str, Any], annotations: list[dict[str, Any]],
                          note: str) -> tuple[bytes, dict[str, Any]]:
    """把标注写成原表的副本：原始列 + 审查结论/审查理由/涉及字段，另加「标注说明」页。"""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    header_rows = meta.get("header_rows") or [meta.get("header_row") or 1]
    row_offset = max(int(value) for value in header_rows) + 1
    by_row = {item["row"]: item for item in annotations}
    warnings: list[str] = []
    applied = 0
    columns = [str(col) for col in frame.columns] + list(_ANNOTATION_COLUMNS)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = (str(meta.get("sheet") or "标注") or "标注")[:31]
    sheet.append(columns)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="C25E2E")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for index, values in enumerate(frame.itertuples(index=False, name=None)):
        excel_row = index + row_offset
        annotation = by_row.pop(excel_row, None)
        if annotation:
            applied += 1
        verdict = annotation["verdict"] if annotation else ""
        reason = annotation["reason"] if annotation else ""
        field = annotation["field"] if annotation else ""
        sheet.append([_safe_spreadsheet_value(value) for value in values] + [verdict, reason, field])
        if annotation:
            fill, color = _annotation_style(annotation["level"])
            current = sheet.max_row
            for column in range(len(frame.columns) + 1, len(columns) + 1):
                cell = sheet.cell(row=current, column=column)
                cell.fill = PatternFill("solid", fgColor=fill)
                if column <= len(frame.columns) + 2:
                    cell.font = Font(color=color)
            sheet.cell(row=current, column=len(frame.columns) + 2).alignment = Alignment(
                vertical="top", wrap_text=True)
    for missing in sorted(by_row):
        warnings.append(f"第 {missing} 行不在该表的数据范围内，已忽略")
    total = int(frame.shape[0])
    for index, column in enumerate(columns, start=1):
        width = 12 if index <= len(frame.columns) else 26
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.freeze_panes = "A2"
    guide = workbook.create_sheet("标注说明")
    guide.append(["项目", "内容"])
    for cell in guide[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="6B7A5E")
    rows = [
        ("原文件", name),
        ("工作表", str(meta.get("sheet") or "CSV")),
        ("数据行数", total),
        ("标注行数", applied),
        ("未标注行数", max(0, total - applied)),
        ("说明", note or "按行标注由模型结合原表内容给出，仅作复核线索，不修改原文件，也不推断研究意图。"),
        ("提醒", "审查结论是辅助意见，请结合原始记录与实验设计人工确认后再采信。"),
    ]
    for row in rows:
        guide.append([row[0], row[1]])
    guide.column_dimensions["A"].width = 14
    guide.column_dimensions["B"].width = 96
    for row in guide.iter_rows(min_row=2, max_col=2):
        row[1].alignment = Alignment(vertical="top", wrap_text=True)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), {"rows": total, "annotated": applied, "warnings": warnings}


def _frame_to_workbook_bytes(frame, meta: dict[str, Any]) -> bytes:
    """Compatibility wrapper for creating a one-sheet source workbook."""
    return _frames_to_workbook_bytes([(frame, meta)])[0]


def _frames_to_workbook_bytes(tables: list[tuple[Any, dict[str, Any]]]) -> tuple[bytes, list[str]]:
    """Create a source workbook from every successfully parsed table."""
    from openpyxl import Workbook
    from io import BytesIO

    def excel_value(value):
        if value is None:
            return None
        try:
            import pandas as pd
            if bool(pd.isna(value)):
                return None
        except (ImportError, TypeError, ValueError):
            pass
        if hasattr(value, "item"):
            try:
                value = value.item()
            except (TypeError, ValueError):
                pass
        return value

    workbook = Workbook()
    used_names: set[str] = set()
    output_names: list[str] = []
    for index, (frame, meta) in enumerate(tables):
        raw_name = str(meta.get("sheet") or meta.get("name") or f"Sheet{index + 1}")
        base = re.sub(r"[\\/*?:\[\]]", "_", raw_name).strip()[:31] or f"Sheet{index + 1}"
        name = base
        suffix = 2
        while name.casefold() in used_names:
            tail = f"_{suffix}"
            name = base[:31 - len(tail)] + tail
            suffix += 1
        used_names.add(name.casefold())
        output_names.append(name)
        sheet = workbook.active if index == 0 else workbook.create_sheet()
        sheet.title = name
        header_rows = [int(x) for x in (meta.get("header_rows") or [meta.get("header_row") or 1])]
        for _ in range(max(header_rows) - 1):
            sheet.append([])
        sheet.append(list(frame.columns))
        for row in frame.itertuples(index=False, name=None):
            sheet.append([excel_value(value) for value in row])
    if not tables:
        workbook.active.title = "Sheet1"
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue(), output_names


@tool(response_format="content_and_artifact")
def audit_uploaded_data(file_indexes_json: str | list[int] = "",
                        annotations_json: str | list[dict] = "[]",
                        sheet_name: str = "", header_row: int = 1,
                        header_rows_json: str | list[int] = "",
                        note: str = "", checks_json: str | dict = "") -> tuple[str, dict[str, Any]]:
    """把本轮上传的表格做成「可直接定位问题」的标注 Excel（原始数据不改）。

    默认执行确定性检查并把问题标在**具体单元格**上：浅红填充 + 红色边框 + 批注，
    另加「问题清单」（工作表/单元格/类型/原值/规则/严重程度/建议）与「审查概览」。
    检测项：缺失、非数值、重复行、重复主键、疑似离群（组内 IQR）、越界、跨列逻辑冲突。
    checks_json 可选：{"optional_fields":[...], "required_fields":[...],
    "ranges":{"列":{"min":..,"max":..}},
    "relations":[{"left":"列A","op":"<=","right":"列B"}], "key_columns":["样本ID"]}。
    备注/说明类列（如 Note、备注）在没有任何规则覆盖时按可选列处理；如确需逐格标注缺失，
    用 required_fields 显式声明（或用 ranges/key_columns 等规则覆盖该列）。
    annotations_json 非空时走「按行标注」路径（不执行上述规则扫描）。元素必须是对象：
    {"row": 3, "verdict": "正常|需核对|错误", "reason": "判断依据",
     "field": "涉及列名（可选）", "sheet": "工作表名（多表文件必填）"}。
    row 是原文件的 Excel 行号（含表头偏移，表头占第 1 行时首条数据是第 2 行）；
    verdict 与 reason 不能同时为空；未指定 sheet 的标注只在单表文件上生效。
    一份源文件只产出一个综合标注工作簿。先 inspect_uploaded_table 看清字段与工作表名。
    """
    tables = get_request_tables()
    requested = _json_arg(file_indexes_json, list, list(range(len(tables))),
                          shape="整数数组 [0,1]，序号从 0 开始")
    if any(type(index) is not int for index in requested):
        raise ValueError("file_indexes_json 必须是整数数组")
    if not requested:
        text = "未生成审查文件：当前轮没有选择附件。"
        return text, {
            "_tool_outcome": {
                "status": "not_run", "reason_code": "no_selected_attachment",
                "coverage": "未选择附件", "artifacts": [],
            },
            "audit_results": [],
        }
    annotation_errors: list[str] = []
    annotations = _normalize_annotations(_json_arg(
        annotations_json, (list, dict), [], shape=_ANNOTATION_SHAPE), errors=annotation_errors)
    if annotation_errors and not annotations:
        # Never turn an entirely invalid manual batch into an unrequested rule scan.
        raise ToolArgumentError("没有可采用的标注：" + "；".join(annotation_errors))
    checks = _json_arg(checks_json, dict, {},
                       shape='{"ranges":{…},"relations":[…],"key_columns":[…]}') if checks_json else {}
    header_rows = _parse_header_rows(header_rows_json, header_row)
    results: list[dict[str, Any]] = []
    for index in requested:
        if index < 0 or index >= len(tables):
            raise ValueError(
                f"附件序号无效：{index}；本轮共有 {len(tables)} 个附件，"
                f"可用序号（从 0 开始）：{_attachment_choices(tables)}")
        item = tables[index]
        name = str(item.get("name") or f"附件 {index + 1}")
        raw_bytes = item.get("data") or b""
        source_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        source_file_id = str(item.get("file_id") or f"upload:{source_sha256}")
        try:
            _, first_meta = _load_frame(
                index, sheet_name=sheet_name,
                header_row=int(header_row), header_rows=header_rows,
                discover_only=True)
            sheet_names = [sheet_name] if sheet_name else list(
                first_meta.get("sheets") or [first_meta.get("sheet") or ""])
            suffix = Path(name).suffix.lower()
            if suffix in {".pdf", ".docx"} and len(sheet_names) > 1 and not sheet_name:
                results.append({
                    "file": name,
                    "ok": False,
                    "reason_code": "target_required",
                    "error": (
                        f"从 {suffix[1:].upper()} 提取到 {len(sheet_names)} 个表格；"
                        "请用 sheet_name 指定目标，不能静默选择第一张表"),
                })
                continue
        except Exception as exc:
            detail = str(exc)
            reason_code = (
                "target_not_found"
                if "工作表不存在" in detail or "表格不存在" in detail
                else "attachment_read_failed")
            results.append({
                "file": name, "ok": False, "reason_code": reason_code,
                "error": f"{type(exc).__name__}: {detail}",
            })
            continue

        stem = _short_stem(name)
        if annotations:
            # 按行标注路径：与默认路径一致，一份源文件只出一个综合标注工作簿。
            # 逐表各出一份会让用户拿到几个名字相近的文件，无法判断哪份是最终产物。
            annotated_targets: list[dict[str, Any]] = []
            annotated_sources: list[tuple[Any, dict[str, Any]]] = []
            annotated_skipped: list[dict[str, Any]] = []
            unresolved = [item for item in annotations
                          if (not item["sheet"] and len(sheet_names) > 1)
                          or (item["sheet"] and item["sheet"] not in sheet_names)]
            for sheet in sheet_names:
                try:
                    frame, meta = _load_frame(index, sheet_name=str(sheet or ""),
                                              header_row=int(header_row), header_rows=header_rows)
                except Exception as exc:
                    annotated_skipped.append({"sheet": str(sheet or ""),
                                              "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if frame.empty:
                    annotated_skipped.append({"sheet": str(meta.get("sheet") or sheet or ""),
                                              "error": "该表没有数据行，无需标注"})
                    continue
                sheet_label = str(meta.get("sheet") or "")
                header_rows_used = int(max(meta.get("header_rows")
                                           or [meta.get("header_row") or 1]))
                row_offset = header_rows_used + 1
                # 未指定 sheet 的标注只在唯一可读表上生效；多表时不猜，如实列出。
                applicable = [item for item in annotations
                              if item["sheet"] == sheet_label
                              or (not item["sheet"] and len(sheet_names) == 1)]
                findings = []
                annotated_rows = set()
                for item in applicable:
                    if not row_offset <= int(item["row"]) < row_offset + int(frame.shape[0]):
                        annotated_skipped.append({
                            "sheet": sheet_label,
                            "error": f"第 {item['row']} 行不在该表的数据范围内，已忽略"})
                        continue
                    annotated_rows.add(int(item["row"]))
                    findings.append({
                        "check": "row_annotation", "row": int(item["row"]),
                        "level": item["level"], "field": item["field"],
                        "detail": f"{item['verdict']}：{item['reason']}".rstrip("："),
                    })
                annotated_sources.append((frame, meta))
                annotated_targets.append({
                    "sheet_name": sheet_label,
                    "header_row": header_rows_used,
                    "findings": findings,
                    "row_count": int(frame.shape[0]),
                    "column_count": int(frame.shape[1]),
                    "coverage": {
                        "mode": "manual_annotations",
                        "sheet": sheet_label,
                        "input_rows": int(meta.get("source_rows") or frame.shape[0]),
                        "processed_rows": int(frame.shape[0]),
                        "input_columns": int(meta.get("source_columns") or frame.shape[1]),
                        "processed_columns": int(frame.shape[1]),
                        "annotated_rows": len(annotated_rows),
                        "unannotated_rows": max(0, int(meta.get("source_rows") or frame.shape[0])
                                                 - len(annotated_rows)),
                        "truncation_reasons": list(meta.get("truncation_reasons") or []),
                    },
                })
            if not annotated_targets:
                results.append({"file": name, "ok": False,
                                "reason_code": "no_processable_table",
                                "error": "没有可标注的数据表：" + "；".join(
                                    f"{item['sheet']} {item['error']}" for item in annotated_skipped)})
                continue
            for item in unresolved:
                annotated_skipped.append({
                    "sheet": item["sheet"] or "（未指定）",
                    "error": (f"第 {item['row']} 行标注的工作表不在本文件中，已忽略；"
                              f"可用工作表：{'、'.join(str(s) for s in sheet_names)}"),
                })
            if suffix in {".xlsx", ".xlsm"} and raw_bytes:
                annotated_bytes = raw_bytes
            else:
                annotated_bytes, output_names = _frames_to_workbook_bytes(annotated_sources)
                for target, output_name in zip(annotated_targets, output_names):
                    target["sheet_name"] = output_name
            from src.core.data_audit_xlsx import annotate_workbook_multi
            notes = [note] if note else []
            notes.append("本文件为按行标注结果，未执行确定性规则扫描；"
                         "结论由模型结合原表内容给出，仅作复核线索。")
            notes.extend("未采用标注：" + message for message in annotation_errors)
            notes.extend(f"未采用或未覆盖「{item['sheet']}」：{item['error']}"
                         for item in annotated_skipped)
            try:
                content, stats = annotate_workbook_multi(
                    annotated_bytes, targets=annotated_targets, notes=notes,
                    source_name=name, source_sha256=source_sha256)
            except Exception as exc:
                results.append({"file": name, "ok": False,
                                "error": f"生成标注工作簿失败：{type(exc).__name__}: {exc}"})
                continue
            content = normalize_ooxml_bytes(content)
            try:
                artifact_path = _save_artifact(
                    f"{stem}_标注.xlsx", content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    source_file_id=source_file_id,
                    source_sha256=source_sha256)
            except DeliverableRejected as exc:
                # 未通过的产物不得记为 ok=True —— 那正是"不完整内容被当成完成"。
                results.append({"file": name, "ok": False, "error": str(exc)})
                continue
            total_rows = sum(int(target["coverage"]["input_rows"]) for target in annotated_targets)
            processed_rows = sum(int(target["row_count"]) for target in annotated_targets)
            total_annotated = sum(int(target["coverage"]["annotated_rows"])
                                  for target in annotated_targets)
            results.append({"file": name, "ok": True,
                            "sheets": [target["sheet_name"] for target in annotated_targets],
                            "mode": "row_annotations", "artifact": artifact_path,
                            "rows": total_rows, "annotated": total_annotated,
                            "unannotated": max(0, total_rows - total_annotated),
                            "issues": stats["issues"], "cells": stats["cells"],
                            "kinds": stats["kinds"], "skipped": annotated_skipped,
                            "source_sha256": source_sha256,
                            "artifact_sha256": hashlib.sha256(content).hexdigest(),
                            "artifact_bytes": len(content),
                            "deterministic_checks_executed": False,
                            "annotation_errors": list(annotation_errors),
                            "coverage": {
                                "mode": "manual_annotations",
                                "input_rows": sum(int(t["coverage"]["input_rows"])
                                                  for t in annotated_targets),
                                "processed_rows": processed_rows,
                                "truncation_reasons": sorted({reason for t in annotated_targets
                                                              for reason in t["coverage"]["truncation_reasons"]}),
                                "input_columns": max(int(t["coverage"]["input_columns"])
                                                     for t in annotated_targets),
                                "processed_columns": max(int(t["column_count"])
                                                         for t in annotated_targets),
                                "annotated_rows": total_annotated,
                                "unannotated_rows": max(0, total_rows - total_annotated),
                                "unprocessed_sheets": [item["sheet"] for item in annotated_skipped
                                                       if item["error"].startswith(("ValueError", "OSError"))],
                            }})
            continue

        # 默认路径：把这份文件里所有可读工作表一次性标注进**同一个**输出文件
        targets: list[dict[str, Any]] = []
        source_tables: list[tuple[Any, dict[str, Any]]] = []
        skipped: list[dict[str, Any]] = []
        for sheet in sheet_names:
            try:
                frame, meta = _load_frame(index, sheet_name=str(sheet or ""),
                                          header_row=int(header_row), header_rows=header_rows)
            except Exception as exc:
                skipped.append({"sheet": str(sheet or ""), "error": f"{type(exc).__name__}: {exc}"})
                continue
            if frame.empty:
                skipped.append({"sheet": str(meta.get("sheet") or sheet or ""),
                                "error": "该表没有数据行，无需审查"})
                continue
            header_rows_used = int(max(meta.get("header_rows") or [meta.get("header_row") or 1]))
            source_tables.append((frame, meta))
            findings, _ = _audit_frame(
                frame,
                checks.get("column_mapping") if isinstance(
                    checks.get("column_mapping"), dict) else {},
                checks.get("groups") if isinstance(checks.get("groups"), list) else [],
                checks.get("values") if isinstance(checks.get("values"), list) else [],
                row_offset=header_rows_used + 1,
                integer_data_confirmed=bool(checks.get("integer_data_confirmed")),
                sd_kind=str(checks.get("sd_kind") or ""),
                optional_fields=checks.get("optional_fields") or None,
                required_fields=checks.get("required_fields") or None,
                ranges=checks.get("ranges") or None,
                relations=checks.get("relations") or None,
                key_columns=checks.get("key_columns") or None)
            targets.append({
                "sheet_name": str(meta.get("sheet") or ""),
                "header_row": header_rows_used,
                "findings": findings,
                "row_count": int(frame.shape[0]),
                "column_count": int(frame.shape[1]),
                "coverage": {
                    "sheet": str(meta.get("sheet") or ""),
                    "input_rows": int(meta.get("source_rows")
                                      or frame.shape[0]),
                    "processed_rows": int(frame.shape[0]),
                    "input_columns": int(meta.get("source_columns")
                                         or frame.shape[1]),
                    "processed_columns": int(frame.shape[1]),
                    "findings_truncated": len(findings) >= 20_000,
                    "truncation_reasons": list(meta.get("truncation_reasons") or []),
                    "unscanned_columns": [
                        f"{col}（超过数值关系检查的前30列范围）"
                        for col in frame.columns[30:]
                    ],
                    "unprocessed_sheets": [],
                },
            })
        if not targets:
            results.append({"file": name, "ok": False,
                            "reason_code": "no_processable_table",
                            "error": "没有可审查的数据表：" + "；".join(
                                f"{item['sheet']} {item['error']}" for item in skipped)})
            continue

        if suffix in {".xlsx", ".xlsm"} and raw_bytes:
            source_bytes = raw_bytes
        else:
            source_bytes, output_sheet_names = _frames_to_workbook_bytes(source_tables)
            for target, output_name in zip(targets, output_sheet_names):
                target["sheet_name"] = output_name
        from src.core.data_audit_xlsx import annotate_workbook_multi
        from src.research.data_audit_rules import RULES, rule_manifest
        executed_rules = {
            "empty_table", "empty_column", "missing_values", "missing_cell",
            "constant_field", "mixed_numeric_text", "non_numeric_cell",
            "duplicate_row", "multi_header_suspected", "identical_columns",
            "constant_offset_columns", "constant_ratio_columns",
            "exact_linear_columns", "decimal_ending_concentration",
        }
        skipped_rules: dict[str, str] = {}
        table_columns = [
            {str(column) for column in frame.columns}
            for frame, _meta in source_tables
        ]

        def any_target_has(fields: list[str]) -> bool:
            needed = {str(field) for field in fields if str(field)}
            return bool(needed and any(needed.issubset(columns)
                                       for columns in table_columns))

        configured_ranges = checks.get("ranges")
        if isinstance(configured_ranges, dict) and configured_ranges:
            if any_target_has(list(configured_ranges)):
                executed_rules.add("out_of_range")
            else:
                skipped_rules["out_of_range"] = "配置的字段不在已审查表格中"
        else:
            skipped_rules["out_of_range"] = "未提供字段范围（checks_json.ranges）"
        configured_relations = checks.get("relations")
        if isinstance(configured_relations, list) and configured_relations:
            relation_fields = [
                [str(item.get("left") or ""), str(item.get("right") or "")]
                for item in configured_relations if isinstance(item, dict)
            ]
            if any(any_target_has(fields) for fields in relation_fields):
                executed_rules.add("logic_conflict")
            else:
                skipped_rules["logic_conflict"] = "关系规则字段不在已审查表格中"
        else:
            skipped_rules["logic_conflict"] = "未提供跨列关系规则（checks_json.relations）"
        configured_keys = checks.get("key_columns")
        if isinstance(configured_keys, list) and configured_keys:
            if any_target_has([str(item) for item in configured_keys]):
                executed_rules.add("duplicate_key")
            else:
                skipped_rules["duplicate_key"] = "主键字段不在已审查表格中"
        else:
            skipped_rules["duplicate_key"] = "未声明主键字段（checks_json.key_columns）"
        configured_groups = checks.get("groups")
        group_fields = [str(item) for item in configured_groups] \
            if isinstance(configured_groups, list) else []
        group_available = bool(group_fields) and any_target_has(group_fields)
        if group_available:
            executed_rules.add("unequal_group_size")
            skipped_rules["design_checks_skipped"] = "已确认实验分组字段"
        else:
            reason = "未确认分组字段" if not group_fields else "配置的分组字段不在已审查表格中"
            executed_rules.add("design_checks_skipped")
            skipped_rules.update({
                "unequal_group_size": reason,
                "unequal_replicates": reason,
            })
        configured_values = checks.get("values")
        value_fields = [str(item) for item in configured_values] \
            if isinstance(configured_values, list) else []
        measurements_available = bool(value_fields) and any_target_has(value_fields)
        if group_available and measurements_available:
            executed_rules.update({"iqr_outlier", "iqr_insufficient"})
        else:
            reason = (
                "未同时确认分组字段和测量字段"
                if not (group_available and measurements_available)
                else "组内 IQR 未执行"
            )
            skipped_rules.update({
                "iqr_outlier": reason,
                "iqr_insufficient": reason,
            })
        column_mapping = checks.get("column_mapping")
        mapping = column_mapping if isinstance(column_mapping, dict) else {}
        replicate_field = str(mapping.get("replicate") or "")
        if group_available and replicate_field and any_target_has(
                group_fields + [replicate_field]):
            executed_rules.add("unequal_replicates")
        elif group_available:
            skipped_rules["unequal_replicates"] = "未映射重复编号字段（column_mapping.replicate）"
        integer_fields = [str(mapping.get(key) or "") for key in ("mean", "n")]
        integer_available = bool(checks.get("integer_data_confirmed")) \
            and all(integer_fields) and any_target_has(integer_fields)
        if integer_available:
            executed_rules.add("integer_mean_inconsistent")
            sd_field = str(mapping.get("sd") or "")
            sd_available = bool(sd_field) and any_target_has(integer_fields + [sd_field])
            if checks.get("sd_kind") in {"sample", "population"} and sd_available:
                executed_rules.add("integer_summary_inconsistent")
            else:
                skipped_rules["integer_summary_inconsistent"] = (
                    "未映射 SD 字段或未确认样本 SD／总体 SD")
                skipped_rules["grim_sd_skipped"] = "未映射 SD 字段或未确认 SD 口径"
        else:
            skipped_rules["integer_mean_inconsistent"] = "未确认数据为整数观测并映射均值/样本量字段"
            skipped_rules["integer_summary_inconsistent"] = "未确认整数观测、SD 类型与映射字段"
            skipped_rules["grim_sd_skipped"] = "未确认整数观测与 SD 口径"
        if isinstance(checks.get("groups"), list):
            executed_rules.add("invalid_group_mapping")
        if isinstance(checks.get("values"), list):
            executed_rules.add("invalid_value_mapping")
        rule_catalog = rule_manifest(
            list(RULES), executed=executed_rules, skipped=skipped_rules)
        try:
            content, stats = annotate_workbook_multi(
                source_bytes, targets=targets, notes=[note] if note else [],
                rule_catalog=rule_catalog, source_name=name,
                source_sha256=hashlib.sha256(raw_bytes).hexdigest())
        except Exception as exc:
            results.append({"file": name, "ok": False, "error": f"生成标注工作簿失败：{exc}"})
            continue
        content = normalize_ooxml_bytes(content)
        try:
            artifact_path = _save_artifact(
                f"{stem}_数据审查.xlsx", content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                source_file_id=source_file_id,
                source_sha256=source_sha256)
        except DeliverableRejected as exc:
            results.append({"file": name, "ok": False, "error": str(exc)})
            continue
        results.append({
            "file": name,
            "ok": True,
            "artifact": artifact_path,
            "artifact_sha256": hashlib.sha256(content).hexdigest(),
            "artifact_bytes": len(content),
            "sheets": [target["sheet_name"] for target in targets],
            "rows": sum(int(target["row_count"]) for target in targets),
            "columns": max(int(target["column_count"]) for target in targets),
            "issues": stats["issues"],
            "source_sha256": stats.get("source_sha256", ""),
            "cells": stats["cells"],
            "kinds": stats["kinds"],
            "skipped": skipped,
            "coverage": {
                "input_rows": sum(int(target["coverage"]["input_rows"]) for target in targets),
                "processed_rows": sum(int(target["coverage"]["processed_rows"]) for target in targets),
                "input_columns": max(int(target["coverage"]["input_columns"]) for target in targets),
                "processed_columns": max(int(target["coverage"]["processed_columns"]) for target in targets),
                "findings_truncated": any(bool(target["coverage"]["findings_truncated"])
                                          for target in targets),
                "truncation_reasons": sorted({
                    reason for target in targets
                    for reason in target["coverage"].get("truncation_reasons") or []
                }),
                "unscanned_columns": sorted({
                    item for target in targets
                    for item in target["coverage"].get("unscanned_columns") or []
                }),
                "unprocessed_sheets": [item.get("sheet") for item in skipped],
            },
        })
    if not results:
        text = "未生成审查文件：没有可处理的表格。"
        return text, {
            "_tool_outcome": {
                "status": "not_run", "reason_code": "no_processable_table",
                "coverage": "没有可处理的表格", "artifacts": [],
            },
            "audit_results": results,
        }
    ok_items = [item for item in results if item.get("ok")]
    failed_items = [item for item in results if not item.get("ok")]
    if not ok_items:
        no_table = bool(failed_items) and all(
            item.get("reason_code") == "no_processable_table" for item in failed_items)
        status = "not_run" if no_table else "failed"
        reason = "no_processable_table" if no_table else "audit_failed"
        text = json.dumps({"status": status, "results": results},
                          ensure_ascii=False, default=str)
        return text, {
            "_tool_outcome": {
                "status": status, "reason_code": reason,
                "coverage": f"0/{len(results)} 个文件完成",
                "artifacts": [],
            },
            "audit_results": results,
        }
    total_issues = sum(int(item.get("issues") or 0) for item in ok_items)
    kind_totals: dict[str, int] = {}
    for item in ok_items:
        for kind, count in (item.get("kinds") or {}).items():
            kind_totals[kind] = kind_totals.get(kind, 0) + int(count)
    top = "、".join(f"{k} {v}" for k, v in sorted(kind_totals.items(), key=lambda kv: -kv[1])[:4])
    total_sheets = sum(len(item.get("sheets") or [item.get("sheet") or ""]) for item in ok_items)
    lines = [f"完成 {total_sheets} 个工作表的审查：共 {total_issues} 条待核查信号"
             + (f"，主要是 {top}。" if top else "。")]
    for item in ok_items:
        sheet_names_ = item.get("sheets") or [item.get("sheet") or ""]
        if item.get("mode") == "row_annotations":
            uncovered = int(item.get("unannotated") or 0)
            lines.append(f"- {item['file']}（{'、'.join(sheet_names_)}）：{item['rows']} 行，"
                         f"按你给出的 {item.get('annotated', 0)} 条标注写入"
                         + (f"（{uncovered} 行未覆盖）" if uncovered else "（全部行已覆盖）"))
        else:
            lines.append(f"- {item['file']}（{'、'.join(sheet_names_)}）：{item['rows']} 行 / "
                         f"{item.get('issues', 0)} 条问题，已标在 {item.get('cells', 0)} 个单元格")
        for skipped_item in item.get("skipped") or []:
            lines.append(f"  · 未审查「{skipped_item['sheet']}」：{skipped_item['error']}")
        for message in item.get("annotation_errors") or []:
            lines.append("  · 未采用标注：" + message)
        lines.append(f"  标注文件：{item['artifact']}")
    lines.append("检测命中属于待核查信号，不代表数据造假；原始文件未修改。")
    partial_reasons: list[str] = []
    if failed_items:
        partial_reasons.append("部分文件或工作表未完成")
    if any(item.get("skipped") for item in ok_items):
        partial_reasons.append("部分工作表或标注未处理")
    if any(item.get("annotation_errors") for item in ok_items):
        partial_reasons.append("部分标注条目无效，未采用")
    if any((item.get("coverage") or {}).get("findings_truncated") for item in ok_items):
        partial_reasons.append("问题数量达到上限，发现已截断")
    if any((item.get("coverage") or {}).get("truncation_reasons") for item in ok_items):
        partial_reasons.append("输入行数或单元格数超过审查预算")
    if any(item.get("mode") == "row_annotations" and item.get("unannotated")
           for item in ok_items):
        partial_reasons.append("人工标注模式存在未覆盖行")
    # 数值关系规则的前 30 列是设计上的固定范围，不是这一轮"没做完"。
    # 把它记成 partial 会让宽表永远无法完成（审核包里 FW14 因此重跑 13 次），
    # 但范围必须如实写进回答与文件，不能当作"全表都查过了"。
    scope_notes: list[str] = []
    unscanned = sorted({
        column for item in ok_items
        for column in (item.get("coverage") or {}).get("unscanned_columns") or []
    })
    if unscanned:
        scope_notes.append(
            f"数值关系规则仅扫描每张表的前 30 列；未纳入该规则的列共 {len(unscanned)} 个："
            + "、".join(unscanned[:12]) + ("…" if len(unscanned) > 12 else "")
            + "。这些列的缺失/类型/重复等逐格检查已执行，未做跨列数值关系推断。")
        lines.append("范围说明：" + scope_notes[-1])
    status = "partial" if partial_reasons else "complete"
    reason_code = ("coverage_incomplete" if partial_reasons else
                   "scope_limited" if scope_notes else "audit_completed")
    artifact_links = [item["artifact"] for item in ok_items if item.get("artifact")]
    coverage_text = (
        f"{len(ok_items)}/{len(results)} 个文件完成；"
        f"{total_sheets} 个工作表；{total_issues} 条待核查信号"
        + (f"；{len(unscanned)} 列未纳入数值关系规则" if unscanned else "")
    )
    artifact = {
        "_tool_outcome": {
            "status": status,
            "reason_code": reason_code,
            "coverage": coverage_text,
            "artifacts": artifact_links,
        },
        "audit_status": status,
        "reason_code": reason_code,
        "coverage_summary": coverage_text,
        "results": results,
    }
    return "\n".join(lines), artifact


@tool
def fetch_and_audit_open_supplements(doi: str) -> str:
    """按用户明确要求的 DOI 查找 Europe PMC/NCBI PMC 开放获取补充材料；仅下载可信 PMC OA 清单中的 HTTPS 文件，扫描其中可识别的表格，返回数据线索和下载报告。未找到不代表论文无补充数据。"""
    doi = _normalize_doi(doi)
    if not re.fullmatch(r"10\.\d{4,9}/[^\s<>\[\]]+", doi):
        return "DOI 格式无效；未发起下载。"
    import httpx
    try:
        response = httpx.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                             params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"},
                             timeout=10, follow_redirects=True)
        response.raise_for_status()
        records = response.json().get("resultList", {}).get("result", [])
        record = next((r for r in records if _normalize_doi(r.get("doi", "")) == doi), None)
        pmcid = str((record or {}).get("pmcid") or "")
        if not re.fullmatch(r"PMC\d+", pmcid, re.I):
            return ("Europe PMC 未找到该 DOI 对应的开放全文补充材料入口；"
                    "这不代表论文没有补充数据，其他仓储未在本次查询范围内。")
        oa = httpx.get("https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi",
                       params={"id": pmcid}, timeout=10, follow_redirects=True)
        oa.raise_for_status()
        root = ET.fromstring(oa.content)
        package_url = next((node.attrib.get("href", "") for node in root.iter()
                            if node.tag.lower().endswith("link") and
                            node.attrib.get("href", "").lower().endswith((".tar.gz", ".tgz"))), "")
        if not package_url:
            return (f"已找到开放全文记录 {pmcid}，但 OA 清单没有可下载归档；"
                    "未把它记作无补充数据。")
        from urllib.parse import urlsplit
        parsed = urlsplit(package_url)
        if parsed.scheme != "https" or parsed.hostname not in {"ftp.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov"}:
            return "补充材料下载地址不在可信 PMC OA 主机白名单中，已停止下载。"
        with httpx.stream("GET", package_url, timeout=30, follow_redirects=False) as stream:
            stream.raise_for_status()
            size_header = int(stream.headers.get("content-length") or 0)
            if size_header > 100 * 1024 * 1024:
                return "补充材料归档超过 100 MB，本次未下载。"
            chunks = []; total = 0
            for chunk in stream.iter_bytes():
                total += len(chunk)
                if total > 100 * 1024 * 1024:
                    return "补充材料归档超过 100 MB，已停止下载。"
                chunks.append(chunk)
        archive_bytes = b"".join(chunks)
    except Exception as exc:
        return f"公开补充材料获取未完成：{type(exc).__name__}。未把网络失败解释为文献或数据不存在。"
    supported = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm", ".pdf", ".docx"}
    table_findings = []; scanned = []; skipped = []
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as tar:
            for member in tar.getmembers():
                if not member.isfile() or member.size > 30 * 1024 * 1024:
                    continue
                suffix = Path(member.name).suffix.lower()
                if suffix not in supported:
                    continue
                if len(scanned) >= 30 or sum(len(x[1]) for x in scanned) + member.size > 100 * 1024 * 1024:
                    skipped.append(Path(member.name).name + "（扫描预算达到上限）")
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                content = handle.read(30 * 1024 * 1024 + 1)
                if len(content) > 30 * 1024 * 1024:
                    skipped.append(Path(member.name).name + "（单文件超过 30 MB）"); continue
                scanned.append((Path(member.name).name, content))
    except Exception as exc:
        return f"已下载 PMC OA 归档，但归档解析失败（{type(exc).__name__}）；没有声明审查通过。"
    for name, content in scanned:
        try:
            suffix = Path(name).suffix.lower()
            if suffix in {".csv", ".tsv"}:
                frame, *_ = _decode_csv(content, 1, [1])
            elif suffix in {".xlsx", ".xlsm"}:
                frame = _pd().read_excel(io.BytesIO(content), sheet_name=0, engine="openpyxl")
            elif suffix == ".xls":
                import python_calamine  # noqa: F401
                frame = _pd().read_excel(io.BytesIO(content), sheet_name=0, engine="calamine")
            elif suffix == ".pdf":
                import pdfplumber
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    rows = next((t for page in pdf.pages[:50] for t in (page.extract_tables() or []) if t), None)
                if not rows:
                    skipped.append(name + "（未提取到表格）"); continue
                width = max(len(row) for row in rows)
                headers = _normalize_headers(rows, [1], width)
                frame = _pd().DataFrame([(r + [None] * width)[:width] for r in rows[1:100001]], columns=headers)
            else:
                skipped.append(name + "（Word 表格暂未纳入补充材料批处理）"); continue
            findings, _ = _audit_frame(frame, {}, [], [], row_offset=2)
            for finding in findings:
                finding["field"] = f"{name} · {finding.get('field', '')}"
            table_findings.extend(findings)
        except Exception as exc:
            skipped.append(f"{name}（解析失败：{type(exc).__name__}）")
    meta = {"name": f"PMC OA {pmcid}", "sheet": "补充材料", "rows": len(scanned), "columns": 0,
            "scan_errors": skipped}
    report_bytes = _write_audit_xlsx(f"{doi.replace('/', '_')}_补充数据审查.xlsx", meta, table_findings)
    try:
        report_url = _save_artifact(
            f"{doi.replace('/', '_')}_补充数据审查.xlsx", report_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        archive_url = _save_artifact(f"{pmcid}_补充材料.tar.gz", archive_bytes, "application/gzip")
    except DeliverableRejected as exc:
        return str(exc)
    status = f"找到 {len(scanned)} 个支持格式的材料并完成基础扫描，产生 {len(table_findings)} 项复核线索。"
    if not scanned:
        status = "已取得开放归档，但没有识别到可审查的表格材料；不能据此判断没有数据问题。"
    if skipped:
        status += f"另有 {len(skipped)} 项未能审查：" + "；".join(skipped[:10])
    return (f"{status}\n\n来源：Europe PMC / NCBI PMC OA（{pmcid}）。线索不构成错误或不端结论。\n"
            f"[下载原始补充材料归档]({archive_url})\n[下载数据审查报告]({report_url})")


def _clean_reference_value(value: Any) -> str:
    text = str(value or "")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\r\n\t]+", " ", text).strip()[:4000]


def _render_reference_export(rows: list[dict[str, Any]], output_format: str) -> tuple[str, str, str]:
    """Render verified bibliography metadata without inferring missing fields.

    Callers bound the batch size and reject oversized input; this renderer never
    silently drops rows, so an exported bibliography is the whole supplied list.
    """
    fmt = str(output_format or "xlsx").strip().lower()
    if fmt == "ris":
        records = []
        for row in rows:
            fields = ["TY  - JOUR" if _clean_reference_value(row.get("journal")) else "TY  - GEN"]
            for key, tag in (("title", "TI"), ("journal", "JO"), ("year", "PY"),
                             ("volume", "VL"), ("issue", "IS"), ("pages", "SP"),
                             ("doi", "DO"), ("url", "UR")):
                value = _clean_reference_value(row.get(key, ""))
                if value:
                    fields.append(f"{tag}  - {value}")
            authors = row.get("authors") or []
            if isinstance(authors, str):
                authors = [part.strip() for part in re.split(r"\s*;\s*|\s*\|\s*", authors) if part.strip()]
            if isinstance(authors, list):
                fields.extend(f"AU  - {_clean_reference_value(author)}" for author in authors if _clean_reference_value(author))
            citation = _clean_reference_value(row.get("citation", ""))
            evidence = _clean_reference_value(row.get("evidence", ""))
            claim = _clean_reference_value(row.get("claim", ""))
            notes = " | ".join(x for x in (citation, f"结论: {claim}" if claim else "",
                                             f"证据: {evidence}" if evidence else "") if x)
            if notes:
                fields.append(f"N1  - {notes}")
            fields.append("ER  - ")
            records.append("\r\n".join(fields))
        return "\r\n\r\n".join(records) + "\r\n", ".ris", "application/x-research-info-systems; charset=utf-8"
    if fmt == "bibtex":
        def escape(value: str) -> str:
            value = _clean_reference_value(value)
            substitutions = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}",
                             "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                             "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
            return "".join(substitutions.get(character, character) for character in value)

        records = []
        for index, row in enumerate(rows, 1):
            seed = _clean_reference_value(row.get("doi") or row.get("url") or row.get("citation") or f"ref{index}")
            key = "ref_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
            fields = []
            for key_name, source_key in (("title", "title"), ("author", "authors"),
                                         ("journal", "journal"), ("year", "year"),
                                         ("volume", "volume"), ("number", "issue"),
                                         ("pages", "pages"), ("doi", "doi"), ("url", "url")):
                value = row.get(source_key, "")
                if isinstance(value, list):
                    value = " and ".join(_clean_reference_value(part) for part in value if _clean_reference_value(part))
                elif source_key == "authors" and isinstance(value, str):
                    value = " and ".join(part.strip() for part in re.split(r"\s*;\s*|\s*\|\s*", value) if part.strip())
                value = escape(value)
                if value:
                    fields.append(f"  {key_name} = {{{value}}}")
            citation = escape(row.get("citation", ""))
            evidence = escape(row.get("evidence", ""))
            claim = escape(row.get("claim", ""))
            note = "; ".join(x for x in (citation, f"Claim: {claim}" if claim else "",
                                              f"Evidence: {evidence}" if evidence else "") if x)
            if note:
                fields.append(f"  note = {{{note}}}")
            entry_type = "article" if _clean_reference_value(row.get("journal")) else "misc"
            records.append("@" + entry_type + "{" + key + ",\n" + ",\n".join(fields) + "\n}")
        return "\n\n".join(records) + "\n", ".bib", "application/x-bibtex; charset=utf-8"
    raise ValueError("format 必须为 xlsx、ris 或 bibtex")


def _latest_verification_artifact() -> Path | None:
    """Locate the newest verification workbook produced in this session."""
    from src.core.tracing import get_client_request_id
    session_id, request_id = str(get_session_id() or ""), str(get_client_request_id() or "")
    if not session_id or not request_id:
        return None
    try:
        with sqlite3.connect(str(_db_path())) as conn:
            row = conn.execute(
                "SELECT path,artifact_sha256 FROM artifacts WHERE session_id=? AND request_id=? "
                "AND producer_tool='verify_uploaded_references' "
                "ORDER BY created_at DESC LIMIT 1", (session_id, request_id)).fetchone()
        if not row:
            return None
        path = Path(str(row[0])).resolve()
        root = (PROJECT_ROOT / "state" / "artifacts" / session_id).resolve()
        if not path.is_relative_to(root) or not path.is_file() or _sha256_file(path) != row[1]:
            return None
        return path
    except (sqlite3.Error, OSError, ValueError):
        return None


def _copy_workbook_cells(source: Path, title: str) -> tuple[bytes, int]:
    """Copy the verification sheet verbatim into a newly titled workbook."""
    from openpyxl import Workbook, load_workbook
    src = load_workbook(source, read_only=True, data_only=True)
    dst = Workbook()
    dst.remove(dst.active)
    rows_written = 0
    for sheet in src.worksheets:
        target = dst.create_sheet(title=(sheet.title or "核验")[:31])
        for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
            values = [_safe_spreadsheet_value(value) for value in row]
            if not any(str(value or "").strip() for value in values):
                continue
            target.append(values)
            if row_index:
                rows_written += 1
        for col in range(1, min(target.max_column, 14) + 1):
            target.column_dimensions[target.cell(row=1, column=col).column_letter].width = 24
        target.freeze_panes = "A2"
    src.close()
    out = io.BytesIO(); dst.save(out)
    return out.getvalue(), rows_written


@tool
def export_table(title: str, columns_json: str | list, rows_json: str | list,
                 output_format: str = "csv", append: bool = False) -> str:
    """将明确提供的二维数据导出为 CSV/TSV/XLSX，不推断或补造科学数据。

    columns_json 为非空、无重名列名数组；rows_json 为等宽行数组或以列名为键的对象数组。
    每批最多 5000 行/100000 单元格；较大数据按相同标题分批 append=True。
    仅完整批次写入，超量或列宽不符会明确拒绝；同轮相同追加批次重试不重复。
    """
    columns = _json_arg(columns_json, list, [], shape='列名字符串数组 ["样本","值"]')
    rows = _json_arg(rows_json, list, [], shape='二维数组 [["甲",1],["乙",2]]，每行长度等于列数')
    fmt = str(output_format or "csv").strip().lower()
    if fmt not in {"csv", "tsv", "xlsx"}:
        raise ToolArgumentError("output_format 只支持 csv、tsv、xlsx。")
    if (not columns or len(columns) > 200
            or any(not isinstance(c, str) or not c.strip() for c in columns)
            or len(set(columns)) != len(columns)):
        raise ToolArgumentError("columns_json 必须提供 1–200 个非空且无重名的列名。")
    if len(rows) > 5000 or len(rows) * len(columns) > 100000:
        raise ToolArgumentError("本批数据过多，未写入；请分成不超过 5000 行/100000 单元格的批次。")
    normalized = []
    for index, row in enumerate(rows, 1):
        if isinstance(row, dict):
            if set(row) - set(columns):
                raise ToolArgumentError(f"第 {index} 行存在未声明的列，未写入。")
            values = [row.get(c) for c in columns]
        elif isinstance(row, list) and len(row) == len(columns):
            values = row
        else:
            raise ToolArgumentError(f"第 {index} 行的类型或列数不正确，未写入。")
        if any(v is not None and not isinstance(v, (str, int, float, bool)) for v in values):
            raise ToolArgumentError(f"第 {index} 行包含嵌套值，未写入；单元格须为文字、数字或空值。")
        if any(isinstance(v, float) and not math.isfinite(v) for v in values):
            raise ToolArgumentError(f"第 {index} 行包含非有限数值，未写入；缺失值请用 null。")
        if any(isinstance(v, str) and len(v) > 32767 for v in values):
            raise ToolArgumentError(f"第 {index} 行单元格超过长度限制，未写入。")
        normalized.append([_safe_spreadsheet_value(v) for v in values])
    headers = [_safe_spreadsheet_value(c) for c in columns]
    filename = _safe_name(title, "数据表") + "." + fmt
    if fmt == "xlsx":
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "数据"
        ws.append(headers)
        for row in normalized:
            ws.append(row)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        output = io.BytesIO()
        wb.save(output)
        payload = output.getvalue()
        mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t" if fmt == "tsv" else ",")
        writer.writerow(headers)
        writer.writerows(normalized)
        payload = output.getvalue().encode("utf-8-sig")
        mime = "text/tab-separated-values; charset=utf-8" if fmt == "tsv" else "text/csv; charset=utf-8"
    try:
        url = _save_artifact(filename, payload, mime, append=append)
    except DeliverableRejected as exc:
        return str(exc)
    return f"数据表已生成（本批 {len(rows)} 行、{len(columns)} 列）。[下载 {fmt.upper()}]({url})"


@tool
def export_literature_evidence(title: str, evidence_rows_json: str | list = "",
                               output_format: str = "xlsx",
                               source: str = "", append: bool = False) -> str:
    """导出现有检索/核验结果；output_format 可选 xlsx、ris、bibtex。

    source="last_verification" 时直接取本轮已生成的核验表内容，支持 xlsx/ris/bibtex，
    不必再手写整份清单（长清单手写容易撞输出上限被截断）。行字段可含 citation、title、
    authors、journal、year、volume、issue、pages、claim、evidence、source、doi、url；
    缺失字段不补造。evidence_rows_json 每批 1–2000 条，超量明确拒绝、不静默截断。
    """
    handoff = str(source or "").strip().lower()
    if handoff in {"last_verification", "verification", "verify"} or (
            not str(evidence_rows_json or "").strip() and handoff):
        artifact = _latest_verification_artifact()
        if artifact is None:
            raise ToolArgumentError(
                "没有找到本会话已生成的核验表；请先调用 verify_uploaded_references，"
                "或改用 evidence_rows_json 提供条目。")
        if str(output_format or "xlsx").lower() in {"ris", "bibtex"}:
            from openpyxl import load_workbook
            book = load_workbook(artifact, read_only=True, data_only=True)
            try:
                sheet = book.worksheets[0]
                headers = [c.value for c in next(sheet.iter_rows(max_row=1))]
                records = []
                for values in sheet.iter_rows(min_row=2, values_only=True):
                    row = dict(zip(headers, values))
                    if not (row.get("DOI") or row.get("登记标题") or row.get("输入标题")):
                        continue
                    records.append({"doi": row.get("DOI") or "", "title": row.get("登记标题") or row.get("输入标题") or "",
                                    "authors": row.get("登记作者") or row.get("输入作者") or "",
                                    "year": row.get("登记年份") or row.get("输入年份") or "", "journal": row.get("期刊") or "",
                                    "url": row.get("来源链接") or "", "evidence": f"文献身份核验状态：{row.get('状态') or '未提供'}"})
            finally:
                book.close()
            return export_literature_evidence.func(title=title, evidence_rows_json=records, output_format=output_format, append=append)
        if output_format not in {"xlsx", ""}:
            raise ToolArgumentError("output_format 只支持 xlsx、ris、bibtex。")
        body, count = _copy_workbook_cells(artifact, title)
        filename = _safe_name(title, "文献核验") + ".xlsx"
        try:
            url = _save_artifact(
                filename, body,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                logical_task_id="export_literature_evidence", replace_in_request=True, append=append)
        except DeliverableRejected as exc:
            return str(exc)
        return (f"已整理 {count} 条核验记录（来自上一步核验结果）。"
                f"[下载 XLSX]({url})")
    rows = _json_arg(evidence_rows_json, list, [],
                     shape='[{"citation":"…","doi":"10.xxxx/yyy","evidence":"…"}]')
    if not rows or len(rows) > 2000 or any(not isinstance(row, dict) for row in rows):
        raise ToolArgumentError("需要 1–2000 条完整文献对象；本批未写入，请缩小批次，不会静默截断。")
    output_format = str(output_format or "xlsx").strip().lower()
    if output_format in {"ris", "bibtex"}:
        body, extension, mime = _render_reference_export(rows, output_format)
        filename = _safe_name(title, "文献证据") + extension
        try:
            url = _save_artifact(filename, body.encode("utf-8"), mime, append=append)
        except DeliverableRejected as exc:
            return str(exc)
        return f"文献引用已导出（{len(rows)} 条）。[下载 {output_format.upper()}]({url})"
    if output_format != "xlsx":
        return "[ERR_PARSE] output_format 只支持 xlsx、ris 或 bibtex。"
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "文献证据"
    headers = ["文献", "支持的结论", "原文/证据摘要", "来源", "DOI", "链接"]
    ws.append([_safe_spreadsheet_value(value) for value in headers])
    for item in rows:
        ws.append([_safe_spreadsheet_value(item.get(key, "")) for key in
                   ("citation", "claim", "evidence", "source", "doi", "url")])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for col, width in zip("ABCDEF", (34, 42, 70, 20, 25, 55)):
        ws.column_dimensions[col].width = width
    out = io.BytesIO(); wb.save(out)
    filename = _safe_name(title, "文献证据") + ".xlsx"
    try:
        url = _save_artifact(
            filename, out.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            append=append)
    except DeliverableRejected as exc:
        return str(exc)
    return f"文献证据表已生成（{len(rows)} 条）。[下载 XLSX]({url})"


def _export_markdown(title: str, content: str, kind: str, svg_code: str = "",
                     append: bool = False) -> str:
    body = str(content or "").strip()
    svg_source = str(svg_code or "").strip()
    if not svg_source:
        svg_block = re.search(r"```(?:svg|xml)\s*\n([\s\S]*?)```", body, re.I)
        if svg_block:
            svg_source = svg_block.group(1).strip()
        else:
            inline_svg = re.search(r"<svg\b[\s\S]*?</svg\s*>", body, re.I)
            if inline_svg:
                svg_source = inline_svg.group(0)
    if svg_source:
        fenced = re.fullmatch(r"```(?:svg|xml)?\s*([\s\S]*?)\s*```", svg_source, re.I)
        if fenced:
            svg_source = fenced.group(1).strip()
        else:
            start = svg_source.lower().find("<svg")
            end = svg_source.lower().rfind("</svg>")
            if start >= 0 and end >= start:
                svg_source = svg_source[start:end + len("</svg>")]
        svg_code = _sanitize_svg_for_export(svg_source)
        if not svg_code:
            return "[SVG_FORMAT_ERROR] SVG 不完整或未通过安全校验，未生成图形文件。"
    if not body and svg_code:
        body = f"## 图示\n\n```svg\n{svg_code}\n```"
    if not body:
        return "[ERR_PARSE] 导出内容不能为空；请提供正文或有效 SVG。"
    supplied_title = str(title or "").strip()
    heading = re.search(r"^#\s+(.+)$", body, re.M)
    generic = {"汇总", "导出文件", "课题记录", "文件", "结果", "报告"}
    if (not supplied_title or supplied_title in generic) and heading:
        supplied_title = heading.group(1).strip()
    if not supplied_title or supplied_title in generic:
        svg_title = re.search(r"<title\b[^>]*>([\s\S]*?)</title>", svg_code, re.I)
        if svg_title:
            supplied_title = html.unescape(re.sub(r"<[^>]+>", "", svg_title.group(1))).strip()
    final_title = supplied_title or kind
    if svg_code and "<svg" not in body.lower():
        body += "\n\n## 配图\n\n" + str(svg_code).strip()
    # 必须在套 <html> 外壳**之前**校验正文。外壳会把任何断尾正文包成"结构
    # 完整"（</html> 齐全、标签平衡），截断因此在磁盘上完全不可见；这里用断尾
    # 在文本上留下的确定痕迹（未闭合代码围栏 / 半个 <svg> / 无标题且过短）
    # 判掉，并把真实原因交给调用方，而不是生成一份"看起来成功"的半份报告。
    try:
        validate_markdown_payload(body, filename=_safe_name(final_title or kind, kind))
    except DeliverableRejected as exc:
        return str(exc) + " 本批未发布；请补全正文，或改用分块文档工具继续写入。"
    rendered = _markdown_to_safe_html(body)
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(final_title)}</title><style>
body{{max-width:980px;margin:40px auto;padding:0 24px;color:#242424;font:16px/1.75 system-ui,"Microsoft YaHei",sans-serif}}
h1,h2,h3{{line-height:1.35;margin-top:1.6em}} table{{border-collapse:collapse;width:100%;margin:1em 0;font-size:14px}}
th,td{{border:1px solid #ddd;padding:8px 10px;text-align:left;vertical-align:top}} th{{background:#f6f6f6}}
pre{{overflow:auto;background:#f6f6f6;padding:14px;border-radius:8px}} code{{background:#f6f6f6;padding:2px 4px;border-radius:4px}}
a{{color:#d97706}} .figure{{max-width:100%;overflow:auto;margin:20px 0}} .figure svg{{display:block;max-width:100%;height:auto}}
.muted{{color:#777;font-size:13px}}
</style></head><body><h1>{html.escape(final_title)}</h1>{rendered}</body></html>'''
    filename = _safe_name(final_title, kind) + ".html"
    # 发布前再按「交付契约」校验**最终文档**（外壳 + 渲染结果）：外壳拼坏了、
    # 渲染丢了正文都在这里拦下，而不是等模型说「已生成」。
    try:
        validate_html_document(document.encode("utf-8"), filename=filename)
    except DeliverableRejected as exc:
        return str(exc)
    try:
        url = _save_artifact(filename, document.encode("utf-8"), "text/html; charset=utf-8", append=append)
    except DeliverableRejected as exc:
        # 真实失败原因直接回给模型：绝不能让它把"没发出去"说成"已生成"。
        return str(exc)
    links = [f"[下载 HTML]({url})"]
    if svg_code:
        svg_filename = _safe_name(final_title, kind) + ".svg"
        try:
            svg_url = _save_artifact(svg_filename, svg_code.encode("utf-8"),
                                     "image/svg+xml; charset=utf-8", append=append)
        except DeliverableRejected as exc:
            svg_url = ""
            links.append(str(exc))
        if svg_url:
            links.append(f"[下载 SVG]({svg_url})")
    suffix = "（SVG 已内嵌）" if svg_code else ""
    return f"{kind}已生成 HTML{suffix}：" + " ｜ ".join(links)


@tool
def export_paper_analysis(analysis_markdown: str = "", title: str = "", svg_code: str = "",
                          append: bool = False) -> str:
    """将本轮论文解读或绘图导出为 HTML；title 可省略并根据正文标题或 SVG 标题自动命名，svg_code 可单独用于导出绘图。"""
    return _export_markdown(title, analysis_markdown, "论文解读", svg_code, append=append)


@tool
def export_experiment_design(design_markdown: str = "", title: str = "", svg_code: str = "",
                             append: bool = False) -> str:
    """导出实验设计 HTML；注明待确认假设。适合随文呈现的 SVG 可放入 svg_code 自动内嵌。"""
    return _export_markdown(title, design_markdown, "实验设计草案", svg_code, append=append)


@tool
def save_research_note(project_name: str, note: str) -> str:
    """仅在用户明确要求时，向该账号跨会话共享的课题记录写入一条备注。"""
    sid = str(get_session_id() or "")
    owner = get_user_id()
    project = str(project_name or "").strip()[:120]
    text = str(note or "").strip()[:10000]
    if not sid or not owner or not project or not text:
        return "[ERR_PARSE] 需要有效登录账号、会话、课题名称和非空记录"
    with sqlite3.connect(str(_db_path())) as conn:
        conn.execute("INSERT INTO research_notes(session_id,owner_user_id,project_name,note,created_at) "
                     "VALUES(?,?,?,?,?)", (sid, int(owner), project, text, time.time()))
    return f"已将记录保存到账号内课题“{project}”，可在该账号的其他会话回顾；不会写入共享知识库。"


@tool
def recall_research_notes(project_name: str, limit: int = 20) -> str:
    """回顾当前登录账号在不同会话中已明确保存的指定课题记录。"""
    owner = get_user_id()
    project = str(project_name or "").strip()[:120]
    if not owner or not project:
        return "[ERR_PARSE] 需要有效登录账号和课题名称"
    with sqlite3.connect(str(_db_path())) as conn:
        rows = conn.execute("SELECT note,created_at FROM research_notes WHERE owner_user_id=? "
                            "AND project_name=? ORDER BY id DESC LIMIT ?",
                            (int(owner), project, max(1, min(int(limit), 50)))).fetchall()
    if not rows:
        return f"当前账号没有课题“{project}”的已保存记录。"
    return "\n".join(f"- [{datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec='minutes')}] {note}"
                     for note, ts in reversed(rows))


@tool
def export_research_notes(project_name: str, limit: int = 100, svg_code: str = "") -> str:
    """将课题记录导出为按课题命名的 HTML 文件；若记录配有 SVG，将代码放入 svg_code 自动安全清理并内嵌。"""
    owner = get_user_id()
    project = str(project_name or "").strip()[:120]
    if not owner or not project:
        return "[ERR_PARSE] 需要有效登录账号和课题名称"
    with sqlite3.connect(str(_db_path())) as conn:
        rows = conn.execute("SELECT note,created_at FROM research_notes WHERE owner_user_id=? "
                            "AND project_name=? ORDER BY id ASC LIMIT ?",
                            (int(owner), project, max(1, min(int(limit), 500)))).fetchall()
    if not rows:
        return f"当前账号没有课题“{project}”的记录可导出。"
    body = "\n\n".join(
        f"## {datetime.fromtimestamp(ts, timezone.utc).astimezone().isoformat(timespec='minutes')}\n\n{note}"
        for note, ts in rows)
    return _export_markdown(project + "课题记录", body, "课题记录", svg_code)


@tool
def export_corpus_gap_report(title: str, gaps_json: str, append: bool = False) -> str:
    """导出本轮已观察到的本地语料缺口或网页获取失败记录。每项可含 topic、doi_or_title、failure、local_result、source_url；只记录实际观察到的信息。"""
    gaps = _json_arg(gaps_json, list, [],
                     shape='[{"topic":"…","doi_or_title":"…","failure":"…"}]')
    fields = ["topic", "doi_or_title", "failure", "local_result", "source_url"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for item in gaps[:5000]:
        row = item if isinstance(item, dict) else {}
        writer.writerow({field: _safe_spreadsheet_value(row.get(field, "")) for field in fields})
    try:
        url = _save_artifact(_safe_name(title, "语料缺口") + ".csv",
                             output.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                             append=append)
    except DeliverableRejected as exc:
        return str(exc)
    return f"语料缺口报告已生成（{min(len(gaps), 5000)} 条）。[下载 CSV]({url})"


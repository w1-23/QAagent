# -*- coding: utf-8 -*-
"""Append-only source ledger for one answer turn.

``tool_results`` remains the full execution record. This ledger stores only the
small, user-visible source identity needed by the answer bubble, including
sources reused from the recent session-evidence ledger.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit
from typing import Any

_schema_ready = False
_ANGLE_TAG = re.compile(r"<[^>]{1,120}>")
_ABSOLUTE_PATH = re.compile(
    r"(?:(?:[A-Za-z]:[\\/])|(?:/(?:home|root|opt|srv|var|mnt)/))[^\s\"']+"
)


def _connect() -> sqlite3.Connection:
    from src.session.manager import DB_PATH, _connect_db
    return _connect_db(str(DB_PATH))


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS turn_source_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                origin TEXT NOT NULL,
                kind TEXT NOT NULL,
                source_key TEXT NOT NULL,
                title TEXT,
                url TEXT,
                snippet TEXT,
                source_turn_seq INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_turn_source_identity "
            "ON turn_source_ledger(session_id, request_id, origin, kind, source_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_source_request "
            "ON turn_source_ledger(session_id, request_id, origin, id)"
        )
        conn.commit()
    _schema_ready = True


def safe_http_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    return raw


def _safe_text(value: object, limit: int) -> str:
    text = " ".join(_ANGLE_TAG.sub(" ", str(value or "")).split())
    text = _ABSOLUTE_PATH.sub("[路径已隐藏]", text)
    return text[:max(0, int(limit))]


def safe_text(value: object, limit: int) -> str:
    """Public display sanitizer shared by ledger readers and API projection."""
    return _safe_text(value, limit)


def _insert(row: dict[str, Any]) -> bool:
    required = ("session_id", "request_id", "origin", "kind", "source_key")
    if any(not str(row.get(key) or "").strip() for key in required):
        return False
    try:
        ensure_schema()
        stamp = datetime.now(timezone.utc).isoformat()
        with _connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO turn_source_ledger
                   (session_id,request_id,origin,kind,source_key,title,url,
                    snippet,source_turn_seq,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(row["session_id"]), str(row["request_id"]),
                    str(row["origin"]), str(row["kind"]),
                    str(row["source_key"]), _safe_text(row.get("title"), 200),
                    safe_http_url(row.get("url")) if row["kind"] == "web" else "",
                    _safe_text(row.get("snippet"), 320),
                    row.get("source_turn_seq"), stamp,
                ),
            )
            conn.commit()
        return True
    except Exception:
        return False


def _local_source_key(row: dict) -> str:
    from src.core.corpus_source import is_valid_record_key

    for key in ("record_key", "paper_id", "record_id", "doc_id"):
        value = str(row.get(key) or "").strip()
        if is_valid_record_key(value):
            return value
    chunk = str(row.get("chunk_id") or "").split("::", 1)[0].split(":", 1)[0]
    return chunk if is_valid_record_key(chunk) else ""


def _local_title(row: dict, record_key: str) -> str:
    from src.core.record_label import display_label

    if isinstance(row, dict):
        title = row.get("title") or row.get("paper_title")
        if title:
            return display_label(row)
    return "本地文献"


def record_tool_sources(session_id: str, request_id: str, *,
                        artifact: dict | None,
                        result_text: str = "",
                        limit: int = 12) -> int:
    """Register successful current-turn sources produced by a real tool."""
    sid, rid = str(session_id or ""), str(request_id or "")
    if not sid or not rid:
        return 0
    data = artifact if isinstance(artifact, dict) else {}
    rows: list[tuple[str, dict]] = []
    for row in (data.get("main_results") or []):
        if isinstance(row, dict):
            rows.append(("local", row))
    for row in (data.get("web_results") or []):
        if isinstance(row, dict):
            rows.append(("web", row))

    # Older tools may return text only. Reuse the existing parser as a fallback.
    if not rows and result_text:
        from src.core.turn_sources import parse_sources
        for item in parse_sources(result_text, limit=limit):
            key = item.get("record_key") or item.get("url") or ""
            rows.append((str(item.get("kind") or ""), {
                "record_key": key, "url": item.get("url") or "",
                "title": item.get("title") or "", "snippet": item.get("snippet") or "",
            }))

    added = 0
    seen: set[str] = set()
    for kind, row in rows:
        if len(seen) >= max(1, int(limit or 12)):
            break
        if kind == "local":
            key = _local_source_key(row)
            if not key or key in seen:
                continue
            seen.add(key)
            item = {
                "session_id": sid, "request_id": rid, "origin": "current",
                "kind": "local", "source_key": key,
                "title": _local_title(row, key),
                "snippet": row.get("text") or row.get("snippet") or "",
                "url": "",
            }
        elif kind == "web":
            url = safe_http_url(row.get("url") or row.get("link"))
            if not url or url in seen:
                continue
            seen.add(url)
            from src.core.web_output_separator import clean_web_title
            item = {
                "session_id": sid, "request_id": rid, "origin": "current",
                "kind": "web", "source_key": url,
                "title": clean_web_title(
                    row.get("title"), url, row.get("site_name") or row.get("media") or ""),
                "url": url,
                "snippet": row.get("summary") or row.get("snippet")
                or row.get("abstract") or row.get("text") or "",
            }
        else:
            continue
        if _insert(item):
            added += 1
    return added


def record_history_sources(session_id: str, request_id: str,
                           turn_seqs: list[int], limit: int = 12) -> int:
    """Copy the actually selected recent evidence into this turn's source view."""
    sid, rid = str(session_id or ""), str(request_id or "")
    turns = sorted({int(v) for v in (turn_seqs or []) if str(v).strip().isdigit()})
    if not sid or not rid or not turns:
        return 0
    try:
        ensure_schema()
        marks = ",".join("?" for _ in turns)
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT turn_seq,evidence_json FROM session_evidence "
                f"WHERE session_id=? AND turn_seq IN ({marks}) ORDER BY turn_seq",
                (sid, *turns),
            ).fetchall()
    except Exception:
        return 0

    added = 0
    seen: set[tuple[str, str]] = set()
    for turn_seq, evidence_json in rows:
        try:
            evidence = json.loads(evidence_json or "[]")
        except Exception:
            continue
        for entry in evidence if isinstance(evidence, list) else []:
            if not isinstance(entry, dict) or len(seen) >= max(1, int(limit or 12)):
                continue
            url = safe_http_url(entry.get("url"))
            source = str(entry.get("source") or "").lower()
            if url and (source == "web" or "http" in url):
                marker = ("web", url)
                if marker in seen:
                    continue
                seen.add(marker)
                from src.core.web_output_separator import clean_web_title
                item = {
                    "session_id": sid, "request_id": rid, "origin": "history",
                    "kind": "web", "source_key": url,
                    "title": clean_web_title(entry.get("title"), url),
                    "url": url, "snippet": entry.get("snippet") or "",
                    "source_turn_seq": int(turn_seq),
                }
            else:
                key = _local_source_key(entry)
                if not key:
                    continue
                marker = ("local", key)
                if marker in seen:
                    continue
                seen.add(marker)
                item = {
                    "session_id": sid, "request_id": rid, "origin": "history",
                    "kind": "local", "source_key": key,
                    "title": _local_title(entry, key),
                    "url": "", "snippet": entry.get("snippet") or "",
                    "source_turn_seq": int(turn_seq),
                }
            if _insert(item):
                added += 1
    return added


def fetch_rows(session_id: str, request_id: str) -> list[dict]:
    sid, rid = str(session_id or ""), str(request_id or "")
    if not sid or not rid:
        return []
    try:
        ensure_schema()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT origin,kind,source_key,title,url,snippet,source_turn_seq "
                "FROM turn_source_ledger WHERE session_id=? AND request_id=? "
                "ORDER BY CASE origin WHEN 'current' THEN 0 ELSE 1 END, "
                "CASE kind WHEN 'local' THEN 0 ELSE 1 END, id",
                (sid, rid),
            ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []

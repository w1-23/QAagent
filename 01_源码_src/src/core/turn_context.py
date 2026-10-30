"""Request-level record of what was actually sent to the model (D-A1).

Why this exists
---------------
The context panel used to *rebuild* a plausible context from stored messages.
That is a guess: it cannot show which messages the Agent actually handed to the
model, nor the real token counts of that request.  This module records the
payload at the single funnel (``general_agent_graph._call_llm``) so the panel can
show measured facts and say "measured" instead of "estimated".

Hard boundaries
---------------
* Observation only.  Nothing here edits the payload, the prompt, the message
  list, or the loop; a failure here is swallowed and logged at debug level.
* No private reasoning, no system prompt body, no keys: entries carry role,
  character count and a PII-masked preview only.
* Storage is a separate table (``turn_context``) in the existing sessions.db, so
  no existing column or reader changes shape.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any

from src.config import PROJECT_ROOT
from src.core.pii_mask import mask_sensitive

logger = logging.getLogger(__name__)

_DB_PATH = PROJECT_ROOT / "state" / "sessions.db"
_LOCK = threading.Lock()
PREVIEW_CHARS = 160
KEEP_PER_SESSION = 200

# langchain message type -> wire role
_KINDS = {"human": "user", "ai": "assistant", "system": "system",
          "tool": "tool", "function": "tool"}

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS turn_context (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT    NOT NULL,
    request_id    TEXT    NOT NULL DEFAULT '',
    created_at    REAL    NOT NULL,
    model         TEXT    NOT NULL DEFAULT '',
    msg_count     INTEGER NOT NULL DEFAULT 0,
    chars         INTEGER NOT NULL DEFAULT 0,
    tool_msgs     INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    error         TEXT    NOT NULL DEFAULT '',
    payload_json  TEXT    NOT NULL DEFAULT '{}'
)
"""


def _kind_of(message: Any) -> str:
    kind = str(getattr(message, "type", "") or "").lower()
    if kind in _KINDS:
        return _KINDS[kind]
    name = type(message).__name__.lower()
    for key, value in _KINDS.items():
        if key in name:
            return value
    return name or "unknown"


def _flat_content(content: Any) -> str:
    """Multimodal payloads carry a list of parts; keep only their text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        return " ".join(parts)
    return "" if content is None else str(content)


def capture(messages, *, model: str = "") -> dict:
    """Summarize the exact list handed to the model. Never raises."""
    entries: list[dict] = []
    chars = 0
    tool_msgs = 0
    try:
        for index, message in enumerate(messages or []):
            kind = _kind_of(message)
            text = _flat_content(getattr(message, "content", ""))
            chars += len(text)
            if kind == "tool":
                tool_msgs += 1
            entry: dict = {
                "i": index,
                "kind": kind,
                "chars": len(text),
                "preview": mask_sensitive(text)[:PREVIEW_CHARS],
            }
            calls = getattr(message, "tool_calls", None) or []
            if calls:
                entry["tool_calls"] = [str(c.get("name") or "") for c in calls
                                       if isinstance(c, dict)]
            if kind == "tool":
                entry["tool_call_id"] = str(getattr(message, "tool_call_id", "") or "")
                entry["name"] = str(getattr(message, "name", "") or "")
            entries.append(entry)
    except Exception as exc:                     # pragma: no cover - defensive
        logger.debug("[turn_context] capture failed: %s", type(exc).__name__)
    return {"model": model, "msg_count": len(entries), "chars": chars,
            "tool_msgs": tool_msgs, "messages": entries}


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=5.0)
    conn.execute(_TABLE_SQL)
    return conn


def record(snapshot: dict, *, session_id: str, request_id: str = "",
           usage: dict | None = None, error: str = "") -> int:
    """Append one row for one model call. Returns the row id (0 on failure)."""
    if not session_id:
        return 0
    usage = usage or {}
    try:
        input_tokens = max(0, int(usage.get("input_tokens") or 0))
    except (TypeError, ValueError):
        input_tokens = 0
    try:
        output_tokens = max(0, int(usage.get("output_tokens") or 0))
    except (TypeError, ValueError):
        output_tokens = 0
    payload = json.dumps(snapshot or {}, ensure_ascii=False)
    with _LOCK:
        conn = None
        try:
            conn = _connect()
            with conn:
                cur = conn.execute(
                    "INSERT INTO turn_context(session_id, request_id, created_at, model,"
                    " msg_count, chars, tool_msgs, input_tokens, output_tokens, error,"
                    " payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (session_id, request_id or "", time.time(),
                     str((snapshot or {}).get("model") or "")[:80],
                     int((snapshot or {}).get("msg_count") or 0),
                     int((snapshot or {}).get("chars") or 0),
                     int((snapshot or {}).get("tool_msgs") or 0),
                     input_tokens, output_tokens, error[:120], payload))
                row_id = int(cur.lastrowid or 0)
                conn.execute(
                    "DELETE FROM turn_context WHERE session_id=? AND id NOT IN "
                    "(SELECT id FROM turn_context WHERE session_id=? "
                    "ORDER BY id DESC LIMIT ?)",
                    (session_id, session_id, KEEP_PER_SESSION))
                return row_id
        except Exception as exc:
            logger.debug("[turn_context] record failed: %s", type(exc).__name__)
            return 0
        finally:
            # sqlite 的 with 只提交不关闭；Windows 上不关会把库文件锁住。
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def latest(session_id: str, request_id: str = "", limit: int = 50) -> dict:
    """Newest-first rows for one session (optionally one request). Never raises."""
    out: dict = {"found": False, "calls": 0, "rows": [], "input_tokens": 0,
                 "output_tokens": 0, "scope": "measured"}
    if not session_id:
        out["scope"] = "unavailable"
        return out
    conn = None
    try:
        conn = _connect()
        conn.row_factory = sqlite3.Row
        sql = ("SELECT id, request_id, created_at, model, msg_count, chars,"
               " tool_msgs, input_tokens, output_tokens, error, payload_json"
               " FROM turn_context WHERE session_id=?")
        args: list = [session_id]
        if request_id:
            sql += " AND request_id=?"
            args.append(request_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit or 50), 200)))
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception as exc:
        logger.debug("[turn_context] read failed: %s", type(exc).__name__)
        out["scope"] = "unavailable"
        return out
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if not rows:
        return out
    for row in rows:
        try:
            row["payload"] = json.loads(row.pop("payload_json") or "{}")
        except Exception:
            row["payload"] = {}
            row.pop("payload_json", None)
    out.update({
        "found": True,
        "calls": len(rows),
        "rows": rows,
        "input_tokens": sum(int(r.get("input_tokens") or 0) for r in rows),
        "output_tokens": sum(int(r.get("output_tokens") or 0) for r in rows),
        "last": rows[0],
    })
    return out

"""Metadata-only observations of logical calls and prepared physical attempts.

``capture_request`` observes the SDK-converted JSON request body at transport
send time. It never persists messages, tool arguments, schemas, URLs or headers.
Sizes and hashes use canonical JSON, not a claim about HTTP framing/tokenization.
The old capture/record/latest surface remains available to admin readers; rows
and returned-window sums explicitly distinguish logical and physical scopes.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from typing import Any

from src.config import PROJECT_ROOT

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
    """Legacy logical-call observation. It is not a transport attempt."""
    entries: list[dict] = []
    chars = 0
    try:
        for index, message in enumerate(messages or []):
            kind = _kind_of(message)
            kind = kind if kind in {"system", "user", "assistant", "tool"} else "unknown"
            text = _flat_content(getattr(message, "content", ""))
            chars += len(text)
            entries.append({"i": index, "kind": kind, "chars": len(text),
                            "hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            "tool_call_count": len(getattr(message, "tool_calls", None) or [])})
    except Exception as exc:                     # pragma: no cover - defensive
        logger.debug("[turn_context] capture failed: %s", type(exc).__name__)
    return {"scope": "logical_call", "model": _identifier(model),
            "msg_count": len(entries), "chars": chars,
            "tool_msgs": sum(e["kind"] == "tool" for e in entries), "messages": entries}


_previous_layout = contextvars.ContextVar("prepared_request_layout", default=None)


def _identifier(value) -> str:
    """Identifiers are application metadata, never free-form provider errors."""
    value = str(value or "")
    return value[:120] if re.fullmatch(r"[\w./:@+\-]*", value, flags=re.ASCII) else ""


def _fingerprint(value) -> dict:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    encoded = text.encode("utf-8")
    return {"chars": len(text), "bytes": len(encoded), "hash": hashlib.sha256(encoded).hexdigest()}


def capture_request(payload, *, model: str = "", purpose: str = "",
                    attempt_id: str = "", epoch=0, logical_call_id: str = "",
                    previous: dict | None = None, change_reason: str = "") -> dict:
    """Observe converted SDK parameters; never mutate or retain their contents.

    The client hook runs in each explicit attempt with SDK retries disabled.
    SDK extra_body merging and wire serialization happen later. Pass only the
    parameters, never headers/client objects. ``common_prefix`` counts complete layout
    parts (tools, message objects, then other options), not wire bytes or tokens.
    ``previous`` may be the prior returned metadata; otherwise the latest layout
    in this turn's context/model/purpose is compared. Epoch changes are explicit.
    """
    out = {"scope": "physical_attempt", "layout_version": 1,
           "serialization": "canonical_json", "model": _identifier(model),
           "purpose": _identifier(purpose), "attempt_id": _identifier(attempt_id),
           "logical_call_id": _identifier(logical_call_id),
           "epoch": _identifier(epoch) if not isinstance(epoch, int) else epoch,
           "messages": [], "parts": [], "msg_count": 0, "tool_msgs": 0, "chars": 0}
    try:
        if not isinstance(payload, dict):
            out["change_reason"] = "invalid_prepared_payload"
            return out
        out.update(_fingerprint(payload))
        ordered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        out["ordered_parameters"] = {"bytes": len(ordered), "hash": hashlib.sha256(ordered).hexdigest(),
                                     "scope": "sdk_parameters_before_wire_serialization"}
        parts = out["parts"]
        # The prefix is deliberately defined over this stable layout, rather
        # than pretending to know the provider's cache key or JSON wire framing.
        if "tools" in payload:
            parts.append({"kind": "tools", **_fingerprint(payload["tools"])})
        if "functions" in payload:
            parts.append({"kind": "functions", **_fingerprint(payload["functions"])})
        for index, message in enumerate(payload.get("messages") or []):
            role = message.get("role") if isinstance(message, dict) else "unknown"
            role = role if role in {"system", "developer", "user", "assistant", "tool", "function"} else "unknown"
            entry = {"i": index, "kind": role, **_fingerprint(message)}
            out["messages"].append(entry)
            parts.append({"kind": "message", "i": index, **_fingerprint(message)})
        options = {key: value for key, value in payload.items() if key not in {"messages", "tools", "functions"}}
        if options:
            parts.append({"kind": "options", **_fingerprint(options)})
        out["msg_count"] = len(out["messages"])
        out["tool_msgs"] = sum(e["kind"] in {"tool", "function"} for e in out["messages"])
        from src.core.progress_bus import get_turn_scope, get_request_layouts
        key = (*get_turn_scope(), out["model"], out["purpose"])
        request_history = get_request_layouts()
        history = request_history if request_history is not None else (_previous_layout.get() or {})
        prior = previous if previous is not None else history.get(key)
        common = None
        reason = "initial_attempt"
        if prior and prior.get("layout_version") == out["layout_version"]:
            count = size = 0
            for old, new in zip(prior.get("parts") or [], parts):
                if old != new:
                    break
                count += 1
                size += new["bytes"]
            common = {"parts": count, "bytes": size, "unit": "complete_layout_parts",
                      "previous_attempt_id": prior.get("attempt_id", "")}
            reason = ("epoch_changed" if prior.get("epoch") != out["epoch"] else
                      "unchanged" if prior.get("hash") == out["hash"] else "prepared_payload_changed")
        allowed_reasons = {"retry", "protocol_fallback", "history_compacted", "tools_changed",
                           "epoch_changed", "initial_attempt", "unchanged", "prepared_payload_changed",
                           "initial", "instructions_changed", "request_layout_changed", "working_set_compacted"}
        out["change_reason"] = change_reason if change_reason in allowed_reasons else reason
        out["common_prefix"] = common
        updated = dict(history)
        updated[key] = out
        if len(updated) > 32:
            updated.pop(next(iter(updated)))
        if request_history is not None:
            request_history.clear()
            request_history.update(updated)
        else:
            _previous_layout.set(updated)
    except Exception as exc:
        out["change_reason"] = "capture_unavailable"
        logger.debug("[turn_context] prepared capture failed: %s", type(exc).__name__)
    return out


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
    from src.core.cache_metrics import _count
    stored = dict(snapshot or {})
    stored.setdefault("scope", "logical_call")
    # Only normalized counters/field provenance survive, never response text.
    stored["usage"] = {key: _count(usage.get(key)) for key in
                       ("input_tokens", "output_tokens", "total_tokens", "cache_read",
                        "cache_write", "cache_uncached", "cache_hit", "cache_miss")}
    stored["usage"]["cache_status"] = (usage.get("cache_status")
        if usage.get("cache_status") in {"reported", "partial", "unavailable"} else "unavailable")
    payload = json.dumps(stored, ensure_ascii=False)
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
                     input_tokens, output_tokens, _identifier(error), payload))
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
    out: dict = {"found": False, "calls": 0, "rows": [], "input_tokens": None,
                 "output_tokens": None, "scope": "returned_window",
                 "totals_scope": "returned_window", "is_task_total": False,
                 "logical_calls": 0, "physical_attempts": 0, "totals_by_scope": {}}
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
    groups = {}
    for row in rows:
        payload = row["payload"]
        row["scope"] = payload.get("scope", "logical_call")
        if row["scope"] not in {"logical_call", "physical_attempt"}:
            row["scope"] = "logical_call"
        for key in ("purpose", "attempt_id", "logical_call_id", "epoch"):
            row[key] = payload.get(key)
        row["usage"] = payload.get("usage") or {}
        for key in ("input_tokens", "output_tokens"):
            if key in row["usage"]:
                row[key] = row["usage"][key]
        groups.setdefault(row["scope"], []).append(row)
    totals = {}
    for scope, members in groups.items():
        totals[scope] = {"calls": len(members)}
        for key in ("input_tokens", "output_tokens"):
            values = [r[key] for r in members if r.get(key) is not None]
            totals[scope][key] = sum(values) if values else None
            totals[scope][key + "_reported_calls"] = len(values)
    # Logical observations and physical attempts can describe the same work.
    # Do not sum the two scopes and present double-counted tokens as a task sum.
    only = next(iter(totals.values())) if len(totals) == 1 else {}
    out.update({
        "found": True, "calls": len(rows), "rows": rows, "last": rows[0],
        "input_tokens": only.get("input_tokens"),
        "output_tokens": only.get("output_tokens"),
        "logical_calls": len(groups.get("logical_call", [])),
        "physical_attempts": len(groups.get("physical_attempt", [])),
        "totals_by_scope": totals,
    })
    return out

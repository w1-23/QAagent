# -*- coding: utf-8 -*-
"""A2: 工具结果全文入库（可追溯、可复现、按需读取）。

为什么单独一张表
----------------
会话消息表里的 ``content`` 是**给模型看的**那一份：它被有意裁剪过（去重、
摘要、长度上限），刷新后前端只能看到这一份。用户要的是"这一步到底返回了
什么原文"——包括被裁剪掉的部分。把全文塞回 messages 会让每次读历史都拖着
几十 MB 走；所以单独存，按 ``tool_call_id`` 精确取。

写入内容
--------
tool_call_id / session_id / request_id / tool_name / status / args_json /
result_text（新记录完整原文；旧截断记录保持原状态）/ result_chars /
full_sha256 / error_text / elapsed_ms / summary / created_at

保留策略
--------
- 新记录保留完整原文与 sha256，不进行入库裁剪。旧的截断数据不能恢复。
- 每会话最多 ``KEEP_PER_SESSION`` 份未固定原文，超出按 created_at 轮转最旧的。
  检查点引用可原子固定，保留到会话/账号删除；不占未固定原文的轮转额度。
  轮转后仅保留不可复用的 ID 墓碑；读取返回 None，不能承诺永久可用。
- tool_call_id 不可覆盖；相同会话、请求、工具、参数与结果的重复写入幂等。
"""
from __future__ import annotations

import hashlib
import json
import re
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

MAX_CHARS = 200_000
HEAD_CHARS = 150_000
TAIL_CHARS = 50_000
KEEP_PER_SESSION = 800

_schema_ready = False


def _connect() -> sqlite3.Connection:
    from src.session.manager import DB_PATH, _connect_db
    return _connect_db(str(DB_PATH))


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_results (
                tool_call_id TEXT PRIMARY KEY,
                session_id TEXT,
                request_id TEXT,
                tool_name TEXT,
                status TEXT,
                args_json TEXT,
                result_text TEXT,
                result_chars INTEGER,
                truncated INTEGER DEFAULT 0,
                full_sha256 TEXT,
                error_text TEXT,
                elapsed_ms INTEGER,
                summary TEXT,
                created_at TEXT
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tool_results)")}
        if "retained" not in columns:
            conn.execute("ALTER TABLE tool_results ADD COLUMN retained INTEGER NOT NULL DEFAULT 1")
        if "pinned" not in columns:
            conn.execute("ALTER TABLE tool_results ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tr_session "
            "ON tool_results(session_id, created_at)")
        conn.commit()
    _schema_ready = True


def _clip(text: str) -> tuple[str, int, int, str]:
    """返回 (入库文本, 原文长度, 是否截断, 全文 sha256)。"""
    raw = text or ""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if len(raw) <= MAX_CHARS:
        return raw, len(raw), 0, digest
    # The omission marker also counts toward the hard storage limit.
    tail_chars = TAIL_CHARS
    for _ in range(3):
        marker = f"\n\n…（此处省略 {len(raw) - HEAD_CHARS - tail_chars} 字符）…\n\n"
        tail_chars = max(0, MAX_CHARS - HEAD_CHARS - len(marker))
    marker = f"\n\n…（此处省略 {len(raw) - HEAD_CHARS - tail_chars} 字符）…\n\n"
    clipped = raw[:HEAD_CHARS] + marker + raw[-tail_chars:]
    return clipped, len(raw), 1, digest


# --- 给模型看的长度上限（与上面证据库的 MAX_CHARS 是两件事）---------------
# 证据库保留完整原文（仅旧数据可能截断），但送进模型上下文的单条
# 工具结果必须按 token 预算收敛：40k 字符约 10k token，占 1M 窗口的 1%。
MODEL_MAX_CHARS = 40_000
MODEL_HEAD_CHARS = 30_000
MODEL_TAIL_CHARS = 10_000

_IDENT_RE = re.compile(
    r"\b(doi|url|pmid|pmcid|evidence_id|artifact_id|chunk_id|source_id|isbn)\b", re.I)


def clamp_for_model(text: str, tool_name: str = "",
                    limit: int = MODEL_MAX_CHARS) -> tuple[str, bool]:
    """把单条工具结果收敛到模型预算内，返回 (文本, 是否截断)。

    预算分配：尾 ``MODEL_TAIL_CHARS`` 固定保留；中段按标识符（DOI / URL /
    PMID / chunk_id 等）抽取上下文片段（每处 ±约 120 字符，最多 40 处且不超
    6k 字符）；剩余额度给头部正文。结果总长不超过 ``limit``，引用锚点不因
    截断丢失。已保留文本可按 tool_call_id 读取，受存储截断及轮转限制。
    """
    raw = str(text or "")
    limit = max(0, int(limit))
    if len(raw) <= limit:
        return raw, False
    if limit <= MODEL_TAIL_CHARS + 1:
        return raw[:limit], True
    tail = raw[-MODEL_TAIL_CHARS:]
    dropped = raw[:len(raw) - MODEL_TAIL_CHARS]
    seen: set[str] = set()
    ident: list[str] = []
    ident_used = 0
    for match in _IDENT_RE.finditer(dropped):
        snippet = dropped[max(0, match.start() - 80): match.end() + 120]
        snippet = " ".join(snippet.split())
        if not snippet or snippet in seen:
            continue
        if ident_used + len(snippet) + 1 > 6_000:
            break
        seen.add(snippet)
        ident.append(snippet)
        ident_used += len(snippet) + 1
        if len(ident) >= 40:
            break
    ident_block = ("[中段保留的标识符片段]\n" + "\n".join(ident) + "\n") if ident else ""
    note = (f"\n\n…（此处省略 {len(dropped) - ident_used} 字符；可按 "
            f"tool_call_id 请求已保留原文，实际可用性受存储截断与轮转限制）…\n")
    head_len = max(0, limit - len(tail) - len(ident_block) - len(note))
    return raw[:head_len] + note + ident_block + tail, True


def record(*, tool_call_id: str, session_id: str = "", request_id: str = "",
           tool_name: str = "", status: str = "", args: Any = None,
           result_text: str = "", error_text: str = "",
           elapsed_ms: int = 0, summary: str = "") -> bool:
    """Persist complete evidence once; conflicting or expired IDs cannot be reused.

    Identical retries do not update timestamps or extend retention. Elapsed time
    and display summary are not identity fields; the first values remain stored.
    """
    cid = str(tool_call_id or "").strip()
    if not cid:
        return False
    try:
        ensure_schema()
        stored = str(result_text or "")
        digest = hashlib.sha256(stored.encode("utf-8")).hexdigest()
        args_json = json.dumps(args, ensure_ascii=False, default=str, sort_keys=True)
        identity = {
            "session_id": session_id, "request_id": request_id,
            "tool_name": tool_name, "status": status, "args_json": args_json,
            "result_text": stored, "result_chars": len(stored),
            "truncated": 0, "full_sha256": digest, "error_text": str(error_text or ""),
        }
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).isoformat()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            # Serialize check + insert + retention, including concurrent writers.
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT * FROM tool_results WHERE tool_call_id=?", (cid,)).fetchone()
            if previous is not None:
                return bool(previous["retained"]) and all(
                    previous[key] == value for key, value in identity.items())
            conn.execute(
                "INSERT INTO tool_results "
                "(tool_call_id, session_id, request_id, tool_name, status, "
                " args_json, result_text, result_chars, truncated, full_sha256, "
                " error_text, elapsed_ms, summary, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, session_id, request_id, tool_name, status, args_json,
                 stored, len(stored), 0, digest, identity["error_text"],
                 int(elapsed_ms or 0), str(summary or "")[:2_000], stamp))
            if session_id:
                conn.execute(
                    "UPDATE tool_results SET retained=0, result_text=NULL, args_json=NULL, "
                    "error_text=NULL, summary=NULL WHERE session_id=? AND retained=1 AND pinned=0 "
                    "AND tool_call_id NOT IN (SELECT tool_call_id FROM tool_results "
                    "WHERE session_id=? AND retained=1 AND pinned=0 "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?)",
                    (session_id, session_id, KEEP_PER_SESSION))
            retained = conn.execute(
                "SELECT retained FROM tool_results WHERE tool_call_id=?", (cid,)).fetchone()[0]
            conn.commit()
        return bool(retained)
    except Exception:
        logger.debug("tool_results.record failed", exc_info=True)
        return False


def record_with_reference(**kwargs: Any) -> dict | None:
    """Record then verify a safe reference for first-entry views (no evidence body).

    Internal writer API: pass the same trusted session/request arguments as record.
    None means no usable handle may be advertised. The reference describes current
    persistence only: later retention rotation can make readback unavailable.
    """
    if not str(kwargs.get("session_id") or "").strip() or not record(**kwargs):
        return None
    row = fetch(kwargs.get("tool_call_id", ""))
    if row is None or row.get("session_id") != kwargs.get("session_id", ""):
        return None
    storage = storage_integrity(row)
    expected = str(kwargs.get("result_text") or "")
    if not storage["complete"] or row["result_text"] != expected:
        return None
    return {"tool_call_id": row["tool_call_id"], "tool_name": row["tool_name"],
            "request_id": row["request_id"], "full_sha256": storage["full_sha256"],
            "original_chars": storage["original_chars"], "stored_chars": storage["stored_chars"],
            "storage": storage}


def pin_references(session_id: str, call_ids: list[str]) -> bool:
    """Atomically pin verified checkpoint evidence in a trusted server session.

    Every ID must still name complete, hash-valid evidence in this session.
    Validate the entire set before pinning any row. Pins bypass ordinary result
    rotation and last until the containing session/account data is deleted.
    This is an internal API, not an authorization boundary for model arguments.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        return False
    if not isinstance(call_ids, (list, tuple)) or any(
            not isinstance(cid, str) or not cid.strip() for cid in call_ids):
        return False
    wanted = list(dict.fromkeys(cid.strip() for cid in call_ids))
    try:
        ensure_schema()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            for cid in wanted:
                row = conn.execute(
                    "SELECT * FROM tool_results WHERE tool_call_id=? AND session_id=? AND retained=1",
                    (cid, session_id)).fetchone()
                if row is None or not storage_integrity(dict(row))["complete"]:
                    return False
            conn.executemany(
                "UPDATE tool_results SET pinned=1 WHERE tool_call_id=? AND session_id=?",
                ((cid, session_id) for cid in wanted))
            conn.commit()
        return True
    except Exception:
        logger.debug("tool_results.pin_references failed", exc_info=True)
        return False


def storage_integrity(row: dict) -> dict:
    """Describe retained evidence honestly, including legacy clipped records.

    Hash identifies the original result; it cannot restore an omitted middle.
    Offsets for readback always refer to stored text, not original coordinates.
    """
    text = str(row.get("result_text") or "")
    truncated = bool(row.get("truncated"))
    verified = (bool(row.get("retained", 1)) and not truncated
                and row.get("result_chars") == len(text)
                and row.get("full_sha256") == hashlib.sha256(text.encode("utf-8")).hexdigest())
    return {
        "state": "truncated" if truncated else "complete" if verified else "unverified",
        "complete": verified,
        "original_chars": int(row.get("result_chars") or 0),
        "stored_chars": len(text),
        "full_sha256": row.get("full_sha256") or "",
        "storage_max_chars": MAX_CHARS if truncated else None,
        "retention_max_results_per_session": KEEP_PER_SESSION,
        "retention_limit_scope": "unpinned_results",
        "pinned": bool(row.get("pinned")),
        "retention_policy": "until_session_or_account_deletion" if row.get("pinned") else "session_rotation",
        "omitted_middle_recoverable": False if truncated else None,
        "offset_basis": "stored_text",
        "notice": ("Stored head/tail only; the omitted original middle cannot be recovered here. "
                   if truncated else "") + (
                       "Pinned checkpoint evidence is retained until session/account deletion."
                       if row.get("pinned") else
                       "Unpinned results may become unavailable after session retention rotation."),
    }


def fetch_for_current_request(tool_call_id: str, *, strict: bool = False) -> dict | None:
    """Owner + session scoped read, using authenticated server context only.

    Like account-file reads, the model selects an opaque ID, never an owner.
    Missing auth, wrong owner/session and absent/rotated results all return None.
    Strict readback distinguishes a rejected current scope or storage error.
    Missing IDs and IDs elsewhere in an authorized scope remain indistinguishable.
    """
    from src.core.tracing import get_session_id, get_user_id
    from src.core.resource_access import PRIVATE_FILE, authorize_resource
    from src.session.manager import session_manager
    uid, sid = get_user_id(), get_session_id()
    cid = str(tool_call_id or "").strip()
    if not uid or uid <= 0 or not sid:
        if strict:
            raise PermissionError("result_access_denied")
        return None
    if not cid:
        return None
    decision = authorize_resource(PRIVATE_FILE, {"user_id": uid},
                                  owner_user_id=session_manager.get_session_owner(sid))
    if not decision.allowed:
        if strict:
            raise PermissionError("result_access_denied")
        return None
    try:
        ensure_schema()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tool_results WHERE retained=1 AND tool_call_id=? AND session_id=?",
                (cid, sid)).fetchone()
        return dict(row) if row else None
    except sqlite3.Error as exc:
        if strict:
            raise
        logger.debug("tool_results scoped read failed: %s", type(exc).__name__)
        return None


def fetch(tool_call_id: str) -> dict | None:
    cid = str(tool_call_id or "").strip()
    if not cid:
        return None
    try:
        ensure_schema()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tool_results WHERE retained=1 AND tool_call_id = ?", (cid,)).fetchone()
        return dict(row) if row else None
    except Exception:
        logger.debug("tool_results.fetch failed", exc_info=True)
        return None


def fetch_many(ids: list[str]) -> dict[str, dict]:
    wanted = [str(i) for i in (ids or []) if str(i or "").strip()]
    if not wanted:
        return {}
    try:
        ensure_schema()
        marks = ",".join("?" for _ in wanted)
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM tool_results WHERE retained=1 AND tool_call_id IN ({marks})",
                wanted).fetchall()
        return {row["tool_call_id"]: dict(row) for row in rows}
    except Exception:
        logger.debug("tool_results.fetch_many failed", exc_info=True)
        return {}


def fetch_by_request(session_id: str, request_id: str) -> list[dict]:
    """取某个轮次（request_id）下的全部工具结果，按写入顺序返回。

    用于「本轮本地来源」：工具结果里保留着未脱敏的 ``[n] <record_key>``，
    对外展示时再逐条换成可读标签，原文键不直接进模型上下文。
    """
    sid = str(session_id or "").strip()
    rid = str(request_id or "").strip()
    if not sid or not rid:
        return []
    try:
        ensure_schema()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM tool_results WHERE retained=1 AND session_id = ? AND request_id = ? "
                "ORDER BY rowid ASC", (sid, rid)).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        logger.debug("tool_results.fetch_by_request failed", exc_info=True)
        return []


def stats(session_id: str = "") -> dict:
    """给审计/自检用：条数、总字符、截断条数。"""
    try:
        ensure_schema()
        with _connect() as conn:
            if session_id:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(result_chars),0), "
                    "COALESCE(SUM(truncated),0) FROM tool_results WHERE retained=1 AND session_id = ?",
                    (session_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(result_chars),0), "
                    "COALESCE(SUM(truncated),0) FROM tool_results WHERE retained=1").fetchone()
        return {"rows": int(row[0]), "chars": int(row[1]), "truncated": int(row[2])}
    except Exception:
        return {"rows": 0, "chars": 0, "truncated": 0}

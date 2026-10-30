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
result_text（全文，超限则首尾保留并置 truncated）/ result_chars /
full_sha256 / error_text / elapsed_ms / summary / created_at

保留策略
--------
- 单条超过 ``MAX_CHARS``：保留首 ``HEAD_CHARS`` + 尾 ``TAIL_CHARS``，置
  ``truncated=1``，同时记录全文长度与 sha256（可用它证明"原文确实是这一份"）。
- 每会话最多 ``KEEP_PER_SESSION`` 条，超出按 created_at 删除最旧的。
  历史消息里的 tool_call_id 仍能查到"曾存在但已轮转"的记录状态吗？不能——
  所以删除时只删最旧的，并且 ``fetch`` 返回 None 时前端回退到消息里的摘要。
"""
from __future__ import annotations

import hashlib
import json
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
    clipped = (raw[:HEAD_CHARS]
               + f"\n\n…（此处省略 {len(raw) - HEAD_CHARS - TAIL_CHARS} 字符）…\n\n"
               + raw[-TAIL_CHARS:])
    return clipped, len(raw), 1, digest


def record(*, tool_call_id: str, session_id: str = "", request_id: str = "",
           tool_name: str = "", status: str = "", args: Any = None,
           result_text: str = "", error_text: str = "",
           elapsed_ms: int = 0, summary: str = "") -> bool:
    """写入（幂等：同一 tool_call_id 覆盖为最新一次执行结果）。"""
    cid = str(tool_call_id or "").strip()
    if not cid:
        return False
    try:
        ensure_schema()
        stored, chars, truncated, digest = _clip(str(result_text or ""))
        try:
            args_json = json.dumps(args, ensure_ascii=False, default=str)[:20_000]
        except Exception:
            args_json = ""
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).isoformat()
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tool_results "
                "(tool_call_id, session_id, request_id, tool_name, status, "
                " args_json, result_text, result_chars, truncated, full_sha256, "
                " error_text, elapsed_ms, summary, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, session_id, request_id, tool_name, status, args_json,
                 stored, chars, truncated, digest, str(error_text or "")[:20_000],
                 int(elapsed_ms or 0), str(summary or "")[:2_000], stamp))
            # 轮转：只留最近 KEEP_PER_SESSION 条（按会话）
            if session_id:
                conn.execute(
                    "DELETE FROM tool_results WHERE session_id = ? AND tool_call_id "
                    "NOT IN (SELECT tool_call_id FROM tool_results WHERE session_id = ? "
                    "ORDER BY created_at DESC LIMIT ?)",
                    (session_id, session_id, KEEP_PER_SESSION))
            conn.commit()
        return True
    except Exception:
        logger.debug("tool_results.record failed", exc_info=True)
        return False


def fetch(tool_call_id: str) -> dict | None:
    cid = str(tool_call_id or "").strip()
    if not cid:
        return None
    try:
        ensure_schema()
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tool_results WHERE tool_call_id = ?", (cid,)).fetchone()
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
                f"SELECT * FROM tool_results WHERE tool_call_id IN ({marks})",
                wanted).fetchall()
        return {row["tool_call_id"]: dict(row) for row in rows}
    except Exception:
        logger.debug("tool_results.fetch_many failed", exc_info=True)
        return {}


def stats(session_id: str = "") -> dict:
    """给审计/自检用：条数、总字符、截断条数。"""
    try:
        ensure_schema()
        with _connect() as conn:
            if session_id:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(result_chars),0), "
                    "COALESCE(SUM(truncated),0) FROM tool_results WHERE session_id = ?",
                    (session_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(result_chars),0), "
                    "COALESCE(SUM(truncated),0) FROM tool_results").fetchone()
        return {"rows": int(row[0]), "chars": int(row[1]), "truncated": int(row[2])}
    except Exception:
        return {"rows": 0, "chars": 0, "truncated": 0}

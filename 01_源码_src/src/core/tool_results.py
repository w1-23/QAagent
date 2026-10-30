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
import re
import logging
import sqlite3
from contextvars import ContextVar
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


# --- 给模型看的长度上限（与上面证据库的 MAX_CHARS 是两件事）---------------
# 证据库可以留 200k（用户点"查看原文"时要能取回），但送进模型上下文的单条
# 工具结果必须按 token 预算收敛：40k 字符约 10k token，占 1M 窗口的 1%。
MODEL_MAX_CHARS = 40_000
MODEL_HEAD_CHARS = 30_000
MODEL_TAIL_CHARS = 10_000

# --- 同轮工具结果总预算（与单条上限是两个层次）------------------------------
# 单条 40k 字符仍可能让一轮里的多条检索结果把上下文吃掉：4 条各 40k 就是
# 160k 字符（≈40k token）。这里用 ContextVar 记录「本轮已经交给模型多少
# 字符」，超出后后续结果只保留摘要——全文仍在证据库里按 tool_call_id 可取。
TURN_BUDGET_CHARS = 120_000
_TURN_BUDGET: ContextVar[dict | None] = ContextVar("tool_turn_budget", default=None)


def begin_turn_budget(limit: int = TURN_BUDGET_CHARS) -> None:
    """开始一轮工具调度时调用；不调用则退回「只按单条上限」的旧行为。"""
    _TURN_BUDGET.set({"limit": int(limit), "used": 0})


def turn_budget_remaining() -> int | None:
    state = _TURN_BUDGET.get()
    if not state:
        return None
    return max(0, int(state["limit"]) - int(state["used"]))


def consume_turn_budget(chars: int) -> None:
    state = _TURN_BUDGET.get()
    if state:
        state["used"] = int(state["used"]) + max(0, int(chars))

_IDENT_RE = re.compile(
    r"\b(doi|url|pmid|pmcid|evidence_id|artifact_id|chunk_id|source_id|isbn)\b", re.I)


def clamp_for_model(text: str, tool_name: str = "",
                    limit: int = MODEL_MAX_CHARS) -> tuple[str, bool]:
    """把单条工具结果收敛到模型预算内，返回 (文本, 是否截断)。

    预算分配：尾 ``MODEL_TAIL_CHARS`` 固定保留；中段按标识符（DOI / URL /
    PMID / chunk_id 等）抽取上下文片段（每处 ±约 120 字符，最多 40 处且不超
    6k 字符）；剩余额度给头部正文。结果总长不超过 ``limit``，引用锚点不因
    截断丢失。全文仍可由 ``fetch(tool_call_id)`` 取回。
    """
    raw = str(text or "")
    remaining = turn_budget_remaining()
    if remaining is not None:
        if remaining <= 0:
            stub = ("[TOOL_RESULT_OMITTED] 本轮工具结果预算已用尽；完整原文已按 "
                    "tool_call_id 存入证据库，可按需回取。")
            return stub, True
        if len(raw) <= remaining and len(raw) <= limit:
            consume_turn_budget(len(raw))
            return raw, False
        if remaining < limit:
            # 轮级预算已经不多：直接按剩余额度硬截，不再走「尾段+标识符」的完整算法。
            limit = remaining
            note = ("\n…（本轮工具结果预算已用尽，此处截断；完整原文已按 "
                    "tool_call_id 存入证据库）…\n")
            head = max(0, limit - len(note))
            out = raw[:head] + note
            consume_turn_budget(len(out))
            return out, True
    if len(raw) <= limit or limit <= MODEL_TAIL_CHARS + 1:
        consume_turn_budget(len(raw))
        return raw, False
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
    note = (f"\n\n…（此处省略 {len(dropped) - ident_used} 字符；完整原文已按 "
            f"tool_call_id 存入证据库，可按需回取）…\n")
    head_len = max(0, limit - len(tail) - len(ident_block) - len(note))
    out = raw[:head_len] + note + ident_block + tail
    consume_turn_budget(len(out))
    return out, True


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

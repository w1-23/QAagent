# -*- coding: utf-8 -*-
"""§11.11 定向取证：479,708 字符 ToolMessage 是否仍会进入模型上下文。

只读 + 临时库，不碰生产库、不改历史、不调用付费模型。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

REPORT = {}
BIG = 479_708


def build_big_text(n: int = BIG) -> str:
    """按真实结构造：头部说明 + 多篇正文 + DOI 行，避免出现真实文献正文。"""
    head = ("Parallel full-text attempt: 2 content page(s) succeeded, 3 attempt(s) failed "
            "or returned metadata only; elapsed 16522 ms.\n\n")
    para = ("[synthetic] leaf spot management paragraph. " * 8
            + "DOI: 10.3390/plants12010160 | Figure 3 | Table 2\n")
    body = []
    size = len(head)
    i = 0
    while size < n:
        line = f"[{i}] " + para
        body.append(line)
        size += len(line)
        i += 1
    text = head + "".join(body)
    return text[:n]


def main() -> int:
    from src.config import settings
    from src.core import tool_results
    from src.core.context_budget import ContextBudget, ContextBudgetConfig
    from src.graph.general_agent_graph import _safe_persisted_trace
    from src.tools.outcome import safe_tool_step
    import src.session.manager as mgr_mod

    big = build_big_text()
    REPORT["synthetic_chars"] = len(big)

    # --- 路径 0：持久化前裁剪（_safe_persisted_trace）---
    safe = _safe_persisted_trace([ToolMessage(
        content=big, tool_call_id="call_big", name="deepseek_fetch_paper")])
    REPORT["persist_trim"] = {
        "in": len(big),
        "out": len(str(safe[0].content)),
        "changed": len(str(safe[0].content)) != len(big),
    }

    # --- 路径 C：/messages 序列化（只给状态摘要）---
    step = safe_tool_step("deepseek_fetch_paper", big)
    REPORT["api_serialized"] = {
        "keys": sorted(step.keys()),
        "summary_chars": len(str(step.get("summary") or "")),
        "status": step.get("status"),
    }

    # --- 临时库：模拟「历史里已存在 479,708 字符 tool 行」---
    src_db = Path(ROOT).parent / "agent" / "state" / "sessions.db"
    if not src_db.exists():
        src_db = Path(ROOT) / "agent" / "state" / "sessions.db"
    tmpdir = Path(tempfile.mkdtemp(prefix="toolbudget_"))
    tmp_db = tmpdir / "sessions.db"
    shutil.copyfile(src_db, tmp_db)
    mgr_mod.DB_PATH = tmp_db

    sid = "probe-toolbudget-479k"
    con = sqlite3.connect(tmp_db)
    con.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
    con.execute("DELETE FROM sessions WHERE session_id = ?", (sid,))
    con.execute("INSERT OR IGNORE INTO sessions (session_id, owner_user_id, title, created_at, updated_at) "
                "VALUES (?, 1, 'probe', datetime('now'), datetime('now'))", (sid,))
    con.execute("INSERT INTO messages (session_id, msg_type, content, timestamp) VALUES (?,?,?,datetime('now'))",
                (sid, "human", "帮我查这两篇文献的全文"))
    con.execute("INSERT INTO messages (session_id, msg_type, content, tool_calls_json, timestamp) "
                "VALUES (?,?,?,?,datetime('now'))",
                (sid, "ai", "",
                 json.dumps([{"id": "call_big", "name": "deepseek_fetch_paper",
                              "args": {"doi": "10.3390/plants12010160"}}], ensure_ascii=False)))
    con.execute("INSERT INTO messages (session_id, msg_type, content, tool_call_id, name, timestamp) "
                "VALUES (?,?,?,?,?,datetime('now'))",
                (sid, "tool", big, "call_big", "deepseek_fetch_paper"))
    con.execute("INSERT INTO messages (session_id, msg_type, content, timestamp) VALUES (?,?,?,datetime('now'))",
                (sid, "ai", "这两篇是开放获取全文。"))
    con.commit()
    con.close()

    manager = mgr_mod.SessionManager()
    manager.db_path = str(tmp_db)  # 单例：显式指向临时库
    msgs = manager._get_messages_sync(sid, True)[0]
    total_chars = sum(len(str(getattr(m, "content", "") or "")) for m in msgs)
    tool_chars = [len(str(getattr(m, "content", "") or "")) for m in msgs
                  if isinstance(m, ToolMessage)]
    REPORT["history_load"] = {
        "messages": len(msgs),
        "total_chars": total_chars,
        "tool_chars": tool_chars,
    }

    # --- 路径 B：用户轮边界预算检查（生产配置）---
    cfg = ContextBudgetConfig(
        enabled=settings.CONTEXT_BUDGET_ENABLED,
        max_tokens=settings.RESOLVED_CONTEXT_BUDGET_MAX_TOKENS,
        soft_threshold=settings.CONTEXT_BUDGET_SOFT_THRESHOLD,
        hard_threshold=settings.CONTEXT_BUDGET_HARD_THRESHOLD,
        target_ratio=settings.CONTEXT_BUDGET_TARGET_RATIO,
        protect_recent_turns=settings.CONTEXT_BUDGET_PROTECT_RECENT_TURNS,
    )
    budget = ContextBudget(cfg)
    est_before = budget.estimate_tokens(msgs)
    result = asyncio.run(budget.check(msgs, query="下一轮", session_id=sid))
    after_chars = sum(len(str(getattr(m, "content", "") or "")) for m in result.messages)
    REPORT["budget_check"] = {
        "enabled": cfg.enabled,
        "max_tokens": cfg.max_tokens,
        "soft_threshold": cfg.soft_threshold,
        "ratio_before": round(est_before / max(cfg.max_tokens, 1), 4),
        "level": str(result.level),
        "chars_before": total_chars,
        "chars_after": after_chars,
        "tool_chars_after": [len(str(getattr(m, "content", "") or "")) for m in result.messages
                             if isinstance(m, ToolMessage)],
    }

    # --- 多条累加 ---
    many = []
    for i in range(3):
        many.append(ToolMessage(content=build_big_text(200_000), tool_call_id=f"c{i}", name="deepseek_fetch_paper"))
    est_many = budget.estimate_tokens(many)
    REPORT["accumulation_3x200k"] = {
        "chars": sum(len(str(m.content)) for m in many),
        "est_tokens": est_many,
        "ratio": round(est_many / max(cfg.max_tokens, 1), 4),
    }

    # --- 证据库：tool_results 的 200k 首尾保留是否真的生效 ---
    tool_results.ensure_schema()
    rec = tool_results.record(tool_call_id="probe-479k", session_id=sid, request_id="r1",
                              tool_name="deepseek_fetch_paper", status="complete", args={},
                              result_text=big, error_text="", elapsed_ms=18112, summary="s")
    row = tool_results.fetch("probe-479k")
    REPORT["evidence_store"] = {
        "stored_chars": len(str(row.get("result_text") or "")) if row else 0,
        "result_chars": (row or {}).get("result_chars"),
        "truncated": (row or {}).get("truncated"),
    }

    out = ROOT / "experiments" / "trace" / "runs" / "20261030" / "S11"
    out.mkdir(parents=True, exist_ok=True)
    (out / "tool_result_budget_probe.json").write_text(
        json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(REPORT, ensure_ascii=False, indent=2))
    shutil.rmtree(tmpdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

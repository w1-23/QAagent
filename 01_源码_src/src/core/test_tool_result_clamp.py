# -*- coding: utf-8 -*-
"""§11.11：工具结果进入模型上下文的长度闭环（新消息 + 旧库跨轮）。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from src.core import tool_results
from src.core.tool_results import MODEL_MAX_CHARS, clamp_for_model


def _long_text(n: int, with_doi: bool = True) -> str:
    unit = "第{i}段：柑橘黄龙病防控要点 DOI: 10.1000/xyz.{i} URL=https://example.org/p/{i}\n"
    out = []
    size = 0
    i = 0
    while size < n:
        line = unit.format(i=i)
        out.append(line)
        size += len(line)
        i += 1
    return "".join(out)[:n]


def test_single_long_result_is_clamped_within_budget():
    raw = _long_text(479_708)
    out, cut = clamp_for_model(raw, "deepseek_fetch_paper")
    assert cut is True
    assert len(out) <= MODEL_MAX_CHARS
    assert len(out) < len(raw)
    assert "省略" in out


def test_short_result_untouched():
    raw = "标题：柑橘黄龙病研究\nDOI: 10.1000/abc"
    out, cut = clamp_for_model(raw)
    assert cut is False and out == raw


def test_middle_identifiers_survive_truncation():
    head = "正文" * 20_000          # 头部正文
    middle = "".join(f"文献{i} DOI: 10.9999/mid.{i}\n" for i in range(200))
    tail = "尾部结论" * 5_000
    out, cut = clamp_for_model(head + middle + tail)
    assert cut is True
    assert "10.9999/mid.0" in out          # 中段 DOI 被抽出来
    assert "中段保留的标识符片段" in out
    assert len(out) <= MODEL_MAX_CHARS


def test_many_results_each_bounded():
    msgs = [clamp_for_model(_long_text(200_000))[0] for _ in range(3)]
    assert all(len(m) <= MODEL_MAX_CHARS for m in msgs)
    assert sum(len(m) for m in msgs) <= 3 * MODEL_MAX_CHARS


def test_tool_message_from_graph_is_clamped(monkeypatch):
    """工具执行返回后，进入模型的那份内容必须被收敛。"""
    import src.graph.general_agent_graph as g
    from src.tools import registry

    async def fake_run(tool, call):
        return ToolMessage(content=_long_text(479_708), tool_call_id=call["id"],
                           name=call["name"], artifact={})

    monkeypatch.setattr(registry, "_run_single_tool", fake_run)
    monkeypatch.setattr(g, "_emit_completed_tool_step", lambda *a, **k: None)
    out = asyncio.run(g._run_regular_calls(
        [{"id": "c1", "name": "deepseek_fetch_paper", "args": {}}], {"deepseek_fetch_paper": object()}))
    assert len(out) == 1
    assert len(str(out[0].content)) <= MODEL_MAX_CHARS


def test_legacy_row_is_clamped_on_history_load():
    """旧库里 479,708 字符的行不改写，但读历史时按预算收敛。"""
    import src.session.manager as mgr_mod

    manager = mgr_mod.SessionManager()
    original = manager.db_path
    tmp = Path(tempfile.mkdtemp(prefix="legacy_")) / "s.db"
    try:
        manager.db_path = str(tmp)
        manager._init_db_sync()
        big = _long_text(479_708)
        con = sqlite3.connect(tmp)
        con.execute("INSERT INTO messages (session_id,msg_type,content,timestamp) "
                    "VALUES ('s1','human','查全文',datetime('now'))")
        con.execute("INSERT INTO messages (session_id,msg_type,content,tool_calls_json,timestamp) "
                    "VALUES ('s1','ai','',?,datetime('now'))",
                    (json.dumps([{"id": "c1", "name": "deepseek_fetch_paper", "args": {}}]),))
        con.execute("INSERT INTO messages (session_id,msg_type,content,tool_call_id,name,timestamp) "
                    "VALUES ('s1','tool',?,'c1','deepseek_fetch_paper',datetime('now'))", (big,))
        con.commit()
        # 落库长度仍是原文（不迁移、不改写）
        stored = con.execute("SELECT length(content) FROM messages WHERE msg_type='tool'").fetchone()[0]
        con.close()
        assert stored == len(big)

        msgs = manager._get_messages_sync("s1")
        tool_msgs = [m for m in msgs if isinstance(m, ToolMessage)]
        assert tool_msgs and len(str(tool_msgs[0].content)) <= MODEL_MAX_CHARS
        assert "10.1000/xyz.0" in str(tool_msgs[0].content)
    finally:
        manager.db_path = original


def test_evidence_store_keeps_more_than_model_copy():
    tool_results.ensure_schema()
    big = _long_text(479_708)
    tool_results.record(tool_call_id="clamp-test-1", session_id="s-clamp", request_id="r",
                        tool_name="deepseek_fetch_paper", status="complete", args={},
                        result_text=big, error_text="", elapsed_ms=1, summary="s")
    row = tool_results.fetch("clamp-test-1")
    assert row and row["result_chars"] == len(big)
    assert row["truncated"] == 1
    assert len(str(row["result_text"])) > MODEL_MAX_CHARS  # 证据库留得更多


def test_api_serializes_status_only():
    from src.tools.outcome import safe_tool_step
    step = safe_tool_step("deepseek_fetch_paper", _long_text(479_708))
    assert set(step) <= {"status", "reason_code", "summary"}
    assert "10.1000/xyz" not in json.dumps(step, ensure_ascii=False)

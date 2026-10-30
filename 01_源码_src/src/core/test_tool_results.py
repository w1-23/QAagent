# -*- coding: utf-8 -*-
"""A2 回归：工具结果全文入库、截断标记、轮转、按会话隔离。"""
from __future__ import annotations

import sqlite3

import pytest

from src.core import tool_results as tr


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    from src.session import manager as sm
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(sm, "DB_PATH", db, raising=False)
    monkeypatch.setattr(tr, "_schema_ready", False, raising=False)
    tr.ensure_schema()
    yield db
    tr._schema_ready = False


def test_record_and_fetch_roundtrip(temp_db):
    ok = tr.record(tool_call_id="c1", session_id="s1", request_id="r1",
                   tool_name="local_hybrid_search", status="complete",
                   args={"query": "黄龙病"}, result_text="命中 8 条候选资料",
                   elapsed_ms=1234, summary="找到 8 条")
    assert ok
    row = tr.fetch("c1")
    assert row["tool_name"] == "local_hybrid_search"
    assert row["result_text"] == "命中 8 条候选资料"
    assert row["elapsed_ms"] == 1234
    assert row["truncated"] == 0
    assert len(row["full_sha256"]) == 64


def test_long_result_is_clipped_with_flag_and_digest(temp_db, monkeypatch):
    monkeypatch.setattr(tr, "MAX_CHARS", 100, raising=False)
    monkeypatch.setattr(tr, "HEAD_CHARS", 60, raising=False)
    monkeypatch.setattr(tr, "TAIL_CHARS", 30, raising=False)
    payload = "甲" * 500
    tr.record(tool_call_id="c2", session_id="s1", result_text=payload)
    row = tr.fetch("c2")
    assert row["truncated"] == 1
    assert row["result_chars"] == 500
    assert len(row["result_text"]) < 500
    assert row["result_text"].startswith("甲")
    assert "省略" in row["result_text"]


def test_rotation_keeps_newest(temp_db, monkeypatch):
    monkeypatch.setattr(tr, "KEEP_PER_SESSION", 3, raising=False)
    for i in range(6):
        tr.record(tool_call_id=f"k{i}", session_id="s1", result_text=f"r{i}",
                  created_at_override=None) if False else tr.record(
            tool_call_id=f"k{i}", session_id="s1", result_text=f"r{i}")
    with sqlite3.connect(temp_db) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT tool_call_id FROM tool_results WHERE session_id='s1' "
            "ORDER BY created_at").fetchall()]
    assert len(ids) == 3
    assert tr.fetch("k0") is None
    assert tr.fetch("k5") is not None


def test_fetch_many_and_stats(temp_db):
    tr.record(tool_call_id="m1", session_id="s2", result_text="a")
    tr.record(tool_call_id="m2", session_id="s2", result_text="bb")
    got = tr.fetch_many(["m1", "m2", "missing"])
    assert set(got) == {"m1", "m2"}
    st = tr.stats("s2")
    assert st["rows"] == 2 and st["chars"] == 3


def test_record_rejects_empty_id(temp_db):
    assert tr.record(tool_call_id="", result_text="x") is False

"""Right-edge rail turn index: only real user questions become ticks."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from pathlib import Path

import pytest

import src.session.manager as manager_module
from src.session.manager import SessionManager

LIBRARY = ("<account_file_library>\n本账号资料库索引（跨会话共享）\n- a.xlsx\n"
           "</account_file_library>")
QUERY = "<user_query>\nCRC 4079 的品种登记号是多少？\n</user_query>"


@pytest.fixture()
def manager(tmp_path: Path, monkeypatch):
    """SessionManager 是单例：先把模块级路径指到临时目录，再重置单例。"""
    monkeypatch.setattr(manager_module, "DB_PATH", tmp_path / "sessions.db")
    monkeypatch.setattr(manager_module, "STATE_DIR", tmp_path)
    manager_module.SessionManager._instance = None
    instance = SessionManager()
    yield instance
    manager_module.SessionManager._instance = None


def _add(m: SessionManager, session_id: str, content: str, crid: str = "") -> int:
    conn = sqlite3.connect(m.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO messages(session_id, msg_type, content, timestamp, client_request_id)"
            " VALUES(?,?,?,?,?)",
            (session_id, "human", content, "2026-10-30T00:00:00", crid))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _turns(m: SessionManager, session_id: str, limit: int = 400, before: int = 0):
    return asyncio.run(m.get_user_turns(session_id, limit, before))


def test_injected_library_row_is_not_a_turn(manager):
    _add(manager, "s1", LIBRARY)
    _add(manager, "s1", QUERY, "crid:abc")
    data = _turns(manager, "s1")
    assert len(data["turns"]) == 1
    assert data["turns"][0]["text"] == "CRC 4079 的品种登记号是多少？"
    assert data["turns"][0]["request_id"] == "abc"


def test_library_plus_query_keeps_only_the_question(manager):
    _add(manager, "s1", LIBRARY + "\n\n" + QUERY, "crid:x1")
    data = _turns(manager, "s1")
    assert len(data["turns"]) == 1
    text = data["turns"][0]["text"]
    assert "本账号资料库索引" not in text
    assert "CRC 4079" in text


def test_plain_legacy_row_is_kept(manager):
    _add(manager, "s1", "  直接写的旧消息   带多余空白  ")
    data = _turns(manager, "s1")
    assert data["turns"][0]["text"] == "直接写的旧消息 带多余空白"


def test_force_final_synthetic_row_is_skipped(manager):
    from src.session.manager import _SYNTH_FORCE_FINAL_MARK
    _add(manager, "s1", _SYNTH_FORCE_FINAL_MARK + "强制收尾")
    _add(manager, "s1", QUERY)
    data = _turns(manager, "s1")
    assert len(data["turns"]) == 1


def test_paging_walks_backwards_without_duplicates(manager):
    ids = [_add(manager, "s1", f"<user_query>第 {i} 问</user_query>") for i in range(1, 6)]
    first = _turns(manager, "s1", limit=3)
    assert [t["text"] for t in first["turns"]] == ["第 3 问", "第 4 问", "第 5 问"]
    assert first["has_more"] is True
    assert first["next_before"] == ids[2]
    second = _turns(manager, "s1", limit=3, before=first["next_before"])
    assert [t["text"] for t in second["turns"]] == ["第 1 问", "第 2 问"]
    assert second["has_more"] is False


def test_other_sessions_are_not_mixed_in(manager):
    _add(manager, "s1", QUERY)
    _add(manager, "s2", "<user_query>另一个会话的问题</user_query>")
    assert [t["text"] for t in _turns(manager, "s1")["turns"]] == ["CRC 4079 的品种登记号是多少？"]


SYNTHETIC_CASES = (
    "上一轮回答因输出长度上限被截断。请更精简地继续：需要绘图时调用 draw_svg",
    "上一轮输出了未执行的工具调用协议。不要复述或输出任何 DSML、XML 或工具调用标记。",
    "用户明确要求的任务或来源尚未完成。请调用以下尚未尝试的工具组：deepseek_search。",
    "现在请根据用户的原始问题，以及本轮已取得的全部网页和本地证据，直接给出最终回答。",
)


def test_agent_self_prompts_are_not_turns(manager):
    """主循环自己追加的四类“人类口吻”提示会以 human 行落库，但都不是用户提问。"""
    for text in SYNTHETIC_CASES:
        _add(manager, "s1", text)
    _add(manager, "s1", QUERY)
    data = _turns(manager, "s1")
    assert [t["text"] for t in data["turns"]] == ["CRC 4079 的品种登记号是多少？"]


def test_synthetic_prefixes_still_match_the_graph_source():
    """文案一旦改动，这里会失败：提醒同步 manager._SYNTHETIC_TURN_PREFIXES。"""
    from pathlib import Path as _P
    source = _P(__file__).resolve().parents[1].joinpath(
        "graph", "general_agent_graph.py").read_text(encoding="utf-8")
    for text in SYNTHETIC_CASES:
        assert text[:18] in source, text

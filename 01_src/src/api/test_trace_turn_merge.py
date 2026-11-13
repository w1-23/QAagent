# -*- coding: utf-8 -*-
"""P0-A 回归：刷新后执行过程气泡分裂（逻辑轮次归并）。

背景（用户实测）：一轮任务执行时工具进度与公开工作说明处在同一个连续执行
区域；**刷新页面后原来的执行过程出现额外独立气泡**，已完成/失败/重试等步骤
被分别重建，同一轮生成的文件卡片也会重复或散落。

根因：`web/assets/app.js` 的 `renderHistoryMessages()` 每遇到一条 assistant
消息就新建一个气泡，且渲染完工具轨迹后**不清空** `pendingTools`，于是同一轮
里第 N 条 assistant 会把前面积累的全部步骤再渲染一遍；而"公开工作说明"在库
里正是 `ai(正文 + tool_calls)`，`/messages` 会为它额外返回一条 assistant，所以
一轮会变成多个气泡。实时流式路径只有"1 气泡 + 1 份轨迹 + 1 个回答"，两套布局
逻辑不一致。

修复：历史 → 按逻辑轮次（`turn_request_id`）归并 → 统一视图模型 → 渲染，
`build_history_turns()`（服务端）与 `groupHistoryTurns()`（前端）是同一套规则：
一轮 = 问题 + 工作说明 + 全部工具调用/结果（含失败与重试）+ 最终回答 + 该轮
生成文件；幂等键为 message_id / tool_call_id / file_id；**不得用文本相同判重**。

运行：`python -m pytest src/api/test_trace_turn_merge.py -q`
"""
import io
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.api.main import (                       # noqa: E402
    _history_items_from_messages,
    _slice_history,
    build_history_turns,
)

APP_JS = _SRC / "web" / "assets" / "app.js"
REAL_DB_CANDIDATES = [
    os.environ.get("CITRUS_TRACE_DB", ""),
    str(_SRC / "agent" / "state" / "sessions.db"),
    str(_SRC.parent / "agent" / "state" / "sessions.db"),
]


# ─────────────────────────── 夹具 ───────────────────────────
def user(mid, text, rid, attachments=None):
    return {"role": "user", "content": text, "message_id": mid, "request_id": rid,
            "turn_request_id": rid, "attachments": attachments or []}


def calls(mid, rid, items):
    return {"role": "tool_calls", "message_id": mid, "request_id": rid,
            "turn_request_id": rid,
            "calls": [{"id": cid, "name": name, "summary": summary}
                      for cid, name, summary in items]}


def tool(mid, rid, cid, name, status, summary="", **kw):
    item = {"role": "tool", "message_id": mid, "turn_request_id": rid,
            "tool_call_id": cid, "name": name, "content": status, "status": status,
            "reason_code": kw.get("reason_code", ""),
            "summary": summary, "action_hint": kw.get("action_hint", ""),
            "completed_at_ms": kw.get("completed_at_ms", 0),
            "elapsed_ms": kw.get("elapsed_ms", 0),
            "execution_order_available": bool(kw.get("completed_at_ms", 0)),
            "truncated": False}
    return item


def note(mid, rid, text):
    return {"role": "assistant", "content": text, "message_id": mid,
            "request_id": rid, "turn_request_id": rid, "progress": True}


def answer(mid, rid, text, gen_ms=0):
    item = {"role": "assistant", "content": text, "message_id": mid,
            "request_id": rid, "turn_request_id": rid, "progress": False}
    if gen_ms:
        item["gen_time_ms"] = gen_ms
    return item


def file(fid, name="report.docx", origin="generated"):
    return {"file_id": fid, "filename": name, "origin": origin,
            "kind": "docx", "size_bytes": 2048}


# ─────────────── 1. 正常完成的多工具轮次 ───────────────
def test_multi_tool_turn_is_one_bubble():
    rid = "crid-multi"
    items = [
        user(1, "帮我查柑橘品种", rid),
        calls(2, rid, [("c1", "deepseek_search", "检索 柑橘 品种"),
                       ("c2", "deepseek_search", "检索 柑橘 育种"),
                       ("c3", "local_hybrid_search", "本地检索 柑橘")]),
        tool(3, rid, "c1", "网页搜索", "complete", "获得 8 条网页资料", completed_at_ms=101),
        tool(4, rid, "c2", "网页搜索", "complete", "获得 8 条网页资料", completed_at_ms=102),
        tool(5, rid, "c3", "本地检索", "complete", "找到 7 条候选资料", completed_at_ms=103),
        note(2, rid, "先看公开资料，再核对本地文献。"),   # 与调用同一条助手消息
        answer(7, rid, "华农共记录 200 余个柑橘品种。"),
    ]
    turns = build_history_turns(items)
    assert len(turns) == 1
    turn = turns[0]
    assert turn["turn_request_id"] == rid
    assert turn["user"]["message_id"] == 1
    assert turn["answer"]["message_id"] == 7
    # 3 个工具 + 1 条工作说明，全部留在同一轮
    assert [e["kind"] for e in turn["trace"]] == ["note", "call", "call", "call"]
    assert {e["tool_call_id"] for e in turn["trace"] if e["kind"] == "call"} == {"c1", "c2", "c3"}
    # 工作说明排在它所解释的那批调用之前
    assert turn["trace"][0]["kind"] == "note"
    assert turn["trace"][1]["message_id"] == 2


def test_standalone_narration_keeps_its_position_and_is_not_the_answer():
    rid = "crid-narration"
    items = [
        user(1, "检索一下", rid),
        calls(2, rid, [("c1", "deepseek_search", "q")]),
        tool(3, rid, "c1", "网页搜索", "complete", "ok", completed_at_ms=1),
        # 没有 tool_calls 的中途旁白（真实库 id=629 的形状）
        {"role": "assistant", "content": "结果不完整，我再补一次。",
         "message_id": 4, "request_id": rid, "turn_request_id": rid,
         "progress": False},
        answer(5, rid, "最终回答。"),
    ]
    turns = build_history_turns(items)
    assert len(turns) == 1
    trace = turns[0]["trace"]
    assert [e["kind"] for e in trace] == ["call", "note"]
    assert trace[1]["content"] == "结果不完整，我再补一次。"
    # 最后一条才是回答，旁白不得被当成答案
    assert turns[0]["answer"]["message_id"] == 5
    assert turns[0]["answer"]["content"] == "最终回答。"


def test_partial_window_does_not_promote_a_narration_to_answer():
    rid = "crid-partial"
    items = [user(1, "问题", rid), note(2, rid, "正在检索。")]
    turns = build_history_turns(items)
    assert len(turns) == 1
    assert turns[0].get("partial") is True
    assert turns[0]["answer"] is None
    assert [e["kind"] for e in turns[0]["trace"]] == ["note"]


# ─────────────── 2. 先失败后成功（真实重试都保留） ───────────────
def test_failed_step_then_retry_are_both_kept():
    rid = "crid-retry"
    items = [
        user(1, "画一张图", rid),
        calls(2, rid, [("call-fail", "draw_svg", "绘制示意图")]),
        tool(3, rid, "call-fail", "绘制示意图", "failed", "SVG 格式不合法",
             reason_code="SVG_FORMAT_ERROR", completed_at_ms=10),
        calls(4, rid, [("call-ok", "draw_svg", "绘制示意图")]),
        tool(5, rid, "call-ok", "绘制示意图", "complete", "已生成图形", completed_at_ms=20),
        answer(6, rid, "图已生成。"),
    ]
    turns = build_history_turns(items)
    assert len(turns) == 1
    trace = turns[0]["trace"]
    assert [e["tool_call_id"] for e in trace] == ["call-fail", "call-ok"]
    assert [e["status"] for e in trace] == ["failed", "complete"]
    assert trace[0]["reason_code"] == "SVG_FORMAT_ERROR"
    # 失败记录不得被"修复界面"删掉
    assert any(e["status"] == "failed" for e in trace)


# ─────────────── 3. 报告生成失败后重试成功 ───────────────
def test_report_failure_then_retry_stays_in_one_turn():
    rid = "crid-report"
    items = [
        user(1, "导出一份分析报告", rid),
        note(2, rid, "先把正文整理成报告结构。"),
        calls(3, rid, [("exp-1", "export_paper_analysis", "导出分析报告")]),
        tool(4, rid, "exp-1", "导出报告", "failed", "末段截断", completed_at_ms=30),
        note(5, rid, "报告生成时末段截断，我补全并重新导出。"),
        calls(6, rid, [("exp-2", "export_paper_analysis", "导出分析报告")]),
        tool(7, rid, "exp-2", "导出报告", "complete", "报告已导出", completed_at_ms=40),
        answer(8, rid, "报告已重新导出。"),
    ]
    turns = build_history_turns(items)
    assert len(turns) == 1
    trace = turns[0]["trace"]
    assert [e["kind"] for e in trace] == ["note", "call", "note", "call"]
    assert [e["tool_call_id"] for e in trace if e["kind"] == "call"] == ["exp-1", "exp-2"]
    assert turns[0]["answer"]["message_id"] == 8


# ─────────────── 4. 一轮多个生成文件（不重复） ───────────────
def test_generated_files_are_deduped_within_the_turn():
    rid = "crid-files"
    attachments = [file("f" * 32, "report.docx"), file("e" * 32, "fig1.png"),
                   file("f" * 32, "report.docx"),          # 重复行
                   file("u" * 32, "input.pdf", origin="uploaded")]
    items = [user(1, "生成报告和配图", rid, attachments),
             calls(2, rid, [("c1", "export_paper_analysis", "导出报告")]),
             tool(3, rid, "c1", "导出报告", "complete", "报告已导出", completed_at_ms=5),
             answer(4, rid, "已生成。")]
    turns = build_history_turns(items)
    assert len(turns) == 1
    turn = turns[0]
    assert [f["file_id"] for f in turn["files"]] == ["f" * 32, "e" * 32]
    # 上传件留在用户气泡，生成件留在助手气泡
    assert [f["filename"] for f in turn["user"]["attachments"]] == ["input.pdf"]


# ─────────────── 5. 两次相同的检索都必须保留 ───────────────
def test_identical_searches_are_not_treated_as_duplicates():
    rid = "crid-same"
    items = [
        user(1, "查一下华农柑橘", rid),
        calls(2, rid, [("call-a", "deepseek_search", "华中农业大学 柑橘 品种")]),
        tool(3, rid, "call-a", "网页搜索", "complete", "获得 8 条网页资料", completed_at_ms=1),
        calls(4, rid, [("call-b", "deepseek_search", "华中农业大学 柑橘 品种")]),
        tool(5, rid, "call-b", "网页搜索", "complete", "获得 8 条网页资料", completed_at_ms=2),
        answer(6, rid, "两次检索结果一致。"),
    ]
    turns = build_history_turns(items)
    trace = turns[0]["trace"]
    assert [e["tool_call_id"] for e in trace] == ["call-a", "call-b"]
    assert len({e["tool_call_id"] for e in trace}) == 2


# ─────────────── 6. 一轮多个回答 → 只留最后一条（图源展开） ───────────────
def test_last_answer_wins_so_expanded_figure_is_the_only_bubble():
    rid = "crid-figure"
    items = [
        user(1, "画个示意图", rid),
        calls(2, rid, [("c1", "draw_svg", "绘制示意图")]),
        tool(3, rid, "c1", "绘制示意图", "complete", "已生成图形", completed_at_ms=1),
        answer(4, rid, "示意图如下：[[FIGURE:1]]"),      # 占位符版本
        answer(5, rid, "示意图如下：```svg\n<svg/>\n```"),  # figures.expand 版本
    ]
    turns = build_history_turns(items)
    assert len(turns) == 1
    assert turns[0]["answer"]["message_id"] == 5
    assert "```svg" in turns[0]["answer"]["content"]


# ─────────────── 7. 分页边界不切断逻辑轮次 ───────────────
def test_pagination_never_cuts_a_turn_in_half():
    items = []
    mid = 0
    for turn_no in range(6):
        rid = "crid-%d" % turn_no
        mid += 1
        items.append(user(mid, "问题%d" % turn_no, rid))
        for k in range(3):
            mid += 1
            items.append(calls(mid, rid, [("c%d-%d" % (turn_no, k), "deepseek_search", "q")]))
            mid += 1
            items.append(tool(mid, rid, "c%d-%d" % (turn_no, k), "网页搜索", "complete",
                              "ok", completed_at_ms=mid))
        mid += 1
        items.append(answer(mid, rid, "回答%d" % turn_no))
    for limit in (1, 2, 3, 5, 8, 13, 60):
        window, has_more, cursor = _slice_history(items, limit)
        assert window[0]["role"] == "user", "limit=%d 窗口首行不是问题" % limit
        turns = build_history_turns(window)
        assert turns[0]["user"] is not None
        # 窗口里每一轮的步骤都齐全：3 个工具 + 1 个回答
        for turn in turns:
            assert len([e for e in turn["trace"] if e["kind"] == "call"]) == 3
            assert turn["answer"] is not None
        if has_more:
            earlier, _, _ = _slice_history(items, limit, cursor)
            assert earlier, "有更早数据时游标必须能翻页"


def test_window_starting_mid_turn_does_not_leak_into_next_question():
    rid_old, rid_new = "crid-old", "crid-new"
    items = [
        user(1, "旧问题", rid_old),
        calls(2, rid_old, [("c1", "deepseek_search", "q")]),
        tool(3, rid_old, "c1", "网页搜索", "complete", "ok", completed_at_ms=3),
        answer(4, rid_old, "旧回答"),
        user(5, "新问题", rid_new),
        answer(6, rid_new, "新回答"),
    ]
    # 人为构造一个从旧轮中间开始的窗口（模拟旧版本分页/游标行为）
    window = items[2:]
    turns = build_history_turns(window)
    assert len(turns) == 2
    assert turns[0]["turn_request_id"] == rid_old
    assert turns[0]["user"] is None            # 问题在更早一页
    assert [e["tool_call_id"] for e in turns[0]["trace"]] == ["c1"]
    assert turns[1]["turn_request_id"] == rid_new
    assert turns[1]["user"]["message_id"] == 5


# ─────────────── 8. 刷新 = 首次渲染（分页合并前后气泡数一致） ───────────────
def test_refresh_and_earlier_pages_keep_bubble_counts():
    items = []
    mid = 0
    for turn_no in range(4):
        rid = "crid-p%d" % turn_no
        mid += 1
        items.append(user(mid, "问题%d" % turn_no, rid))
        mid += 1
        items.append(calls(mid, rid, [("x%d" % turn_no, "deepseek_search", "q")]))
        mid += 1
        items.append(tool(mid, rid, "x%d" % turn_no, "网页搜索", "complete", "ok",
                          completed_at_ms=mid))
        mid += 1
        items.append(note(mid, rid, "分析%d" % turn_no))
        mid += 1
        items.append(answer(mid, rid, "回答%d" % turn_no))

    page1, has_more, cursor = _slice_history(items, 2)
    first = build_history_turns(page1)
    assert has_more
    page2, _, _ = _slice_history(items, 2, cursor)
    # 前端按 message_id 去重后拼接再整体归并（等价于 loadEarlierHistory）
    merged = page2 + page1
    seen, deduped = set(), []
    for row in merged:
        m = row.get("message_id")
        if m and m in seen:
            continue
        if m:
            seen.add(m)
        deduped.append(row)
    second = build_history_turns(deduped)
    # 已渲染过的轮次不会被重建成新气泡，只新增更早的轮次
    assert len(second) == len(first) + 1
    keys_first = [t["turn_request_id"] for t in first]
    keys_second = [t["turn_request_id"] for t in second]
    assert keys_second[-len(keys_first):] == keys_first
    for turn in second:
        assert len([e for e in turn["trace"] if e["kind"] == "note"]) == 1
        assert turn["answer"] is not None


# ─────────────── 9. Agent 注入的假 human 行不得开新轮 ───────────────
def test_injected_human_nudge_does_not_open_a_turn():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    nudge = ("用户明确要求的任务或来源尚未完成。请调用以下尚未尝试的工具组："
             "deepseek_search。完成一次尝试后检查回执；若仍无法完成，请如实说明。")
    msgs = [
        HumanMessage(content="<user_query>检索本地文献</user_query>"),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "local_hybrid_search",
                                           "args": {"query": "柑橘"}}]),
        ToolMessage(content="本地检索已执行", tool_call_id="c1", name="local_hybrid_search"),
        AIMessage(content="本地有 8 条候选资料。"),
        HumanMessage(content=nudge),          # 主循环自己追加的"人类口吻"提示
        AIMessage(content="", tool_calls=[{"id": "c2", "name": "deepseek_search",
                                           "args": {"query": "柑橘"}}]),
        ToolMessage(content="获得 8 条网页资料", tool_call_id="c2", name="deepseek_search"),
        AIMessage(content="综合本地与联网资料，共 200 余个品种。"),
    ]
    items = _history_items_from_messages(msgs, list(range(101, 109)), ["crid-real"], {})
    assert [it["role"] for it in items].count("user") == 1
    turns = build_history_turns(items)
    assert len(turns) == 1, "注入提示把一轮拆成了两轮"
    assert turns[0]["answer"]["content"].startswith("综合本地与联网资料")
    assert {e["tool_call_id"] for e in turns[0]["trace"] if e["kind"] == "call"} == {"c1", "c2"}


# ─────────────── 10. 前端归并规则（与后端同一套） ───────────────
@pytest.fixture(scope="module")
def app_js():
    return io.open(str(APP_JS), encoding="utf-8").read()


def test_history_renderer_groups_by_turn_instead_of_per_assistant(app_js):
    start = app_js.index("function renderHistoryMessages(")
    end = app_js.index("\nfunction ", start + 10)
    body = app_js[start:end]
    assert "groupHistoryTurns(" in body, "历史渲染必须走统一轮次归并"
    assert "toolStatusText(" in body, "轨迹行渲染仍在该函数内（作用域契约）"
    # 旧的"累积 pendingTools、每条 assistant 再渲染一份"实现必须消失
    assert "pendingTools" not in app_js
    assert "pendingGenerated" not in app_js
    # 每轮只创建一个助手气泡
    assert body.count("addMessage('bot'") == 1


def test_file_section_is_reused_not_stacked(app_js):
    start = app_js.index("function appendTurnGeneratedFiles(")
    end = app_js.index("\nfunction ", start + 10)
    body = app_js[start:end]
    assert "content.querySelector('.turn-files')" in body
    assert "if (!added) return;" in body


def test_pages_merge_by_message_id_not_by_text(app_js):
    start = app_js.index("async function loadEarlierHistory(")
    end = app_js.index("\nfunction ", start + 10)
    body = app_js[start:end]
    assert "message_id" in body
    assert "seenIds" in body


# ─────────────── 10b. 主循环 nudge 不开启新轮次（结构规则） ───────────────
def test_unkeyed_agent_nudges_never_open_a_user_bubble():
    """Real questions carry a request identity; loop nudges do not.

    Live sessions already contain budget/reflection nudges the text phrase
    list never knew about; they must not reset the turn key or render a
    question bubble.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    msgs = [
        HumanMessage(content="<user_query>第一问</user_query>"),
        AIMessage(content="回答一"),
        HumanMessage(content="预算已延长。请继续完成尚未完成的步骤：还需调用 export_paper_analysis。"),
        HumanMessage(content="[反思] 尚未满足的要求：尚未生成可下载的核验表。"),
        AIMessage(content="回答二"),
        HumanMessage(content="<user_query>第二问</user_query>"),
        AIMessage(content="回答三"),
    ]
    row_ids = [10, 11, 12, 13, 14, 15, 16]
    # One identity per human row: real question, nudge, nudge, real question.
    request_ids = ["crid-1", "", "", "crid-2"]
    items = _history_items_from_messages(msgs, row_ids, request_ids, {})
    users = [it for it in items if it["role"] == "user"]
    assert [u["content"] for u in users] == ["第一问", "第二问"]
    assert [u["turn_request_id"] for u in users] == ["crid-1", "crid-2"]
    # The assistant rows after the nudges stay in their own turn.
    answers = [it for it in items if it["role"] == "assistant" and not it.get("progress")]
    assert [a["turn_request_id"] for a in answers] == ["crid-1", "crid-1", "crid-2"]
    turns = build_history_turns(items)
    assert len(turns) == 2
    assert turns[0]["answer"]["content"] == "回答二"
    assert turns[1]["answer"]["content"] == "回答三"


def test_legacy_session_without_any_identity_keeps_text_rule():
    """Pre-identity sessions must not lose every question to the new rule."""
    from langchain_core.messages import HumanMessage

    msgs = [HumanMessage(content="旧会话里真实的问题"), HumanMessage(content="上一轮回答因输出长度上限被截断。请更精简地继续")]
    request_ids = ["", ""]
    items = _history_items_from_messages(msgs, [1, 2], request_ids, {})
    assert [it["role"] for it in items] == ["user"]


# ─────────────── 11. 真实会话（只读副本） ───────────────
def _real_db():
    for path in REAL_DB_CANDIDATES:
        if path and pathlib.Path(path).is_file():
            return pathlib.Path(path)
    return None


@pytest.mark.skipif(_real_db() is None, reason="本机没有真实会话库（agent/state/sessions.db）")
def test_real_session_history_regroups_into_one_bubble_per_turn(tmp_path):
    import asyncio

    from src.session.manager import SessionManager

    src = _real_db()
    copy = tmp_path / "sessions.db"
    shutil.copy2(str(src), str(copy))          # 只读副本：绝不写真实库

    conn = sqlite3.connect("file:%s?mode=ro" % copy, uri=True)
    try:
        rows = conn.execute(
            "SELECT session_id, SUM(CASE WHEN msg_type = 'ai' THEN 1 ELSE 0 END) a, "
            "COUNT(*) c FROM messages GROUP BY session_id "
            "ORDER BY a DESC, c DESC LIMIT 1").fetchall()
    finally:
        conn.close()
    if not rows:
        pytest.skip("真实库副本里没有会话（该树未接真实数据）")
    session_id = rows[0][0]

    mgr = SessionManager.__new__(SessionManager)
    mgr.db_path = str(copy)
    msgs, row_ids = asyncio.run(mgr.get_messages_with_ids(session_id))
    request_ids = asyncio.run(mgr.get_user_message_request_ids(session_id))
    items = _history_items_from_messages(msgs, row_ids, request_ids, {})

    assistant_items = [it for it in items if it["role"] == "assistant"]
    turns = build_history_turns(items)
    bubbles = [t for t in turns if t["answer"] or t["trace"]]

    # 1) 每个助手气泡恰好对应一个逻辑轮次
    assert len(bubbles) == len(turns)
    assert all(t["user"] is not None for t in turns), "每一轮都要有自己的问题行"
    # 2) 工作说明进轨迹，不再各自占一个气泡
    notes = [e for t in turns for e in t["trace"] if e["kind"] == "note"]
    # 该会话若存在“一轮多条 assistant 行”，合并后气泡必须严格少于行数；
    # 若库内每个轮次本来就只有一条 assistant 行（1:1），上面 len(bubbles)==len(turns) 已足够。
    if len(assistant_items) > len(turns):
        assert len(bubbles) < len(assistant_items) + len(notes)
    # 3) 幂等：轮内 tool_call_id / message_id 不重复
    for turn in turns:
        call_ids = [e["tool_call_id"] for e in turn["trace"] if e["kind"] == "call"]
        assert len(call_ids) == len(set(call_ids))
        assert len(turn["message_ids"]) == len(set(turn["message_ids"]))
    # 4) 分页不切轮次
    for limit in (2, 5, 11, 60):
        window, _has_more, _cursor = _slice_history(items, limit)
        if window:
            assert window[0]["role"] == "user"

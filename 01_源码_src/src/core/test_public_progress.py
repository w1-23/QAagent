# -*- coding: utf-8 -*-
"""S2：公开工作说明（``assistant_progress``）回归测试。

要证明的三件事，正好对应最容易出错的三个地方：

1. **分类在"轮次"层面**：有工具调用的一轮，文本是工作说明；没有工具调用
   的一轮，文本是最终回答 —— 后者绝不能进时间线（否则答案被污染/重复）。
2. **过滤可靠**：DSML/工具协议、引用标记、凭证样式的内容一律不出门，
   长度有上限，空文本不产生事件。
3. **失败不影响执行**：通道关闭、事件发射抛异常、重复文本，都不能让
   Agent 报错或产生假步骤。

运行：`python -m pytest src/core/test_public_progress.py -q`
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from src.core import public_progress as pp  # noqa: E402
from src.core import progress_bus  # noqa: E402


def _drain(queue: asyncio.Queue) -> list[dict]:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


# ── 1) 轮次级分类 ──────────────────────────────────────────────────────
def test_final_round_text_is_never_a_progress_note():
    """没有工具调用的轮次 = 最终回答，不得产出工作说明。"""
    assert pp.public_progress_text(["我先检索证据。"], has_tool_calls=False) == ""


def test_tool_round_text_becomes_a_progress_note():
    assert pp.public_progress_text(
        ["已有初步线索，", "我接下来核对公开来源。"], has_tool_calls=True
    ) == "已有初步线索，我接下来核对公开来源。"


def test_round_without_text_emits_nothing():
    assert pp.public_progress_text([], has_tool_calls=True) == ""
    assert pp.public_progress_text(["   \n  "], has_tool_calls=True) == ""


# ── 2) 过滤与边界 ──────────────────────────────────────────────────────
def test_dsml_tool_protocol_is_stripped():
    raw = ("<||DSML|| invoke name=\"local_hybrid_search\">"
           "<||DSML|| parameter name=\"query\">黄龙病</||DSML|| parameter>"
           "</||DSML|| invoke> 我先查本地资料。")
    note = pp.sanitize(raw)
    assert "DSML" not in note and "invoke" not in note and "parameter" not in note
    assert note == "我先查本地资料。"


def test_citation_markers_and_whitespace_are_normalized():
    assert pp.sanitize("结论[W1]与[2]成立。\n\n下一步核对来源。") == "结论与成立。 下一步核对来源。"


def test_credential_like_text_is_rejected():
    for raw in ("key 是 sk-abcdefgh12345678", "api_key = abc", "token: 123456",
                "A" * 64):
        assert pp.sanitize(raw) == "", raw


def test_long_note_is_bounded():
    note = pp.sanitize("字" * (pp.MAX_CHARS + 200))
    assert len(note) == pp.MAX_CHARS and note.endswith("…")


# ── 3) 事件发射 ────────────────────────────────────────────────────────
def test_event_carries_contract_fields():
    queue: asyncio.Queue = asyncio.Queue()
    progress_bus.set_request_queue(queue)
    try:
        assert pp.emit_public_progress("我先核对公开来源。", step_id="analysis_002")
        events = _drain(queue)
    finally:
        progress_bus.clear_request_queue()
        pp.reset_emitted()
    assert len(events) == 1
    assert events[0]["event"] == "assistant_progress"
    payload = json.loads(events[0]["data"])
    assert payload["step_id"] == "analysis_002"
    assert payload["phase"] == "working"
    assert payload["text"] == "我先核对公开来源。"
    assert payload["chars"] == len("我先核对公开来源。")


def test_duplicate_note_is_emitted_once_per_request():
    queue: asyncio.Queue = asyncio.Queue()
    progress_bus.set_request_queue(queue)
    try:
        assert pp.emit_public_progress("同一句话。")
        assert not pp.emit_public_progress("同一句话。")
        assert len(_drain(queue)) == 1
        pp.reset_emitted()
        assert pp.emit_public_progress("同一句话。")
    finally:
        progress_bus.clear_request_queue()
        pp.reset_emitted()


def test_disabled_channel_emits_nothing():
    queue: asyncio.Queue = asyncio.Queue()
    progress_bus.set_request_queue(queue)
    os.environ["CITRUS_PUBLIC_PROGRESS"] = "0"
    try:
        assert not pp.emit_public_progress("我先核对来源。")
        assert _drain(queue) == []
    finally:
        os.environ.pop("CITRUS_PUBLIC_PROGRESS", None)
        progress_bus.clear_request_queue()
        pp.reset_emitted()


def test_emitter_failure_never_propagates(monkeypatch):
    """时间线出问题不能影响 Agent 执行。"""
    def boom(*args, **kwargs):
        raise RuntimeError("sse down")

    monkeypatch.setattr(pp, "emit_progress", boom)
    pp.reset_emitted()
    assert pp.emit_public_progress("我先核对来源。") is False


def test_graph_forwards_public_text_only_for_tool_rounds(monkeypatch):
    """接线检查：`_call_llm` 必须把回调交给流式函数，而不是丢掉。"""
    from src.graph import general_agent_graph as g

    seen = {}

    async def fake_stream(llm, messages, on_text=None):
        seen["on_text"] = on_text
        if on_text:
            on_text("我先检索证据。")

        class _Resp:
            content = ""
            tool_calls = []
            response_metadata = {}

        return _Resp()

    monkeypatch.setattr(g, "stream_llm_response", fake_stream)
    asyncio.run(g._call_llm(object(), [], timeout=5, on_text=lambda _c: None))
    assert callable(seen["on_text"])

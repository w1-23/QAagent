# -*- coding: utf-8 -*-
"""A1 回归：工具计时必须跨上下文可见 + 临时正文可作废。

两个真实缺陷的守卫：
1. 计时表曾在 ContextVar 里 → SSE 心跳任务读不到，长工具执行期间前端
   5 秒无帧（"请求无响应"）。这里用线程模拟"工具在别的上下文里跑"。
2. 流式增量可能先把工具轮文本推上屏，轮末必须能发 ``text_reset`` 作废。
"""
from __future__ import annotations

import asyncio
import threading
import time

from src.core import progress_bus as pb


def _drain(queue: asyncio.Queue) -> list[dict]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def test_tool_timer_visible_from_another_thread_and_context():
    pb.clear_tool_timers()
    done = threading.Event()

    def _worker() -> None:
        pb.mark_tool_start("call-x", "local_search")
        done.wait(1.0)

    t = threading.Thread(target=_worker)
    t.start()
    time.sleep(0.05)

    # 主线程（另一个上下文副本）必须看得到这个正在跑的工具
    running = pb.get_running_tools()
    assert any(cid == "call-x" and name == "local_search" for cid, name, _ in running), running

    elapsed_ms = pb.mark_tool_end("call-x")
    assert elapsed_ms >= 0
    assert all(cid != "call-x" for cid, _n, _e in pb.get_running_tools())

    done.set()
    t.join(2.0)


def test_emit_text_reset_reaches_queue():
    queue: asyncio.Queue = asyncio.Queue()
    pb.set_request_queue(queue)
    try:
        pb.emit_text("协议片段")
        pb.emit_text_reset("tool_round")
        events = _drain(queue)
    finally:
        pb.clear_request_queue()

    assert [e["event"] for e in events] == ["text", "text_reset"]
    assert events[1]["data"] and "tool_round" in events[1]["data"]


def test_streamed_deltas_join_into_answer():
    """节流泵语义：多次增量拼起来必须等于原文，不丢字、不重复。"""
    chunks = ["黄龙病", "由韧皮部杆菌引起，", "通过木虱传播。"]
    pending: list[str] = []
    sent: list[str] = []

    def flush(force: bool = False) -> None:
        if not pending:
            return
        if not force:
            return
        sent.append("".join(pending))
        pending.clear()

    for c in chunks:
        pending.append(c)
        flush()
    flush(force=True)

    assert "".join(sent) == "".join(chunks)

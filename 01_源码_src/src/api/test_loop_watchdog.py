# -*- coding: utf-8 -*-
"""事件循环卡顿看门狗的行为测试。

看门狗的价值在于：**它能在循环被阻塞时仍然运行**，并指出是哪一帧卡住了
循环。这两点都无法用"测一下耗时"证明，所以这里用真实阻塞来验证：

1. 在协程里做同步 ``time.sleep``（这就是线上"心跳与断线检测排不上队"的
   最小复现），断言被捕获，且栈里能读到造成阻塞的函数名。
2. 正常 ``await asyncio.sleep`` 不得误报。
3. ``uninstall`` 之后不再采样。
"""
from __future__ import annotations

import asyncio
import time

import pytest

from src.core import loop_watchdog


def _run(coro):
    return asyncio.run(coro)


def _blocking_sync_step(seconds: float) -> None:
    """同步阻塞步骤：等价于线上"检索/重排序占住事件循环"。"""
    time.sleep(seconds)


def test_sync_sleep_in_coroutine_is_captured_with_its_frame():
    async def scenario():
        loop_watchdog.install(stall_ms=100, tick_s=0.02)
        await asyncio.sleep(0.05)      # 让 ticker 先跑一拍，起点干净
        _blocking_sync_step(0.5)
        await asyncio.sleep(0.2)       # 给看门狗线程一次采样机会
        return loop_watchdog.snapshot()

    try:
        snap = _run(scenario())
    finally:
        loop_watchdog.uninstall()

    assert snap["installed"] is True     # 快照取自卸载前，这是预期
    assert snap["stall_count"] >= 1, snap
    assert snap["max_stall_ms"] >= 100, snap
    frames = snap["stalls"][0]["frames"]
    assert frames, "阻塞栈为空，看门狗没有取到循环线程的帧"
    assert any("_blocking_sync_step" in line for line in frames), frames


def test_async_wait_does_not_trip_the_watchdog():
    async def scenario():
        loop_watchdog.install(stall_ms=120, tick_s=0.02)
        try:
            await asyncio.sleep(0.5)
            return loop_watchdog.snapshot()
        finally:
            loop_watchdog.uninstall()

    snap = _run(scenario())

    assert snap["stall_count"] == 0, snap
    assert snap["max_stall_ms"] == 0.0, snap


def test_uninstall_stops_sampling():
    async def scenario():
        loop_watchdog.install(stall_ms=120, tick_s=0.02)
        thread = loop_watchdog._STATE["thread"]
        await asyncio.sleep(0.1)
        loop_watchdog.uninstall()
        tick_after_uninstall = loop_watchdog.snapshot()["tick"]
        await asyncio.sleep(0.15)
        return thread, tick_after_uninstall, loop_watchdog.snapshot()["tick"]

    thread, tick_a, tick_b = _run(scenario())

    assert thread is not None and not thread.is_alive()
    assert tick_b == tick_a, "uninstall 之后仍在采样"


def test_env_switch_disables_install(monkeypatch):
    monkeypatch.setenv("CITRUSQA_LOOP_WATCHDOG", "0")

    async def scenario():
        started = loop_watchdog.install(stall_ms=120, tick_s=0.02)
        return started, loop_watchdog.snapshot()["installed"]

    started, installed = _run(scenario())
    assert started is False
    assert installed is False


def test_snapshot_shape_is_stable():
    snap = loop_watchdog.snapshot()
    for key in ("installed", "stall_ms", "tick_s", "tick", "stall_count",
                "max_stall_ms", "stalls"):
        assert key in snap, key
    assert isinstance(snap["stalls"], list)

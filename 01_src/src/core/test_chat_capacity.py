"""Local concurrency checks for the shared chat capacity gate."""
from __future__ import annotations

import asyncio
import sys

from src.core.chat_capacity import ChatCapacity


def _run(coro):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)


def test_ten_active_two_waiting_and_cancellation_recover_capacity():
    async def scenario():
        capacity = ChatCapacity()
        capacity.max_active = 10
        capacity.max_pending = 2
        capacity.wait_timeout = 5
        release_active = asyncio.Event()
        entered = 0
        current = 0
        peak = 0

        async def worker(wait_for_release: bool):
            nonlocal entered, current, peak
            await capacity.acquire()
            entered += 1
            current += 1
            peak = max(peak, current)
            try:
                if wait_for_release:
                    await release_active.wait()
            finally:
                current -= 1
                await capacity.release()

        active = [asyncio.create_task(worker(True)) for _ in range(10)]
        for _ in range(1000):
            if (await capacity.snapshot())["active"] == 10:
                break
            await asyncio.sleep(0.001)
        assert (await capacity.snapshot())["active"] == 10

        queued_a = asyncio.create_task(worker(False))
        queued_b = asyncio.create_task(worker(False))
        for _ in range(1000):
            if (await capacity.snapshot())["queued"] == 2:
                break
            await asyncio.sleep(0.001)
        assert (await capacity.snapshot())["queued"] == 2

        try:
            await capacity.acquire()
        except OverflowError:
            pass
        else:
            raise AssertionError("third waiting task should be rejected at queue capacity")

        queued_b.cancel()
        try:
            await queued_b
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled queue waiter should propagate cancellation")
        assert (await capacity.snapshot())["queued"] == 1

        release_active.set()
        await asyncio.gather(*active, queued_a)
        snapshot = await capacity.snapshot()
        assert snapshot["active"] == 0
        assert snapshot["queued"] == 0
        assert entered == 11
        assert peak <= 10

    _run(scenario())


def test_queued_payload_limit_and_cancel_cleanup():
    async def scenario():
        capacity = ChatCapacity()
        capacity.max_active = 1
        capacity.max_pending = 3
        capacity.max_queued_payload_bytes = 10
        capacity.wait_timeout = 5
        await capacity.acquire()
        queued = asyncio.create_task(capacity.acquire(queued_payload_bytes=8))
        for _ in range(1000):
            if (await capacity.snapshot())["queued"] == 1:
                break
            await asyncio.sleep(0.001)
        try:
            await capacity.acquire(queued_payload_bytes=3)
        except OverflowError as exc:
            assert "附件容量" in str(exc)
        else:
            raise AssertionError("queued payload over budget must be rejected")
        queued.cancel()
        try:
            await queued
        except asyncio.CancelledError:
            pass
        await capacity.release()
        snapshot = await capacity.snapshot()
        assert snapshot["active"] == snapshot["queued"] == snapshot["queued_payload_bytes"] == 0

    _run(scenario())


def test_queue_timeout_removes_ticket_and_payload_reservation():
    async def scenario():
        capacity = ChatCapacity()
        capacity.max_active = 1
        capacity.max_pending = 2
        capacity.max_queued_payload_bytes = 100
        capacity.wait_timeout = 0.02
        await capacity.acquire()

        try:
            await capacity.acquire(queued_payload_bytes=37)
        except TimeoutError as exc:
            assert "排队等待" in str(exc)
            assert "0.02 秒" in str(exc)
        else:
            raise AssertionError("a waiter that exceeds its deadline must time out")

        snapshot = await capacity.snapshot()
        assert snapshot["active"] == 1
        assert snapshot["queued"] == 0
        assert snapshot["queued_payload_bytes"] == 0
        await capacity.release()
        assert (await capacity.snapshot())["active"] == 0

    _run(scenario())


def test_capacity_configuration_is_bounded_and_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv("CITRUSQA_MAX_ACTIVE_CHATS", "999999")
    monkeypatch.setenv("CITRUSQA_MAX_PENDING_CHATS", "not-an-integer")
    monkeypatch.setenv("CITRUSQA_MAX_QUEUED_ATTACHMENT_BYTES", "-1")
    monkeypatch.setenv("CITRUSQA_QUEUE_WAIT_SECONDS", "999999999")
    capacity = ChatCapacity()
    assert capacity.max_active == 128
    assert capacity.max_pending == 100
    assert capacity.max_queued_payload_bytes == 0
    assert capacity.wait_timeout == 24 * 60 * 60


def test_owner_limits_share_fifo_gate_and_owner_release_cannot_free_another_account():
    async def scenario():
        capacity = ChatCapacity()
        capacity.max_active = 3
        capacity.max_pending = 5
        capacity.max_active_per_owner = 1
        capacity.max_pending_per_owner = 1
        capacity.wait_timeout = 2
        await capacity.acquire(owner_user_id=1)
        await capacity.acquire(owner_user_id=2)
        order = []
        async def waiting(owner, name):
            await capacity.acquire(owner_user_id=owner)
            order.append(name)
        first = asyncio.create_task(waiting(1, "owner-1-next"))
        for _ in range(1000):
            if (await capacity.snapshot(owner_user_id=1))["owner_queued"] == 1:
                break
            await asyncio.sleep(0.001)
        try:
            await capacity.acquire(owner_user_id=1, queued_payload_bytes=9)
        except OverflowError as exc:
            assert "本账号" in str(exc)
        else:
            raise AssertionError("the owner's pending quota must reject excess work")
        second = asyncio.create_task(waiting(3, "owner-3"))
        for _ in range(1000):
            if (await capacity.snapshot())["queued"] == 2:
                break
            await asyncio.sleep(0.001)
        await capacity.release(owner_user_id=99)
        assert (await capacity.snapshot())["active"] == 2
        assert not order, "a later account must not bypass the existing FIFO ticket"
        await capacity.release(owner_user_id=1)
        await asyncio.wait_for(asyncio.gather(first, second), 1)
        assert order == ["owner-1-next", "owner-3"]
        assert (await capacity.snapshot())["active"] == 3
        assert (await capacity.snapshot(owner_user_id=1))["owner_active"] == 1
        assert (await capacity.snapshot(owner_user_id=1))["owner_queued"] == 0
        assert (await capacity.snapshot())["queued_payload_bytes"] == 0
        for owner in (1, 2, 3):
            await capacity.release(owner_user_id=owner)
        assert (await capacity.snapshot())["active"] == 0
        assert capacity._active_by_owner == capacity._pending_by_owner == {}
    _run(scenario())


def test_owner_pending_cancel_timeout_and_callback_error_release_all_reservations():
    async def scenario():
        capacity = ChatCapacity()
        capacity.max_active = 4
        capacity.max_active_per_owner = capacity.max_pending_per_owner = 1
        await capacity.acquire(owner_user_id=7)
        waiter = asyncio.create_task(capacity.acquire(owner_user_id=7, queued_payload_bytes=8))
        for _ in range(1000):
            if (await capacity.snapshot(owner_user_id=7))["owner_queued"] == 1:
                break
            await asyncio.sleep(0.001)
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        assert (await capacity.snapshot(owner_user_id=7))["owner_queued"] == 0
        capacity.wait_timeout = 0.01
        try:
            await capacity.acquire(owner_user_id=7, queued_payload_bytes=9)
        except TimeoutError:
            pass
        else:
            raise AssertionError("owner-blocked waiter must obey the same deadline")
        async def broken_callback(*args):
            raise RuntimeError("fixture notification failed")
        try:
            await capacity.acquire(owner_user_id=7, queued_payload_bytes=10, on_wait=broken_callback)
        except RuntimeError:
            pass
        else:
            raise AssertionError("callback error must propagate after removing its ticket")
        snapshot = await capacity.snapshot(owner_user_id=7)
        assert snapshot["active"] == snapshot["owner_active"] == 1
        assert snapshot["owner_queued"] == snapshot["queued"] == snapshot["queued_payload_bytes"] == 0
        await capacity.release(owner_user_id=7)
        assert capacity._active_by_owner == capacity._pending_by_owner == {}
    _run(scenario())


def test_owner_configuration_is_positive_and_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv("CITRUSQA_MAX_ACTIVE_CHATS_PER_OWNER", "-5")
    monkeypatch.setenv("CITRUSQA_MAX_PENDING_CHATS_PER_OWNER", "bad")
    capacity = ChatCapacity()
    assert capacity.max_active_per_owner == 1
    assert capacity.max_pending_per_owner == 10
    monkeypatch.setenv("CITRUSQA_MAX_ACTIVE_CHATS_PER_OWNER", "999999")
    monkeypatch.setenv("CITRUSQA_MAX_PENDING_CHATS_PER_OWNER", "999999")
    capacity = ChatCapacity()
    assert capacity.max_active_per_owner == 128
    assert capacity.max_pending_per_owner == 1000


def test_simultaneous_arrivals_cannot_exceed_active_or_queue_limit():
    async def scenario():
        capacity = ChatCapacity()
        capacity.max_active = 2
        capacity.max_pending = 3
        capacity.wait_timeout = 5
        await asyncio.gather(*(capacity.acquire() for _ in range(2)))
        waiters = [asyncio.create_task(capacity.acquire()) for _ in range(8)]
        for _ in range(1000):
            if (await capacity.snapshot())["queued"] == 3:
                break
            await asyncio.sleep(0.001)
        assert (await capacity.snapshot())["queued"] == 3
        for task in waiters:
            if not task.done():
                task.cancel()
        results = await asyncio.gather(*waiters, return_exceptions=True)
        assert sum(isinstance(result, OverflowError) for result in results) == 5
        await capacity.release()
        await capacity.release()
        snapshot = await capacity.snapshot()
        assert snapshot["active"] == snapshot["queued"] == 0

    _run(scenario())

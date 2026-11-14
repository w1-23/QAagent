"""Single-worker FIFO capacity gate for chat requests."""
from __future__ import annotations

import asyncio
import os
from collections import deque


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


class ChatCapacity:
    def __init__(self) -> None:
        self.max_active = _env_int("CITRUSQA_MAX_ACTIVE_CHATS", 10, 1, 128)
        self.max_pending = _env_int("CITRUSQA_MAX_PENDING_CHATS", 100, 0, 1000)
        self.max_active_per_owner = _env_int("CITRUSQA_MAX_ACTIVE_CHATS_PER_OWNER", 2, 1, 128)
        self.max_pending_per_owner = _env_int("CITRUSQA_MAX_PENDING_CHATS_PER_OWNER", 10, 1, 1000)
        self.max_queued_payload_bytes = _env_int(
            "CITRUSQA_MAX_QUEUED_ATTACHMENT_BYTES", 64 * 1024 * 1024, 0, 1024 * 1024 * 1024)
        self.wait_timeout = _env_int("CITRUSQA_QUEUE_WAIT_SECONDS", 900, 1, 24 * 60 * 60)
        self._condition = asyncio.Condition()
        self._queue: deque[object] = deque()
        self._queued_payload_bytes = 0
        self._active = 0
        self._active_by_owner: dict[int | None, int] = {}
        self._pending_by_owner: dict[int | None, int] = {}

    @staticmethod
    def _validate_owner(owner_user_id):
        # None retains the existing internal/CLI API. HTTP always supplies the
        # authenticated account; model/client payloads never select this owner.
        if owner_user_id is not None and (type(owner_user_id) is not int or owner_user_id <= 0):
            raise ValueError("owner_user_id must be a positive integer")

    @staticmethod
    def _decrement(counts, owner):
        remaining = counts.get(owner, 0) - 1
        if remaining > 0:
            counts[owner] = remaining
        else:
            counts.pop(owner, None)

    def _owner_has_slot(self, owner):
        return owner is None or self._active_by_owner.get(owner, 0) < self.max_active_per_owner

    def _take_slot(self, owner):
        self._active += 1
        self._active_by_owner[owner] = self._active_by_owner.get(owner, 0) + 1

    async def acquire(self, on_wait=None, *, queued_payload_bytes: int = 0,
                      owner_user_id: int | None = None) -> None:
        self._validate_owner(owner_user_id)
        ticket = object()
        queued = False
        promoted = False
        payload_bytes = max(0, int(queued_payload_bytes or 0))
        async with self._condition:
            # Reserve an active slot atomically. New work cannot jump ahead of
            # an existing waiter, and simultaneous arrivals cannot bypass the
            # bounded queue while the semaphore's first waiter is being set up.
            if not self._queue and self._active < self.max_active and self._owner_has_slot(owner_user_id):
                self._take_slot(owner_user_id)
                return
            if (owner_user_id is not None
                    and self._pending_by_owner.get(owner_user_id, 0) >= self.max_pending_per_owner):
                raise OverflowError("本账号的排队任务数已达上限，请等待已有任务完成。")
            if len(self._queue) >= self.max_pending:
                raise OverflowError("当前排队人数已满，请稍后重试。")
            if payload_bytes and self._queued_payload_bytes + payload_bytes > self.max_queued_payload_bytes:
                raise OverflowError("等待队列的附件容量已满，请稍后重试；本轮附件尚未解码或处理。")
            self._queue.append(ticket)
            self._pending_by_owner[owner_user_id] = self._pending_by_owner.get(owner_user_id, 0) + 1
            self._queued_payload_bytes += payload_bytes
            queued = True
            position = len(self._queue)

        try:
            if on_wait:
                await on_wait(position, self.max_active, self.max_pending)
            async with asyncio.timeout(self.wait_timeout):
                async with self._condition:
                    await self._condition.wait_for(
                        lambda: bool(self._queue)
                        and self._queue[0] is ticket
                        and self._active < self.max_active
                        and self._owner_has_slot(owner_user_id)
                    )
                    self._queue.popleft()
                    self._decrement(self._pending_by_owner, owner_user_id)
                    self._queued_payload_bytes = max(0, self._queued_payload_bytes - payload_bytes)
                    self._take_slot(owner_user_id)
                    promoted = True
                    self._condition.notify_all()
        except TimeoutError as exc:
            seconds = self.wait_timeout
            duration = f"{seconds // 60} 分钟" if seconds % 60 == 0 else f"{seconds} 秒"
            raise TimeoutError(f"排队等待超过 {duration}，请稍后重试。") from exc
        finally:
            if queued and not promoted:
                cleanup = asyncio.create_task(self._remove_waiter(ticket, payload_bytes, owner_user_id))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup
                    raise

    async def _remove_waiter(self, ticket: object, payload_bytes: int, owner_user_id: int | None) -> None:
        async with self._condition:
            try:
                self._queue.remove(ticket)
            except ValueError:
                return
            self._decrement(self._pending_by_owner, owner_user_id)
            self._queued_payload_bytes = max(0, self._queued_payload_bytes - payload_bytes)
            self._condition.notify_all()

    async def release(self, *, owner_user_id: int | None = None) -> None:
        self._validate_owner(owner_user_id)
        async with self._condition:
            if self._active_by_owner.get(owner_user_id, 0):
                self._decrement(self._active_by_owner, owner_user_id)
                self._active -= 1
            self._condition.notify_all()

    async def snapshot(self, *, owner_user_id: int | None = None) -> dict[str, int]:
        self._validate_owner(owner_user_id)
        async with self._condition:
            result = {"active": self._active, "queued": len(self._queue),
                      "queued_payload_bytes": self._queued_payload_bytes,
                      "max_active": self.max_active, "max_pending": self.max_pending,
                      "max_active_per_owner": self.max_active_per_owner,
                      "max_pending_per_owner": self.max_pending_per_owner,
                      "max_queued_payload_bytes": self.max_queued_payload_bytes,
                      "wait_timeout_seconds": self.wait_timeout}
            if owner_user_id is not None:
                result.update(owner_active=self._active_by_owner.get(owner_user_id, 0),
                              owner_queued=self._pending_by_owner.get(owner_user_id, 0))
            return result


chat_capacity = ChatCapacity()

"""Single-worker account admission and real in-flight work ownership.

This is a deletion barrier, not another cancellation mechanism. Existing cancel
checks still decide whether a tool should stop. A thread retains its lease until
its concurrent future finishes, even if its awaiting coroutine has been cancelled.
Multi-worker deployment needs a shared admission/lease store instead.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


class AccountUnavailable(RuntimeError):
    """The account no longer accepts new work or publications."""


class AccountDeletionPending(RuntimeError):
    """Deletion is waiting for admitted work to finish."""


@dataclass
class _State:
    closed: bool = False
    active: int = 0
    deletion_lock: Any = field(default_factory=threading.RLock)


_lock = threading.RLock()
_states: dict[int, _State] = {}
_current: contextvars.ContextVar[AccountLease | None] = contextvars.ContextVar(
    "citrus_account_lease", default=None)


def current_account_lease():
    return _current.get()


def set_current_account_lease(lease):
    return _current.set(lease)


def reset_current_account_lease(token):
    _current.reset(token)


@contextmanager
def bound_account_lease(lease):
    token = set_current_account_lease(lease)
    try:
        yield lease
    finally:
        reset_current_account_lease(token)


def _state(owner: int) -> _State:
    return _states.setdefault(int(owner), _State())


@dataclass
class _LeaseGroup:
    references: int = 1
    finalizers: list = field(default_factory=list)


class AccountLease:
    def __init__(self, owner: int, *, _group: _LeaseGroup | None = None):
        self.owner = int(owner)
        self._released = False
        self._group = _group or _LeaseGroup()

    def retain(self) -> AccountLease:
        """Transfer ownership to child work already admitted by this request."""
        with _lock:
            if self._released:
                raise AccountUnavailable("account lease already released")
            _state(self.owner).active += 1
            self._group.references += 1
            return AccountLease(self.owner, _group=self._group)

    def add_finalizer(self, callback) -> None:
        """Release request resources only after every retained child finishes."""
        if not callable(callback):
            raise TypeError("lease finalizer must be callable")
        with _lock:
            if self._released:
                raise AccountUnavailable("account lease already released")
            self._group.finalizers.append(callback)

    def release(self) -> None:
        with _lock:
            if self._released:
                return
            self._released = True
            self._group.references -= 1
            callbacks = list(self._group.finalizers) if self._group.references == 0 else []
            if self._group.references == 0:
                self._group.finalizers.clear()
        # Keep the final active count until cleanup finishes. Never hold the
        # admission lock across file-store I/O (the publisher uses both locks).
        try:
            for callback in callbacks:
                try:
                    callback()
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("Account resource cleanup failed; references retained")
        finally:
            with _lock:
                _state(self.owner).active -= 1


def acquire_account_lease(owner: int) -> AccountLease:
    """Check current DB identity, not a possibly stale authenticated user dict."""
    from src.auth import user_by_id

    owner = int(owner)
    with _lock:
        state = _state(owner)
        if owner <= 0 or state.closed:
            raise AccountUnavailable("account is closing")
        user = user_by_id(owner)
        if not user or not user.get("active"):
            raise AccountUnavailable("account is disabled or deleted")
        state.active += 1
        return AccountLease(owner)


@contextmanager
def account_lease(owner: int) -> Iterator[AccountLease]:
    lease = acquire_account_lease(owner)
    try:
        with bound_account_lease(lease):
            yield lease
    finally:
        lease.release()


@contextmanager
def deletion_lock(owner: int) -> Iterator[None]:
    with _lock:
        lock = _state(owner).deletion_lock
    with lock:
        yield


def close_admission(owner: int) -> None:
    with _lock:
        _state(owner).closed = True


def active_count(owner: int) -> int:
    with _lock:
        return _state(owner).active


def assert_idle(owner: int) -> None:
    if active_count(owner):
        raise AccountDeletionPending("account has admitted work still running")


def create_account_task(lease: AccountLease, coroutine):
    """Retain at scheduling time so detached persistence cannot miss the barrier."""
    try:
        child = lease.retain()
    except BaseException:
        coroutine.close()
        raise

    async def run():
        with bound_account_lease(child):
            return await coroutine

    wrapper = run()
    try:
        task = asyncio.create_task(wrapper)
    except BaseException:
        child.release()
        wrapper.close()
        coroutine.close()
        raise

    def finished(done):
        child.release()
        if not done.cancelled():
            done.exception()  # retrieve errors even if the observer went away
        else:
            coroutine.close()

    task.add_done_callback(finished)
    return task


async def run_account_async(lease: AccountLease, coroutine):
    """Retain nested async work across cancellation of its request observer."""
    return await asyncio.shield(create_account_task(lease, coroutine))


async def run_account_thread(lease: AccountLease, func, /, *args,
                             executor=None, _context=None, **kwargs):
    """Use existing bounded pools and count actual thread completion, not await."""
    if executor is None:
        from src.core.executors import TOOL_EXECUTOR
        executor = TOOL_EXECUTOR
    child = lease.retain()
    context = _context if _context is not None else contextvars.copy_context()

    def invoke():
        with bound_account_lease(child):
            return func(*args, **kwargs)

    try:
        future = executor.submit(context.run, invoke)
    except BaseException:
        child.release()
        raise
    future.add_done_callback(lambda _done: child.release())
    return await asyncio.wrap_future(future)

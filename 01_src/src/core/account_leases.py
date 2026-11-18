"""Shared account admission and real in-flight work ownership.

Persistent references fence deletion across processes. Cancellation observers do
not release work: threads, async children and finalizers retain their actual
ownership until completion. Crashed writers require explicit reconciliation.
"""
from __future__ import annotations

import asyncio
import contextvars
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

from src.core.account_admission import (
    AccountDeletionPending, AccountUnavailable, AdmissionStore, LeaseTicket, current_store,
)


_lock = threading.RLock()
# Compatibility for older test fixtures only; never consulted for authorization.
_states: dict = {}
_deletion_context = threading.local()
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


@dataclass
class _LeaseGroup:
    store: AdmissionStore
    ticket: LeaseTicket
    references: int = 1
    finalizers: list = field(default_factory=list)


class AccountLease:
    def __init__(self, owner: int, *, _group: _LeaseGroup | None = None):
        self.owner = int(owner)
        self._released = False
        if _group is None:
            store = current_store()
            _group = _LeaseGroup(store, store.acquire(self.owner))
        self._group = _group

    @property
    def epoch(self) -> int:
        return self._group.ticket.epoch

    @property
    def lease_id(self) -> str:
        return self._group.ticket.lease_id

    @property
    def process_token(self) -> str:
        """Private diagnostic identity for explicit stopped-writer recovery."""
        return self._group.ticket.process_token

    @property
    def group_released(self) -> bool:
        """Observation for an abandoned admission, never global account idleness."""
        with _lock:
            return self._group.references == 0

    def retain(self) -> AccountLease:
        """Transfer ownership to child work already admitted by this request."""
        with _lock:
            if self._released:
                raise AccountUnavailable("account lease already released")
            self._group.store.retain(self._group.ticket)
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
            if self._group.references > 1:
                self._group.store.release(self._group.ticket)
                self._group.references -= 1
                self._released = True
                return
            self._released = True
            callbacks = list(self._group.finalizers)
            self._group.finalizers.clear()
        # Keep the final persistent reference across all resource cleanup. No
        # SQLite transaction or admission mutex spans callbacks/file-store I/O.
        try:
            for callback in callbacks:
                try:
                    callback()
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("Account resource cleanup failed")
        finally:
            with _lock:
                try:
                    self._group.store.release(self._group.ticket)
                except BaseException:
                    self._released = False  # a failed DB release remains retryable
                    raise
                self._group.references = 0


def acquire_account_lease(owner: int, *, expected_epoch: int | None = None) -> AccountLease:
    """Atomically validate active identity/epoch and persist its initial holder."""
    store = current_store()
    ticket = store.acquire(int(owner), expected_epoch=expected_epoch)
    return AccountLease(owner, _group=_LeaseGroup(store, ticket))


@contextmanager
def account_lease(owner: int) -> Iterator[AccountLease]:
    lease = acquire_account_lease(owner)
    try:
        with bound_account_lease(lease):
            yield lease
    finally:
        lease.release()


@contextmanager
def deletion_lock(owner: int):
    """Single persistent cleanup owner, reentrant only in its originating thread.

    Normal exit relinquishes the claim but never reopens admission. Process
    death leaves the claim for explicit reconciliation, with no TTL takeover.
    """
    import os
    store = current_store()
    key = (str(store.path), int(owner), os.getpid())
    claims = getattr(_deletion_context, "claims", None)
    if claims is None:
        claims = _deletion_context.claims = {}
    if key in claims:
        yield claims[key]
        return
    claim = store.claim_deletion(owner)
    claims[key] = claim
    try:
        yield claim
    finally:
        del claims[key]
        if claim is not None:
            store.release_claim(claim)


def close_admission(owner: int) -> None:
    current_store().close_admission(owner)


def active_count(owner: int) -> int:
    return current_store().active_count(owner)


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

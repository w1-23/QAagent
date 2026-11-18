"""Bounded discovery for fully cleaned execution segments, never a second Agent.

Queue records authorize completion; notifications merely prompt a fresh read.
No TTL takeover or resubmission lives here. The API dispatch callback retains its
own account handle before returning, and owns activation and outer cleanup.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import time

from src.core import jobs, task_events
from src.core.account_leases import acquire_account_lease, run_account_async, run_account_thread
from src.code_execution.protocol import TERMINAL
from .execution_segments import ExecutionSegments
from .execution_waits import ExecutionWaitBoundary
from .identity import ExecutionIdentity
from .recovery import load_recovery_state, restore_deadline

logger = logging.getLogger(__name__)


class ExecutionCompletionCoordinator:
    def __init__(self, root: Path, provider_scope, *, dispatch, busy, service_factory=None):
        self.root = Path(root)
        self.provider_scope = provider_scope
        self.dispatch = dispatch
        self.busy = busy
        if service_factory is None:
            from src.code_execution.service import ExecutionService
            service_factory = ExecutionService
        self.service_factory = service_factory
        self.cursor = ''
        self.retry_after = {}
        self.cancelled_admissions = {}

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('Code discovery sweep deferred: %s', type(exc).__name__)
            await asyncio.sleep(2)

    async def tick(self):
        for run_id, (segments, claim, abandoned_lease) in list(self.cancelled_admissions.items()):
            if abandoned_lease.group_released and not self.busy(run_id):
                try:
                    segments.release_cancel(claim)
                except task_events.TaskConflict:
                    pass
                del self.cancelled_admissions[run_id]
        candidates = await asyncio.to_thread(ExecutionSegments.candidates, after=self.cursor, limit=16)
        # The cursor always advances past unready and blocked jobs. End-of-page
        # restarts the next sweep, so an old unknown cannot starve later runs.
        self.cursor = candidates[-1]['job_id'] if len(candidates) == 16 else ''
        now = time.monotonic()
        self.retry_after = {key: deadline for key, deadline in self.retry_after.items() if deadline > now}
        for candidate in candidates:
            run_id = candidate['job_id']
            if self.busy(run_id) or self.retry_after.get(run_id, 0) > now:
                continue
            identity = ExecutionIdentity(candidate['owner_user_id'], candidate['session_id'],
                                         candidate['request_id'], run_id)
            try:
                await self._consider(identity)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # No exception message, token, saved query or account data in logs.
                logger.warning('Code segment discovery deferred: %s', type(exc).__name__)
                self.retry_after[run_id] = time.monotonic() + 30

    async def _consider(self, identity):
        segments = ExecutionSegments(identity)
        snapshot = await asyncio.to_thread(segments.snapshot)
        lease = acquire_account_lease(identity.owner_user_id, expected_epoch=snapshot['account_epoch'])
        try:
            job = jobs.get_job(identity.run_id) or {}
            if self.busy(identity.run_id):
                return
            service = self.service_factory(identity)
            if job.get('status') == 'cancel_requested':
                statuses = await run_account_thread(lease, service.cancel_run)
                if (snapshot['phase'] in {'parked', 'cancel_parked'}
                        and all(item['status'] in TERMINAL for item in statuses)):
                    from .harness import execution_lock
                    with execution_lock(self.root / 'agent_locks', identity):
                        await run_account_thread(lease, segments.finish_cancelled)
                return
            if snapshot['phase'] != 'parked' or job.get('status') not in {'running', 'detached'}:
                return
            wait_ids = tuple(json.loads(snapshot['wait_ids']))
            boundary = ExecutionWaitBoundary(service)
            observations = await run_account_async(lease, boundary.resolve(identity, wait_ids))
            if observations is None:
                return
            try:
                state = await run_account_async(lease, load_recovery_state(
                    self.root, identity, self.provider_scope,
                    execution_wait={'checkpoint_ref': snapshot['checkpoint_ref'], 'wait_ids': list(wait_ids)}))
            except (ValueError, LookupError) as exc:
                # A missing/incompatible commit or process-local candidate pool
                # is diagnostic, never permission to synthesize/replay a turn.
                self._blocked(identity, 'code_checkpoint_reconciliation_required')
                raise exc
            admission = segments.claim_resume()
            try:
                state = restore_deadline(state, admission['budget'])
                # A user stop may cancel the admission task registered by API.
                # Keep it separate from the application-wide discovery task.
                dispatch_task = asyncio.create_task(self.dispatch(identity, state, admission, lease))
                try:
                    await dispatch_task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise  # Application shutdown still stops this coordinator.
                    if (jobs.get_job(identity.run_id) or {}).get('status') != 'cancel_requested':
                        raise RuntimeError('code_resume_admission_cancelled') from None
                    # API setup has returned; its map fence has been removed.
                    # Retained read/preparation threads may still hold this group.
                    self.cancelled_admissions[identity.run_id] = (segments, admission['claim'], lease)
            except BaseException:
                # The API takes ownership by activation or task registration.
                # Return only an admission that provably never activated.
                if not self.busy(identity.run_id):
                    current = segments.snapshot()
                    if current['phase'] == 'resuming':
                        try:
                            segments.release_unstarted_resume(admission['claim'])
                        except task_events.TaskConflict:
                            if (jobs.get_job(identity.run_id) or {}).get('status') == 'cancel_requested':
                                self.cancelled_admissions[identity.run_id] = (segments, admission['claim'], lease)
                raise
        finally:
            lease.release()

    @staticmethod
    def _blocked(identity, code):
        with task_events.transaction() as conn:
            job = task_events._job(conn, identity.run_id)
            if job['status'] not in {'running', 'detached'} or job['error'] == code:
                return
            conn.execute('UPDATE task_jobs SET current_step=?,error=? WHERE job_id=?',
                         ('code_reconciliation_required', code, identity.run_id))
            task_events._append(conn, identity.run_id, 'execution_segment', {
                'job_id': identity.run_id, 'phase': 'reconciliation_required', 'reason_code': code})

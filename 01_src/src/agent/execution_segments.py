"""Durable admission for Host/API execution-wait segments.

Same job, turn, original budget and account epoch. A wake cannot claim a run
until the previous segment explicitly releases its OUTER cleanup barrier. These
primitives do not dispatch, poll, resume a model, or replace the OS run lock.
The API owns cleanup; the opt-in offline coordinator owns completion discovery.
Crashed in-flight phases require explicit reconciliation, never a TTL takeover.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import uuid

from src.core import task_events
from src.core.account_leases import acquire_account_lease
from .identity import ExecutionIdentity
from .runtime import validate_execution_waits

_SCHEMA = """CREATE TABLE IF NOT EXISTS agent_execution_segments (
 job_id TEXT PRIMARY KEY REFERENCES task_jobs(job_id),
 account_epoch INTEGER NOT NULL, generation INTEGER NOT NULL,
 token TEXT NOT NULL, phase TEXT NOT NULL,
 checkpoint_ref TEXT NOT NULL DEFAULT '', wait_ids TEXT NOT NULL DEFAULT '[]')"""


@dataclass(frozen=True)
class ExecutionSegmentClaim:
    identity: ExecutionIdentity
    generation: int
    token: str


class ExecutionSegments:
    def __init__(self, identity):
        if type(identity) is not ExecutionIdentity:
            raise TypeError('execution_segment_identity_required')
        self.identity = identity

    def _job(self, conn, *, active=True):
        job = task_events._job(conn,self.identity.run_id)
        if (job['owner_user_id'] != self.identity.owner_user_id
                or job['session_id'] != self.identity.session_id
                or job['request_id'] != self.identity.user_turn_id):
            raise task_events.TaskConflict('execution_segment_identity_conflict')
        if active and (job['status'] not in {'running','detached'} or job['cancel_requested_at']
                       or task_events.current_terminal_event(conn,self.identity.run_id) is not None):
            raise task_events.TaskConflict('execution_segment_not_active')
        return job

    @staticmethod
    def _settled(conn, run_id):
        if conn.execute("SELECT 1 FROM task_operations WHERE job_id=? AND (status IN ('running','interrupted','unknown') OR result='')",
                        (run_id,)).fetchone():
            raise task_events.TaskConflict('execution_segment_requires_reconciliation')
        journal = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_batch_journal'").fetchone()
        if journal:
            for row in conn.execute('SELECT status,observation FROM agent_batch_journal WHERE run_id=?',(run_id,)):
                try:
                    batch = json.loads(row['observation'])
                except (TypeError,ValueError):
                    raise task_events.TaskConflict('execution_segment_requires_reconciliation') from None
                if row['status'] != 'complete' or not isinstance(batch,dict) or batch.get('unknown_operations'):
                    raise task_events.TaskConflict('execution_segment_requires_reconciliation')

    def _row(self, conn):
        conn.execute(_SCHEMA)
        self._job(conn)
        row = conn.execute('SELECT * FROM agent_execution_segments WHERE job_id=?',(self.identity.run_id,)).fetchone()
        if row is None:
            raise task_events.TaskConflict('execution_segment_missing')
        return row

    @staticmethod
    def _reference(checkpoint_ref):
        from src.code_execution.protocol import token
        token(checkpoint_ref)

    def _claim(self, row):
        return ExecutionSegmentClaim(self.identity,row['generation'],row['token'])

    def _owned(self, row, claim):
        if (type(claim) is not ExecutionSegmentClaim or claim.identity != self.identity
                or claim.generation != row['generation'] or claim.token != row['token']):
            raise task_events.TaskConflict('execution_segment_claim_lost')

    @contextmanager
    def _admit(self):
        with task_events.transaction() as conn:
            epoch = self._row(conn)['account_epoch']
        lease = acquire_account_lease(self.identity.owner_user_id,expected_epoch=epoch)
        try:
            yield
        finally:
            lease.release()

    def _step(self, conn, step, phase):
        conn.execute('UPDATE task_jobs SET current_step=?,updated_at=? WHERE job_id=?',
                     (step,datetime.now().isoformat(),self.identity.run_id))
        task_events._append(conn,self.identity.run_id,'execution_segment',{
            'job_id':self.identity.run_id,'phase':phase})

    @staticmethod
    def candidates(*, after="", limit=32):
        """Bounded host-only discovery; never expose claim tokens to clients."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('execution_segment_page_invalid')
        with task_events.transaction() as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_execution_segments'").fetchone() is None:
                return []
            return [dict(row) for row in conn.execute(
                "SELECT j.job_id,j.owner_user_id,j.session_id,j.request_id,j.status,s.phase "
                "FROM agent_execution_segments s JOIN task_jobs j ON j.job_id=s.job_id "
                "WHERE j.status IN ('running','detached','cancel_requested') AND j.job_id>? "
                "ORDER BY j.job_id LIMIT ?", (after, limit))]

    def snapshot(self):
        """Trusted lifecycle observation, including a pending cancellation."""
        with task_events.transaction() as conn:
            self._job(conn, active=False)
            row = conn.execute('SELECT * FROM agent_execution_segments WHERE job_id=?',
                               (self.identity.run_id,)).fetchone()
            if row is None:
                raise task_events.TaskConflict('execution_segment_missing')
            return dict(row)

    def release_unstarted_resume(self, claim):
        """Return an unactivated admission; invalidate its token before reuse."""
        with self._admit(), task_events.transaction() as conn:
            row = self._row(conn)
            self._owned(row, claim)
            if row['phase'] != 'resuming':
                raise task_events.TaskConflict('execution_segment_phase_conflict')
            self._settled(conn, self.identity.run_id)
            conn.execute("UPDATE agent_execution_segments SET phase='parked',generation=generation+1,token=? WHERE job_id=?",
                         (uuid.uuid4().hex, self.identity.run_id))
            self._step(conn, 'waiting_code', 'parked')

    def start(self):
        """Only a new init job may gain its first segment; retries cannot dispatch."""
        lease = acquire_account_lease(self.identity.owner_user_id)
        try:
            with task_events.transaction() as conn:
                conn.execute(_SCHEMA)
                job = self._job(conn)
                if job['current_step'] != 'init':
                    raise task_events.TaskConflict('execution_segment_initial_admission_lost')
                if conn.execute('SELECT 1 FROM task_budgets WHERE job_id=?',(self.identity.run_id,)).fetchone() is None:
                    raise task_events.TaskConflict('execution_segment_budget_missing')
                self._settled(conn,self.identity.run_id)
                changed = conn.execute("INSERT INTO agent_execution_segments(job_id,account_epoch,generation,token,phase) "
                    "VALUES(?,?,1,?,'active') ON CONFLICT(job_id) DO NOTHING",
                    (self.identity.run_id,lease.epoch,uuid.uuid4().hex)).rowcount
                if changed != 1:
                    raise task_events.TaskConflict('execution_segment_initial_admission_lost')
                self._step(conn,'segment_active','active')
                return self._claim(self._row(conn))
        finally:
            lease.release()

    def prepare_park(self, claim, *, checkpoint_ref, wait_ids):
        """After a real paired Harness checkpoint; old outer cleanup still owns run."""
        self._reference(checkpoint_ref)
        validate_execution_waits(wait_ids)
        if not wait_ids:
            raise ValueError('execution_wait_references_required')
        with self._admit(), task_events.transaction() as conn:
            row = self._row(conn)
            self._owned(row,claim)
            encoded = json.dumps(wait_ids,separators=(',',':'))
            if row['phase'] == 'parking' and row['checkpoint_ref'] == checkpoint_ref and row['wait_ids'] == encoded:
                return
            if row['phase'] != 'active':
                raise task_events.TaskConflict('execution_segment_phase_conflict')
            self._settled(conn,self.identity.run_id)
            conn.execute("UPDATE agent_execution_segments SET phase='parking',checkpoint_ref=?,wait_ids=? WHERE job_id=?",
                         (checkpoint_ref,encoded,self.identity.run_id))
            self._step(conn,'parking_code','parking')

    def release_park(self, claim):
        """Only AFTER outer API capacity/progress/account cleanup is settled."""
        with self._admit(), task_events.transaction() as conn:
            row = self._row(conn)
            self._owned(row,claim)
            if row['phase'] == 'parked':
                return
            if row['phase'] != 'parking':
                raise task_events.TaskConflict('execution_segment_phase_conflict')
            conn.execute("UPDATE agent_execution_segments SET phase='parked' WHERE job_id=?",(self.identity.run_id,))
            self._step(conn,'waiting_code','parked')

    def release_cancel(self, claim):
        """AFTER the same outer cleanup, retain cancellation for Worker settlement."""
        with task_events.transaction() as conn:
            self._job(conn, active=False)
            row = conn.execute('SELECT * FROM agent_execution_segments WHERE job_id=?',
                               (self.identity.run_id,)).fetchone()
            if row is None:
                raise task_events.TaskConflict('execution_segment_missing')
            self._owned(row, claim)
            job = self._job(conn, active=False)
            if job['status'] != 'cancel_requested':
                raise task_events.TaskConflict('execution_segment_not_cancelling')
            if row['phase'] not in {'active', 'parking', 'resuming', 'finalizing', 'cancel_parked'}:
                raise task_events.TaskConflict('execution_segment_phase_conflict')
            conn.execute("UPDATE agent_execution_segments SET phase='cancel_parked' WHERE job_id=?",
                         (self.identity.run_id,))
            self._step(conn, 'code_cancel_pending', 'cancel_parked')

    def finish_cancelled(self):
        """Trusted caller already proved all run executions terminal under the OS lock."""
        from src.core.turn_envelope import build_turn_envelope
        with task_events.transaction() as conn:
            job = self._job(conn, active=False)
            row = conn.execute('SELECT * FROM agent_execution_segments WHERE job_id=?',
                               (self.identity.run_id,)).fetchone()
            if (job['status'] != 'cancel_requested' or row is None
                    or row['phase'] not in {'parked', 'cancel_parked'}):
                raise task_events.TaskConflict('execution_segment_not_cancelling')
            self._settled(conn, self.identity.run_id)
            if task_events.current_terminal_event(conn, self.identity.run_id) is not None:
                return
            envelope = build_turn_envelope(display_status='cancelled', job_status='cancelled',
                requested_status='cancelled', stop_reason=job['stop_reason'] or 'user',
                history_saved=False, notices=['执行已停止；本轮未生成最终回答。']).as_dict()
            task_events._append(conn, self.identity.run_id, 'done', {
                'job_id': self.identity.run_id, 'session_id': self.identity.session_id,
                'answer': '', 'turn_status': 'cancelled', 'history_saved': False,
                'turn_envelope': envelope})
            now = datetime.now().isoformat()
            conn.execute("UPDATE task_jobs SET status='cancelled',current_step='cancelled',"
                "finished_at=?,updated_at=?,terminal_snapshot=? WHERE job_id=?",
                (now, now, json.dumps(envelope, ensure_ascii=False), self.identity.run_id))
            conn.execute("UPDATE agent_execution_segments SET phase='terminal',token=? WHERE job_id=?",
                         (uuid.uuid4().hex, self.identity.run_id))

    def claim_resume(self):
        """One CAS from fully parked to resuming. No new job/quota/model attempt."""
        with self._admit(), task_events.transaction() as conn:
            row = self._row(conn)
            if row['phase'] != 'parked':
                raise task_events.TaskConflict('execution_segment_not_parked')
            self._settled(conn,self.identity.run_id)
            budget = conn.execute('SELECT * FROM task_budgets WHERE job_id=?',(self.identity.run_id,)).fetchone()
            if budget is None:
                raise task_events.TaskConflict('execution_segment_budget_missing')
            # Exhaustion still permits receipt-only reconciliation. Physical
            # model admission must enforce THIS original budget after resume.
            conn.execute("UPDATE agent_execution_segments SET phase='resuming',generation=generation+1,token=? WHERE job_id=?",
                         (uuid.uuid4().hex,self.identity.run_id))
            self._step(conn,'resuming_code','resuming')
            row = self._row(conn)
            return {'claim':self._claim(row),'checkpoint_ref':row['checkpoint_ref'],
                    'wait_ids':tuple(json.loads(row['wait_ids'])),'budget':dict(budget)}

    def activate(self, claim):
        """After protected checkpoint/identity validation, before model admission."""
        with self._admit(), task_events.transaction() as conn:
            row = self._row(conn)
            self._owned(row,claim)
            if row['phase'] != 'resuming':
                raise task_events.TaskConflict('execution_segment_phase_conflict')
            conn.execute("UPDATE agent_execution_segments SET phase='active' WHERE job_id=?",(self.identity.run_id,))
            self._step(conn,'segment_active','active')

    def begin_finalize(self, claim):
        """Fence outer finalization so a wake cannot overlap history/terminal save."""
        with self._admit(), task_events.transaction() as conn:
            row = self._row(conn)
            self._owned(row,claim)
            if row['phase'] != 'active':
                raise task_events.TaskConflict('execution_segment_phase_conflict')
            conn.execute("UPDATE agent_execution_segments SET phase='finalizing' WHERE job_id=?",(self.identity.run_id,))
            self._step(conn,'finalizing','finalizing')

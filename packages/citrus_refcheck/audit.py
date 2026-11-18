"""Explicit SQLite repository for standalone audits; no platform state coupling."""
from __future__ import annotations
from contextlib import contextmanager
import io
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid
from .compare import hint_differences
from .lookup import AuditStopped, LookupLimits, evidence_cache_key, lookup_evidence


class BudgetExceeded(RuntimeError):
    pass


def _successful(result):
    """Processing a row is distinct from collecting all requested evidence."""
    return (not result.get('errors') and bool(result.get('providers'))
            and all(state == 'ok' for state in result['providers'].values()))


class AuditRepository:
    def __init__(self, path: Path, *, owner: str, session_id: str, lease_seconds=30):
        if not owner or not session_id:
            raise ValueError('owner and session_id are required')
        if (isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float))
                or not math.isfinite(lease_seconds) or lease_seconds <= 0):
            raise ValueError('invalid_lease_seconds')
        self.path = Path(path)
        self.owner, self.session_id = str(owner), str(session_id)
        self.lease_seconds = lease_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            for statement in (
                'CREATE TABLE IF NOT EXISTS audits(id TEXT PRIMARY KEY,owner TEXT,session TEXT,budget INTEGER,spent INTEGER DEFAULT 0,inventory TEXT)',
                'CREATE TABLE IF NOT EXISTS results(audit TEXT,record TEXT,payload TEXT,PRIMARY KEY(audit,record))',
                'CREATE TABLE IF NOT EXISTS attempts(seq INTEGER PRIMARY KEY AUTOINCREMENT,audit TEXT,record TEXT,payload TEXT)',
                'CREATE TABLE IF NOT EXISTS cache(audit TEXT,key TEXT,payload TEXT,PRIMARY KEY(audit,key))',
                '''CREATE TABLE IF NOT EXISTS request_reservations(
                    audit TEXT NOT NULL,ordinal INTEGER NOT NULL,record TEXT,run_token TEXT NOT NULL,
                    reserved_at REAL NOT NULL,state TEXT NOT NULL,outcome TEXT,
                    PRIMARY KEY(audit,ordinal))''',
            ):
                conn.execute(statement)
            # Upgrade repositories made by the original standalone package in place.
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(audits)')}
            for name, definition in (('cancelled', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('lease_token', "TEXT NOT NULL DEFAULT ''"),
                                     ('lease_until', 'REAL NOT NULL DEFAULT 0')):
                if name not in columns:
                    conn.execute(f'ALTER TABLE audits ADD COLUMN {name} {definition}')

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            # All read/modify/write decisions, including fencing, see one snapshot.
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _owned(self, conn, audit_id):
        row = conn.execute('SELECT * FROM audits WHERE id=? AND owner=? AND session=?',
                           (audit_id, self.owner, self.session_id)).fetchone()
        if row is None:
            raise ValueError('audit_not_found')
        return row

    def _fenced(self, conn, audit_id, token):
        row = self._owned(conn, audit_id)
        if row['cancelled']:
            raise AuditStopped('cancelled')
        if row['lease_token'] != token or row['lease_until'] <= time.time():
            raise AuditStopped('lease_lost')
        return row

    def create(self, inventory, *, request_budget=1200):
        if type(request_budget) is not int or not 1 <= request_budget <= 50000:
            raise ValueError('invalid_request_budget')
        entries = inventory['entries']
        if len({entry['record_id'] for entry in entries}) != len(entries):
            raise ValueError('duplicate_record_id')
        audit_id = uuid.uuid4().hex
        with self._db() as conn:
            conn.execute('INSERT INTO audits(id,owner,session,budget,inventory) VALUES(?,?,?,?,?)',
                         (audit_id, self.owner, self.session_id, request_budget,
                          json.dumps(inventory, ensure_ascii=False, allow_nan=False)))
        return audit_id

    def debit(self, audit_id):
        """Public atomic reservation, retained for existing budget-hook callers."""
        with self._db() as conn:
            row = self._owned(conn, audit_id)
            if row['cancelled']:
                raise AuditStopped('cancelled')
            self._reserve(conn, audit_id)

    def _reserve(self, conn, audit_id, *, record=None, run_token=''):
        """Reserve budget and its durable trace in the caller's transaction.

        reserved never proves HTTP dispatch: an inherited debit or the final
        cancellation check may prevent dispatch after this transaction commits.
        Public debit callers have no record/run context, so those stay null/empty.
        """
        changed = conn.execute('UPDATE audits SET spent=spent+1 WHERE id=? AND spent<budget',
                               (audit_id,)).rowcount
        if not changed:
            raise BudgetExceeded('request_budget_exhausted')
        ordinal = conn.execute('SELECT spent FROM audits WHERE id=?', (audit_id,)).fetchone()['spent']
        conn.execute('''INSERT INTO request_reservations
            (audit,ordinal,record,run_token,reserved_at,state) VALUES(?,?,?,?,?,'reserved')''',
            (audit_id, ordinal, record, run_token, time.time()))
        return ordinal

    def cancel(self, audit_id):
        """Immediately fence any runner, including a transport still in flight."""
        with self._db() as conn:
            self._owned(conn, audit_id)
            conn.execute("UPDATE audits SET cancelled=1,lease_token='',lease_until=0 WHERE id=?", (audit_id,))
        return self.snapshot(audit_id)

    def snapshot(self, audit_id):
        with self._db() as conn:
            row = self._owned(conn, audit_id)
            results = {r['record']: json.loads(r['payload']) for r in
                       conn.execute('SELECT * FROM results WHERE audit=?', (audit_id,))}
            inventory = json.loads(row['inventory'])
            entries = inventory['entries']
            actual = sum(entry['record_id'] in results for entry in entries)
            failed = sum(not _successful(results[entry['record_id']]) for entry in entries
                         if entry['record_id'] in results)
            extraction_complete = inventory.get('extraction', {}).get('status') == 'complete'
            complete = bool(entries) and actual == len(entries) and not failed and extraction_complete
            status = 'cancelled' if row['cancelled'] else 'complete' if complete else 'partial'
            reservations = [{'audit_id': r['audit'], 'record_id': r['record'],
                             'run_token': r['run_token'], 'ordinal': r['ordinal'],
                             'reserved_at': r['reserved_at'], 'state': r['state'],
                             'outcome': json.loads(r['outcome']) if r['outcome'] else None}
                            for r in conn.execute('''SELECT * FROM request_reservations
                                WHERE audit=? ORDER BY ordinal''', (audit_id,))]
            return {'audit_id': audit_id, 'inventory': inventory,
                    'entries': [{**entry, 'evidence': results.get(entry['record_id'])} for entry in entries],
                    'budget': {'limit': row['budget'], 'spent': row['spent']},
                    'coverage': {'requested': len(entries), 'actual': actual, 'gap': len(entries)-actual},
                    'evidence_coverage': {'successful': actual-failed, 'failed': failed,
                                          'extraction_complete': extraction_complete},
                    'status': status,
                    'request_reservations': reservations,
                    'reservation_semantics': 'Budget reserved; HTTP dispatch is not asserted. '
                                             'outcome_accepted links a durable attempt only.',
                    'reservation_coverage': {'tracked': len(reservations),
                                             'legacy_untracked_spent': max(0, row['spent']-len(reservations))},
                    'attempts': [json.loads(r['payload']) for r in conn.execute(
                        'SELECT * FROM attempts WHERE audit=? ORDER BY seq', (audit_id,))]}

    def run(self, audit_id, limits: LookupLimits, *, max_rows=None, resume=False):
        """Process untouched entries under a finite, renewable exclusive lease.

        resume=True additionally clears persistent cancellation and retries failed
        evidence. Neither action resets spent requests. A caller stop is raised;
        a concurrent runner returns run_reason=already_running without dispatch.
        """
        if not isinstance(limits, LookupLimits):
            raise TypeError('Explicit LookupLimits required')
        if max_rows is not None and (type(max_rows) is not int or max_rows < 1):
            raise ValueError('invalid_max_rows')
        token = uuid.uuid4().hex
        with self._db() as conn:
            row = self._owned(conn, audit_id)
            if row['lease_token'] and row['lease_until'] > time.time():
                reason = 'already_running'
            elif row['cancelled'] and not resume:
                reason = 'cancelled_use_resume'
            else:
                reason = ''
                conn.execute('UPDATE audits SET cancelled=0,lease_token=?,lease_until=? WHERE id=?',
                             (token, time.time()+self.lease_seconds, audit_id))
        if reason:
            result = self.snapshot(audit_id)
            result['run_reason'] = reason
            return result

        def check():
            if callable(limits.check):
                limits.check()
            if time.monotonic() >= limits.deadline:
                raise AuditStopped('deadline')
            with self._db() as conn:
                self._fenced(conn, audit_id, token)
                conn.execute('UPDATE audits SET lease_until=? WHERE id=?',
                             (time.time()+self.lease_seconds, audit_id))

        def debit():
            check()
            with self._db() as conn:
                self._fenced(conn, audit_id, token)
                ordinal = self._reserve(conn, audit_id, record=entry['record_id'], run_token=token)
            reservation_ordinals.append(ordinal)
            # Both persisted and inherited budgets constrain dispatch. A rejected
            # inherited reservation may consume a local reservation, never refund it.
            if callable(limits.debit):
                limits.debit()

        completed = 0
        try:
            snapshot = self.snapshot(audit_id)
            # Untouched rows first; explicit resume may retry failed observations.
            entries = [entry for entry in snapshot['entries'] if entry['evidence'] is None]
            if resume:
                entries += [entry for entry in snapshot['entries']
                            if entry['evidence'] is not None and not _successful(entry['evidence'])]
            for entry in entries:
                if max_rows is not None and completed >= max_rows:
                    break
                reservation_ordinals = []
                check()
                key = evidence_cache_key(entry['claims'], limits)
                with self._db() as conn:
                    self._fenced(conn, audit_id, token)
                    cached = conn.execute('SELECT payload FROM cache WHERE audit=? AND key=?',
                                          (audit_id, key)).fetchone()
                result = json.loads(cached['payload']) if cached else lookup_evidence(entry['claims'], LookupLimits(
                    limits.transport, limits.providers, limits.deadline, check, debit,
                    limits.authorized, limits.cache_namespace))
                attempt = {'record_id': entry['record_id'], 'cached': bool(cached), 'result': result,
                           'accepted': False}
                try:
                    check()  # check the caller after a late transport response too
                except BaseException:
                    # Retain diagnostics only while this token is still current;
                    # a superseded/cancelled runner cannot write anything.
                    with self._db() as conn:
                        row = self._owned(conn, audit_id)
                        if (row['lease_token'] == token and not row['cancelled']
                                and row['lease_until'] > time.time()):
                            conn.execute('INSERT INTO attempts(audit,record,payload) VALUES(?,?,?)',
                                         (audit_id, entry['record_id'], json.dumps(attempt, ensure_ascii=False)))
                    raise
                result['differences'] = [{'record': i, 'fields': hint_differences(
                    entry['claims'], record, uncertain_fields=entry.get('uncertain_fields', ()))
                    } for i, record in enumerate(result['records'])]
                attempt['accepted'] = True
                with self._db() as conn:
                    self._fenced(conn, audit_id, token)
                    attempt_seq = conn.execute('INSERT INTO attempts(audit,record,payload) VALUES(?,?,?)',
                        (audit_id, entry['record_id'], json.dumps(attempt, ensure_ascii=False))).lastrowid
                    for ordinal in reservation_ordinals:
                        conn.execute('''UPDATE request_reservations SET state='outcome_accepted',outcome=?
                            WHERE audit=? AND ordinal=? AND record=? AND run_token=? AND state='reserved' ''',
                            (json.dumps({'attempt_seq': attempt_seq}), audit_id, ordinal, entry['record_id'], token))
                    conn.execute('INSERT OR REPLACE INTO results VALUES(?,?,?)',
                                 (audit_id, entry['record_id'], json.dumps(result, ensure_ascii=False)))
                    if not cached and _successful(result):
                        clean = {k: v for k, v in result.items() if k != 'differences'}
                        conn.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',
                                     (audit_id, key, json.dumps(clean, ensure_ascii=False)))
                completed += 1
                if any(error.get('code') == 'request_budget_exhausted' for error in result['errors'].values()):
                    break
        finally:
            with self._db() as conn:
                self._owned(conn, audit_id)
                conn.execute("UPDATE audits SET lease_token='',lease_until=0 WHERE id=? AND lease_token=?",
                             (audit_id, token))
        return self.snapshot(audit_id)


def _text_chunks(text):
    # Excel limits strings to 32767 UTF-16 units. Leave headroom, including for
    # astral Unicode, rather than relying on openpyxl's silent slicing.
    chunk, size = [], 0
    for char in text:
        units = 2 if ord(char) > 0xffff else 1
        if size+units > 30000:
            yield ''.join(chunk)
            chunk, size = [], 0
        chunk.append(char)
        size += units
    yield ''.join(chunk)


def export_snapshot(snapshot, *, format='json'):
    """Full inventory projection, including entries without evidence; no lookups.

    XLSX retains the first chunk in References. LongText holds subsequent chunks
    keyed by reference_row, field and part (the References cell is part 1).
    Every submitted text cell is a literal string, including continuation chunks.
    """
    if format == 'json':
        return json.dumps(snapshot, ensure_ascii=False, indent=2).encode('utf-8')
    if format == 'xlsx':
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = 'References'
        fields = ['record_id', 'original', 'claims', 'evidence']
        ws.append(fields)
        overflow = None
        for row_number, entry in enumerate(snapshot['entries'], 2):
            values = [entry['record_id'], entry['citation'], json.dumps(entry['claims'], ensure_ascii=False),
                      json.dumps(entry.get('evidence'), ensure_ascii=False)]
            for col, (field, value) in enumerate(zip(fields, values), 1):
                for part, chunk in enumerate(_text_chunks(str(value)), 1):
                    if part == 1:
                        cell = ws.cell(row_number, col, chunk)
                    else:
                        if overflow is None:
                            overflow = wb.create_sheet('LongText')
                            overflow.append(['reference_row', 'field', 'part', 'text'])
                        overflow.append([row_number, field, part, chunk])
                        cell = overflow.cell(overflow.max_row, 4)
                    cell.data_type = 's'
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()
    if format not in {'ris', 'bibtex'}:
        raise ValueError('unsupported_export_format')
    parts = []
    for i, entry in enumerate(snapshot['entries'], 1):
        claims = entry['claims']
        title = str(claims.get('title') or entry['citation']).replace('\n', ' ')
        doi = str(claims.get('doi') or '')
        note = 'Submitted bibliography projection; evidence is in JSON/XLSX.'
        if format == 'ris':
            parts.append(f'TY  - GEN\nTI  - {title}\nDO  - {doi}\nN1  - {note}\nER  -\n')
        else:
            escape = lambda s: str(s).replace('\\', '\\textbackslash{}').replace('{', '\\{').replace('}', '\\}')
            parts.append('@misc{ref'+str(i)+',\n title={'+escape(title)+'},\n doi={'+escape(doi)+'},\n note={'+note+'}\n}')
    return '\n'.join(parts).encode('utf-8')

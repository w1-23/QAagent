"""Private, immutable Python assets and bounded execution provenance.

Refs are identities, never authorization. Owner/session come from the host. No
script is executed here. The host must retain an account lease for the *actual*
execution (including detached threads), then finish_run before releasing it.
Each API admits/retains a short lease; purge_owner is a deletion hook requiring
closed admission and drained leases. Account admission is single-worker only.
Inputs/results/environment must be public JSON provenance (opaque resource refs,
not credentials or filesystem locations). Traceback locations are redacted from
error text. This store never materializes source into the public file library.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid

MAX_SOURCE = 200000
MAX_JSON = 64000
MAX_LIST = 100
TERMINAL_STATUSES = frozenset({'succeeded', 'partial', 'failed', 'cancelled'})


class ScriptVersionConflict(ValueError):
    """A rejected write with an authorized, integrity-checked recovery ref."""

    def __init__(self, latest_ref):
        super().__init__('Script version conflict')
        self.latest_ref = dict(latest_ref)


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _scope(owner, session_id):
    if type(owner) is not int or owner <= 0:
        raise ValueError('Authenticated owner required')
    if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', session_id):
        raise ValueError('Invalid session id')
    return owner, session_id


def _ref(ref):
    if (not isinstance(ref, dict) or set(ref) != {'kind', 'id', 'version', 'sha256'}
            or ref['kind'] != 'research_script'
            or not isinstance(ref['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', ref['id'])
            or type(ref['version']) is not int or ref['version'] < 1
            or not isinstance(ref['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256'])):
        raise ValueError('Invalid immutable script reference')
    return dict(ref)


def _json(value, *, optional=False):
    if optional and value is None:
        return 'null'
    if not isinstance(value, dict):
        raise ValueError('Provenance must be a JSON object')
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        text.encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('Invalid JSON provenance') from None
    if len(text) > MAX_JSON:
        raise ValueError('Provenance exceeds size limit')
    return text


def _limit(limit):
    if type(limit) is not int or limit < 1:
        raise ValueError('Invalid list limit')
    return min(limit, MAX_LIST)


def _token(value):
    if not isinstance(value, str) or len(value) > 256 or (value and not re.fullmatch(r'[A-Za-z0-9_.:-]+', value)):
        raise ValueError('Invalid request or resource identity')
    return value


@contextmanager
def _lease(owner):
    from src.core.account_leases import current_account_lease, acquire_account_lease, bound_account_lease
    current = current_account_lease()
    lease = current.retain() if current is not None and current.owner == owner else acquire_account_lease(owner)
    try:
        with bound_account_lease(lease):
            yield
    finally:
        lease.release()


@contextmanager
def _connect():
    from src import config
    path = config.PROJECT_ROOT / 'state' / 'research_scripts.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    try:
        if os.name != 'nt':
            os.chmod(path, 0o600)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=30000')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''CREATE TABLE IF NOT EXISTS scripts (
            id TEXT PRIMARY KEY, owner INTEGER NOT NULL, session TEXT NOT NULL,
            latest INTEGER NOT NULL, created REAL NOT NULL, accessed REAL NOT NULL)''')
        conn.execute('CREATE INDEX IF NOT EXISTS scripts_scope ON scripts(owner,session,accessed)')
        conn.execute('''CREATE TABLE IF NOT EXISTS versions (
            id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
            version INTEGER NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL,
            sha256 TEXT NOT NULL, origin TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY(id,version))''')
        conn.execute('CREATE INDEX IF NOT EXISTS versions_hash ON versions(sha256)')
        conn.execute('''CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY, script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
            version INTEGER NOT NULL, sha256 TEXT NOT NULL,
            owner INTEGER NOT NULL, session TEXT NOT NULL,
            request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
            payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','succeeded','partial','failed','cancelled')),
            result TEXT NOT NULL DEFAULT 'null', error TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL, finished REAL)''')
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS runs_request ON runs(owner,session,request_key) WHERE request_key != ''")
        conn.execute('CREATE INDEX IF NOT EXISTS runs_scope ON runs(owner,session,created)')
        conn.execute('''CREATE TABLE IF NOT EXISTS pins (
            script_id TEXT NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
            version INTEGER NOT NULL, resource_kind TEXT NOT NULL, resource_id TEXT NOT NULL,
            PRIMARY KEY(script_id,version,resource_kind,resource_id))''')
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


_LIVE_SQL = """(s.accessed >= ?
    OR EXISTS (SELECT 1 FROM pins p WHERE p.script_id=s.id)
    OR EXISTS (SELECT 1 FROM runs r WHERE r.script_id=s.id AND r.status='running'))"""


def _idle_cutoff(now=None):
    from src.config import settings
    days = getattr(settings, 'RESEARCH_SCRIPT_IDLE_DAYS', 7)
    if isinstance(days, bool) or not isinstance(days, (int, float)) or not math.isfinite(days) or days <= 0:
        raise ValueError('Invalid script retention')
    timestamp = time.time() if now is None else float(now)
    if not math.isfinite(timestamp):
        raise ValueError('Invalid retention timestamp')
    return timestamp - days * 86400


def _load(conn, owner, session, ref, *, allow_expired=False):
    ref = _ref(ref)
    row = conn.execute('''SELECT v.*,s.latest,s.accessed FROM versions v JOIN scripts s ON s.id=v.id
        WHERE s.owner=? AND s.session=? AND v.id=? AND v.version=?''',
        (owner, session, ref['id'], ref['version'])).fetchone()
    if row is None:
        raise LookupError('Script not found')
    if row['sha256'] != ref['sha256'] or _hash(row['source']) != row['sha256']:
        raise ValueError('Script integrity check failed')
    if not allow_expired and not conn.execute(
            'SELECT 1 FROM scripts s WHERE s.id=? AND ' + _LIVE_SQL,
            (row['id'], _idle_cutoff())).fetchone():
        raise LookupError('Script expired')
    return row


def _script(row, *, source=True):
    value = {'script_ref': {'kind': 'research_script', 'id': row['id'],
             'version': row['version'], 'sha256': row['sha256']},
             'name': row['name'], 'origin_ref': json.loads(row['origin']),
             'created_at': row['created'], 'last_used_at': row['accessed'],
             'latest_version': row['latest'], 'source_chars': len(row['source'])}
    if source:
        value['source'] = row['source']
    return value


def _touch(conn, identity):
    conn.execute('UPDATE scripts SET accessed=? WHERE id=?', (time.time(), identity))


def save_script(owner: int, session_id: str, *, name: str, source: str,
                script_ref: dict | None = None, expected_version: int | None = None,
                origin_ref: dict | None = None) -> dict:
    _scope(owner, session_id)
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,119}\.py', name)
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))}):
        raise ValueError('A safe single-level Python filename is required')
    if not isinstance(source, str) or not source.strip() or len(source) > MAX_SOURCE or '\x00' in source:
        raise ValueError('Invalid script source or source limit exceeded')
    try:
        digest = _hash(source)
    except UnicodeError:
        raise ValueError('Invalid script source encoding') from None
    origin = _json(origin_ref, optional=True)
    with _lease(owner), _connect() as conn:
        now = time.time()
        if script_ref is None:
            if expected_version is not None:
                raise ValueError('Expected version requires a script reference')
            row = conn.execute('''SELECT v.*,s.latest,s.accessed FROM versions v JOIN scripts s ON s.id=v.id
                WHERE s.owner=? AND s.session=? AND v.sha256=? AND ''' + _LIVE_SQL +
                ' ORDER BY v.created DESC LIMIT 1',
                (owner, session_id, digest, _idle_cutoff())).fetchone()
            if row is not None:
                _load(conn, owner, session_id, _script(row)['script_ref'])
                _touch(conn, row['id'])
                return _script(_load(conn, owner, session_id, _script(row)['script_ref']))
            identity, version = uuid.uuid4().hex, 1
            conn.execute('INSERT INTO scripts VALUES (?,?,?,?,?,?)', (identity, owner, session_id, version, now, now))
        else:
            row = _load(conn, owner, session_id, script_ref)
            if type(expected_version) is not int or expected_version != row['latest'] or row['version'] != row['latest']:
                latest = conn.execute('SELECT sha256 FROM versions WHERE id=? AND version=?',
                                      (row['id'], row['latest'])).fetchone()
                if latest is None:
                    raise ValueError('Script integrity check failed')
                latest_ref = {'kind': 'research_script', 'id': row['id'],
                              'version': row['latest'], 'sha256': latest['sha256']}
                _load(conn, owner, session_id, latest_ref)
                raise ScriptVersionConflict(latest_ref)
            if digest == row['sha256']:
                _touch(conn, row['id'])
                return _script(_load(conn, owner, session_id, script_ref))
            identity, version = row['id'], row['latest'] + 1
            if origin_ref is None:
                origin = row['origin']
            conn.execute('UPDATE scripts SET latest=?,accessed=? WHERE id=?', (version, now, identity))
        conn.execute('INSERT INTO versions VALUES (?,?,?,?,?,?,?)', (identity, version, name, source, digest, origin, now))
        return _script(_load(conn, owner, session_id, {'kind': 'research_script', 'id': identity, 'version': version, 'sha256': digest}))


def get_script(owner, session_id, script_ref, *, touch=False) -> dict:
    _scope(owner, session_id)
    with _lease(owner), _connect() as conn:
        row = _load(conn, owner, session_id, script_ref)
        if touch:
            _touch(conn, row['id'])
            row = _load(conn, owner, session_id, script_ref)
        return _script(row)


def list_scripts(owner, session_id, *, limit=30) -> list[dict]:
    _scope(owner, session_id)
    limit = _limit(limit)
    with _lease(owner), _connect() as conn:
        rows = conn.execute('''SELECT v.*,s.latest,s.accessed FROM scripts s JOIN versions v
            ON s.id=v.id AND s.latest=v.version WHERE s.owner=? AND s.session=? AND ''' + _LIVE_SQL +
            ' ORDER BY s.accessed DESC,s.id LIMIT ?', (owner, session_id, _idle_cutoff(), limit)).fetchall()
        return [_script(_load(conn, owner, session_id, _script(r, source=False)['script_ref']), source=False) for r in rows]


def _run(row):
    payload = json.loads(row['payload'])
    payload['environment'] = public_provenance(payload.get('environment', {}))
    return {'run_id': row['run_id'], 'script_ref': {'kind': 'research_script',
            'id': row['script_id'], 'version': row['version'], 'sha256': row['sha256']},
            **payload, 'status': row['status'],
            'result': public_provenance(json.loads(row['result'])), 'error': _error(row['error']),
            'created_at': row['created'], 'finished_at': row['finished']}


def begin_run(owner, session_id, script_ref, *, inputs: dict, parameters: dict,
              output_contract: dict | None, environment: dict, logical_task_id='', request_key='') -> dict:
    run, _ = _begin_run(owner, session_id, script_ref, inputs=inputs, parameters=parameters,
        output_contract=output_contract, environment=environment,
        logical_task_id=logical_task_id, request_key=request_key)
    return run


def begin_budgeted_run(owner, session_id, script_ref, *, inputs: dict, parameters: dict,
                       output_contract: dict | None, environment: dict,
                       logical_task_id: str, request_key='', max_failures=3) -> tuple[dict, bool]:
    """Atomically reserve a run; return (record, created_by_this_call).

    Exact request-key retries are checked before the budget and never consume it
    again. Running reservations count until finalized, preventing concurrent
    starts from each spending the same final repair. Cancelled/succeeded runs
    do not consume the failed-repair budget. Counts use the full task history.
    """
    if type(max_failures) is not int or max_failures < 1 or not logical_task_id:
        raise ValueError('A positive failure budget and logical task are required')
    return _begin_run(owner, session_id, script_ref, inputs=inputs, parameters=parameters,
        output_contract=output_contract, environment=environment,
        logical_task_id=logical_task_id, request_key=request_key, max_failures=max_failures)


def _begin_run(owner, session_id, script_ref, *, inputs, parameters, output_contract,
               environment, logical_task_id='', request_key='', max_failures=None):
    _scope(owner, session_id)
    ref = _ref(script_ref)
    _token(logical_task_id)
    _token(request_key)
    for value in (inputs, parameters, environment):
        _json(value)
    _json(output_contract, optional=True)
    raw_payload = {'inputs': inputs, 'parameters': parameters, 'output_contract': output_contract,
                   'environment': environment, 'logical_task_id': logical_task_id}
    # The private identity hash still distinguishes differing host environments;
    # the public record contains no host executable/image filesystem locations.
    fingerprint = _hash(_json({'script_ref': ref, 'payload': raw_payload}))
    payload = _json({**raw_payload, 'environment': public_provenance(environment)})
    with _lease(owner), _connect() as conn:
        _load(conn, owner, session_id, ref)
        if request_key:
            previous = conn.execute('SELECT * FROM runs WHERE owner=? AND session=? AND request_key=?',
                                    (owner, session_id, request_key)).fetchone()
            if previous is not None:
                if previous['fingerprint'] != fingerprint:
                    raise ValueError('Run request key conflict')
                _touch(conn, ref['id'])
                return _run(previous), False
        if max_failures is not None:
            spent = conn.execute("""SELECT count(*) FROM runs WHERE owner=? AND session=?
                AND json_extract(payload, '$.logical_task_id')=?
                AND status IN ('failed','partial','running')""", (owner, session_id, logical_task_id)).fetchone()[0]
            if spent >= max_failures:
                raise ValueError('Initial attempt and two repairs exhausted or reserved; inspect retained runs')
        run_id = uuid.uuid4().hex
        conn.execute('''INSERT INTO runs (run_id,script_id,version,sha256,owner,session,request_key,
            fingerprint,payload,status,created) VALUES (?,?,?,?,?,?,?,?,?,'running',?)''',
            (run_id, ref['id'], ref['version'], ref['sha256'], owner, session_id, request_key, fingerprint, payload, time.time()))
        _touch(conn, ref['id'])
        return _run(conn.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()), True


def _run_row(conn, owner, session, run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ValueError('Invalid run identity')
    row = conn.execute('SELECT * FROM runs WHERE run_id=? AND owner=? AND session=?', (run_id, owner, session)).fetchone()
    if row is None:
        raise LookupError('Run not found')
    _load(conn, owner, session, _run(row)['script_ref'], allow_expired=True)
    return row


def _public_text(text):
    # Preserve real HTTP/app artifact URLs, including their relative /api links.
    # Quoted paths may contain spaces; unquoted private paths consume the rest
    # of their line rather than exposing a username suffix after the first space.
    text = re.sub(r'File "[^"]+"', 'File "[redacted]"', text)
    for quote in ('"', "'"):
        pattern = re.escape(quote) + r'(?:[A-Za-z]:[\\/]|\\\\|/(?!api/))[^' + quote + r'\r\n]*' + re.escape(quote)
        text = re.sub(pattern, quote + '[redacted]' + quote, text)
    pattern = (r'(?P<public>https?://[^\s\"\'<>]+|(?<![\w:/])/api/[^\s\"\'<>]+)'
               r'|file://[^\r\n\"\'<>]+|[A-Za-z]:[\\/][^\r\n\"\'<>]+'
               r'|\\\\[^\r\n\"\'<>]+|(?<![\w:/])/(?!api/)[^\r\n\"\'<>]+')
    return re.sub(pattern, lambda match: match.group('public') or '[redacted]', text, flags=re.I)


def public_provenance(value, *, _depth=0):
    """Return a JSON-shaped copy with private filesystem locations redacted.

    This is for public execution metadata, not for script source or scientific
    table bytes. It is deliberately shared by stored and returned run receipts.
    """
    if _depth > 64:
        return '[metadata nesting limit]'
    if isinstance(value, str):
        return _public_text(value)
    if isinstance(value, dict):
        return {_public_text(str(key)): public_provenance(item, _depth=_depth + 1)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_provenance(item, _depth=_depth + 1) for item in value]
    return value


def _error(error):
    if not isinstance(error, str):
        raise ValueError('Error must be text')
    return _public_text(error[:16000])[:4000]


def _result_json(result):
    """Bound the retained log, not the already-published caller receipt."""
    safe = public_provenance(result)
    try:
        return _json(safe, optional=True)
    except ValueError:
        if not isinstance(safe, dict):
            raise
        # Invalid JSON remains an error; only valid oversized receipts compact.
        encoded = json.dumps(safe, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
        if len(encoded) <= MAX_JSON:
            raise
    summary = {'run_result_compacted': True, 'original_result_chars': len(encoded),
               'original_result_sha256': _hash(encoded), 'omitted_result_fields': []}
    receipt_keys = {'kind', 'id', 'version', 'sha256', 'script_ref', 'run_id', 'execution_id',
        '_tool_outcome', 'status', 'reason_code', 'artifacts', 'artifact_url', 'artifact_id',
        'download_url', 'url', 'file_id', 'dataset_ref', 'dataset_refs', 'figure_id', 'figure_ids',
        'figure_ref', 'figure_refs', 'tables', 'figures', 'exports', 'figure_placeholders',
        'name', 'format', 'mime', 'error'}

    def compact(value, depth=0):
        if depth > 8:
            return '[omitted nested metadata]'
        if isinstance(value, str):
            return value if len(value) <= 2048 else value[:2048] + '[truncated]'
        if isinstance(value, list):
            items = [compact(item, depth + 1) for item in value[:128]]
            return items + ([{'omitted_items': len(value) - 128}] if len(value) > 128 else [])
        if isinstance(value, dict):
            return {key: compact(item, depth + 1) for key, item in value.items() if key in receipt_keys}
        return value

    # Keep accepted artifact/dataset identities first. Scientific results remain
    # in their immutable assets; large QA text, stdout and observations are logs.
    # Repair bindings must remain exact or be absent, never recursively clipped.
    # They contain bounded host-created refs/options, not arbitrary script logs.
    if 'replay' in safe:
        try:
            if len(_json({**summary, 'replay': safe['replay']})) > MAX_JSON // 2:
                raise ValueError('Repair context exceeds retained limit')
        except ValueError:
            summary['omitted_result_fields'].append('replay')
        else:
            summary['replay'] = safe['replay']
    priority = ('_tool_outcome', 'script_ref', 'run_id', 'execution_id', 'dataset_refs',
                'figure_refs', 'artifacts', 'exports', 'tables', 'figures', 'error')
    for key in priority:
        if key not in safe:
            continue
        candidate = compact(safe[key])
        try:
            if len(_json({**summary, key: candidate})) > MAX_JSON - 10000:
                raise ValueError('Reserve room for truncation notices')
        except ValueError:
            summary['omitted_result_fields'].append(key)
        else:
            summary[key] = candidate
    summary['omitted_result_fields'] = sorted(set(summary['omitted_result_fields']) |
        {key for key in safe if key not in summary})[:100]
    # Key names themselves are bounded; callers retain the full original receipt.
    summary['omitted_result_fields'] = [str(key)[:80] for key in summary['omitted_result_fields']]
    return _json(summary)


def finish_run(owner, session_id, run_id, *, status: str, result: dict | None = None, error='') -> dict:
    _scope(owner, session_id)
    if not isinstance(status, str) or status not in TERMINAL_STATUSES:
        raise ValueError('Invalid terminal run status')
    encoded, error = _result_json(result), _error(error)
    with _lease(owner), _connect() as conn:
        row = _run_row(conn, owner, session_id, run_id)
        if row['status'] != 'running':
            if (row['status'], row['result'], row['error']) != (status, encoded, error):
                raise ValueError('Run is already terminal')
            return _run(row)
        conn.execute('UPDATE runs SET status=?,result=?,error=?,finished=? WHERE run_id=?',
                     (status, encoded, error, time.time(), run_id))
        _touch(conn, row['script_id'])
        return _run(_run_row(conn, owner, session_id, run_id))


def get_run(owner, session_id, run_id) -> dict:
    _scope(owner, session_id)
    with _lease(owner), _connect() as conn:
        return _run(_run_row(conn, owner, session_id, run_id))


def list_runs(owner, session_id, *, script_ref=None, limit=10) -> list:
    _scope(owner, session_id)
    limit = _limit(limit)
    with _lease(owner), _connect() as conn:
        query, args = 'SELECT * FROM runs WHERE owner=? AND session=?', [owner, session_id]
        if script_ref is not None:
            row = _load(conn, owner, session_id, script_ref)
            query += ' AND script_id=? AND version=?'
            args.extend((row['id'], row['version']))
        return [_run(_run_row(conn, owner, session_id, r['run_id'])) for r in conn.execute(
            query + ' ORDER BY created DESC,run_id LIMIT ?', (*args, limit)).fetchall()]


def pin_script(owner, session_id, script_ref, resource_kind, resource_id):
    _scope(owner, session_id)
    if not _token(resource_kind) or not _token(resource_id):
        raise ValueError('Resource identity required')
    with _lease(owner), _connect() as conn:
        row = _load(conn, owner, session_id, script_ref)
        conn.execute('INSERT OR IGNORE INTO pins VALUES (?,?,?,?)', (row['id'], row['version'], resource_kind, resource_id))
        _touch(conn, row['id'])
        return _ref(script_ref)


def release_script_pin(owner, session_id, script_ref, resource_kind, resource_id) -> int:
    """Release a validated dependency without admitting new account work.

    Safe for deletion and lease finalizers after account admission closes. The
    host owner/session and exact immutable ref still constrain the deletion.
    """
    _scope(owner, session_id)
    _token(resource_kind)
    _token(resource_id)
    with _connect() as conn:
        row = _load(conn, owner, session_id, script_ref, allow_expired=True)
        return conn.execute('DELETE FROM pins WHERE script_id=? AND version=? AND resource_kind=? AND resource_id=?',
                            (row['id'], row['version'], resource_kind, resource_id)).rowcount


def _reconcile_locked(conn, timestamp, stale_after_seconds):
    ids = [row[0] for row in conn.execute(
        "SELECT DISTINCT script_id FROM runs WHERE status='running' AND created < ?",
        (timestamp - stale_after_seconds,))]
    count = conn.execute("""UPDATE runs SET status='failed',finished=?,
        error='Execution record abandoned: process interrupted or host completion missing'
        WHERE status='running' AND created < ?""", (timestamp, timestamp - stale_after_seconds)).rowcount
    for identity in ids:
        conn.execute('UPDATE scripts SET accessed=MAX(accessed,?) WHERE id=?', (timestamp, identity))
    return count


def reconcile_stale_runs(*, now=None, stale_after_seconds=86400) -> dict:
    """Recover abandoned executions, retaining their provenance for another TTL.

    Default 24h is far beyond the host sandbox's 120s hard execution timeout.
    This is a storage recovery horizon, not a live-process cancellation signal;
    hosts permitting runs longer than this must supply a larger horizon.
    """
    timestamp = time.time() if now is None else float(now)
    if (not math.isfinite(timestamp) or isinstance(stale_after_seconds, bool)
            or not isinstance(stale_after_seconds, (int, float))
            or not math.isfinite(stale_after_seconds) or stale_after_seconds < 3600):
        raise ValueError('Invalid stale-run recovery horizon (minimum one hour)')
    with _connect() as conn:
        return {'reconciled_runs': _reconcile_locked(conn, timestamp, stale_after_seconds)}


def cleanup_scripts(*, now=None) -> dict:
    timestamp = time.time() if now is None else float(now)
    cutoff = _idle_cutoff(timestamp)
    with _connect() as conn:
        _reconcile_locked(conn, timestamp, 86400)
        ids = [r[0] for r in conn.execute('''SELECT id FROM scripts s WHERE accessed < ?
            AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.script_id=s.id AND r.status='running')
            AND NOT EXISTS (SELECT 1 FROM pins p WHERE p.script_id=s.id)''', (cutoff,))]
        versions = runs = 0
        for identity in ids:
            versions += conn.execute('SELECT count(*) FROM versions WHERE id=?', (identity,)).fetchone()[0]
            runs += conn.execute('SELECT count(*) FROM runs WHERE script_id=?', (identity,)).fetchone()[0]
            conn.execute('DELETE FROM scripts WHERE id=?', (identity,))
        return {'expired_scripts': len(ids), 'expired_versions': versions, 'expired_runs': runs}


def purge_owner(owner) -> int:
    """Delete all owned families, versions, pins and runs after admission drains.

    Does not acquire a fresh lease: deletion intentionally closes admission first.
    The host must close admission and hold deletion_lock through its deletion flow.
    """
    if type(owner) is not int or owner <= 0:
        raise ValueError('Authenticated owner required')
    from src.core.account_leases import assert_idle, deletion_lock
    with deletion_lock(owner):
        assert_idle(owner)
        with _connect() as conn:
            return conn.execute('DELETE FROM scripts WHERE owner=?', (owner,)).rowcount

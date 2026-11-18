"""Owner/session-bound, incremental ReferenceAudit service shared by chat/PDF.

The host owns operation authorization and job scheduling. This service never
spawns a worker or an LLM. Each HTTP dispatch is charged durably before sending;
each completed record is committed before progress is emitted. A crashed run's
finite lease can be reclaimed, but its request budget is never replenished.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import io
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid

from citrus_refcheck.compare import (
    FIELDS, _value, _author_comparison, _journal_comparison, compare_fields,
)

from src.research import literature as lit
from src.research.reference_extraction import digest, extract_inventory

RULE_VERSION = 'reference-fields/4'
_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 6 * 3600


class AuditStopped(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _scope(owner, session_id):
    from src.session import manager
    if type(owner) is not int or owner <= 0 or not isinstance(session_id, str) or not session_id:
        raise PermissionError('Authenticated owner and session required')
    if manager.session_manager.get_session_owner(session_id) != owner:
        raise PermissionError('Session not found for this owner')


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
    path = config.PROJECT_ROOT / 'state' / 'reference_audits.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=3, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if os.name != 'nt':
            os.chmod(path, 0o600)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''CREATE TABLE IF NOT EXISTS reference_audits (
            id TEXT PRIMARY KEY, owner INTEGER NOT NULL, session TEXT NOT NULL,
            inventory TEXT NOT NULL, status TEXT NOT NULL, request_limit INTEGER NOT NULL,
            requests_used INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0,
            lease_token TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
            created REAL NOT NULL, updated REAL NOT NULL)''')
        conn.execute('CREATE INDEX IF NOT EXISTS reference_scope ON reference_audits(owner,session,created)')
        conn.execute('''CREATE TABLE IF NOT EXISTS reference_records (
            audit_id TEXT NOT NULL REFERENCES reference_audits(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, entry TEXT NOT NULL, result TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(audit_id,ordinal))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS reference_attempts (
            audit_id TEXT NOT NULL REFERENCES reference_audits(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, attempt INTEGER NOT NULL, result TEXT NOT NULL,
            created REAL NOT NULL, PRIMARY KEY(audit_id,ordinal,attempt))''')
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _load(conn, owner, session_id, audit_id):
    if not isinstance(audit_id, str) or not re.fullmatch(r'[a-f0-9]{32}', audit_id):
        raise ValueError('Explicit valid audit_id required')
    row = conn.execute('SELECT * FROM reference_audits WHERE id=? AND owner=? AND session=?',
                       (audit_id, owner, session_id)).fetchone()
    if row is None:
        raise LookupError('Reference audit not found')
    return row


def create_audit(owner, session_id, *, name='references.txt', content=None, text='', references=None,
                 request_budget=1200, source_ref=None, column_map=None, encoding=""):
    _scope(owner, session_id)
    inventory = extract_inventory(name=name, content=content, text=text,
                                  references=references, source_ref=source_ref,
                                  column_map=column_map, encoding=encoding)
    return _create_inventory(owner, session_id, inventory, request_budget=request_budget)


def _create_inventory(owner, session_id, inventory, *, request_budget=1200):
    _scope(owner, session_id)
    if type(request_budget) is not int or not 1 <= request_budget <= 50000:
        raise ValueError('request_budget must be 1..50000')
    if len(_json(inventory)) > 24 * 1024 * 1024:
        raise ValueError('Reference inventory exceeds storage limit')
    entries = inventory['entries']
    audit_id, now = uuid.uuid4().hex, time.time()
    with _lease(owner), _connect() as conn:
        conn.execute('''INSERT INTO reference_audits
            (id,owner,session,inventory,status,request_limit,created,updated) VALUES (?,?,?,?,?,?,?,?)''',
            (audit_id, owner, session_id, _json({k: v for k, v in inventory.items() if k != 'entries'}),
             'pending' if entries else 'partial', request_budget, now, now))
        conn.executemany('INSERT INTO reference_records(audit_id,ordinal,entry) VALUES (?,?,?)',
                         [(audit_id, i, _json(entry)) for i, entry in enumerate(entries)])
    return get_audit(owner, session_id, audit_id)


def _row(item):
    entry = json.loads(item['entry'])
    result = json.loads(item['result']) if item['result'] else None
    # Keep the immutable source intact; flattened fields are compatibility only.
    return {**(result or {}), **entry, 'result': result, 'attempts': item['attempts'],
            'status': result['status'] if result else 'not_checked'}


def _view(conn, audit, *, offset=0, limit=100):
    inventory = json.loads(audit['inventory'])
    raw = conn.execute('SELECT * FROM reference_records WHERE audit_id=? ORDER BY ordinal', (audit['id'],)).fetchall()
    counts = {}
    processed = retryable = stale = 0
    for item in raw:
        result = json.loads(item['result']) if item['result'] else None
        status = result['status'] if result else 'not_checked'
        counts[status] = counts.get(status, 0) + 1
        if result:
            processed += 1
            outdated = (result.get('rule_version') != RULE_VERSION
                        or result.get('claims_sha256') != json.loads(item['entry'])['claims_sha256'])
            stale += int(outdated)
            retryable += int(bool(result.get('retryable')) or outdated)
    total = len(raw)
    pending = total - processed
    complete = bool(total and not pending and not retryable and not counts.get('not_checked')
                    and not counts.get('network_error') and inventory['extraction']['status'] == 'complete')
    processing_complete = bool(total and not pending and not retryable)
    review_required = bool(inventory['extraction']['status'] != 'complete'
                           or any(status != 'metadata_matched' for status in counts))
    terminal = processing_complete and not audit['cancelled']
    status = ('completed_with_review' if review_required else 'complete') if terminal else audit['status']
    has_budget = audit['requests_used'] < audit['request_limit']
    retry_optional = bool(total and not pending and retryable and not stale)
    next_action = ('export_and_review' if terminal and review_required else 'export' if terminal
                   else 'budget_exhausted_export_partial' if not has_budget
                   else 'export_and_review' if retry_optional
                   else 'resume' if audit['cancelled'] or (retryable and not pending)
                   else 'run' if pending else 'review_source')
    history = {}
    for attempt in conn.execute('SELECT ordinal,attempt,result,created FROM reference_attempts WHERE audit_id=? AND ordinal>=? AND ordinal<? ORDER BY ordinal,attempt',
                                (audit['id'], offset, offset + limit)):
        history.setdefault(attempt['ordinal'], []).append({'attempt': attempt['attempt'],
            'created_at': attempt['created'], 'result': json.loads(attempt['result'])})
    return {'schema': 'reference_audit/2', 'audit_id': audit['id'],
            'status': status, 'source': inventory['source'],
            'terminal': terminal, 'review_required': review_required,
            'next_action': next_action, 'can_resume': bool(not terminal and has_budget and (pending or retryable)),
            'export_ready': True, 'retry_optional': retry_optional,
            'parser_version': inventory['parser_version'], 'rule_version': RULE_VERSION,
            'extraction': inventory['extraction'], 'counts': counts,
            'coverage': {'total': total, 'submitted': total, 'processed': processed,
                         'pending': pending, 'retryable': retryable, 'complete': complete,
                         'processing_complete': processing_complete,
                         'extraction_complete': inventory['extraction']['status'] == 'complete',
                         'source_sha256': inventory['source']['sha256']},
            'budget': {'request_limit': audit['request_limit'], 'requests_used': audit['requests_used'],
                       'requests_remaining': max(0, audit['request_limit'] - audit['requests_used'])},
            'rows': [{**_row(item), 'attempt_history': history.get(item['ordinal'], [])}
                     for item in raw[offset:offset + limit]],
            'offset': offset, 'next_offset': offset + limit if offset + limit < total else None,
            'created_at': audit['created'], 'updated_at': audit['updated'],
            'summary': ('库存条目均已尝试，待人工复核解析或字段结果；请导出此 audit_id 的全量证据，无需反复 resume。'
                        if terminal and review_required else
                        '全部库存条目均已尝试，但来源错误仍未解决；请先按此 audit_id 导出完整证据并人工复核。'
                        '如需重试，显式 resume=True，仍服从剩余预算；无需为交付持续续跑。'
                        if retry_optional else
                        '登记记录与字段准确性分开呈现；未查到、解析遗漏或联网失败不能证明文献虚构。'),
            '_tool_outcome': {'status': 'complete' if terminal and complete else 'partial',
                              'reason_code': 'audit_completed_with_review' if terminal and complete and review_required
                              else 'audit_complete' if terminal and complete
                              else 'reference_extraction_incomplete' if terminal
                              else 'reference_coverage_incomplete',
                              'artifacts': [], 'result_refs': [{'kind': 'reference_audit', 'audit_id': audit['id']}]}}


def get_audit(owner, session_id, audit_id, *, offset=0, limit=100):
    _scope(owner, session_id)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 5000:
        raise ValueError('Invalid audit page')
    with _lease(owner), _connect() as conn:
        return _view(conn, _load(conn, owner, session_id, audit_id), offset=offset, limit=limit)


def list_audits(owner, session_id, *, limit=20):
    _scope(owner, session_id)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid audit list limit')
    with _lease(owner), _connect() as conn:
        rows = conn.execute('SELECT * FROM reference_audits WHERE owner=? AND session=? ORDER BY created DESC LIMIT ?',
                            (owner, session_id, limit)).fetchall()
        return [_view(conn, row, limit=0) for row in rows]


def cancel_audit(owner, session_id, audit_id):
    _scope(owner, session_id)
    with _lease(owner), _connect() as conn:
        _load(conn, owner, session_id, audit_id)
        # Fence this runner immediately; late results cannot overwrite cancelled state.
        conn.execute("UPDATE reference_audits SET cancelled=1,status='cancelled',lease_token='',lease_until=0,updated=? WHERE id=?",
                     (time.time(), audit_id))
    return get_audit(owner, session_id, audit_id)


def _lookup_cached(owner, session_id, key, call, context):
    cache_key = (owner, session_id, digest(key))
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            evidence = deepcopy(cached[2])
            for item in evidence:
                item.update(cache_hit=True, cache_age_seconds=round(now - cached[0], 3))
            context.evidence.extend(evidence)
            return deepcopy(cached[1])
    start = len(context.evidence)
    result = call()
    if (result[0] == 200 and isinstance(result[1], list) and result[1]
            and all(isinstance(item, dict) for item in result[1]) and not result[2]):
        with _CACHE_LOCK:
            _CACHE[cache_key] = (now, deepcopy(result), deepcopy(context.evidence[start:]))
            if len(_CACHE) > 2000:
                for stale in sorted(_CACHE, key=lambda item: _CACHE[item][0])[:500]:
                    del _CACHE[stale]
    return result


def verify_record(claims, *, owner=0, session_id='', uncertain_fields=(), deadline=None, check=None, debit=None):
    """Direct adapter for one entry; callers persist it through run_audit.

    Uses existing registry adapters, original language queries and no LLM/web
    fallback. Returns every provider candidate and actual HTTP receipt.
    """
    from src.core.tracing import web_search_enabled
    if not web_search_enabled():
        return {'status': 'not_checked', 'reason': 'network_disabled', 'retryable': True,
                'existence': 'not_checked', 'identity': 'not_checked', 'rule_version': RULE_VERSION,
                'fields': compare_fields(claims, None), 'evidence': [], 'candidates': [],
                'facts': {'records': [], 'providers': {}, 'errors': {}, 'http_evidence': []}}
    deadline = min(deadline or time.monotonic() + 25, time.monotonic() + 25)
    doi, title = lit.normalize_doi(claims.get('doi')), _value(claims, 'title')
    arxiv_id = _value(claims, 'arxiv_id')
    records, errors, providers = [], {}, {}
    stopped = ''
    with lit.bounded_lookup(deadline=deadline, check=check, debit=debit) as context:
        def query(provider, kind, value, function):
            nonlocal stopped
            if check:
                check()
            if time.monotonic() >= deadline:
                stopped = 'deadline'
                return
            try:
                status, found, error = _lookup_cached(owner, session_id,
                    {'provider': provider, 'kind': kind, 'value': value}, function, context)
            except AuditStopped as exc:
                stopped = exc.reason
                raise
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                status, found, error = 0, [], 'invalid_provider_payload:' + type(exc).__name__
            key = provider + ':' + kind
            if status not in (200, 404) and not error:
                error = f'http_{status}' if isinstance(status, int) and 100 <= status <= 599 else 'invalid_response'
            if status == 200 and (not isinstance(found, list) or any(not isinstance(item, dict) for item in found)):
                error = error or 'invalid_provider_payload'
            providers[key] = 'error' if error else 'not_found' if status == 404 else 'ok'
            if error:
                errors[key] = error
            if status == 200 and not error:
                # Preserve provider differences instead of flattening deduplication.
                for record in found:
                    if record.get('title') or record.get('doi'):
                        records.append({**record, 'lookup_kind': kind,
                                        'record_sha256': digest(record)})
                if context.evidence:
                    context.evidence[-1]['candidate_count'] = len(found)
        try:
            if doi:
                encoded = lit.quote(doi, safe='/')
                for provider, function in (('Crossref', lit._crossref_work), ('OpenAlex', lit._openalex_work),
                                           ('DataCite', lit._datacite_work), ('Semantic Scholar', lit._s2_doi)):
                    query(provider, 'doi', doi, lambda fn=function: fn(encoded))
            elif arxiv_id:
                query('arXiv', 'arxiv_id', arxiv_id, lambda: lit._arxiv_by_id(arxiv_id))
                query('DataCite', 'arxiv_id', arxiv_id, lambda: lit._datacite_arxiv(arxiv_id))
            # A wrong identifier must not suppress the independent title path.
            exact = [item for item in records if doi and lit.normalize_doi(item.get('doi')) == doi]
            needs_title = not exact or any(
                lit.norm_title(item.get('title')) != lit.norm_title(title)
                or any(value['status'] == 'conflicting' for value in compare_fields(claims, item).values())
                for item in exact)
            if title and (not doi or needs_title):
                for provider, function in (('Crossref', lit._crossref_search), ('OpenAlex', lit._openalex_search),
                                           ('Europe PMC', lit._epmc_search), ('PubMed', lit._pubmed_search)):
                    query(provider, 'title', title, lambda fn=function: fn(title))
        except AuditStopped as exc:
            stopped = exc.reason
        evidence = deepcopy(context.evidence)
    facts = {'records': records, 'providers': providers, 'errors': errors,
             'http_evidence': evidence}
    return _project_verification(claims, facts, uncertain_fields=uncertain_fields, stopped=stopped)


def _project_verification(claims, facts, *, uncertain_fields=(), stopped=''):
    """Project provider facts into the host's historical verification fields.

    Legacy statuses describe submitted-field observations, never a truth verdict.
    Keep the underlying facts separate from candidate ranking and field hints.
    """
    doi, title = lit.normalize_doi(claims.get('doi')), _value(claims, 'title')
    arxiv_id = _value(claims, 'arxiv_id')
    records = deepcopy(facts['records'])
    errors, evidence = facts['errors'], facts['http_evidence']

    def identifier_matches(item):
        return bool(lit.normalize_doi(item.get('doi')) == doi if doi
                    else arxiv_id and item.get('arxiv_id') == arxiv_id)

    # Identity is the primary selection axis. Title search can suggest an
    # alternative citation but must never replace the record named by the DOI.
    candidates = sorted(records, key=lambda item: (
        0 if identifier_matches(item) else 1,
        -lit.title_similarity(title, item.get('title')) if title else 0))
    direct = [item for item in candidates if identifier_matches(item)]
    relevant = [item for item in candidates if item in direct
                or (title and lit.title_similarity(title, item.get('title')) >= lit.CANDIDATE_FLOOR)]
    best = relevant[0] if relevant else None
    for item in candidates:
        item['fields'] = compare_fields(claims, item, uncertain_fields=uncertain_fields)
        item['identifier_match'] = item in direct
        item['title_similarity'] = round(lit.title_similarity(title, item.get('title')), 3)
    fields = compare_fields(claims, best, uncertain_fields=uncertain_fields)
    corroborated = bool(best and fields['title']['status'] == 'matched'
                        and fields['authors']['status'] == 'matched' and fields['year']['status'] == 'matched')
    identity = 'identifier_match' if direct and best in direct else 'metadata_corroborated' if corroborated else 'candidate' if best else 'unresolved'
    if doi and best and lit.normalize_doi(best.get('doi')) != doi:
        identity = 'identifier_conflict_candidate'
    # A returned record exists, but may be unrelated to the submitted citation.
    existence = 'record_found' if direct or corroborated else 'candidate_records_found' if best else 'not_established'
    provider_conflicts = []
    if best:
        for item in candidates:
            if item is best or not best.get('doi') or lit.normalize_doi(item.get('doi')) != lit.normalize_doi(best['doi']):
                continue
            comparison = compare_fields(best, item)
            different = [key for key, value in comparison.items() if value['status'] == 'conflicting']
            if different:
                provider_conflicts.append({'provider': item.get('source'), 'fields': different,
                                           'record_sha256': item['record_sha256']})
    # Assess disagreement against the submitted claim, keeping the independent
    # provider-to-provider differences in the evidence above.
    same_record = [item for item in relevant if best and item is not best
                   and (item in direct if best in direct else best.get('doi')
                        and lit.normalize_doi(item.get('doi')) == lit.normalize_doi(best['doi']))]
    if fields['year']['reason'] in {'possible_online_print_difference_not_documented', 'provider_field_missing'}:
        corroborating_year = next((item for item in same_record
                                   if item['fields']['year']['status'] == 'matched'), None)
        if corroborating_year:
            fields['year'] = {**corroborating_year['fields']['year'],
                              'record_sha256': corroborating_year['record_sha256']}
    for field, observation in fields.items():
        if observation['status'] != 'matched':
            continue
        if any(item['fields'][field]['status'] == 'conflicting' for item in same_record):
            observation.update(status='uncertain', reason='providers_disagree_on_submitted_field')
    conflicts = [key for key, value in fields.items() if value['status'] == 'conflicting']
    uncertain = any(value['status'] in {'uncertain', 'missing'} for value in fields.values())
    if not best:
        status = 'not_checked' if not (doi or arxiv_id or title) else 'network_error' if errors or stopped else 'not_found_in_checked_registries'
    elif conflicts:
        status = 'metadata_mismatch'
    elif identity in {'identifier_match', 'metadata_corroborated'} and not uncertain and not (errors or stopped):
        status = 'metadata_matched'
    else:
        status = 'title_candidate' if identity in {'candidate', 'identifier_conflict_candidate'} else 'metadata_uncertain'
    identities = {lit.normalize_doi(item.get('doi')) or item.get('arxiv_id') or item.get('url')
                  for item in relevant if lit.norm_title(item.get('title')) == lit.norm_title(title)} - {None, ''}
    if len(identities) > 1 and identity == 'metadata_corroborated':
        identity, status = 'ambiguous_candidates', 'metadata_uncertain'
    return {'status': status, 'existence': existence, 'identity': identity, 'fields': fields,
            'metadata_scope': 'submitted_fields_only',
            'rule_version': RULE_VERSION, 'claims_sha256': digest(claims),
            'retryable': bool(errors or stopped), 'stop_reason': stopped,
            'facts': facts,
            'evidence': evidence, 'errors': errors, 'candidates': candidates,
            'selected_record': best, 'identifier_records': direct,
            'title_candidates': [item for item in candidates if item.get('lookup_kind') == 'title'
                                 and not identifier_matches(item)],
            'selection_basis': 'exact_identifier' if direct else 'title_candidate' if best else 'unresolved',
            'provider_conflicts': provider_conflicts,
            'limits': {'max_seconds': 25, 'title_candidates_per_provider': 8,
                       'web_fallback': 'not_performed', 'translation': 'not_performed',
                       'response_limit_bytes': 4 * 1024 * 1024, 'retries': 0},
            'doi': doi, 'input_title': title, 'input_authors': _value(claims, 'authors'),
            'input_year': _value(claims, 'year'),
            'metadata_title': _value(best or {}, 'title'), 'metadata_authors': _value(best or {}, 'authors'),
            'metadata_year': _value(best or {}, 'year'), 'metadata_journal': _value(best or {}, 'journal'),
            'provider': (best or {}).get('source', ''), 'source_url': (best or {}).get('url', ''),
            'evidence_url': (best or {}).get('url', ''), 'field_conflicts': '; '.join(conflicts),
            'detail': '登记记录存在与引用身份、字段准确性分别报告；未查到不等于不存在。'}


def run_audit(owner, session_id, audit_id, *, max_rows=20, time_budget=45, resume=False, progress=None):
    from src.core.cancel import is_cancelled
    from src.core.tracing import web_search_enabled
    _scope(owner, session_id)
    if type(max_rows) is not int or not 1 <= max_rows <= 100:
        raise ValueError('max_rows must be 1..100')
    if not isinstance(time_budget, (int, float)) or not math.isfinite(time_budget) or not 0 < time_budget <= 120:
        raise ValueError('time_budget must be >0 and <=120 seconds')
    if not web_search_enabled():
        result = get_audit(owner, session_id, audit_id)
        result['run_reason'] = 'network_disabled'
        return result
    from src.core.tracing import get_job_id
    from src.core.task_events import remaining_seconds
    inherited_remaining = remaining_seconds(get_job_id())
    if inherited_remaining <= 0:
        result = get_audit(owner, session_id, audit_id)
        result['run_reason'] = 'deadline'
        return result
    time_budget = min(time_budget, inherited_remaining)
    token, deadline = uuid.uuid4().hex, time.monotonic() + time_budget
    completed, reason = [], ''
    with _lease(owner):
        with _connect() as conn:
            audit = _load(conn, owner, session_id, audit_id)
            if audit['lease_token'] and audit['lease_until'] > time.time():
                result = _view(conn, audit)
                result['run_reason'] = 'already_running'
                return result
            if audit['cancelled'] and not resume:
                result = _view(conn, audit)
                result['run_reason'] = 'cancelled_use_resume'
                return result
            view = _view(conn, audit)
            if view['terminal']:
                view['run_reason'] = 'already_completed'
                view['processed_indices'] = []
                return view
            if view['retry_optional'] and not resume:
                view['run_reason'] = 'all_entries_attempted_use_explicit_resume'
                view['processed_indices'] = []
                return view
            conn.execute("UPDATE reference_audits SET status='running',cancelled=0,lease_token=?,lease_until=?,updated=? WHERE id=?",
                         (token, time.time() + time_budget + 10, time.time(), audit_id))
            raw = conn.execute('SELECT * FROM reference_records WHERE audit_id=? ORDER BY ordinal', (audit_id,)).fetchall()
            # First finish untouched entries; retry failures only on explicit resume.
            pending, retries = [], []
            for item in raw:
                entry = json.loads(item['entry'])
                prior = json.loads(item['result']) if item['result'] else None
                pair = (item['ordinal'], entry, prior)
                if prior is None:
                    pending.append(pair)
                elif prior.get('rule_version') != RULE_VERSION or prior.get('claims_sha256') != entry['claims_sha256']:
                    pending.append(pair)
                elif resume and prior.get('retryable'):
                    retries.append(pair)
            selected = (pending + retries)[:max_rows]

        def check():
            if is_cancelled():
                raise AuditStopped('cancelled')
            if time.monotonic() >= deadline:
                raise AuditStopped('deadline')
            if not web_search_enabled():
                raise AuditStopped('network_disabled')
            with _connect() as conn:
                audit = _load(conn, owner, session_id, audit_id)
                if audit['cancelled'] or audit['lease_token'] != token:
                    raise AuditStopped('cancelled' if audit['cancelled'] else 'lease_lost')

        def debit():
            check()
            with _connect() as conn:
                audit = _load(conn, owner, session_id, audit_id)
                if audit['lease_token'] != token or audit['cancelled']:
                    raise AuditStopped('lease_lost')
                if audit['requests_used'] >= audit['request_limit']:
                    raise AuditStopped('budget_exhausted')
                conn.execute('UPDATE reference_audits SET requests_used=requests_used+1 WHERE id=?', (audit_id,))

        try:
            for ordinal, entry, prior in selected:
                check()
                if entry.get('parse_status') == 'invalid_row':
                    result = {'status': 'not_checked', 'existence': 'not_checked', 'identity': 'not_checked',
                              'reason': entry['parse_error'], 'rule_version': RULE_VERSION,
                              'claims_sha256': entry['claims_sha256'], 'retryable': False}
                elif (prior and prior.get('rule_version') != RULE_VERSION and not prior.get('retryable')
                      and prior.get('claims_sha256') == entry['claims_sha256'] and prior.get('facts')):
                    result = _project_verification(entry['claims'], prior['facts'],
                        uncertain_fields=entry.get('uncertain_fields', []))
                else:
                    result = verify_record(entry['claims'], owner=owner, session_id=session_id,
                        uncertain_fields=entry.get('uncertain_fields', []), deadline=deadline, check=check, debit=debit)
                with _connect() as conn:
                    audit = _load(conn, owner, session_id, audit_id)
                    if audit['lease_token'] != token or audit['cancelled']:
                        raise AuditStopped('cancelled' if audit['cancelled'] else 'lease_lost')
                    # Preserve bounded partial evidence too; resume rechecks retryable results.
                    conn.execute('UPDATE reference_records SET result=?,attempts=attempts+1 WHERE audit_id=? AND ordinal=?',
                                 (_json(result), audit_id, ordinal))
                    conn.execute('''INSERT INTO reference_attempts(audit_id,ordinal,attempt,result,created)
                        SELECT audit_id,ordinal,attempts,result,? FROM reference_records WHERE audit_id=? AND ordinal=?''',
                        (time.time(), audit_id, ordinal))
                    conn.execute('UPDATE reference_audits SET updated=? WHERE id=?', (time.time(), audit_id))
                completed.append(ordinal)
                if progress:
                    progress({'audit_id': audit_id, 'record_id': entry['record_id'], 'record_index': ordinal,
                              'status': result['status'], 'durable': True})
                if result.get('stop_reason'):
                    raise AuditStopped(result['stop_reason'])
        except AuditStopped as exc:
            reason = exc.reason
        finally:
            with _connect() as conn:
                audit = _load(conn, owner, session_id, audit_id)
                if audit['lease_token'] == token:
                    view = _view(conn, audit, limit=0)
                    status = ('cancelled' if reason == 'cancelled' else 'budget_exhausted' if reason == 'budget_exhausted'
                              else view['status'] if view['terminal'] else 'partial')
                    conn.execute('UPDATE reference_audits SET status=?,cancelled=?,lease_token=?,lease_until=0,updated=? WHERE id=?',
                                 (status, int(reason == 'cancelled'), '', time.time(), audit_id))
    result = get_audit(owner, session_id, audit_id)
    result.update(run_reason=reason or 'batch_finished', processed_indices=completed)
    return result


def verification_rows(owner, session_id, audit_id):
    """All original entries, including unprocessed/failed; never read 'latest'."""
    return get_audit(owner, session_id, audit_id, limit=5000)['rows']


def export_audit(owner, session_id, audit_id, *, format='json'):
    payload = get_audit(owner, session_id, audit_id, limit=5000)
    format = str(format).lower()
    name = 'reference_audit_' + audit_id
    if format == 'json':
        return _json(payload).encode('utf-8'), 'application/json', name + '.json'
    if format == 'xlsx':
        from openpyxl import Workbook
        wb = Workbook()
        entries = wb.active
        entries.title = 'References'
        entries.append(['record_index', 'record_id', 'citation', 'status', 'existence', 'identity', 'claims', 'positions'])
        fields = wb.create_sheet('Fields')
        fields.append(['record_id', 'field', 'status', 'submitted', 'registered', 'reason',
                       'input_certainty', 'comparison_status'])
        evidence = wb.create_sheet('Evidence')
        evidence.append(['record_id', 'kind', 'sequence', 'json_part', 'json'])

        def safe(value):
            text = str(value if value is not None else '')
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
            return "'" + text if text.startswith(('=', '+', '-', '@')) else text

        def evidence_json(record_id, kind, value, sequence=0):
            text = _json(value)
            # Excel's cell limit must not silently truncate original evidence.
            for part, start in enumerate(range(0, max(1, len(text)), 30000)):
                evidence.append([record_id, kind, sequence, part, text[start:start + 30000]])
                evidence.cell(evidence.max_row, 5).data_type = 's'

        for row in payload['rows']:
            result = row.get('result') or {}
            entries.append([row['record_index'], row['record_id'], safe(row['citation'][:30000]), row['status'],
                            result.get('existence', 'not_checked'), result.get('identity', 'not_checked'),
                            safe(_json(row['claims'])[:30000]), safe(_json(row['positions'])[:30000])])
            evidence_json(row['record_id'], 'original_entry', {k: v for k, v in row.items() if k != 'result'})
            evidence_json(row['record_id'], 'full_result', result)
            for field, check in result.get('fields', {}).items():
                fields.append([row['record_id'], field, check['status'], safe(check['submitted'][:30000]),
                               safe(check['registered'][:30000]), check['reason'],
                               check.get('input_certainty', ''), check.get('comparison_status', '')])
        evidence_json('', 'audit_metadata', {k: v for k, v in payload.items() if k != 'rows'})
        for sheet in wb:
            sheet.freeze_panes = 'A2'
            sheet.auto_filter.ref = sheet.dimensions
        output = io.BytesIO()
        wb.save(output)
        return output.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', name + '.xlsx'
    if format not in {'ris', 'bibtex'}:
        raise ValueError('Supported exports: json/xlsx/ris/bibtex')
    lines = []
    for row in payload['rows']:
        claims = row['claims']
        # Bibliographic projection of submitted claims, never silently replace
        # an incorrect DOI with a candidate's DOI. JSON/XLSX is the full audit.
        note = f"audit_id={audit_id}; record_id={row['record_id']}; status={row['status']}; bibliographic projection only; full evidence in JSON/XLSX"
        clean = lambda value: re.sub(r'[\r\n\x00-\x1f]+', ' ', str(value or ''))
        if format == 'ris':
            lines.append('TY  - GEN')
            for tag, key in (('TI', 'title'), ('AU', 'authors'), ('PY', 'year'), ('JO', 'journal'),
                             ('VL', 'volume'), ('IS', 'issue'), ('SP', 'pages'), ('DO', 'doi')):
                value = _value(claims, key)
                if value:
                    lines.append(tag + '  - ' + clean(value))
            lines.extend(['N1  - ' + note, 'ER  - ', ''])
        else:
            def bib(value):
                return clean(value).replace('\\', '\\textbackslash{}').replace('{', '\\{').replace('}', '\\}')
            values = []
            for tag, key in (('title', 'title'), ('author', 'authors'), ('year', 'year'), ('journal', 'journal'),
                             ('volume', 'volume'), ('number', 'issue'), ('pages', 'pages'), ('doi', 'doi')):
                value = _value(claims, key)
                if value:
                    values.append('  ' + tag + ' = {' + bib(value) + '}')
            values.append('  note = {' + bib(note) + '}')
            lines.append('@misc{ref_' + row['record_id'][:16] + ',\n' + ',\n'.join(values) + '\n}\n')
    extension = 'bib' if format == 'bibtex' else 'ris'
    mime = 'application/x-bibtex' if format == 'bibtex' else 'application/x-research-info-systems'
    return '\n'.join(lines).encode('utf-8'), mime, name + '.' + extension


def purge_owner(owner):
    """Account deletion hook; caller must close admission and drain leases first."""
    with _connect() as conn:
        count = conn.execute('DELETE FROM reference_audits WHERE owner=?', (owner,)).rowcount
    with _CACHE_LOCK:
        for key in list(_CACHE):
            if key[0] == owner:
                del _CACHE[key]
    return count

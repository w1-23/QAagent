"""Explicit provider lookup; facts and field hints are separate operations."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import time
from urllib.parse import urlsplit
from . import providers as p


@dataclass(frozen=True)
class LookupLimits:
    transport: object
    providers: tuple[str, ...] = ('Crossref',)
    deadline: float = float('inf')
    check: object = None
    debit: object = None
    authorized: bool = False
    # Change this when injected provider/client settings change. It participates
    # in persistent evidence cache identity, never in submitted-field comparison.
    cache_namespace: str = ''


class AuditStopped(RuntimeError):
    pass


def evidence_cache_key(claims, limits):
    """Key actual provider queries, rather than unrelated submitted claims."""
    doi = p.normalize_doi(claims.get('doi'))
    title = str(claims.get('title') or claims.get('query') or '').strip()
    arxiv = str(claims.get('arxiv_id') or '')
    queries = []
    for name in limits.providers:
        if name == 'arXiv':
            query = ['id', arxiv] if arxiv else ['title', title]
        elif name == 'DataCite':
            query = ['doi', doi] if doi else ['arxiv', arxiv]
        else:
            query = ['doi', doi] if doi else ['title', title]
        queries.append([name, query])
    transport = limits.transport
    identity = [type(transport).__module__, type(transport).__qualname__,
                getattr(transport, 'cache_namespace', ''), limits.cache_namespace]
    return hashlib.sha256(json.dumps(['evidence/2', queries, identity, limits.authorized],
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class FixtureTransport:
    """Deterministic recorded HTTP payloads; no network and no guessed records."""
    offline = True
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
    @property
    def cache_namespace(self):
        return hashlib.sha256(json.dumps(self.responses, sort_keys=True,
            ensure_ascii=False, default=str).encode()).hexdigest()
    def get(self, url, *, params=None, source=''):
        key = json.dumps([url, params or {}], sort_keys=True, ensure_ascii=False)
        self.calls.append({'url':url, 'params':params or {}, 'source':source})
        row = self.responses.get(key, self.responses.get(url))
        if row is None:
            return 404, None, ''
        return row.get('http_status',200), row.get('payload'), row.get('error','')


class HttpTransport:
    """Fixed registry origins, bounded bytes, no redirects or implicit retries."""
    offline = False
    HOSTS = frozenset({'api.crossref.org','api.datacite.org','api.openalex.org',
        'www.ebi.ac.uk','eutils.ncbi.nlm.nih.gov','export.arxiv.org','api.semanticscholar.org'})
    def __init__(self, *, client, deadline, check, headers=None, cache_namespace=''):
        self.client, self.deadline, self.check = client, deadline, check
        self.headers = dict(headers or {'User-Agent':'CitrusScholar/2.0 (reference evidence)'})
        self._cache_namespace = cache_namespace
    @property
    def cache_namespace(self):
        return hashlib.sha256(json.dumps(
            [self.headers, self._cache_namespace], sort_keys=True).encode()).hexdigest()
    def get(self, url, *, params=None, source=''):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in self.HOSTS
                or parsed.username or parsed.password or parsed.port not in (None,443)):
            return 0, None, 'network_scope_denied'
        if not callable(self.check):
            return 0, None, 'operation_context_required'
        self.check()
        remaining = self.deadline-time.monotonic()
        if remaining <= 0:
            return 0,None,'deadline'
        try:
            with self.client.stream('GET',url,params=params,headers=self.headers,
                    timeout=min(3.0,remaining),follow_redirects=False) as response:
                if response.status_code != 200:
                    return response.status_code,None,'' if response.status_code == 404 else 'http_'+str(response.status_code)
                chunks, size = [],0
                for chunk in response.iter_bytes():
                    self.check()
                    if time.monotonic() >= self.deadline:
                        return 0,None,'deadline'
                    size += len(chunk)
                    if size > 4*1024*1024:
                        return 0,None,'response_too_large'
                    chunks.append(chunk)
                text=b''.join(chunks).decode('utf-8',errors='replace')
                if text.lstrip().startswith('<'):
                    return 200,text,''
                try:
                    return 200,json.loads(text),''
                except ValueError:
                    return 0,None,'invalid_response'
        except AuditStopped:
            raise
        except Exception as error:
            return 0,None,type(error).__name__


class _ControlledTransport:
    def __init__(self, limits, evidence):
        self.limits,self.evidence=limits,evidence

    def _callback(self, callback):
        if not callable(callback):
            return
        try:
            callback()
        except Exception as error:
            if isinstance(error, AuditStopped) or type(error).__name__ == 'BudgetExceeded':
                raise
            code = str(error) if str(error) in {'canceled', 'cancelled', 'deadline',
                'lease_lost', 'request_budget_exhausted'} else 'operation_stopped'
            raise AuditStopped(code) from error

    def get(self,url,*,params=None,source=''):
        limits=self.limits
        self._callback(limits.check)
        if time.monotonic() >= limits.deadline:
            raise AuditStopped('deadline')
        if not getattr(limits.transport,'offline',False) and (
                not limits.authorized or not callable(limits.check) or not callable(limits.debit)):
            return 0,None,'not_authorized'
        self._callback(limits.debit)  # reserve before dispatch, including failures
        self._callback(limits.check)  # a budget hook may itself cancel the operation
        try:
            status,payload,error=limits.transport.get(url,params=params,source=source)
        except AuditStopped:
            raise
        except Exception as exc:
            status,payload,error=0,None,type(exc).__name__
        if status not in (200, 404) and not error:
            error = 'http_'+str(status) if isinstance(status, int) and 100 <= status <= 599 else 'invalid_response'
        body=json.dumps(payload,ensure_ascii=False,sort_keys=True,default=str).encode()
        self.evidence.append({'provider':source,'url':url,'query':params or {},'http_status':status,
            'error':error,'response_sha256':hashlib.sha256(body).hexdigest(),
            'response_bytes':len(body),'ref':'http:'+hashlib.sha256(body+url.encode()).hexdigest()})
        self._callback(limits.check)
        if time.monotonic() >= limits.deadline:
            raise AuditStopped('deadline')
        return status,payload,error


def lookup_evidence(claims, limits: LookupLimits):
    if not isinstance(limits,LookupLimits):
        raise TypeError('Explicit LookupLimits required')
    doi=p.normalize_doi(claims.get('doi'))
    title=str(claims.get('title') or claims.get('query') or '').strip()
    arxiv=str(claims.get('arxiv_id') or '')
    routes={
        'Crossref':lambda:p._crossref_work(doi) if doi else p._crossref_search(title),
        'DataCite':lambda:p._datacite_work(doi) if doi else p._datacite_arxiv(arxiv),
        'OpenAlex':lambda:p._openalex_work(doi) if doi else p._openalex_search(title),
        'Europe PMC':lambda:p._epmc_doi(doi) if doi else p._epmc_search(title),
        'PubMed':lambda:p._pubmed_search(doi or title),
        'arXiv':lambda:p._arxiv_by_id(arxiv) if arxiv else p._arxiv_by_title(title),
        'Semantic Scholar':lambda:p._s2_doi(doi) if doi else p._s2_search(title),
    }
    records,states,errors,evidence=[],{},{},[]
    with p.using_transport(_ControlledTransport(limits,evidence)):
        for name in limits.providers:
            applicable = ((doi or arxiv) if name == 'DataCite' else
                          (arxiv or title) if name == 'arXiv' else (doi or title))
            if name not in routes or not applicable:
                states[name]='skipped'
                continue
            if not getattr(limits.transport,'offline',False) and not limits.authorized:
                states[name]='not_authorized'
                continue
            try:
                status,rows,error=routes[name]()
                if error:
                    states[name]='not_authorized' if error=='not_authorized' else 'error'
                    errors[name]={'code':error,'message':'Provider evidence could not be collected.',
                                  'retryable':error in {'timeout','deadline','http_429','http_500','http_502','http_503','http_504'}}
                else:
                    states[name]='ok'
                    records.extend(rows)
            except Exception as error:
                code=str(error) if type(error).__name__ in {'AuditStopped','BudgetExceeded'} else type(error).__name__
                states[name]='error';errors[name]={'code':code,
                    'message':'Provider evidence could not be collected.','retryable':False}
                if type(error).__name__ in {'AuditStopped','BudgetExceeded'}:
                    break
    return {'records':records,'providers':states,'errors':errors,
        'evidence_refs':[row['ref'] for row in evidence],'http_evidence':evidence,
        'fetched_at':datetime.now(timezone.utc).isoformat()}

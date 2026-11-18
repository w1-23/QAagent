"""Generic scholarly identity search for the General Agent.

One capability, many registries. Callers pass whatever they have (natural
language query, title, DOI, arXiv id, authors, year) and receive structured
candidates with provenance, identifier match flags and per-source errors.

The module *reports* evidence. It never decides that a citation is "verified":
that judgement stays with the agent, which can search again, fetch a publisher
page, or ask the user. The only hard rule enforced here is honesty - a record
is only reported when a registry actually returned it. Network failures are
returned as errors, never as "not found".
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from urllib.parse import quote
import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Iterable

import httpx

from citrus_refcheck import providers as _registry
from citrus_refcheck.providers import (
    _DOI_PATTERN, _ARXIV_ID_PATTERN, _TRANSLITERATE, _DASHES,
    normalize_doi, norm_title, title_similarity, first_text,
    _rec, _crossref_record, _openalex_record, _epmc_records, _arxiv_entries,
    _fold, _author_conflict, _surname_initials, _name_tokens,
)

TIMEOUT = 8.0
RETRIES = 1
POOL = 6
# Hard wall-clock budget for one call: return the evidence that arrived,
# report stragglers as "deadline" instead of making the caller wait.
DEADLINE = 6.0
# Only controls whether a candidate is worth *showing*; it never marks a
# citation as verified.
CANDIDATE_FLOOR = 0.55
SOURCE_PRIORITY = (
    "Crossref", "Europe PMC", "PubMed", "OpenAlex", "arXiv", "DataCite", "Semantic Scholar",
)
_CJK = re.compile(r"[\u4e00-\u9fff]")

_MIN_INTERVAL = {"pubmed": 0.34, "arxiv": 3.0}
_last_call: dict[str, float] = {}
_rate_lock = threading.Lock()


# ---------------------------------------------------------------- helpers
def _headers() -> dict[str, str]:
    mailto = str(os.environ.get("CITRUSQA_CROSSREF_MAILTO") or "").strip()
    agent = "CitrusQA/1.0 (scholarly identity search"
    if mailto:
        agent += f"; mailto:{mailto}"
    return {"User-Agent": agent + ")", "Accept": "application/json, application/atom+xml;q=0.9, */*;q=0.8"}


def _throttle(source: str) -> None:
    gap = _MIN_INTERVAL.get(source)
    if not gap:
        return
    with _rate_lock:
        wait = _last_call.get(source, 0.0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[source] = time.time()


@dataclass
class LookupContext:
    """Bounded audit lookup; no executor, detached requests or implicit retries."""
    deadline: float
    check: Any = None
    debit: Any = None
    evidence: list = field(default_factory=list)


_LOOKUP_CONTEXT: ContextVar[LookupContext | None] = ContextVar('reference_lookup', default=None)


@contextmanager
def bounded_lookup(*, deadline: float, check=None, debit=None):
    context = LookupContext(deadline, check, debit)
    token = _LOOKUP_CONTEXT.set(context)
    try:
        yield context
    finally:
        _LOOKUP_CONTEXT.reset(token)


def _bounded_get(context, url, params, source):
    started = time.time()
    evidence = {'provider': source, 'url': url, 'query': params or {},
                'checked_at': started, 'status': 'not_checked'}
    context.evidence.append(evidence)
    try:
        if context.check:
            context.check()
        remaining = context.deadline - time.monotonic()
        if remaining <= 0:
            evidence.update(status='deadline', error='deadline')
            return 0, None, 'deadline'
        # Rate spacing is part of the same deadline; never sleep while holding
        # the global lock, and poll cancellation while waiting.
        gap = _MIN_INTERVAL.get(source, 0)
        while True:
            with _rate_lock:
                pause = _last_call.get(source, 0) + gap - time.time()
                if pause <= 0:
                    _last_call[source] = time.time()
                    break
            if context.check:
                context.check()
            remaining = context.deadline - time.monotonic()
            if remaining <= 0:
                evidence.update(status='deadline', error='deadline')
                return 0, None, 'deadline'
            time.sleep(min(.05, pause, remaining))
        remaining = context.deadline - time.monotonic()
        if remaining <= 0:
            evidence.update(status='deadline', error='deadline')
            return 0, None, 'deadline'
        if context.debit:
            context.debit()  # durable charge before dispatch, including failures
        chunks, size = [], 0
        evidence['status'] = 'dispatched'
        with httpx.stream('GET', url, params=params, follow_redirects=False,
                          timeout=min(3.0, remaining), headers=_headers()) as response:
            evidence['http_status'] = response.status_code
            if response.status_code != 200:
                error = '' if response.status_code == 404 else f'http_{response.status_code}'
                evidence.update(status='not_found' if response.status_code == 404 else 'error', error=error)
                return response.status_code, None, error
            for chunk in response.iter_bytes():
                if context.check:
                    context.check()
                if time.monotonic() >= context.deadline:
                    evidence.update(status='deadline', error='deadline')
                    return 0, None, 'deadline'
                size += len(chunk)
                if size > 4 * 1024 * 1024:
                    evidence.update(status='error', error='response_too_large')
                    return 0, None, 'response_too_large'
                chunks.append(chunk)
            raw = b''.join(chunks)
            evidence.update(response_sha256=hashlib.sha256(raw).hexdigest(), response_bytes=len(raw))
            text = raw.decode('utf-8', errors='replace')
            if 'atom' in response.headers.get('content-type', '') or text.lstrip().startswith('<?xml'):
                payload = text
            else:
                try:
                    payload = json.loads(text)
                except ValueError:
                    evidence.update(status='error', error='invalid_response')
                    return 0, None, 'invalid_response'
            evidence['status'] = 'returned'
            return 200, payload, ''
    except httpx.HTTPError as exc:
        error = 'timeout' if isinstance(exc, httpx.TimeoutException) else type(exc).__name__
        evidence.update(status='error', error=error)
        return 0, None, error
    finally:
        if evidence['status'] == 'dispatched':
            evidence['status'] = 'interrupted'
        evidence['elapsed_ms'] = int((time.time() - started) * 1000)


def _get(url: str, params: dict | None = None, timeout: float = TIMEOUT,
         source: str = "") -> tuple[int | str, Any, str]:
    """Return (status, payload, error). ``status`` is 200 or an error token."""
    context = _LOOKUP_CONTEXT.get()
    if context is not None:
        return _bounded_get(context, url, params, source)
    _throttle(source)
    last_error = ""
    for attempt in range(RETRIES + 1):
        try:
            response = httpx.get(url, params=params, timeout=timeout,
                                 follow_redirects=True, headers=_headers())
            if response.status_code == 200:
                if "atom" in response.headers.get("content-type", "") or response.text.lstrip().startswith("<?xml"):
                    return 200, response.text, ""
                try:
                    return 200, response.json(), ""
                except ValueError:
                    return 200, response.text, ""
            if response.status_code in (400, 404):
                return response.status_code, None, ""
            if response.status_code == 429:
                last_error = "http_429"
            elif response.status_code in (401, 403):
                return response.status_code, None, "http_blocked"
            else:
                last_error = f"http_{response.status_code}"
        except Exception as exc:  # noqa: BLE001 - network must not raise into tools
            last_error = "timeout" if "imeout" in type(exc).__name__ else type(exc).__name__
        if attempt < RETRIES:
            time.sleep(0.5 * (attempt + 1))
    return 0, None, last_error or "network_error"


# ---------------------------------------------------------------- registries
class _HostTransport:
    """Inject host HTTP controls into the package's pure provider mappings.

    Resolve _get on every dispatch so existing caller/test patches still apply.
    Context-local injection also keeps simultaneous provider calls independent.
    """
    def get(self, url, *, params=None, source=''):
        status, payload, error = _get(url, params=params, source=source)
        if status not in (200, 404) and not error:
            error = f'http_{status}' if isinstance(status, int) and 100 <= status <= 599 else 'invalid_response'
        if error:
            return 0 if status == 200 else status, None, error
        if status == 200 and not isinstance(payload, str if source == 'arxiv' else dict):
            return 0, None, 'invalid_provider_payload'
        return status, payload, ''


def _provider_call(function, *args, **kwargs):
    with _registry.using_transport(_HostTransport()):
        status, records, error = function(*args, **kwargs)
    if status not in (200, 404) and not error:
        error = f'http_{status}' if isinstance(status, int) and 100 <= status <= 599 else 'invalid_response'
    # An errored response cannot supply usable registry records.
    return status, records if status == 200 and not error else [], error


def _crossref_work(doi: str) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._crossref_work, doi)


def _crossref_search(query: str, rows: int = 8) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._crossref_search, query, rows)


def _datacite_work(doi: str) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._datacite_work, doi)


def _datacite_arxiv(arxiv_id: str) -> tuple[int | str, list[dict], str]:
    """Compose the host _datacite_work seam with an arXiv identity alias.

    Registry payload parsing belongs to the package; this small host route keeps
    existing overrides of _datacite_work effective for the arXiv fallback too.
    """
    status, records, error = _datacite_work(f"10.48550/arXiv.{arxiv_id}")
    for record in records:
        record["arxiv_id"] = arxiv_id
        record["source"] = "DataCite (arXiv DOI)"
        if not record.get("venue"):
            record["venue"] = "arXiv"
    return status, records, error


def _openalex_work(doi: str) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._openalex_work, doi)


def _openalex_search(query: str, per_page: int = 8) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._openalex_search, query, per_page)


def _epmc_search(query: str, page_size: int = 8) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._epmc_search, query, page_size)


def _epmc_doi(doi: str) -> tuple[int | str, list[dict], str]:
    return _epmc_search(f'DOI:"{doi}"', page_size=3)


def _pubmed_search(query: str, retmax: int = 6) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._pubmed_search, query, retmax)


def _arxiv_by_id(arxiv_id: str) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._arxiv_by_id, arxiv_id)


def _arxiv_by_title(title: str, max_results: int = 5) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._arxiv_by_title, title, max_results)


def _s2_doi(doi: str) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._s2_doi, doi)


def _s2_search(title: str, limit: int = 5) -> tuple[int | str, list[dict], str]:
    return _provider_call(_registry._s2_search, title, limit)


# ---------------------------------------------------------------- aggregation
def _translate(query: str) -> str | None:
    """Normalize a search query; absence means blocked, never native fallback."""
    from src.retrieval.local_query_language import normalize_search_query

    normalized, _info = normalize_search_query(query)
    return normalized


def _dedupe(records: Iterable[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for record in records:
        if not record.get("title"):
            continue
        key = record["doi"] or (f"arxiv:{record['arxiv_id']}" if record["arxiv_id"] else "") \
            or norm_title(record["title"])
        existing = seen.get(key)
        if existing is None:
            seen[key] = record
            continue
        # keep the first (higher-priority source) but retain extra identifiers
        for field in ("arxiv_id", "doi"):
            if not existing.get(field) and record.get(field):
                existing[field] = record[field]
        if record.get("venue") and not existing.get("venue"):
            existing["venue"] = record["venue"]
        existing["also_in"] = list(dict.fromkeys((existing.get("also_in") or []) + [record["source"]]))
    return list(seen.values())


def _annotate(record: dict, query: str, doi: str, arxiv_id: str,
              authors: str, year: str, claimed_title: str = "") -> dict:
    record = dict(record)
    # ``claimed_title`` is the title as written in the citing document. It is the
    # only signal available when the caller supplies a bare DOI, and it is a
    # stricter reference than the (possibly absent) free-text query.
    reference_title = str(claimed_title or "").strip() or str(query or "").strip()
    record["title_similarity"] = round(title_similarity(reference_title or record["title"], record["title"]), 3)
    record["identifier_match"] = bool(
        (doi and record["doi"] == normalize_doi(doi))
        or (arxiv_id and record["arxiv_id"] == arxiv_id))
    conflicts = []
    if record["identifier_match"]:
        if reference_title and norm_title(reference_title):
            if record["title_similarity"] < 0.82:
                conflicts.append(f"title:{record['title_similarity']}")
            elif norm_title(reference_title) != norm_title(record["title"]):
                # Same identifier but the title is not byte-identical after
                # normalisation (a dropped tail clause scores ~0.96): that is a
                # rewritten title, not a match.
                conflicts.append(f"title_variant:{record['title_similarity']}")
        if _author_conflict(authors, record["authors"]):
            conflicts.append("authors")
        if year and record["year"]:
            supplied_year = str(year)[:4]
            if supplied_year.isdigit() and str(record["year"])[:4].isdigit():
                # 在线优先/纸本年份在不同登记库之间常差 1 年，不算篡改。
                if abs(int(supplied_year) - int(str(record["year"])[:4])) > 1:
                    conflicts.append(f"year:{supplied_year}->{record['year']}")
            elif supplied_year != str(record["year"])[:4]:
                conflicts.append(f"year:{supplied_year}->{record['year']}")
    record["field_conflicts"] = conflicts
    return record


def _web_candidate(query: str, year: str) -> tuple[list[dict], str]:
    """Optional fallback: let the configured web search surface a DOI, then
    confirm it against a registry. Results are candidates, never confirmations."""
    try:
        from src.tools.deepseek_search_fetch import deepseek_search
    except Exception as exc:  # noqa: BLE001
        return [], f"web:{type(exc).__name__}"
    try:
        text = str(deepseek_search.invoke({"query": " ".join(p for p in (query[:160], year, "doi") if p),
                                           "count": 6}))
    except Exception as exc:  # noqa: BLE001
        return [], f"web:{type(exc).__name__}"
    if text.startswith("[WEB_"):
        return [], text.splitlines()[0][:120]
    found: list[dict] = []
    for raw in list(dict.fromkeys(normalize_doi(x) for x in _DOI_PATTERN.findall(text)))[:3]:
        if not raw:
            continue
        status, records, _ = _crossref_work(raw)
        if status == 200 and records:
            record = records[0]
            record["source"] = "Web search"
            if title_similarity(query, record["title"]) >= 0.75:
                found.append(record)
    return found, "" if found else "web:no_matching_doi"


def search_literature(query: str = "", doi: str = "", arxiv_id: str = "",
                      authors: str = "", year: str = "", limit: int = 8,
                      sources: str = "", web_fallback: bool = False,
                      claimed_title: str = "") -> dict[str, Any]:
    """Search scholarly records; returns candidates + per-source errors.

    Nothing here decides "verified": callers inspect ``identifier_match`` and
    ``field_conflicts`` and decide what to report.

    ``claimed_title`` is the title *as written in the citing document*. Pass it
    whenever the citation carries a title: an identifier that resolves to a
    record whose title differs after normalisation is a rewritten title, and
    that must show up as a conflict instead of a clean match.
    """
    started = time.time()
    doi = normalize_doi(doi or (query if _DOI_PATTERN.fullmatch(str(query or "").strip()) else ""))
    arxiv_id = arxiv_id or ""
    if not arxiv_id:
        match = _ARXIV_ID_PATTERN.search(str(query or ""))
        arxiv_id = match.group(1) if match else ""
    query = str(query or "").strip()
    if doi and normalize_doi(query) == doi:
        query = ""  # a bare DOI is an identifier, not a title

    wanted = [s.strip().lower() for s in str(sources or "").split(",") if s.strip()]
    errors: dict[str, str] = {}
    notes: list[str] = []
    # Identifier lookups retain their exact bytes and do not depend on title
    # translation. The original query/claimed_title remain comparison metadata.
    search_query = ""
    translated = ""
    if query and not (doi or arxiv_id):
        search_query = _translate(query)
        if not search_query:
            return {"query_used": "", "original_query": query, "translated": "",
                    "candidates": [], "errors": {"translation": "translation_error"},
                    "notes": ["英文检索转换失败；未发送登记库或网页检索。可重试翻译或复用有效翻译缓存。"],
                    "_tool_outcome": {"status": "not_run", "reason_code": "translation_error",
                                      "artifacts": []},
                    "elapsed_ms": int((time.time() - started) * 1000)}
        translated = search_query if search_query != query else ""
    tasks: list[tuple[str, Any]] = []

    def source_allowed(name: str) -> bool:
        return not wanted or name.lower() in wanted or name.lower().replace(" ", "") in wanted

    def add(name: str, fn, *args) -> None:
        if not source_allowed(name):
            return
        tasks.append((name, lambda: fn(*args)))

    if doi:
        # Fast path: the two registries that cover nearly all DOIs. The slower
        # fallbacks below only run when these two return nothing.
        add("Crossref", _crossref_work, doi)
        add("OpenAlex", _openalex_work, doi)
        add("Europe PMC", _epmc_doi, doi)
    if arxiv_id:
        add("arXiv", _arxiv_by_id, arxiv_id)
        # Cheap, fast registry fallback for the same identifier.
        add("DataCite", _datacite_arxiv, arxiv_id)
    for text in [search_query] if search_query else []:
        add("Crossref", _crossref_search, text, 8)
        add("OpenAlex", _openalex_search, text, 8)
        add("Europe PMC", _epmc_search, f'TITLE:"{text[:200]}"', 8)
        add("PubMed", _pubmed_search, f"{text[:200]}[Title]", 6)
        if not arxiv_id and (wanted or "arxiv" in text.lower()):
            add("arXiv", _arxiv_by_title, text, 5)

    records: list[dict] = []
    if tasks:
        pool = ThreadPoolExecutor(max_workers=min(POOL, len(tasks)))
        futures = [(name, pool.submit(fn)) for name, fn in tasks]
        pending = {future: name for name, future in futures}
        deadline = time.time() + DEADLINE
        resolved = False

        def collect(future, name):
            nonlocal resolved
            try:
                status, found, error = future.result()
            except Exception as exc:  # noqa: BLE001
                errors[name] = type(exc).__name__
                return
            if error:
                errors[name] = error
            records.extend(found or [])
            if doi and found:
                resolved = True

        try:
            while pending and not resolved:
                remaining = max(0.0, deadline - time.time())
                done, _ = wait(list(pending), timeout=remaining, return_when=FIRST_COMPLETED)
                if not done:
                    break
                for future in done:
                    name = pending.pop(future)
                    collect(future, name)
        finally:
            stopped_after_hit = resolved
            not_waited = []
            for future, name in pending.items():
                # Preserve failures/results that completed alongside the hit.
                # A sibling we stopped waiting for is not a timed-out provider.
                if future.done():
                    collect(future, name)
                else:
                    future.cancel()
                    if stopped_after_hit:
                        not_waited.append(name)
                    else:
                        errors.setdefault(name, "deadline")
            if not_waited:
                notes.append("DOI 已命中；未继续等待的来源（不计为超时或已完成覆盖）："
                             + "、".join(not_waited))
            pool.shutdown(wait=False, cancel_futures=True)
        if doi and not records:
            for name, lookup in (("DataCite", _datacite_work), ("Semantic Scholar", _s2_doi)):
                if not source_allowed(name):
                    continue
                status, found, error = lookup(doi)
                if found:
                    records.extend(found)
                    break
                if error:
                    errors[name] = error

    merged = _dedupe(records)
    annotated = [_annotate(record, query, doi, arxiv_id, authors, year,
                                 claimed_title=claimed_title) for record in merged]
    if (not annotated and web_fallback and not doi and not arxiv_id
            and source_allowed("Crossref") and len(norm_title(search_query)) >= 20):
        # The fallback confirms DOIs at Crossref, so a narrower registry scope
        # cannot acquire that source implicitly. Never reuse native query here.
        web_records, web_error = _web_candidate(search_query, year)
        if web_records:
            notes.append("登记库无命中，网页检索补充了候选 DOI 并已回登记库核对；需人工确认。")
            annotated = [_annotate(record, query, "", "", authors, year,
                                       claimed_title=claimed_title) for record in web_records]
        elif web_error:
            errors["Web search"] = web_error
    annotated.sort(key=lambda r: (0 if r["identifier_match"] else 1,
                                  -float(r["title_similarity"] or 0),
                                  SOURCE_PRIORITY.index(r["source"]) if r["source"] in SOURCE_PRIORITY else 99))
    if query and not annotated and not errors:
        notes.append("登记库没有返回候选；未查到不等于文献不存在。")
    return {
        "query_used": search_query,
        "original_query": query,
        "translated": translated,
        "candidates": annotated[:max(1, min(int(limit or 8), 20))],
        "errors": errors,
        "notes": notes,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def lookup_doi(doi: str) -> tuple[str, dict | None, str]:
    """Backward-compatible single-record lookup: (provider, record, error)."""
    doi = normalize_doi(doi)
    if not doi:
        return "", None, "empty_doi"
    checks = [("Crossref", _crossref_work), ("DataCite", _datacite_work),
              ("OpenAlex", _openalex_work), ("Semantic Scholar", _s2_doi)]
    errors = []
    # Sequential bounded requests avoid executor context-exit waiting for every
    # provider after the first usable result. Audit callers use all evidence.
    context = _LOOKUP_CONTEXT.get()
    with bounded_lookup(deadline=context.deadline if context else time.monotonic() + DEADLINE,
                        check=context.check if context else None,
                        debit=context.debit if context else None):
        for name, lookup in checks:
            status, records, error = lookup(quote(doi, safe='/'))
            if error:
                errors.append(f"{name}:{error}")
            if records:
                return name, records[0], ""
    return "", None, "; ".join(errors)


def evidence_row(record: dict) -> dict[str, str]:
    """Flatten a candidate for the verification spreadsheet."""
    conflicts = record.get("field_conflicts") or []
    return {
        "title": record.get("title", ""),
        "authors": record.get("authors", ""),
        "year": record.get("year", ""),
        "venue": record.get("venue", ""),
        "doi": record.get("doi", ""),
        "arxiv_id": record.get("arxiv_id", ""),
        "type": record.get("type", ""),
        "source": record.get("source", ""),
        "url": record.get("url", ""),
        "title_similarity": record.get("title_similarity"),
        "identifier_match": record.get("identifier_match", False),
        "conflicts": "；".join(conflicts),
    }

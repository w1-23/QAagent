"""Bounded same-paper races. Local/web search are complementary, not racers."""
from __future__ import annotations
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
import time

_POOL=ThreadPoolExecutor(max_workers=8,thread_name_prefix='citrus-fetch')


@dataclass(frozen=True)
class FetchCandidate:
    name: str
    call: object


def fetch_race(candidates,*,check,deadline,concurrency=2,min_chars=1500):
    if not callable(check):raise ValueError('cancellation_context_required')
    if type(concurrency)is not int or not 1<=concurrency<=8:raise ValueError('invalid_race_concurrency')
    if type(min_chars)is not int or min_chars<1:raise ValueError('invalid_minimum_text')
    candidates=list(candidates)
    if len(candidates)>32 or len({c.name for c in candidates})!=len(candidates):
        raise ValueError('invalid_race_candidates')
    attempts={c.name:{'source':c.name,'status':'not_started'} for c in candidates}
    pending=list(candidates);active={};winner=None
    started=time.monotonic()
    def submit():
        while pending and len(active)<concurrency:
            check();candidate=pending.pop(0)
            active[_POOL.submit(candidate.call)]=candidate
            attempts[candidate.name]['status']='running'
    try:
        submit()
        while active:
            check();remaining=deadline-time.monotonic()
            if remaining<=0:break
            done,_=wait(active,timeout=min(.05,remaining),return_when=FIRST_COMPLETED)
            for future in done:
                candidate=active.pop(future)
                try:
                    value=future.result()
                    text=str(value.get('text') or '') if isinstance(value,dict) else ''
                    if len(text)>=min_chars:
                        attempts[candidate.name]['status']='success'
                        if winner is None:winner={**value,'source':candidate.name}
                        continue
                    attempts[candidate.name]['status']='no_match'
                except Exception as error:
                    attempts[candidate.name].update(status='error',reason_code=type(error).__name__)
            if winner:break
            submit()
        check()
    finally:
        for future,candidate in active.items():
            canceled=future.cancel()
            attempts[candidate.name]['status']=(
                'cancelled_after_success' if canceled else 'not_awaited_after_success') if winner else 'timeout'
        for candidate in pending:
            attempts[candidate.name]['status']='cancelled_after_success' if winner else 'timeout'
    return {'status':'ok' if winner else 'no_match','winner':winner,'attempts':list(attempts.values()),
        'elapsed_ms':round((time.monotonic()-started)*1000),'http_termination_claimed':False}


def search_web(query,*,search,check,debit,authorized=False):
    if not authorized or not all(callable(x) for x in (search,check,debit)):
        return {'status':'not_authorized','records':[]}
    check();debit()
    records=search(query)  # exact original query; no local-language gate
    check()
    return {'status':'ok','query':query,'records':records}

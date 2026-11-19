"""Exercise the production HostTools, registry and durable retry ledger."""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from types import SimpleNamespace

import pytest

from src.agent.host_tools import HostTools
from src.agent.identity import ExecutionIdentity
from src.browse.relay import BrowseError
from src.code_execution.egress_policy import normalize_https_url
from src.core import jobs, progress_bus, task_events, tool_results, tracing, turn_source_ledger
from src.core.cancel import CancelToken, bound_token
from src.core.execution_ledger import ExecutionLedger, bind_execution_ledger, reset_execution_ledger
from src.core.task_result import effective_tool_records, resolved_tool_attempts
from src.core.turn_outcome import CompletionEnvelope, CompletionGoal, FinalReview, completion_errors
from src.session import manager
from src.tools import registry

browse = importlib.import_module('src.tools.browse_web')
URL = 'https://example.test/catalog'
TAB = 't-123456789abc'


class TestRenderer:
    __test__ = False
    unit = 'fixture-renderer'
    engine = '159.0'

    def __init__(self):
        self.actions = 0
        self.fail = True
        self.relay = SimpleNamespace(authorize=lambda url: normalize_https_url(url.split('#', 1)[0]))

    async def exchange(self, command, *, expected_actions=None):
        if expected_actions is not None and expected_actions != self.actions:
            raise BrowseError('browse_retry_rejected')
        self.actions += 1
        await asyncio.sleep(0)
        if self.fail:
            target = {'retry_target': ''} if command['action'] == 'new_tab' else {'tab_id': TAB}
            return {'error': 'browse_page_action_failed', '_action_sequence': self.actions, **target}
        return {'url': URL, 'text': 'fixture', 'tab_id': TAB, '_action_sequence': self.actions}


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs, '_DB_PATH', tmp_path / 'jobs.db')
    monkeypatch.setattr(manager, 'DB_PATH', tmp_path / 'history.db')
    monkeypatch.setattr(tool_results, '_schema_ready', False)
    monkeypatch.setattr(turn_source_ledger, '_schema_ready', False)
    monkeypatch.setattr(registry, '_TOOL_SPECS', {})
    registry.register_tool(browse.browse_web, write_scope='session_artifact')
    renderer = TestRenderer()

    async def renderer_for(identity, *, create):
        return renderer

    async def allow_fixture(name, args):
        return None

    monkeypatch.setattr(browse, 'enabled', lambda: True)
    monkeypatch.setattr(browse, 'renderer_for', renderer_for)
    monkeypatch.setattr(registry, '_check_tool_sandbox', allow_fixture)

    def host(owner=111, session='retry-session'):
        claim = jobs.claim_job(session, 'server-turn', owner_user_id=owner,
                               client_request_id='client-turn', payload={})
        identity = ExecutionIdentity(owner, session, 'server-turn', claim.job_id)
        tracing.set_user_id(owner)
        tracing.set_session_id(session)
        tracing.set_job_id(claim.job_id)
        tracing.set_web_search_enabled(True)
        progress_bus.set_turn_scope(session, 'client-turn')
        return HostTools(identity, {})

    ledger_token = bind_execution_ledger(ExecutionLedger())
    try:
        yield host, renderer
    finally:
        reset_execution_ledger(ledger_token)
        tracing.set_user_id(None)
        tracing.set_session_id('')
        tracing.set_job_id('')
        tracing.set_web_search_enabled(False)
        progress_bus.set_turn_scope('')


async def run(host, call_id, **args):
    call = {'id': call_id, 'name': 'browse_web', 'args': {'action': 'open', 'url': URL, **args}}
    result = await host.invoke(browse.browse_web, call, host.identity)
    return await host.publish(call, result, host.identity)


def blockers(records):
    statement = CompletionEnvelope(schema_version=1, answer='Opened the catalog.', status='completed',
        goals=[CompletionGoal(goal='Open catalog', kind='answer', evidence_call_ids=['second'], artifact_links=[])],
        remaining=[], incidental_partials=[],
        review=FinalReview(**{key: 'Reviewed.' for key in ('facts', 'sources', 'numbers', 'conditions', 'delivery')}))
    return [error for error in completion_errors(statement, records) if error.startswith('execution_blocker')]


def test_same_arguments_keep_both_operations(runtime):
    make_host, renderer = runtime
    host = make_host()

    async def exercise():
        await run(host, 'first')
        renderer.fail = False
        await run(host, 'second')

    asyncio.run(exercise())
    assert len({row['logical_key'] for row in host.records}) == 2
    assert [row['attempt'] for row in host.records] == [1, 1]
    assert len(effective_tool_records(host.records)) == 2
    assert blockers(host.records) and not resolved_tool_attempts(host.records)


@pytest.mark.parametrize('action', ['open', 'new_tab'])
def test_explicit_retry_survives_restore_and_keeps_original_bytes(runtime, action):
    make_host, renderer = runtime
    host = make_host()

    async def exercise():
        first = await run(host, 'first', action=action)
        retry = json.loads(first.content)['host_browse_retry']
        assert 'host_browse_retry' not in json.loads(tool_results.fetch('first')['result_text'])
        renderer.fail = False
        second = await run(host, 'second', action=action, url=URL + '#section', **retry)
        assert second.additional_kwargs['operation_id'] != retry['retry_of']
        return first, second

    first, second = asyncio.run(exercise())
    assert [row['status'] for row in host.records] == ['failed', 'complete']
    assert [row['attempt'] for row in host.records] == [1, 2]
    assert not blockers(host.records)
    restored = HostTools(host.identity, {})
    restored.restore_observation(first)
    restored.restore_observation(second)
    assert not blockers(restored.records)
    assert resolved_tool_attempts(restored.records) == [
        {'tool_call_id': 'first', 'resolved_by_tool_call_id': 'second', 'resolution': 'repaired'}]


def test_retry_claim_is_exclusive(runtime):
    make_host, renderer = runtime
    host = make_host()

    async def exercise():
        first = await run(host, 'first')
        retry = json.loads(first.content)['host_browse_retry']
        renderer.fail = False
        return await asyncio.gather(run(host, 'second', **retry), run(host, 'third', **retry))

    responses = asyncio.run(exercise())
    assert [item.artifact['_tool_outcome']['status'] for item in responses].count('complete') == 1
    assert renderer.actions == 2
    assert len(resolved_tool_attempts(host.records)) == 1


def test_false_cross_scope_and_stale_retries_do_not_navigate(runtime):
    make_host, renderer = runtime
    host = make_host()

    async def exercise():
        first = await run(host, 'first')
        retry = json.loads(first.content)['host_browse_retry']
        wrong = await run(host, 'wrong', url=URL + '/other', **retry)
        assert wrong.artifact['_tool_outcome']['reason_code'] == 'browse_retry_rejected'
        other = make_host(222, 'other-session')
        cross = await run(other, 'cross', **retry)
        assert cross.artifact['_tool_outcome']['reason_code'] == 'browse_retry_rejected'
        make_host()
        renderer.actions += 1
        stale = await run(host, 'stale', **retry)
        assert stale.artifact['_tool_outcome']['reason_code'] == 'browse_retry_rejected'

    asyncio.run(exercise())
    assert renderer.actions == 2
    assert not resolved_tool_attempts(host.records)


@pytest.mark.parametrize('ineligible', ['failed', 'cancelled', 'unauthorized'])
def test_unsuccessful_retry_does_not_hide_parent(runtime, ineligible):
    make_host, renderer = runtime
    host = make_host()

    async def exercise():
        first = await run(host, 'first')
        retry = json.loads(first.content)['host_browse_retry']
        if ineligible == 'unauthorized':
            tracing.set_web_search_enabled(False)
        token = CancelToken()
        if ineligible == 'cancelled':
            token.cancel('user')
        with bound_token(token):
            await run(host, 'second', **retry)

    asyncio.run(exercise())
    assert renderer.actions == (2 if ineligible == 'failed' else 1)
    assert blockers(host.records) and not resolved_tool_attempts(host.records)


def test_browse_cards_use_only_verified_navigation_evidence(runtime, monkeypatch):
    from src.core.turn_sources import sources_for_request

    make_host, renderer = runtime
    host = make_host()
    raw_url = URL + '?private-marker=1'
    text = 'Observed page text'
    network_digest = hashlib.sha256(b'<main>Observed page text</main>').hexdigest()

    async def observed(command, *, expected_actions=None):
        result = {'url': raw_url, 'title': 'Fixture catalog', 'text': text,
                  'tab_id': TAB, '_action_sequence': 1}
        if command['action'] == 'open':
            result['_navigation_response'] = {'url': raw_url, 'http_status': 200,
                'received_at': '2026-11-19T00:00:00+00:00', 'response_sha256': network_digest}
        return result

    monkeypatch.setattr(renderer, 'exchange', observed)
    asyncio.run(run(host, 'unverified', action='observe'))
    assert not sources_for_request(host.identity.session_id, 'client-turn')
    asyncio.run(run(host, 'verified', url=raw_url))
    rows = sources_for_request(host.identity.session_id, 'client-turn')
    assert len(rows) == 1 and rows[0]['url'] == URL
    evidence = json.loads(tool_results.fetch('verified')['result_text'])['source_evidence']
    assert evidence['rendered_text_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert evidence['response_sha256'] == network_digest and evidence['http_status'] == 200

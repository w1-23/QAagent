"""Deterministic boundary tests for the networkless Browse runtime.

Covers the fixed Chromium controller's tab/reference lifecycle, host session
queue reclamation, trusted-relay download rejection, and download publication
semantics.  Every fixture is in-process: no browser, no sockets, no external
calls, and no runtime state is written.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import re
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.browse import relay as transport
from src.browse import service

_URL = 'https://relay.example/start'
_REF = 'a' * 12


# ---------------------------------------------------------------------------
# Fixed Chromium controller (sandbox.py) with an injected Playwright surface.
# ---------------------------------------------------------------------------

def _load_sandbox():
    """Import sandbox.py against a minimal, in-memory Playwright surface."""
    playwright = types.ModuleType('playwright')
    sync_api = types.ModuleType('playwright.sync_api')

    class Error(Exception):
        pass

    class TimeoutError(Exception):  # noqa: A001 - mirrors playwright.sync_api
        pass

    def sync_playwright():  # pragma: no cover - the fixture never starts a browser
        raise RuntimeError('the fixture never starts a browser')

    sync_api.Error = Error
    sync_api.TimeoutError = TimeoutError
    sync_api.sync_playwright = sync_playwright
    playwright.sync_api = sync_api

    saved = {name: sys.modules.get(name) for name in ('playwright', 'playwright.sync_api')}
    saved_path = list(sys.path)
    sys.modules['playwright'] = playwright
    sys.modules['playwright.sync_api'] = sync_api
    try:
        sys.modules.pop('src.browse.sandbox', None)
        module = importlib.import_module('src.browse.sandbox')
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
        sys.path[:] = saved_path
    return module


sandbox = _load_sandbox()


class _Response:
    def __init__(self, status=200):
        self.status = status


class _Element:
    def __init__(self, *, label='element', role='button', href='', connected=True):
        self.label = label
        self.role = role
        self.href = href
        self.connected = connected
        self.disposed = False
        self.clicks = 0
        self.click_error = None

    def evaluate(self, script, arg=None):
        if 'isConnected' in script:
            return self.connected
        if 'localName' in script:
            return self.href if self.role == 'a' else ''
        if script.strip().startswith('entry =>'):
            return {'label': self.label, 'role': self.role, 'href': self.href}
        return None

    def click(self, timeout=None):
        self.clicks += 1
        if self.click_error is not None:
            raise self.click_error

    def dispose(self):
        self.disposed = True

    def as_element(self):
        return self


class _EntryHandle:
    def __init__(self, element):
        self.element = element
        self.disposed = False

    def get_property(self, name):
        return self.element if name == 'node' else None

    def evaluate(self, script, arg=None):
        return {'label': self.element.label, 'role': self.element.role,
                'href': self.element.href}

    def dispose(self):
        self.disposed = True


class _ArrayHandle:
    def __init__(self, elements):
        self._handles = {str(index): _EntryHandle(element)
                         for index, element in enumerate(elements)}
        self.disposed = False

    def get_properties(self):
        return self._handles

    def dispose(self):
        self.disposed = True


class _ViewHandle:
    def __init__(self, elements, text, scroll_y):
        self._array = _ArrayHandle(elements)
        self._text = text
        self._scroll_y = scroll_y
        self.disposed = False

    def get_property(self, name):
        return self._array if name == 'elements' else None

    def evaluate(self, script, arg=None):
        return {'text': self._text, 'scroll_y': self._scroll_y}

    def dispose(self):
        self.disposed = True


class _Page:
    def __init__(self):
        self.url = 'about:blank'
        self._title = 'Fixture Page'
        self.goto_status = 200
        self.goto_error = None
        self.events = []
        self.closed = False
        self.elements = []
        self.text = 'fixture text'
        self.scroll = 0

    def on(self, event, handler):
        self.events.append((event, handler))

    def goto(self, url, wait_until=None, timeout=None):
        if self.goto_error is not None:
            raise self.goto_error
        self.url = url
        return _Response(self.goto_status)

    def title(self):
        return self._title

    def evaluate(self, script, arg=None):
        if 'scrollBy' in script:
            self.scroll += arg or 0
        return None

    def evaluate_handle(self, script):
        return _ViewHandle(self.elements, self.text, self.scroll)

    def close(self):
        self.closed = True


class _Context:
    def __init__(self):
        self.pages = []

    def new_page(self):
        page = _Page()
        self.pages.append(page)
        return page


class TabLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.context = _Context()
        self.tabs = sandbox.Tabs(self.context)

    def test_initial_tab_is_listed_and_active(self):
        result = self.tabs.action({'action': 'list_tabs'})
        self.assertEqual(len(result['tabs']), 1)
        self.assertTrue(result['tabs'][0]['active'])
        self.assertRegex(result['tab_id'], r'^t-[a-f0-9]{12}$')

    def test_new_tab_enforces_limit_and_tracks_active(self):
        first = self.tabs.active
        for _ in range(sandbox.MAX_TABS - 1):
            result = self.tabs.action({'action': 'new_tab', 'url': 'https://example.org/page'})
            self.assertNotIn('error', result)
            self.assertRegex(result['tab_id'], r'^t-[a-f0-9]{12}$')
            self.assertEqual(self.tabs.active, result['tab_id'])
        self.assertEqual(len(self.tabs.pages), sandbox.MAX_TABS)
        self.assertNotEqual(self.tabs.active, first)
        rejected = self.tabs.action({'action': 'new_tab', 'url': 'https://example.org/extra'})
        self.assertEqual(rejected, {'error': 'browse_tab_limit'})
        self.assertEqual(len(self.tabs.pages), sandbox.MAX_TABS)

    def test_new_tab_navigation_failure_removes_tab(self):
        active = self.tabs.active
        bad = _Page()
        bad.goto_status = 404
        self.context.new_page = lambda: bad
        result = self.tabs.action({'action': 'new_tab', 'url': 'https://example.org/missing'})
        self.assertEqual(result, {'error': 'browse_page_unavailable', 'retry_target': ''})
        self.assertEqual(len(self.tabs.pages), 1)
        self.assertEqual(self.tabs.active, active)
        self.assertTrue(bad.closed)

    def test_new_tab_exception_removes_tab(self):
        active = self.tabs.active
        bad = _Page()
        bad.goto_error = sandbox.Error('navigation aborted')
        self.context.new_page = lambda: bad
        result = self.tabs.action({'action': 'new_tab', 'url': 'https://example.org/boom'})
        self.assertEqual(result, {'error': 'browse_page_action_failed', 'retry_target': ''})
        self.assertEqual(len(self.tabs.pages), 1)
        self.assertEqual(self.tabs.active, active)
        self.assertTrue(bad.closed)

    def test_last_tab_cannot_close(self):
        result = self.tabs.action({'action': 'close_tab', 'tab_id': self.tabs.active})
        self.assertEqual(result, {'error': 'browse_last_tab'})
        self.assertEqual(len(self.tabs.pages), 1)

    def test_unknown_tab_is_rejected(self):
        for action in ('switch_tab', 'close_tab'):
            with self.subTest(action=action):
                result = self.tabs.action({'action': action, 'tab_id': 't-000000000000'})
                self.assertEqual(result, {'error': 'browse_tab_not_found'})

    def test_closing_active_tab_selects_remaining_tab(self):
        original = self.tabs.active
        opened = self.tabs.action({'action': 'new_tab', 'url': 'https://example.org/second'})
        self.assertEqual(self.tabs.active, opened['tab_id'])
        result = self.tabs.action({'action': 'close_tab', 'tab_id': opened['tab_id']})
        self.assertEqual(result['closed_tab_id'], opened['tab_id'])
        self.assertEqual(result['tab_id'], original)
        self.assertEqual(self.tabs.active, original)
        self.assertEqual(len(self.tabs.pages), 1)

    def test_switch_tab_observes_and_activates(self):
        original = self.tabs.active
        self.tabs.action({'action': 'new_tab', 'url': 'https://example.org/second'})
        result = self.tabs.action({'action': 'switch_tab', 'tab_id': original})
        self.assertEqual(result['tab_id'], original)
        self.assertEqual(self.tabs.active, original)
        self.assertIn('elements', result)


class ReferenceLifecycleTest(unittest.TestCase):
    def _browser(self, elements=None):
        page = _Page()
        page.elements = list(elements or [])
        return sandbox.Browser(page), page

    def test_snapshot_replaces_references_and_disposes_previous(self):
        stale = _Element(label='stale')
        browser, _ = self._browser([
            _Element(label='One', role='a', href='https://example.org/one'),
            _Element(label='Two', role='button'),
        ])
        browser.references = {'deadbeef0000': stale}
        result = browser.snapshot()
        self.assertTrue(stale.disposed)
        self.assertEqual(len(browser.references), 2)
        self.assertTrue(all(re.fullmatch(r'[a-f0-9]{12}', ref) for ref in browser.references))
        self.assertEqual([entry['label'] for entry in result['elements']], ['One', 'Two'])
        self.assertEqual(result['url'], 'about:blank')
        self.assertEqual(result['text'], 'fixture text')

    def test_missing_or_disconnected_reference_is_stale(self):
        browser, _ = self._browser()
        browser.references = {'deadbeef0000': _Element(connected=False)}
        self.assertEqual(browser.action({'action': 'click', 'ref': 'deadbeef0000'}),
                         {'error': 'browse_stale_element'})
        self.assertEqual(browser.action({'action': 'click', 'ref': '000000000000'}),
                         {'error': 'browse_stale_element'})

    def test_download_requires_a_link_href(self):
        browser, _ = self._browser()
        browser.references = {'aaaaaaaaaaaa': _Element(role='button', href='')}
        self.assertEqual(browser.action({'action': 'download', 'ref': 'aaaaaaaaaaaa'}),
                         {'error': 'browse_expected_download_link'})

    def test_download_returns_link_href(self):
        browser, _ = self._browser()
        browser.references = {
            'aaaaaaaaaaaa': _Element(role='a', href='https://example.org/data.csv')}
        self.assertEqual(browser.action({'action': 'download', 'ref': 'aaaaaaaaaaaa'}),
                         {'href': 'https://example.org/data.csv'})

    def test_click_timeout_clears_references(self):
        browser, _ = self._browser()
        element = _Element()
        element.click_error = sandbox.TimeoutError('click timed out')
        browser.references = {'bbbbbbbbbbbb': element}
        self.assertEqual(browser.action({'action': 'click', 'ref': 'bbbbbbbbbbbb'}),
                         {'error': 'browse_click_timeout'})
        self.assertEqual(browser.references, {})
        self.assertTrue(element.disposed)

    def test_invalid_action_is_rejected(self):
        browser, _ = self._browser()
        self.assertEqual(browser.action({'action': 'delete'}),
                         {'error': 'browse_invalid_action'})


# ---------------------------------------------------------------------------
# Host session queue: capacity, busy accounts, timeout, and slot reclamation.
# ---------------------------------------------------------------------------

class _FakeProcess:
    def __init__(self):
        self.returncode = None
        self.exited = asyncio.Event()

    async def wait(self):
        await self.exited.wait()
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def finish(self, code=0):
        self.returncode = code
        self.exited.set()


class _FakeRelay:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _FakeRenderer:
    made = []

    def __init__(self, settings):
        self.settings = settings
        self.unit = f'unit-{len(_FakeRenderer.made)}'
        self.closing = None
        self.actions = 0
        self.startup = None
        self.process = _FakeProcess()
        self.relay = _FakeRelay()
        self.stopped = False
        _FakeRenderer.made.append(self)

    async def start(self):
        return None

    async def stop(self):
        self.stopped = True
        self.process.finish()


class SessionQueueTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _FakeRenderer.made = []
        service._SESSIONS.clear()
        service._WAITERS.clear()
        self.settings = object()
        for patcher in (
            patch.object(service, 'Renderer', _FakeRenderer),
            patch.object(service, '_configured', return_value=self.settings),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        for renderer in list(service._SESSIONS.values()):
            renderer.process.finish()
        for _ in range(3):
            await asyncio.sleep(0)
        service._SESSIONS.clear()
        service._WAITERS.clear()

    async def _until(self, predicate, timeout=2):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0)

    async def test_same_owner_cannot_hold_two_sessions(self):
        await service.renderer_for((1, 's1', 'j1'))
        with self.assertRaisesRegex(service.BrowseError, '^browse_account_busy$'):
            await service.renderer_for((1, 's2', 'j2'))

    async def test_queue_full_rejects_beyond_waiter_cap(self):
        await service.renderer_for((1, 's1', 'j1'))
        await service.renderer_for((2, 's2', 'j2'))
        queued = [asyncio.create_task(service.renderer_for((owner, 's', 'j')))
                  for owner in (3, 4, 5, 6)]
        try:
            await self._until(lambda: len(service._WAITERS) == 4)
            with self.assertRaisesRegex(service.BrowseError, '^browse_queue_full$'):
                await service.renderer_for((7, 's', 'j'))
        finally:
            for task in queued:
                task.cancel()
            await asyncio.gather(*queued, return_exceptions=True)
        self.assertEqual(len(service._WAITERS), 0)

    async def test_queue_timeout_is_bounded(self):
        await service.renderer_for((1, 's1', 'j1'))
        await service.renderer_for((2, 's2', 'j2'))
        with patch.object(service, '_WAIT_SECONDS', 0.1):
            with self.assertRaisesRegex(service.BrowseError, '^browse_queue_timeout$'):
                await service.renderer_for((3, 's3', 'j3'))
        self.assertEqual(len(service._SESSIONS), 2)

    async def test_exited_renderer_reclaims_slot_for_waiter(self):
        first = await service.renderer_for((1, 's1', 'j1'))
        await service.renderer_for((2, 's2', 'j2'))
        waiter = asyncio.create_task(service.renderer_for((3, 's3', 'j3')))
        try:
            await self._until(lambda: (3, 's3', 'j3') in service._WAITERS)
            self.assertNotIn((3, 's3', 'j3'), service._SESSIONS)
            first.process.finish()
            claimed = await asyncio.wait_for(waiter, 3)
        finally:
            if not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
        self.assertIs(service._SESSIONS[(3, 's3', 'j3')], claimed)
        self.assertNotIn((1, 's1', 'j1'), service._SESSIONS)
        self.assertEqual(len(_FakeRenderer.made), 3)

    async def test_release_stops_renderer_and_is_idempotent(self):
        renderer = await service.renderer_for((1, 's1', 'j1'))
        await service.release(1, 's1', 'j1')
        self.assertNotIn((1, 's1', 'j1'), service._SESSIONS)
        self.assertIsNotNone(renderer.closing)
        self.assertTrue(renderer.stopped)
        await service.release(1, 's1', 'j1')

    async def test_missing_session_is_expired_when_create_disabled(self):
        with self.assertRaisesRegex(service.BrowseError, '^browse_session_expired$'):
            await service.renderer_for((9, 's', 'j'), create=False)

    async def test_unconfigured_browse_is_unavailable(self):
        with patch.object(service, '_configured', return_value=None):
            with self.assertRaisesRegex(service.BrowseError, '^browse_unavailable$'):
                await service.renderer_for((9, 's', 'j'))


# ---------------------------------------------------------------------------
# Trusted relay: download rejection, redirect recording, and redirect limit.
# ---------------------------------------------------------------------------

class DownloadRejectionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.relay = transport.Relay(frozenset({'relay.example'}))

    @staticmethod
    def _exchange(url, status=200, headers=None, body=b''):
        return transport.Exchange(url, status, headers or {}, body)

    async def _download_with(self, *exchanges):
        with patch.object(self.relay, '_fetch_before', new=AsyncMock(side_effect=exchanges)):
            return await self.relay.download(_URL)

    async def test_non_200_status_is_rejected(self):
        for status in (204, 404, 500):
            with self.subTest(status=status):
                with self.assertRaisesRegex(transport.BrowseError,
                                            '^browse_download_unavailable$'):
                    await self._download_with(self._exchange(_URL, status, body=b'x'))

    async def test_empty_success_body_is_rejected(self):
        with self.assertRaisesRegex(transport.BrowseError, '^browse_download_unavailable$'):
            await self._download_with(self._exchange(_URL, 200, body=b''))

    async def test_success_returns_exchange_and_no_redirects(self):
        exchange = self._exchange('https://relay.example/data.csv', 200,
                                  {'content-type': 'text/csv'}, b'a,b\n1,2\n')
        result, redirects = await self._download_with(exchange)
        self.assertEqual(result, exchange)
        self.assertEqual(redirects, [])

    async def test_redirect_chain_is_recorded(self):
        first = self._exchange(_URL, 302, {'location': '/final'})
        final = self._exchange('https://relay.example/final', 200, body=b'data')
        result, redirects = await self._download_with(first, final)
        self.assertEqual(result, final)
        self.assertEqual(redirects, [_URL])

    async def test_redirect_limit_is_enforced(self):
        hops = [self._exchange(f'https://relay.example/{index}', 302,
                               {'location': f'/{index + 1}'}) for index in range(6)]
        with self.assertRaisesRegex(transport.BrowseError, '^browse_redirect_limit$'):
            await self._download_with(*hops)


# ---------------------------------------------------------------------------
# Model-facing download: file validation and publication accounting.
# ---------------------------------------------------------------------------

_HAVE_FRAMEWORK = importlib.util.find_spec('langchain_core') is not None


@unittest.skipUnless(_HAVE_FRAMEWORK, 'requires the app framework (langchain_core)')
class DownloadFileValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.module = importlib.import_module('src.tools.browse_web')
        except Exception as exc:  # pragma: no cover - environment guard
            raise unittest.SkipTest(f'browse_web unavailable: {exc}')

    def _file(self, url, content_type, body):
        return self.module._file(
            transport.Exchange(url, 200, {'content-type': content_type}, body))

    def test_supported_types_are_published_with_expected_mime(self):
        cases = [
            ('https://example.org/data.csv', 'text/csv; charset=utf-8', b'a,b\n1,2\n',
             'data.csv', 'text/csv'),
            ('https://example.org/data.tsv', 'text/tab-separated-values', b'a\tb\n',
             'data.tsv', 'text/tab-separated-values'),
            ('https://example.org/notes.txt', 'text/plain', b'\xef\xbb\xbfhello',
             'notes.txt', 'text/plain'),
            ('https://example.org/data.json', 'application/json', b'{"a": 1}',
             'data.json', 'application/json'),
            ('https://example.org/paper.pdf', 'application/pdf', b'%PDF-1.7\n...',
             'paper.pdf', 'application/pdf'),
        ]
        for url, content_type, body, filename, mime in cases:
            with self.subTest(filename=filename):
                self.assertEqual(self._file(url, content_type, body), (filename, mime))

    def test_unsafe_filename_is_rejected(self):
        for url in ('https://example.org/a%b.csv', 'https://example.org/'):
            with self.subTest(url=url), self.assertRaisesRegex(
                    transport.BrowseError, '^browse_filename_unsupported$'):
                self._file(url, 'text/csv', b'a,b\n')

    def test_mismatched_or_unknown_type_is_rejected(self):
        for url, content_type in (('https://example.org/data.csv', 'text/html'),
                                  ('https://example.org/data.exe', 'application/octet-stream')):
            with self.subTest(content_type=content_type), self.assertRaisesRegex(
                    transport.BrowseError, '^browse_download_type_unsupported$'):
                self._file(url, content_type, b'payload')

    def test_content_is_validated_against_declared_type(self):
        cases = [
            ('https://example.org/paper.pdf', 'application/pdf', b'not a pdf'),
            ('https://example.org/data.json', 'application/json', b'{not json'),
            ('https://example.org/notes.txt', 'text/plain', b'a\x00b'),
            ('https://example.org/notes.txt', 'text/plain', b'   <html>'),
        ]
        for url, content_type, body in cases:
            with self.subTest(url=url, body=body), self.assertRaisesRegex(
                    transport.BrowseError, '^browse_download_type_unsupported$'):
                self._file(url, content_type, body)


class _ToolRenderer:
    def __init__(self, href, download):
        self.unit = 'renderer-unit'
        self.engine = '1.2.3'
        self._href = href
        self._download = download
        self.relay = SimpleNamespace(authorize=lambda url: SimpleNamespace(url=url))

    async def exchange(self, command, *, expected_actions=None):
        return {'href': self._href, 'tab_id': 't-aaaaaaaaaaaa'}

    async def download(self, href):
        if isinstance(self._download, BaseException):
            raise self._download
        return self._download


@unittest.skipUnless(_HAVE_FRAMEWORK, 'requires the app framework (langchain_core)')
class DownloadPublicationTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.module = importlib.import_module('src.tools.browse_web')
            cls.artifacts = importlib.import_module('src.research.artifacts')
        except Exception as exc:  # pragma: no cover - environment guard
            raise unittest.SkipTest(f'browse_web unavailable: {exc}')

    async def _download(self, renderer, publish, *, element_ref=_REF):
        calls = []

        def publish_artifact(**kwargs):
            calls.append(kwargs)
            return publish(kwargs)

        async def renderer_for(identity, *, create=True):
            return renderer

        module = self.module
        with patch.object(module, 'enabled', lambda: True), \
                patch.object(module, 'renderer_for', renderer_for), \
                patch.object(module, 'emit_progress', lambda *args, **kwargs: None), \
                patch.object(module.tracing, 'get_user_id', lambda: 7), \
                patch.object(module.tracing, 'get_session_id', lambda: 'session-1'), \
                patch.object(module.tracing, 'get_job_id', lambda: 'job-1'), \
                patch.object(module.tracing, 'web_search_enabled', lambda: True), \
                patch.object(module.tracing, 'get_client_request_id', lambda: 'request-1'), \
                patch.object(self.artifacts, 'publish_artifact', publish_artifact):
            text, artifact = await module.browse_web.coroutine(
                action='download', element_ref=element_ref)
        return text, artifact, calls

    async def test_successful_download_is_published_with_digest(self):
        body = b'a,b\n1,2\n'
        exchange = transport.Exchange('https://relay.example/data.csv', 200,
                                      {'content-type': 'text/csv'}, body)
        renderer = _ToolRenderer('https://relay.example/data.csv',
                                 (exchange, ['https://relay.example/start']))

        def publish(kwargs):
            return {'sha256': kwargs['source_sha256'], 'artifact_url': 'artifact://data',
                    'file_id': 'file-1', 'name': 'data.csv', 'size_bytes': len(body)}

        text, artifact, calls = await self._download(renderer, publish)
        digest = hashlib.sha256(body).hexdigest()
        self.assertEqual(artifact['_tool_outcome']['status'], 'complete')
        self.assertEqual(artifact['_tool_outcome']['artifacts'], ['artifact://data'])
        self.assertEqual(artifact['sha256'], digest)
        self.assertEqual(artifact['source_url'], 'https://relay.example/data.csv')
        self.assertEqual(artifact['final_url'], 'https://relay.example/data.csv')
        self.assertEqual(artifact['redirects'], ['https://relay.example/start'])
        self.assertEqual(artifact['received_mime'], 'text/csv')
        self.assertEqual(artifact['http_status'], 200)
        self.assertEqual(calls[0]['source_sha256'], digest)
        self.assertEqual(calls[0]['producer_tool'], 'browse_web')
        self.assertEqual(calls[0]['owner'], 7)

    async def test_digest_mismatch_fails_after_publication(self):
        body = b'a,b\n'
        exchange = transport.Exchange('https://relay.example/data.csv', 200,
                                      {'content-type': 'text/csv'}, body)
        renderer = _ToolRenderer('https://relay.example/data.csv', (exchange, []))

        text, artifact, calls = await self._download(
            renderer, lambda kwargs: {'sha256': '0' * 64, 'artifact_url': 'artifact://x'})
        self.assertEqual(artifact['_tool_outcome']['status'], 'failed')
        self.assertEqual(artifact['_tool_outcome']['reason_code'],
                         'browse_publication_digest_mismatch')
        self.assertEqual(len(calls), 1)

    async def test_transport_rejection_before_publication_is_not_run(self):
        renderer = _ToolRenderer(
            'https://relay.example/data.csv',
            transport.BrowseError('browse_download_unavailable'))
        text, artifact, calls = await self._download(renderer, lambda kwargs: {})
        self.assertEqual(artifact['_tool_outcome']['status'], 'not_run')
        self.assertEqual(artifact['_tool_outcome']['reason_code'], 'browse_download_unavailable')
        self.assertEqual(calls, [])

    async def test_unsupported_type_before_publication_is_not_run(self):
        exchange = transport.Exchange('https://relay.example/data.exe', 200,
                                      {'content-type': 'application/octet-stream'}, b'x')
        renderer = _ToolRenderer(exchange.url, (exchange, []))
        text, artifact, calls = await self._download(renderer, lambda kwargs: {})
        self.assertEqual(artifact['_tool_outcome']['status'], 'not_run')
        self.assertEqual(artifact['_tool_outcome']['reason_code'],
                         'browse_download_type_unsupported')
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
